import unittest

from pylabrobot.agilent.bravo.tips import (
  _TIP_DEFINITIONS,
  TipDefinition,
  get_default_tip_id_for_head,
  get_tip_capacity_ul,
  get_tip_definition,
  get_tip_definition_by_id,
  get_tip_definitions_for_head,
  get_tip_id_for_capacity,
  get_tip_length_mm,
  serialize_tip_options_for_head,
)
from pylabrobot.agilent.bravo.types import HeadType

_SHORT_TIP_HEADS: tuple[HeadType, ...] = (
  "16_d_st",
  "384_d_70",
  "384_d_70_s2",
  "96_d_70",
  "96_d_70_s2",
)
_LONG_TIP_HEADS: tuple[HeadType, ...] = ("8_d_lt", "96_d_200", "96_d_200_s2")
_PINTOOL_HEADS: tuple[HeadType, ...] = ("1536_pintool", "384_pintool", "96_pintool")

# Every row transcribed from config/tips.yaml, in file order, written out
# independently as TipDefinition instances. This is the ground truth the
# table-driven test below compares _TIP_DEFINITIONS against, so a mistyped
# digit in tips.py fails loudly.
_EXPECTED_YAML_ROWS: tuple[TipDefinition, ...] = (
  TipDefinition("st_10ul", 10.0, "10 uL", 19.9, "measured", _SHORT_TIP_HEADS),
  TipDefinition("st_15ul", 15.0, "15 uL", None, "vendor-source-option", _SHORT_TIP_HEADS),
  TipDefinition("lt_200ul", 200.0, "200 uL", None, "vendor-source-option", _LONG_TIP_HEADS),
  TipDefinition("lt_250ul", 250.0, "250 uL", 55.2, "vendor-source-default", _LONG_TIP_HEADS),
  TipDefinition("st_30ul", 30.0, "30 uL", 26.1, "vendor-source-comment", _SHORT_TIP_HEADS),
  TipDefinition("st_50ul", 50.0, "50 uL", None, "vendor-source-option", _SHORT_TIP_HEADS),
  TipDefinition("st_51ul", 51.0, "51 uL", None, "vendor-source-option", _SHORT_TIP_HEADS),
  TipDefinition("st_70ul", 70.0, "70 uL", None, "vendor-source-option", _SHORT_TIP_HEADS),
  TipDefinition("pin_fp1cb", 0.0, "FP1CB", None, "vendor-source-option", _PINTOOL_HEADS),
  TipDefinition("pin_fp1n", 0.0, "FP1N", None, "vendor-source-option", _PINTOOL_HEADS),
  TipDefinition("pin_fp1t", 0.0, "FP1T", None, "vendor-source-option", _PINTOOL_HEADS),
)

_NULL_LENGTH_TIP_IDS = frozenset(
  {"st_15ul", "lt_200ul", "st_50ul", "st_51ul", "st_70ul", "pin_fp1cb", "pin_fp1n", "pin_fp1t"}
)


