"""Golden-frame test: tip length resolution against the reference implementation.

``testdata/tip_lengths_golden.json`` holds ``get_tip_length_mm(head_type, tip_id)``
for every (head type, tip id) combination -- 17 head types x 11 tip ids -- captured
directly from the reference implementation's tip catalogue, by calling
``get_tip_length_mm(head, tip_id)`` for every head type and every tip id its
own tip-definition iterator enumerates, then mapping each head type onto its
ported lowercase :data:`HeadType` literal (e.g. ``"96_d_200"``).

This is what actually pins length resolution to the reference implementation's
*behavior* rather than to a config file: its tip-lookup function reads a
YAML-backed store first and only falls back to a hardcoded per-head table
when that store has no compatible rows for the head, so the value a caller
gets for a given (head, tip) pair is not obvious from either source alone --
only running the reference code settles it. Every entry here (including the
174 combinations that resolve to ``null``, where a tip is not compatible with
a head or has no measured length) is a value this port's
:func:`~pylabrobot.agilent.bravo.tips.get_tip_length_mm` must reproduce exactly,
since tip length feeds the Z-height calculation for aspirate/dispense.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pylabrobot.agilent.bravo.tips import get_tip_length_mm

_GOLDEN_PATH = Path(__file__).parent / "testdata" / "tip_lengths_golden.json"
with open(_GOLDEN_PATH) as _f:
  GOLDEN: list = json.load(_f)


class TipLengthMatchesReferenceImplementationTests(unittest.TestCase):
  def test_fixture_covers_every_head_and_tip(self):
    # 17 head types x 11 tip ids from the reference catalogue.
    self.assertEqual(len(GOLDEN), 187)

  def test_every_head_tip_pair_matches_the_reference_value(self):
    for row in GOLDEN:
      with self.subTest(head_type=row["head_type"], tip_id=row["tip_id"]):
        self.assertEqual(
          get_tip_length_mm(row["head_type"], row["tip_id"]),
          row["length_mm"],
        )

  def test_at_least_one_compatible_pair_per_short_tip_head(self):
    # Sanity check that the fixture is not accidentally all-null: every
    # short-tip head resolves st_10ul to its measured 19.9 mm length.
    short_tip_heads = {"16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"}
    matched = {
      row["head_type"] for row in GOLDEN if row["tip_id"] == "st_10ul" and row["length_mm"] == 19.9
    }
    self.assertEqual(matched, short_tip_heads)


if __name__ == "__main__":
  unittest.main()
