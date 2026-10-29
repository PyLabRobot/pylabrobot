"""Tidy-table tests on results the CLARIOstar decodes from the reader's replies."""

import unittest
from collections import Counter
from datetime import timezone
from typing import Any, Dict, List

from pylabrobot.bmg_labtech.clariostar.driver.features import fluorescence_tests, luminescence_tests
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  RUN_REPLY,
  STATUS_MEASURED,
  STATUS_MEASURING,
  make_absorbance,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.fluorescence_tests import (
  EXCITATION_SPECTRUM,
  SPECTRUM_WELLS,
  fluorescence_plate,
  make_fluorescence,
  spectrum_replies,
)
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_IDLE,
  FakeFTDI,
  load_session_replies,
)
from pylabrobot.lib.absorbance.results import get_grid
from pylabrobot.lib.results.table import COLUMNS, _get_well_columns, get_table
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.testing import AnyioTestBase

EIGHT_WAVELENGTHS = [260, 280, 350, 450, 530, 600, 700, 750]


async def read_absorbance_session(step: str, **arguments: Any) -> Any:
  """Column 1 as the reader answered step `step` of the session."""
  session = load_session_replies()
  io = FakeFTDI(chunk=4096)
  io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
  io.replies.append(
    bytes.fromhex(session[f"{step}_reply" if f"{step}_reply" in session else f"{step}_page"])
  )
  plate = cor_96_wellplate_360uL_Fb("plate")
  absorbance = make_absorbance(io, plate)
  wells = plate.get_items("A1:H1")
  if "start_wavelength" in arguments:
    return await absorbance.measure_spectrum(wells=wells, **arguments)
  return await absorbance.measure(wells=wells, **arguments)


def by(rows: List[Dict[str, Any]], column: str) -> Counter:
  """How many rows have each value of `column`."""
  return Counter(row[column] for row in rows)


class TestShape(AnyioTestBase):
  async def test_absorbance_is_one_row_per_well_and_wavelength(self):
    results = await read_absorbance_session(
      "absorbance_8_wavelengths", wavelengths=EIGHT_WAVELENGTHS
    )
    rows = get_table(results, first_read=3)
    assert len(rows) == 64
    assert all(tuple(row) == COLUMNS for row in rows)
    assert by(rows, "read") == Counter({read: 8 for read in range(3, 11)})
    assert by(rows, "quantity") == Counter(optical_density=64)
    assert [row["wavelength"] for row in rows[::8]] == EIGHT_WAVELENGTHS
    first = rows[0]
    assert [first[key] for key in ("well", "row", "column")] == ["A1", "A", 1]
    assert first["value"] == results[0].data["A1"]
    assert first["start_timestamp"].tzinfo == timezone.utc
    assert first["start_timestamp"] == results[0].start_timestamp.astimezone(timezone.utc)
    assert first["gain"] is None

  async def test_raw_absorbance_adds_the_reference_counts(self):
    results = await read_absorbance_session("absorbance_600_nm", wavelengths=[600], report="raw")
    rows = get_table(results)
    assert by(rows, "quantity") == Counter(counts=8, reference_counts=8)
    references = [row["value"] for row in rows if row["quantity"] == "reference_counts"]
    assert references == list(results[0].references.values())

  async def test_a_spectrum_is_one_row_per_well_and_wavelength(self):
    result = await read_absorbance_session(
      "absorbance_spectrum", start_wavelength=300, end_wavelength=700, step_size=1
    )
    rows = get_table([result])
    assert len(rows) == 8 * 401
    assert by(rows, "mode") == Counter(absorbance_spectrum=8 * 401)
    assert rows[401]["well"] == "B1"
    assert rows[401]["wavelength"] == 300
    assert rows[401]["value"] == result.data["B1"][300]

  async def test_fluorescence_carries_its_settings(self):
    session = load_session_replies()
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(bytes.fromhex(session["fluorescence_column_1_reply"]))
    plate = cor_96_wellplate_360uL_Fb("plate")
    result = await make_fluorescence(io, plate).measure(
      fluorescence_tests.BASELINE, wells=plate.get_items("A1:H1"), gain=1000, focal_height=8.5
    )
    rows = get_table([result])
    assert [row["value"] for row in rows] == [68.0, 62.0, 72.0, 64.0, 67.0, 69.0, 67.0, 68.0]
    row = rows[0]
    assert [row[key] for key in ("mode", "gain", "focal_height", "optic_position", "quantity")] == [
      "fluorescence",
      1000,
      8.5,
      "top",
      "counts",
    ]
    assert (row["excitation_wavelength"], row["emission_wavelength"]) == (485, 528)
    assert (row["excitation_bandwidth"], row["emission_bandwidth"]) == (15, 20)

  async def test_a_fluorescence_spectrum_puts_the_scanned_wavelength_on_its_side(self):
    io = FakeFTDI(chunk=4096)
    io.replies = spectrum_replies("excitation_spectrum")
    plate = fluorescence_plate()
    result = await make_fluorescence(io, plate).measure_spectrum(
      wells=[plate.get_well(well) for well in SPECTRUM_WELLS],
      **{"gain": 1000, "focal_height": 8.5, **EXCITATION_SPECTRUM},
    )
    rows = get_table([result])
    assert len(rows) == 4 * 221
    assert by(rows, "emission_wavelength") == Counter({800: 4 * 221})
    assert [row["excitation_wavelength"] for row in rows[:221]] == list(range(400, 621))
    assert rows[0]["value"] == 41.0

  async def test_luminescence_pivots_back_to_the_plate(self):
    result = await luminescence_tests.read(FakeFTDI())
    plate = luminescence_tests.plate()
    rows = get_table([result])
    assert len(rows) == 96
    grid: List[List[Any]] = [[None] * 12 for _ in range(8)]
    for row in rows:
      grid["ABCDEFGH".index(row["row"])][row["column"] - 1] = row["value"]
    assert grid == get_grid(plate, result.data)
    assert grid == [
      [float(c) for c in line] for line in luminescence_tests.LUMINESCENCE_GROUND_TRUTH
    ]
    assert (rows[0]["gain"], rows[0]["focal_height"]) == (3500, 8.5)


class WellColumnTests(unittest.TestCase):
  def test_identifiers_are_parsed(self):
    self.assertEqual(_get_well_columns("AF48"), {"well": "AF48", "row": "AF", "column": 48})
    for refused in ("a1", "B", "3"):
      with self.subTest(refused=refused), self.assertRaises(ValueError):
        _get_well_columns(refused)


if __name__ == "__main__":
  unittest.main()
