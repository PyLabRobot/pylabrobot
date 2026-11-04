import datetime
import os
import tempfile
import unittest
from typing import Any, Dict, List, Sequence

import pytest

from pylabrobot.lib.absorbance.results import AbsorbanceResult, AbsorbanceSpectrumResult
from pylabrobot.lib.fluorescence.results import FluorescenceResult, FluorescenceSpectrumResult
from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.lib.results.directory import append_results, check_directory
from pylabrobot.lib.results.reading import get_results, load_rows
from pylabrobot.lib.results.table import get_table
from pylabrobot.testing import AnyioTestBase
from pylabrobot.utils.timing import run_cycles


class LoadRowsTests(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.mkdtemp()
    self.path = os.path.join(self.directory, "rows.jsonl")

  def test_missing_file_gives_no_rows(self):
    self.assertEqual(load_rows(os.path.join(self.directory, "not_started")), [])

  def test_cut_last_line_is_skipped(self):
    with open(self.path, "wb") as file:
      file.write(b'{"read": 0}\n{"read": 1}\n{"read": 2, "mo')
    self.assertEqual(load_rows(self.directory), [{"read": 0}, {"read": 1}])

  def test_malformed_complete_line_raises_with_its_line_number(self):
    with open(self.path, "wb") as file:
      file.write(b'{"read": 0}\n{"read": 0,\n{"read": 1}\n')
    with self.assertRaisesRegex(ValueError, "line 2"):
      load_rows(self.directory)


async def write_results(results: Sequence[Any]) -> str:
  """A results directory holding `results`, read 0 on."""
  directory = check_directory(tempfile.mkdtemp())
  await append_results(directory, results)
  return directory


class TestGetResults(AnyioTestBase):
  async def test_each_shared_result_comes_back_as_it_was(self):
    start = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
    end = start + datetime.timedelta(seconds=30)
    times: Dict[str, Any] = {"start_timestamp": start, "end_timestamp": end}
    results: List[Any] = [
      AbsorbanceResult(data={"A1": 0.1, "B1": 0.2}, wavelength=600, temperature=25.0, **times),
      AbsorbanceSpectrumResult(data={"A1": {300: 0.5, 301: 0.6}}, temperature=None, **times),
      FluorescenceResult(
        data={"A1": 100.0},
        excitation_wavelength=485.0,
        excitation_bandwidth=15.0,
        emission_wavelength=528.0,
        emission_bandwidth=20.0,
        temperature=None,
        **times,
      ),
      FluorescenceSpectrumResult(
        data={"A1": {500: 10.0, 510: 12.0}},
        excitation_wavelength=None,
        excitation_bandwidth=10.0,
        emission_wavelength=600.0,
        emission_bandwidth=10.0,
        temperature=None,
        **times,
      ),
      FluorescenceSpectrumResult(
        data={"A1": {600: 10.0, 610: 12.0}},
        excitation_wavelength=485.0,
        excitation_bandwidth=10.0,
        emission_wavelength=None,
        emission_bandwidth=10.0,
        temperature=None,
        **times,
      ),
      LuminescenceResult(
        data={"A1": 41.0},
        emission_wavelength=580.0,
        emission_bandwidth=80.0,
        temperature=None,
        **times,
      ),
    ]
    assert get_results(load_rows(await write_results(results))) == results  # from a directory
    assert get_results(get_table(results)) == results  # from a table, no file

  async def test_a_read_in_a_cycle_comes_back_with_its_cycle(self):
    async def read(cycle: int) -> AbsorbanceResult:
      now = datetime.datetime.now(datetime.timezone.utc)
      return AbsorbanceResult(
        data={"A1": 0.1 * cycle},
        wavelength=600,
        temperature=None,
        start_timestamp=now,
        end_timestamp=now,
      )

    results = await run_cycles(read, cycle_time=0.01, cycles=2)
    loaded = get_results(load_rows(await write_results(results)))
    assert [result.current_cycle["cycle"] for result in loaded] == [0, 1]
    assert loaded == results

  def test_a_read_without_a_shared_result_type_raises_naming_it(self):
    now = datetime.datetime.now(datetime.timezone.utc)
    (row,) = get_table([AbsorbanceResult({"A1": 0.1}, 600, None, now, now)], first_read=1)
    with pytest.raises(ValueError, match="read 1 holds reference_counts"):
      get_results([dict(row, quantity="reference_counts")])
    with pytest.raises(ValueError, match="read 1 is a fluorescence_grid read"):
      get_results([dict(row, mode="fluorescence_grid")])


if __name__ == "__main__":
  unittest.main()
