"""Offline fluorescence reference operation tests."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_absorbance_tests import burst
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


class FluorescenceTests(unittest.IsolatedAsyncioTestCase):
  async def test_unrecognized_plate_is_rejected_before_usb(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb:
      reader = InfiniteM1000Pro()
      with self.assertRaises(ValueError):
        await reader.read_fluorescence_reference(
          biorad_96_wellplate_200uL_Vb("plate"), plate_definition="unknown"
        )
      usb.return_value.write.assert_not_called()

  async def test_bad_calibration_faults_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB"):
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      with (
        patch.object(reader, "_position", new=AsyncMock(return_value="IN")),
        patch.object(reader, "_control_command", new=AsyncMock()),
        patch.object(reader, "_absorbance_burst", new=AsyncMock(return_value=[bytes(18)])),
        self.assertRaisesRegex(ValueError, "calibration"),
      ):
        await reader.read_fluorescence_reference(
          biorad_96_wellplate_200uL_Vb("plate"), plate_definition="GRE96ft"
        )
      self.assertTrue(reader._faulted)
      self.assertFalse(reader._ready)

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
      elif (
        cmd.startswith(("ABSOLUTE MTP,", "POSITION ", "FLASH ", "BEAM "))
        and cmd != "POSITION CLEAR"
        or cmd in ("MODE FI.TOP", "TERMINATE")
      ):
        response = reader_frame("ST", seq)
      else:
        response = reader_frame("+", seq)
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
      result = await reader.read_fluorescence_reference(plate, plate_definition="GRE96ft")
      self.assertEqual(set(result.data), set(data["expected"]))
      for well, value in result.data.items():
        self.assertEqual(round(value), data["expected"][well], well)
      self.assertEqual(commands[-1], "TERMINATE")
      self.assertEqual(scan, 8)
