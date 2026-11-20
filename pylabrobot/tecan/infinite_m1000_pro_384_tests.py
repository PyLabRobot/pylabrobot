"""Offline checks for the captured 384-well A1:H2 absorbance profile."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_384_wellplate_50uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import InfiniteM1000Pro
from pylabrobot.tecan.infinite_m1000_pro_absorbance_tests import burst
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame


class SubsetAbsorbanceTests(unittest.IsolatedAsyncioTestCase):
  async def test_unsupported_profile_is_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      reader = InfiniteM1000Pro()
      plate = biorad_384_wellplate_50uL_Vb("plate")
      with self.assertRaises(ValueError):
        await reader.read_absorbance_384_reference(plate, plate_definition="other")
      usb_class.return_value.write.assert_not_called()

  async def test_captured_payload_matches_reference_workbook(self):
    fixture = json.loads(
      (Path(__file__).parent / "test_data" / "absorbance_384_reference.json").read_text()
    )
    blocks = [[bytes.fromhex(b) for b in tx["blocks"]] for tx in fixture]
    expected = {
      "A1": 0.0905,
      "C1": 0.1747,
      "E1": 0.2519,
      "G1": 0.3661,
      "A2": 0.0438,
      "C2": 0.1743,
      "E2": 0.2636,
      "G2": 0.3632,
      "B2": 0.1367,
      "D2": 0.2372,
      "F2": 0.3161,
      "H2": 0.4107,
      "B1": 0.1363,
      "D1": 0.2202,
      "F1": 0.3154,
      "H1": 0.4144,
    }
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB"):
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      with (
        patch.object(reader, "_position", new=AsyncMock(return_value="IN")),
        patch.object(reader, "_control_command", new=AsyncMock()) as control,
        patch.object(reader, "_absorbance_burst", new=AsyncMock(side_effect=blocks)),
      ):
        plate = biorad_384_wellplate_50uL_Vb("well_map_only")
        result = await reader.read_absorbance_384_reference(plate, plate_definition="GRE384ft")
      self.assertEqual(set(result.data), set(expected))
      for well, value in expected.items():
        self.assertLessEqual(abs(result.data[well] - value), 0.00005001, well)
      self.assertEqual(control.await_args_list[-1].args[0], "TERMINATE")

  async def test_captured_profile_through_fragmented_transport(self):
    cal = bytes.fromhex(
      "0001 1770 00000000 0000 0001 0001 0001 0001 0064 0064 0064 0064 076c 076c 076c 076c 0001 0064 07d0"
    )
    packet = bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    commands = []
    pending = bytearray()

    async def write(data):
      cmd = data[1 : data.index(b"\x03")].decode("ascii")
      seq = data[-5]
      commands.append(cmd)
      if cmd == "?ABSOLUTE MTP,POS":
        reply = reader_frame("IN", seq)
      elif cmd == "PREPARE REF":
        reply = burst(1024, [cal], seq)
      elif cmd.startswith("SCANX "):
        reply = burst(1040, [packet, packet], seq)
      elif (
        cmd.startswith(("ABSOLUTE MTP,", "POSITION ", "FLASH ", "BEAM "))
        and cmd != "POSITION CLEAR"
        or cmd in ("MODE ABS", "TERMINATE")
      ):
        reply = reader_frame("ST", seq)
      else:
        reply = reader_frame("+", seq)
      pending.extend(reply)

    async def read(timeout):
      del timeout
      data = bytes(pending[:11])
      del pending[:11]
      if not data:
        raise TimeoutError("No simulated response")
      return data

    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb_class.return_value.write = AsyncMock(side_effect=write)
      usb_class.return_value.read = AsyncMock(side_effect=read)
      reader = InfiniteM1000Pro()
      reader._ready = reader._initialized = True
      plate = biorad_384_wellplate_50uL_Vb("well_map_only")
      result = await reader.read_absorbance_384_reference(plate, plate_definition="GRE384ft")
      self.assertEqual(set(result.data), {f"{row}{col}" for row in "ABCDEFGH" for col in (1, 2)})
      self.assertTrue(all(abs(v - 0.3010299956639812) < 1e-12 for v in result.data.values()))
      self.assertEqual(commands[-1], "TERMINATE")
      self.assertIn("SCANX 12130,16630,2", commands)
      self.assertIn("SCANX 16630,12130,2", commands)