class TranscribedTipRowsMatchYamlTests(unittest.TestCase):
  """Table-driven check that every transcribed row matches config/tips.yaml."""

  def test_row_count_matches_the_yaml_file(self):
    self.assertEqual(len(_TIP_DEFINITIONS), len(_EXPECTED_YAML_ROWS))

  def test_every_row_matches_field_by_field(self):
    by_id = {tip.tip_id: tip for tip in _TIP_DEFINITIONS}
    for expected in _EXPECTED_YAML_ROWS:
      with self.subTest(tip_id=expected.tip_id):
        actual = by_id.get(expected.tip_id)
        self.assertIsNotNone(actual, f"{expected.tip_id} missing from _TIP_DEFINITIONS")
        assert actual is not None
        self.assertEqual(actual.capacity_ul, expected.capacity_ul)
        self.assertEqual(actual.label, expected.label)
        self.assertEqual(actual.length_mm, expected.length_mm)
        self.assertEqual(actual.source, expected.source)
        self.assertEqual(tuple(actual.compatible_heads), tuple(expected.compatible_heads))

  def test_null_length_rows_preserve_none_rather_than_a_number(self):
    actual_null_ids = {row.tip_id for row in _EXPECTED_YAML_ROWS if row.length_mm is None}
    self.assertEqual(actual_null_ids, set(_NULL_LENGTH_TIP_IDS))
    by_id = {tip.tip_id: tip for tip in _TIP_DEFINITIONS}
    for tip_id in _NULL_LENGTH_TIP_IDS:
      with self.subTest(tip_id=tip_id):
        self.assertIsNone(by_id[tip_id].length_mm)

  def test_measured_length_rows_keep_their_exact_value(self):
    by_id = {tip.tip_id: tip for tip in _TIP_DEFINITIONS}
    self.assertEqual(by_id["st_10ul"].length_mm, 19.9)
    self.assertEqual(by_id["st_30ul"].length_mm, 26.1)
    self.assertEqual(by_id["lt_250ul"].length_mm, 55.2)

  def test_no_duplicate_tip_ids(self):
    ids = [tip.tip_id for tip in _TIP_DEFINITIONS]
    self.assertEqual(len(ids), len(set(ids)))


class GetTipDefinitionsForHeadTests(unittest.TestCase):
  def test_returns_only_tips_compatible_with_the_head(self):
    tips = get_tip_definitions_for_head("96_d_70")
    ids = {tip.tip_id for tip in tips}
    self.assertEqual(ids, {"st_10ul", "st_15ul", "st_30ul", "st_50ul", "st_51ul", "st_70ul"})

  def test_long_tip_head_only_sees_long_tips(self):
    tips = get_tip_definitions_for_head("8_d_lt")
    ids = {tip.tip_id for tip in tips}
    self.assertEqual(ids, {"lt_200ul", "lt_250ul"})

  def test_pintool_head_only_sees_pintool_options(self):
    tips = get_tip_definitions_for_head("96_pintool")
    ids = {tip.tip_id for tip in tips}
    self.assertEqual(ids, {"pin_fp1cb", "pin_fp1n", "pin_fp1t"})

  def test_results_are_sorted_by_capacity(self):
    tips = get_tip_definitions_for_head("96_d_70")
    capacities = [tip.capacity_ul for tip in tips]
    self.assertEqual(capacities, sorted(capacities))

  def test_unrecognized_head_type_returns_empty_list(self):
    self.assertEqual(get_tip_definitions_for_head("not_a_real_head"), [])

  def test_head_type_lookup_is_case_insensitive(self):
    self.assertEqual(
      [t.tip_id for t in get_tip_definitions_for_head("96_D_70")],
      [t.tip_id for t in get_tip_definitions_for_head("96_d_70")],
    )


class GetTipDefinitionTests(unittest.TestCase):
  def test_resolves_by_tip_id(self):
    tip = get_tip_definition("96_d_70", "st_30ul")
    self.assertIsNotNone(tip)
    assert tip is not None
    self.assertEqual(tip.tip_id, "st_30ul")

  def test_resolves_by_capacity(self):
    tip = get_tip_definition("96_d_70", 30.0)
    self.assertIsNotNone(tip)
    assert tip is not None
    self.assertEqual(tip.tip_id, "st_30ul")

  def test_capacity_match_uses_a_small_tolerance(self):
    tip = get_tip_definition("96_d_70", 30.0000001)
    self.assertIsNotNone(tip)
    assert tip is not None
    self.assertEqual(tip.tip_id, "st_30ul")

  def test_incompatible_capacity_for_head_returns_none(self):
    # 200 uL is not compatible with a 96_d_70 (short-tip) head.
    self.assertIsNone(get_tip_definition("96_d_70", 200.0))

  def test_none_input_returns_none(self):
    self.assertIsNone(get_tip_definition("96_d_70", None))


