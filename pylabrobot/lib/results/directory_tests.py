"""Results-directory tests: rows.jsonl appended to, read numbering, cut lines, failed writes."""

import json
import logging
import math
import os
import tempfile
import unittest
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.lib.results import directory
from pylabrobot.lib.results.directory import ROWS, append_results, check_directory
from pylabrobot.lib.results.table import COLUMNS
from pylabrobot.testing import AnyioTestBase


def read_lines(path: str) -> List[Dict[str, Any]]:
  """The JSON objects of a JSON Lines file."""
  with open(path, encoding="utf-8") as f:
    return [json.loads(line) for line in f.read().splitlines()]


def make_result(wells: int = 3) -> AbsorbanceResult:
  """An absorbance result of `wells` wells of column 1."""
  now = datetime.now()
  data = {f"{'ABCDEFGH'[i]}1": 0.1 * i for i in range(wells)}
  return AbsorbanceResult(
    data=data, wavelength=600, temperature=None, start_timestamp=now, end_timestamp=now
  )


class TestDirectory(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    temporary_name = stack.enter_context(tempfile.TemporaryDirectory())
    self.path = check_directory(os.path.join(temporary_name, "a", "b"))
    self.rows = os.path.join(self.path, ROWS)

  async def test_reads_are_appended_and_numbered_after_the_last(self):
    await append_results(self.path, [make_result()])
    await append_results(self.path, [make_result(), make_result(2)])
    rows = read_lines(self.rows)
    assert [row["read"] for row in rows] == [0, 0, 0, 1, 1, 1, 2, 2]
    assert list(rows[0]) == list(COLUMNS)
    assert rows[0]["start_timestamp"].endswith("+00:00")

  async def test_numbering_continues_from_a_last_line_longer_than_a_tail_chunk(self):
    long_row = {"read": 41, "padding": "x" * (3 * directory._TAIL_CHUNK)}
    with open(self.rows, "w", encoding="utf-8") as f:
      f.write(json.dumps({"read": 40}) + "\n" + json.dumps(long_row) + "\n")
    await append_results(self.path, [make_result()])
    assert {row["read"] for row in read_lines(self.rows)[2:]} == {42}

  async def test_a_cut_last_line_is_removed_and_whole_lines_follow(self):
    await append_results(self.path, [make_result()])
    with open(self.rows, "a", encoding="utf-8") as f:
      f.write('{"read": 1, "mode": "absorb')  # a crash mid-write
    await append_results(self.path, [make_result()])
    assert [row["read"] for row in read_lines(self.rows)] == [0, 0, 0, 1, 1, 1]

  async def test_a_value_that_is_not_finite_is_written_as_null(self):
    result = make_result()
    result.data["A1"], result.data["B1"] = math.inf, math.nan
    await append_results(self.path, [result])
    with open(self.rows, encoding="utf-8") as f:
      rows = [json.loads(line, parse_constant=pytest.fail) for line in f]  # strict JSON
    assert [row["value"] for row in rows] == [None, None, 0.2]

  async def test_a_failed_write_is_cut_back_logged_and_raised_once_by_the_next_check(
    self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
  ):
    monkeypatch.setattr(logging.getLogger("pylabrobot"), "propagate", True)
    await append_results(self.path, [make_result()])
    size = os.path.getsize(self.rows)
    with (
      patch.object(directory.os, "fsync", side_effect=OSError(28, "No space left on device")),
      caplog.at_level(logging.ERROR, logger=directory.logger.name),
    ):
      await append_results(self.path, [make_result()])  # logged, not raised
    assert len(caplog.records) == 1
    assert os.path.getsize(self.rows) == size
    with pytest.raises(RuntimeError, match="not all written"):
      check_directory(self.path)
    assert check_directory(self.path) == self.path


if __name__ == "__main__":
  unittest.main()
