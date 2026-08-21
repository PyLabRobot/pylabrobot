import unittest

from pylabrobot.agilent.bravo.tip_offsets import (
  DEFAULT_TIP_OFFSET_TABLE,
  ResolvedTipOffsets,
  TipOffsetEntry,
  TipOffsetTable,
  get_tip_offset_table,
)
from pylabrobot.agilent.bravo.types import TIPBOX_JOG_TOLERANCE

# The two rows transcribed from config/tip_offsets.yaml, written out
# independently as TipOffsetEntry instances.
_EXPECTED_ROWS: tuple[TipOffsetEntry, ...] = (
  TipOffsetEntry(
    head_type="96_d_200",
    tipbox="96 V11 LT200 Tip Box 06880.002",
    tipbox_id="lw-b0704e550d2a",
    tips_off_z_offset=25.0,
    tips_off_w_position=-9.0,
    tips_on_jog_tolerance=12.0,
    tips_on_z_offset=0.0,
  ),
  TipOffsetEntry(
    head_type="384_d_70",
    tipbox="384 V11 ST10 Tip Box 10734.102",
    tipbox_id="lw-4914769d0af7",
    tips_off_z_offset=14.0,
    tips_off_w_position=-7.0,
    tips_on_jog_tolerance=5.0,
    tips_on_z_offset=0.0,
  ),
)


class TranscribedOffsetRowsMatchYamlTests(unittest.TestCase):
  def test_row_count_matches_the_yaml_file(self):
    self.assertEqual(len(DEFAULT_TIP_OFFSET_TABLE.entries), len(_EXPECTED_ROWS))

  def test_every_row_matches_field_by_field(self):
    by_head = {entry.head_type: entry for entry in DEFAULT_TIP_OFFSET_TABLE.entries}
    for expected in _EXPECTED_ROWS:
      with self.subTest(head_type=expected.head_type):
        actual = by_head[expected.head_type]
        self.assertEqual(actual.tipbox, expected.tipbox)
        self.assertEqual(actual.tipbox_id, expected.tipbox_id)
        self.assertEqual(actual.tips_off_z_offset, expected.tips_off_z_offset)
        self.assertEqual(actual.tips_off_w_position, expected.tips_off_w_position)
        self.assertEqual(actual.tips_on_jog_tolerance, expected.tips_on_jog_tolerance)
        self.assertEqual(actual.tips_on_z_offset, expected.tips_on_z_offset)

  def test_lt200_w_position_is_not_rounded_toward_the_mechanical_stop(self):
    entry = next(e for e in DEFAULT_TIP_OFFSET_TABLE.entries if e.head_type == "96_d_200")
    self.assertEqual(entry.tips_off_w_position, -9.0)


class GetTipOffsetTableTests(unittest.TestCase):
  def test_returns_the_default_table(self):
    self.assertIs(get_tip_offset_table(), DEFAULT_TIP_OFFSET_TABLE)


class FindTests(unittest.TestCase):
  def test_finds_by_tipbox_id(self):
    entry = DEFAULT_TIP_OFFSET_TABLE.find("96_d_200", tipbox_id="lw-b0704e550d2a")
    self.assertIsNotNone(entry)
    assert entry is not None
    self.assertEqual(entry.tipbox, "96 V11 LT200 Tip Box 06880.002")

  def test_finds_by_tipbox_name_case_and_whitespace_insensitive(self):
    entry = DEFAULT_TIP_OFFSET_TABLE.find(
      "384_d_70", tipbox_name="  384   v11 st10 TIP box 10734.102  "
    )
    self.assertIsNotNone(entry)
    assert entry is not None
    self.assertEqual(entry.tipbox_id, "lw-4914769d0af7")

  def test_no_match_for_unknown_head_returns_none(self):
    self.assertIsNone(DEFAULT_TIP_OFFSET_TABLE.find("96_pintool", tipbox_id="lw-b0704e550d2a"))

  def test_no_match_when_tipbox_does_not_match(self):
    self.assertIsNone(DEFAULT_TIP_OFFSET_TABLE.find("96_d_200", tipbox_id="not-a-real-id"))

  def test_empty_head_type_returns_none(self):
    self.assertIsNone(DEFAULT_TIP_OFFSET_TABLE.find("", tipbox_id="lw-b0704e550d2a"))


class ResolveWithOverrideTests(unittest.TestCase):
  def test_resolves_matched_entry_values(self):
    resolved = DEFAULT_TIP_OFFSET_TABLE.resolve(
      "96_d_200",
      tipbox_id="lw-b0704e550d2a",
      default_z_offset=15.0,
      default_w_position=-5.0,
    )
    self.assertEqual(
      resolved,
      ResolvedTipOffsets(
        tips_off_z_offset=25.0,
        tips_off_w_position=-9.0,
        tips_on_jog_tolerance=12.0,
        tips_on_z_offset=0.0,
        matched=True,
        source="tip_offsets[96_d_200 / 96 V11 LT200 Tip Box 06880.002]",
      ),
    )

  def test_partial_override_fills_missing_fields_from_defaults(self):
    table = TipOffsetTable(
      [
        TipOffsetEntry(
          head_type="96_d_70",
          tipbox_id="tb-1",
          tips_off_z_offset=20.0,
          # tips_off_w_position / tips_on_jog_tolerance / tips_on_z_offset
          # are left unset (None) on this row.
        )
      ]
    )
    resolved = table.resolve(
      "96_d_70",
      tipbox_id="tb-1",
      default_z_offset=15.0,
      default_w_position=-6.0,
      default_jog_tolerance=8.0,
      default_z_on_offset=1.0,
    )
    self.assertEqual(resolved.tips_off_z_offset, 20.0)  # overridden
    self.assertEqual(resolved.tips_off_w_position, -6.0)  # default
    self.assertEqual(resolved.tips_on_jog_tolerance, 8.0)  # default
    self.assertEqual(resolved.tips_on_z_offset, 1.0)  # default
    self.assertTrue(resolved.matched)


class ResolveWithoutOverrideTests(unittest.TestCase):
  def test_unmatched_head_falls_back_entirely_to_defaults(self):
    resolved = DEFAULT_TIP_OFFSET_TABLE.resolve(
      "96_pintool",
      tipbox_id="lw-b0704e550d2a",
      default_z_offset=15.0,
      default_w_position=-5.0,
    )
    self.assertEqual(
      resolved,
      ResolvedTipOffsets(
        tips_off_z_offset=15.0,
        tips_off_w_position=-5.0,
        tips_on_jog_tolerance=TIPBOX_JOG_TOLERANCE,
        tips_on_z_offset=0.0,
        matched=False,
        source="caller defaults",
      ),
    )

  def test_empty_table_always_falls_back_to_defaults(self):
    table = TipOffsetTable([])
    resolved = table.resolve(
      "96_d_200",
      tipbox_id="anything",
      default_z_offset=1.0,
      default_w_position=2.0,
    )
    self.assertFalse(resolved.matched)
    self.assertEqual(resolved.tips_off_z_offset, 1.0)
    self.assertEqual(resolved.tips_off_w_position, 2.0)

  def test_custom_jog_tolerance_and_z_on_offset_defaults_are_honored(self):
    table = TipOffsetTable([])
    resolved = table.resolve(
      "96_d_200",
      tipbox_id="anything",
      default_z_offset=1.0,
      default_w_position=2.0,
      default_jog_tolerance=3.0,
      default_z_on_offset=4.0,
    )
    self.assertEqual(resolved.tips_on_jog_tolerance, 3.0)
    self.assertEqual(resolved.tips_on_z_offset, 4.0)


if __name__ == "__main__":
  unittest.main()