class GetTipDefinitionByIdTests(unittest.TestCase):
  def test_finds_a_tip_regardless_of_head(self):
    tip = get_tip_definition_by_id("lt_250ul")
    self.assertIsNotNone(tip)
    assert tip is not None
    self.assertEqual(tip.capacity_ul, 250.0)

  def test_unknown_id_returns_none(self):
    self.assertIsNone(get_tip_definition_by_id("does-not-exist"))

  def test_empty_id_returns_none(self):
    self.assertIsNone(get_tip_definition_by_id(""))
    self.assertIsNone(get_tip_definition_by_id(None))


class GetTipLengthMmTests(unittest.TestCase):
  def test_measured_tip_returns_its_length(self):
    self.assertEqual(get_tip_length_mm("96_d_70", "st_10ul"), 19.9)

  def test_unmeasured_tip_returns_none(self):
    self.assertIsNone(get_tip_length_mm("96_d_70", "st_15ul"))

  def test_unresolvable_tip_returns_none(self):
    self.assertIsNone(get_tip_length_mm("96_d_70", "no_such_tip"))


class GetTipCapacityUlTests(unittest.TestCase):
  def test_resolved_tip_returns_its_capacity(self):
    self.assertEqual(get_tip_capacity_ul("96_d_70", "st_30ul"), 30.0)

  def test_unresolved_input_falls_back_to_numeric_parse(self):
    self.assertEqual(get_tip_capacity_ul("96_d_70", 123.0), 123.0)

  def test_unresolved_non_numeric_input_returns_zero(self):
    self.assertEqual(get_tip_capacity_ul("96_d_70", "not-a-number"), 0.0)


class GetTipIdForCapacityTests(unittest.TestCase):
  def test_matches_by_capacity(self):
    self.assertEqual(get_tip_id_for_capacity("96_d_70", 30.0), "st_30ul")

  def test_no_match_returns_none(self):
    self.assertIsNone(get_tip_id_for_capacity("96_d_70", 999.0))


class GetDefaultTipIdForHeadTests(unittest.TestCase):
  def test_long_tip_heads_default_to_200ul(self):
    for head in ("8_d_lt", "96_d_200", "96_d_200_s2"):
      with self.subTest(head=head):
        self.assertEqual(get_default_tip_id_for_head(head), "lt_200ul")

  def test_short_tip_heads_default_to_30ul(self):
    for head in ("16_d_st", "384_d_70", "384_d_70_s2", "96_d_70", "96_d_70_s2"):
      with self.subTest(head=head):
        self.assertEqual(get_default_tip_id_for_head(head), "st_30ul")

  def test_head_with_no_matching_preferred_capacity_falls_back_to_first_option(self):
    tip_id = get_default_tip_id_for_head("96_pintool")
    options = get_tip_definitions_for_head("96_pintool")
    self.assertEqual(tip_id, options[0].tip_id)

  def test_unrecognized_head_type_returns_none(self):
    self.assertIsNone(get_default_tip_id_for_head("not_a_real_head"))


class SerializeTipOptionsForHeadTests(unittest.TestCase):
  def test_serializes_every_compatible_tip_as_a_dict(self):
    serialized = serialize_tip_options_for_head("96_d_70")
    self.assertEqual(len(serialized), len(get_tip_definitions_for_head("96_d_70")))
    for item in serialized:
      self.assertIsInstance(item, dict)
      self.assertIn("tip_id", item)
      self.assertIn("length_mm", item)

  def test_model_3d_is_not_present(self):
    for item in serialize_tip_options_for_head("96_d_70"):
      self.assertNotIn("model_3d", item)


class TipDefinitionShapeTests(unittest.TestCase):
  def test_tip_definition_has_no_model_3d_field(self):
    fields = TipDefinition.__dataclass_fields__
    self.assertNotIn("model_3d", fields)
    self.assertIn("tip_id", fields)
    self.assertIn("capacity_ul", fields)
    self.assertIn("label", fields)
    self.assertIn("length_mm", fields)
    self.assertIn("source", fields)
    self.assertIn("compatible_heads", fields)


if __name__ == "__main__":
  unittest.main()
