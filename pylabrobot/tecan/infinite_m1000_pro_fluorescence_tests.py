"""Offline fluorescence operation tests."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import OVER, InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_absorbance_tests import burst, reply
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


class FluorescenceTests(unittest.IsolatedAsyncioTestCase):
  async def test_unrecognized_plate_is_rejected_before_usb(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb:
      reader = InfiniteM1000Pro()
      with self.assertRaises(ValueError):
        await reader.read_fluorescence(
          biorad_96_wellplate_200uL_Vb("plate"), plate_definition="unknown"
        )
      usb.return_value.write.assert_not_called()

  async def test_bad_calibration_raises(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB"):
      reader = InfiniteM1000Pro()
      reader._ready = True
      with (
        patch.object(reader, "_position", new=AsyncMock(return_value="IN")),
        patch.object(reader, "_query", new=AsyncMock(return_value="+")),
        patch.object(reader, "_control_command", new=AsyncMock()),
        patch.object(reader, "_absorbance_burst", new=AsyncMock(return_value=[bytes(18)])),
        self.assertRaisesRegex(ValueError, "calibration"),
      ):
        await reader.read_fluorescence(
          biorad_96_wellplate_200uL_Vb("plate"),
          excitation_wavelength=230,
          emission_wavelength=280,
          plate_definition="GRE96ft",
        )
      self.assertTrue(reader._ready)

  async def test_saturated_signal_reported_as_over(self):
    data = json.loads(
      (Path(__file__).parent / "test_data" / "fluorescence_reference.json").read_text()
    )
    blocks = [[bytes.fromhex(b) for b in tx["blocks"]] for tx in data["transactions"]]
    saturated = bytearray(blocks[1][0])
    saturated[12:14] = b"\xff\xff"  # count of the first scan point
    blocks[1][0] = bytes(saturated)
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB"):
      reader = InfiniteM1000Pro()
      reader._ready = True
      with (
        patch.object(reader, "_position", new=AsyncMock(return_value="IN")),
        patch.object(reader, "_query", new=AsyncMock(return_value="+")),
        patch.object(reader, "_control_command", new=AsyncMock()) as control,
        patch.object(reader, "_absorbance_burst", new=AsyncMock(side_effect=blocks)) as scans,
      ):
        result = await reader.read_fluorescence(
          biorad_96_wellplate_200uL_Vb("well_map_only"),
          excitation_wavelength=230,
          emission_wavelength=280,
          plate_definition="GRE96ft",
        )
      self.assertEqual(scans.await_count, 9)
      self.assertEqual(
        [c.args[0] for c in control.await_args_list][-2:], ["TERMINATE", "SIBUS TCAN SSL 0"]
      )
      self.assertEqual(result.data["A1"], OVER)

  async def test_captured_measurement_through_fragmented_transport(self):
    data = json.loads(
      (Path(__file__).parent / "test_data" / "fluorescence_reference.json").read_text()
    )
    transactions = data["transactions"]
    pending = bytearray()
    commands = []
    scan = 0

    async def write(data):
      nonlocal scan
      cmd = data[1 : data.index(b"\x03")].decode("ascii")
      seq = data[-5]
      commands.append(cmd)
      if cmd == "?ABSOLUTE MTP,POS":
        response = reader_frame("IN", seq)
      elif cmd == "PREPARE REF":
        response = burst(512, [bytes.fromhex(transactions[0]["blocks"][0])], seq)
      elif cmd.startswith("SCANX "):
        scan += 1
        self.assertEqual(cmd, transactions[scan]["command"])
        response = burst(528, [bytes.fromhex(b) for b in transactions[scan]["blocks"]], seq)
      elif cmd == "MODE FI.TOP":
        response = reader_frame("ST", seq)
      else:
        response = reply(cmd, seq)
      pending.extend(response)

    async def read(timeout):
      del timeout
      chunk = bytes(pending[:13])
      del pending[:13]
      if not chunk:
        raise TimeoutError("No simulated response")
      return chunk

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb:
      usb.return_value.write = AsyncMock(side_effect=write)
      usb.return_value.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      plate = biorad_96_wellplate_200uL_Vb("well_map_only")
      result = await reader.read_fluorescence(
        plate, excitation_wavelength=230, emission_wavelength=280, plate_definition="GRE96ft"
      )
      self.assertEqual(set(result.data), set(data["expected"]))
      for well, value in result.data.items():
        self.assertEqual(round(value), data["expected"][well], well)
      self.assertEqual(
        commands[-3:], ["TERMINATE", "CONFIG CHECK,FLASHCOUNT=1008", "SIBUS TCAN SSL 0"]
      )
      self.assertEqual(scan, 8)
      self.assertEqual(result.excitation_wavelength, 230)
      self.assertEqual(result.temperature, 25.0)

  async def test_parameters_and_well_selection(self):
    cal = bytes.fromhex("12f2 00000000 0000 0096 0064 0000 0001 07d0")
    point = bytes.fromhex("0001 12f2 14e6 00000000 0000 03e8 07d0")
    pending = bytearray()
    commands = []

    async def write(data):
      cmd = data[1 : data.index(b"")].decode("ascii")
      seq = data[-5]
      commands.append(cmd)
      if cmd == "PREPARE REF":
        pending.extend(burst(512, [cal], seq))
      elif cmd.startswith("SCANX "):
        pending.extend(burst(528, [point] * 2, seq))
      elif cmd == "MODE FI.TOP":
        pending.extend(reader_frame("ST", seq))
      else:
        pending.extend(reply(cmd, seq))

    async def read(timeout):
      del timeout
      chunk = bytes(pending)
      pending.clear()
      return chunk

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb:
      usb.return_value.write = AsyncMock(side_effect=write)
      usb.return_value.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      reader._ready = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await reader.read_fluorescence(
        plate,
        plate.get_items(["B5", "B6"]),
        485,
        535,
        plate_definition="GRE96ft",
        excitation_bandwidth=9,
        emission_bandwidth=20,
        gain=150,
        flashes=30,
        integration_time=40,
        lag_time=10,
        settle_time=5,
        z=19000,
      )
    for command in (
      "EXCITATION 1,FI,4850,90,0",
      "EMISSION 0,FI,5350,200,0",
      "TIME 0,INTEGRATION=40",
      "TIME 1,LAG=10",
      "TIME 0,READDELAY=5000",
      "GAIN 1,VALUE=150",
      "READS 0,NUMBER=30",
      "POSITION 1,Z=19000",
      "ABSOLUTE MTP,X=50380,Y=20240",
      "SCANX 50380,59380,2",
    ):
      self.assertIn(command, commands)
    self.assertEqual(sum(c.startswith("SCANX") for c in commands), 1)
    self.assertEqual(set(result.data), {"B5", "B6"})
    self.assertAlmostEqual(result.data["B5"], (1000 - 100) * 2000 / 2000)
    self.assertEqual(result.emission_bandwidth, 20)

  async def test_sub_tenth_wavelength_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb:
      reader = InfiniteM1000Pro()
      reader._ready = True
      with self.assertRaisesRegex(ValueError, "0.1 nm"):
        await reader.read_fluorescence(
          biorad_96_wellplate_200uL_Vb("plate"), None, 485.05, plate_definition="GRE96ft"
        )
      usb.return_value.write.assert_not_called()
