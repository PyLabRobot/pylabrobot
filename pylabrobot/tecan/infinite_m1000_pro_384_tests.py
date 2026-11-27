"""Offline checks for the captured 384-well A1:H2 absorbance profile."""

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import biorad_384_wellplate_50uL_Vb
from pylabrobot.tecan.infinite_m1000_pro import OVER, InfiniteM1000Pro


class SubsetAbsorbanceTests(unittest.IsolatedAsyncioTestCase):
  async def test_unsupported_profile_is_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      reader = InfiniteM1000Pro()
      plate = biorad_384_wellplate_50uL_Vb("plate")
      with self.assertRaises(ValueError):
        await reader.read_absorbance(plate, plate_definition="other")
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
        patch.object(reader, "_query", new=AsyncMock(return_value="+")),
        patch.object(reader, "_control_command", new=AsyncMock()) as control,
        patch.object(reader, "_absorbance_burst", new=AsyncMock(side_effect=blocks)),
      ):
        plate = biorad_384_wellplate_50uL_Vb("well_map_only")
        result = await reader.read_absorbance(
          plate, plate.get_items("A1:H2"), plate_definition="GRE384ft"
        )
      self.assertEqual(set(result.data), set(expected))
      for well, value in expected.items():
        self.assertLessEqual(abs(result.data[well] - value), 0.00005001, well)
      self.assertEqual(
        [c.args[0] for c in control.await_args_list][-2:], ["TERMINATE", "SIBUS TCAN SSL 0"]
      )

  async def test_saturated_signal_reported_as_over(self):
    fixture = json.loads(
      (Path(__file__).parent / "test_data" / "absorbance_384_reference.json").read_text()
    )
    blocks = [[bytes.fromhex(b) for b in tx["blocks"]] for tx in fixture]
    saturated = bytearray(blocks[1][0])
    saturated[10:12] = b"\xff\xff"  # channel 0 count of the first scan point
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
        plate = biorad_384_wellplate_50uL_Vb("well_map_only")
        result = await reader.read_absorbance(
          plate, plate.get_items("A1:H2"), plate_definition="GRE384ft"
        )
      self.assertEqual(scans.await_count, 3)
      self.assertEqual(
        [c.args[0] for c in control.await_args_list][-2:], ["TERMINATE", "SIBUS TCAN SSL 0"]
      )
      self.assertEqual(result.data["A1"], OVER)
      self.assertEqual(sum(v == OVER for v in result.data.values()), 1)
