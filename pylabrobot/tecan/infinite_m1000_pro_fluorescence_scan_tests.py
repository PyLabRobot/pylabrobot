"""Offline tests for M1000 Pro fluorescence scans and optimal gain."""

import struct
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import (
  InfiniteM1000Pro,
  _calculate_optimal_gain,
  _scan_correction,
)
from pylabrobot.tecan.infinite_m1000_pro_absorbance_tests import burst, reply
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


def calibration(ex, gain, dark=100, ref_dark=0, ref_bright=2000):
  return struct.pack(">HI6H", ex, 0, 0, gain, dark, 0, ref_dark, ref_bright)


def point(ex, em, count, reference=2000):
  return struct.pack(">HHHIHHH", 1, ex, em, 0, 0, count, reference)


class FluorescenceReader:
  """Synthetic transport for fluorescence: blocks come from `on_scan(command)`."""

  def __init__(self, prepare, on_scan):
    self.prepare = prepare
    self.on_scan = on_scan
    self.pending = bytearray()
    self.commands = []

  async def write(self, data):
    command = data[1 : data.index(b"\x03")].decode("ascii")
    sequence = data[-5]
    self.commands.append(command)
    if command == "PREPARE REF":
      self.pending.extend(burst(512, [self.prepare(self.commands)], sequence))
    elif command.startswith("SCANX "):
      self.pending.extend(burst(528, self.on_scan(self.commands), sequence))
    elif command in ("MODE FI.TOP", "PREPARE NOREF"):
      self.pending.extend(reader_frame("BY#T5000", sequence) + reader_frame("ST", sequence))
    elif command == "?FLASH ENERGY":
      self.pending.extend(reader_frame("LOW", sequence))
    else:
      self.pending.extend(reply(command, sequence))

  async def read(self, timeout):
    del timeout
    if not self.pending:
      raise TimeoutError("no response")
    chunk = bytes(self.pending[:17])
    del self.pending[:17]
    return chunk

  def device(self, usb_class):
    usb = usb_class.return_value
    usb.write = AsyncMock(side_effect=self.write)
    usb.read = AsyncMock(side_effect=self.read)
    device = InfiniteM1000Pro()
    device._ready = True
    return device


class FluorescenceScanMathTests(unittest.TestCase):
  def test_correction_is_bandwidth_window_mean(self):
    """i-control's DetermineFaktorMean: n = bandwidth/1 nm values ending just above λ."""
    table = [float(i) for i in range(801)]  # value = index = wavelength - 200 nm
    # 300.0 nm, 10 nm bandwidth: indices 95..104 -> mean 99.5
    self.assertEqual(_scan_correction(table, 3000, 100), 99.5)
    # 5 nm bandwidth: indices 98..102 -> 100
    self.assertEqual(_scan_correction(table, 3000, 50), 100.0)

  def test_optimal_gain_formula(self):
    """Signal scales as gain**6.8 (i-control's CalculateOptimalGain)."""
    self.assertEqual(_calculate_optimal_gain(100, 1000, 1000), 100)
    self.assertIn(_calculate_optimal_gain(100, 1000, 1000 * 2**6.8), (199, 200))
    self.assertEqual(_calculate_optimal_gain(200, 4000, 1000), 163)


class FluorescenceSpectrumTests(unittest.IsolatedAsyncioTestCase):
  async def test_excitation_scan_commands_and_correction(self):
    wavelengths = [3000, 3050, 3100]

    def scan(commands):
      return [point(w, 5000, 1100) for w in wavelengths]

    fake = FluorescenceReader(lambda c: calibration(4850, 100), scan)
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      device._correction["EXCORR"] = [2.0] * 801
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_fluorescence_spectrum(
        plate,
        plate.get_items(["A1", "A2"]),
        scan="excitation",
        start_wavelength=300,
        end_wavelength=310,
        step_size=5,
        emission_wavelength=500,
        excitation_bandwidth=10,
        plate_definition="GRE96ft",
      )
    for command in (
      "EXCITATION 0,FI,4850,100,0",
      "EMISSION 0,FI,5000,50,0",
      "EXCITATION 1,FI,3000~3100:50,100,0",
      "EMISSION 1,FI,5000,50,0",
    ):
      self.assertIn(command, fake.commands)
    self.assertNotIn("BEAM DIAMETER=2500", fake.commands)
    self.assertEqual(
      [c for c in fake.commands if c.startswith(("ABSOLUTE MTP,X", "SCANX"))],
      [
        "ABSOLUTE MTP,X=14380,Y=11240",
        "ABSOLUTE MTP,X=14380,Y=11240",
        "SCANX 14380,14380,1",
        "ABSOLUTE MTP,X=23380,Y=11240",
        "SCANX 23380,23380,1",
      ],
    )
    self.assertEqual(
      fake.commands[-3:], ["TERMINATE", "CONFIG CHECK,FLASHCOUNT=1002", "SIBUS TCAN SSL 0"]
    )
    self.assertEqual(result.data["A1"], {300: 2000.0, 305: 2000.0, 310: 2000.0})
    self.assertIsNone(result.excitation_wavelength)
    self.assertEqual(result.emission_wavelength, 500)

  async def test_emission_scan_requires_fixed_excitation(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = InfiniteM1000Pro()
      device._ready = True
      with self.assertRaisesRegex(ValueError, "excitation_wavelength"):
        await device.read_fluorescence_spectrum(
          biorad_96_wellplate_200uL_Vb("plate"),
          scan="emission",
          start_wavelength=450,
          end_wavelength=550,
          step_size=5,
          plate_definition="GRE96ft",
        )
      usb_class.return_value.write.assert_not_called()


class OptimalGainTests(unittest.IsolatedAsyncioTestCase):
  async def test_gain_search_then_measurement(self):
    """A simulated detector (counts ∝ gain**6.8, A2 twice as bright) converges and is measured."""
    state = {"gain": 0}
    brightness = {14380: 1.0, 23380: 2.0}

    def current_gain(commands):
      for command in reversed(commands):
        if command.startswith(("GAIN 1,VALUE=", "GAIN 0,VALUE=")):
          return int(command.split("=")[1])
      return 0

    def scan(commands):
      gain = current_gain(commands)
      state["gain"] = gain
      x = int(commands[-1].split()[1].split(",")[0])
      xs = [x] if commands[-1].endswith(",1") else [14380, 23380]
      return [
        point(4850, 5350, min(60000, int(100 + brightness[p] * 47500 * (gain / 200) ** 6.8)))
        for p in xs
      ]

    fake = FluorescenceReader(lambda c: calibration(4850, current_gain(c)), scan)
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_fluorescence(
        plate, plate.get_items(["A1", "A2"]), 485, 535, plate_definition="GRE96ft", gain="optimal"
      )
    self.assertIn("READS 1,NUMBER=3", fake.commands)
    self.assertIn("PREPARE NOREF", fake.commands)
    self.assertEqual(fake.commands.count("PREPARE REF"), 1)
    # brightest well (A2) ends near the target: 2 * (g / 200)**6.8 ≈ 1 -> g ≈ 181
    self.assertTrue(175 <= result.gain <= 187, result.gain)
    self.assertIn(f"GAIN 0,VALUE={result.gain}", fake.commands)
    self.assertEqual(fake.commands[-3][:9], "TERMINATE")
