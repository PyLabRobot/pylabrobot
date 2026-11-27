"""BDZ parsing, compatibility checks and malformed-input rejection."""

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from .protocol import FlexProtocolError, _preflight, loading_slot, read_flex_bdz
from .test_helpers import bdz_bytes


class FlexProtocolTests(unittest.TestCase):
  """Exercise the container reader with synthetic, non-laboratory test data."""

  def setUp(self) -> None:
    """Build an in-memory container without files or USB."""
    self.raw = bdz_bytes()
    self.protocol = read_flex_bdz(self.raw)

  def test_sections_and_path_input_are_preserved(self) -> None:
    """Retain original bytes, metadata and source line numbers."""
    p = self.protocol
    self.assertEqual(p.name, "unit test")
    self.assertEqual(p.original_bytes, self.raw)
    self.assertEqual(p.sha256, hashlib.sha256(self.raw).hexdigest())
    self.assertEqual(len(p.preflight("record")), 5)
    self.assertEqual([c.line_number for c in p.preflight()], [3, 4, 5])
    self.assertEqual(p.plates[0].id, "plate")
    self.assertEqual(p.tips[0].plate_ids, ("plate",))
    with TemporaryDirectory() as tmp:
      path = Path(tmp) / "input.bdz"
      path.write_bytes(self.raw)
      self.assertEqual(read_flex_bdz(path), p)
    for version in ("4.0.0.45", "4.1", "4.123.456.789"):
      self.assertEqual(read_flex_bdz(bdz_bytes(version=version)).bindit_version, version)

  def test_corruption_truncation_and_trailing_data(self) -> None:
    """Reject malformed lengths, magic, gzip content and trailers."""
    for offset in (0, 12, 16, 48, 56, 80, len(self.raw) - 1):
      raw = bytearray(self.raw)
      raw[offset] ^= 0xFF
      with self.subTest(offset=offset), self.assertRaises(FlexProtocolError):
        read_flex_bdz(bytes(raw))
    for length in (0, 15, 40, 59, len(self.raw) - 1):
      with self.subTest(length=length), self.assertRaises(FlexProtocolError):
        read_flex_bdz(self.raw[:length])
    with self.assertRaises(FlexProtocolError):
      read_flex_bdz(self.raw + b"garbage")

  def test_unknown_commands_and_missing_context_reject_entire_script(self) -> None:
    """Never silently filter unsupported commands or invent timer/orientation state."""
    for script in (
      'REC 1 "x" 1',
      "ABC 1",
      "MOR 9",
      'PAU 3 "x"',
      "MOH 1 2 1 3 0",
      "WAI 2 100",
      'PAU 1 "plate"',
      "WAI 1",
      "MOR 1\vMOR 2",
      "",
    ):
      with self.subTest(script=script), self.assertRaises(FlexProtocolError):
        replace(self.protocol, script=script).preflight()
    with self.assertRaises(FlexProtocolError):
      _preflight("MOH 1 2 0 " + "9" * 5000 + " 0")

  def test_prompt_orientation_and_labels(self) -> None:
    """Labels are not tray indices; MOR determines loading orientation."""
    p = replace(self.protocol, script='MOR 7\nPAU 1 "3"\nMOR 6\nPAU 2 "1"\n')
    self.assertEqual([(q.label, q.loading_slot) for q in p.prompts()], [("3", 2), ("1", 1)])
    self.assertEqual([loading_slot(i) for i in range(1, 9)], [4, 5, 6, 7, 8, 1, 2, 3])

  def test_temperature_range_and_interval(self) -> None:
    """Validate documented setpoints independently of a particular method."""
    for target in (0, *range(10, 97)):
      for interval in (0, 100, 300):
        self.assertEqual(len(_preflight(f"TEM {target} {interval}")), 1)
    for line in ("TEM 9 300", "TEM 97 300", "TEM 37 99", "TEM -1 300", "MOI 311"):
      with self.subTest(line=line), self.assertRaises(FlexProtocolError):
        _preflight(line)

  def test_features_require_supported_metadata_and_commands(self) -> None:
    """Heating requires its target and block movement; unknown features stay blocked."""
    steps = (
      '<Mix name="mix"><Heating enabled="true"><Temperature>37</Temperature>'
      '<Preheat>true</Preheat></Heating><Precollect enabled="true" />'
      '<Postmix enabled="true" /></Mix>'
    )
    p = read_flex_bdz(bdz_bytes('TXT 7 "mix"\nTEM 37 300\nMOI 310\n', steps=steps))
    self.assertEqual(p.compatibility_issues(), ())
    self.assertTrue(replace(p, script='TXT 7 "mix"\nTEM 37 300\n').compatibility_issues())
    for step in (
      '<Unknown name="x" />',
      '<Mix><Pause enabled="true" /></Mix>',
      '<Mix><PostTemperature enabled="true" /></Mix>',
    ):
      with self.subTest(step=step), self.assertRaises(FlexProtocolError):
        read_flex_bdz(bdz_bytes(steps=step)).preflight()

  def test_multiple_blockers_reported_without_mutation(self) -> None:
    """Collect independent labware, syntax and timer failures before execution."""
    p = replace(
      self.protocol,
      plates=(replace(self.protocol.plates[0], type_id="other"),),
      tips=(replace(self.protocol.tips[0], type_id="other"),),
      script='PAU 1 "plate"\nWAI 1\nABC 1\n',
    )
    self.assertEqual(
      {i.code for i in p.compatibility_issues()},
      {
        "unsupported_plate",
        "unsupported_tip",
        "unsupported_command",
        "prompt_without_orientation",
        "wait_without_timer",
      },
    )
    with self.assertRaises(FlexProtocolError):
      p.preflight()

  def test_xml_declarations_and_invalid_identity_rejected(self) -> None:
    """Preflight rejects entity declarations and non-executable XML."""
    for xml in (
      b"<!DOCTYPE x><ExportedData />",
      b"<Wrong />",
      self.protocol.exported_xml.replace(b'IsExecutable="true"', b'IsExecutable="false"'),
    ):
      with self.subTest(xml=xml[:40]), self.assertRaises(FlexProtocolError):
        replace(self.protocol, exported_xml=xml).preflight()
