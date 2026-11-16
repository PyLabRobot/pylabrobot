"""BindIt codec regression tests using synthetic data, not manufacturer fixtures."""

import struct
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from pylabrobot.thermo_fisher.kingfisher.bdz import (
  BdzProtocol,
  MixStep,
  ProtocolTip,
  Reagent,
  SamplePlate,
  WellGroup,
  _bdz_parts,
  _build_header,
  _gzip_compress,
  bdz_variant,
  read_bdz,
  write_bdz,
)


def protocol(variant="presto") -> BdzProtocol:
  """Build a Mix protocol covering independent plates, groups and optional settings."""
  groups = {"Plate": WellGroup(reagent=Reagent("Wash & buffer", 12.5))}
  if variant == "duo":
    groups = {
      "A": WellGroup(reagent=Reagent("Row A", 12.5)),
      "B": WellGroup(reagent=Reagent("Row B", 20)),
    }
  return BdzProtocol(
    name='Test & "mix"',
    variant=variant,
    creator="A < B",
    description="Mix and collect",
    tips=[
      ProtocolTip(
        name="Tip1",
        sample_plates=[SamplePlate("Wash", groups=groups), SamplePlate("Elute")],
        steps=[
          MixStep(
            "Wash",
            shake_duration=90,
            sample_plate_index=0,
            well_group_name="A" if variant == "duo" else "Plate",
            heating_enabled=True,
            temperature=45,
            preheat=True,
            pause_enabled=True,
            pause_message="Check <plate>",
            postmix_enabled=True,
            postmix_duration=3,
          ),
          MixStep("Elute", sample_plate_index=1, shake_duration=5),
        ],
      )
    ],
  )


def replace_xml(data: bytes, *, properties=None, exported=None) -> bytes:
  """Mutate a synthetic file while retaining valid gzip checksums and member sizes."""
  props, exp = _bdz_parts(data)
  first_size = struct.unpack_from("<I", data, 57)[0]
  second_start = 61 + first_size + 8
  second_size = struct.unpack_from("<I", data, second_start - 4)[0]
  first = _gzip_compress(properties if properties is not None else props)
  second = _gzip_compress(exported if exported is not None else exp)
  body = (
    first
    + b"\x01\x00\x00\x01"
    + struct.pack("<I", len(second))
    + second
    + data[second_start + second_size :]
  )
  return _build_header(61 + len(body), len(first)) + body


class BdzTests(unittest.TestCase):
  def test_presto_and_duo_round_trip(self) -> None:
    for variant in ("presto", "duo"):
      with self.subTest(variant=variant):
        source = protocol(variant)
        data = write_bdz(source)
        self.assertEqual(bdz_variant(data), variant)
        parsed = read_bdz(data)
        self.assertEqual(parsed.name, source.name)
        self.assertEqual(parsed.creator, source.creator)
        self.assertEqual(parsed.description, source.description)
        self.assertEqual(parsed.tips[0].steps, source.tips[0].steps)
        reagent = parsed.tips[0].sample_plates[0].reagent
        assert reagent is not None
        self.assertEqual(reagent.volume, 12.5)
        reread = read_bdz(write_bdz(parsed))
        self.assertEqual(reread, parsed)

  def test_synthetic_fixture_has_known_semantics(self) -> None:
    data = (Path(__file__).parent / "test_data" / "presto_mix.bdz").read_bytes()
    parsed = read_bdz(data)
    self.assertEqual(parsed.name, 'Test & "mix"')
    self.assertEqual(parsed.variant, "presto")
    self.assertEqual([step.name for step in parsed.tips[0].steps], ["Wash", "Elute"])
    self.assertEqual(parsed.tips[0].steps[0].shake_duration, 90)
    reagent = parsed.tips[0].sample_plates[0].reagent
    assert reagent is not None
    self.assertEqual(reagent.volume, 12.5)

  def test_truncated_and_corrupt_files_raise_value_error(self) -> None:
    data = write_bdz(protocol())
    for cut in (0, 3, 60, 62, len(data) - 1):
      with self.subTest(cut=cut), self.assertRaises(ValueError):
        read_bdz(data[:cut])
    for offset in (0, 49, 57, 63, len(data) - 1):
      broken = bytearray(data)
      broken[offset] ^= 1
      with self.subTest(offset=offset), self.assertRaises(ValueError):
        read_bdz(bytes(broken))

  def test_gzip_checksum_is_checked(self) -> None:
    data = bytearray(write_bdz(protocol()))
    first_end = 61 + struct.unpack_from("<I", data, 57)[0]
    data[first_end - 8] ^= 1
    with self.assertRaisesRegex(ValueError, "gzip"):
      read_bdz(bytes(data))

  def test_unknown_instrument_and_mismatched_id_are_rejected(self) -> None:
    data = write_bdz(protocol())
    props, _ = _bdz_parts(data)
    for changed in (
      props.replace(b'type="713"', b'type="999"'),
      props.replace(b"9da3c7a3", b"aaaaaaaa"),
    ):
      with self.assertRaisesRegex(ValueError, "instrument"):
        bdz_variant(replace_xml(data, properties=changed))

  def test_unknown_steps_remain_transferable_but_cannot_be_silently_rewritten(self) -> None:
    data = write_bdz(protocol())
    _, exp = _bdz_parts(data)
    root = ET.fromstring(exp)
    steps = root.find("./Protocol/Run/Protocol/Steps/Tip/Steps")
    assert steps is not None
    ET.SubElement(steps, "Dry", name="Dry")
    data = replace_xml(data, exported=ET.tostring(root))
    self.assertEqual(bdz_variant(data), "presto")
    with self.assertRaisesRegex(ValueError, "unsupported steps"):
      read_bdz(data)

  def test_invalid_plate_reference_and_durations_are_rejected(self) -> None:
    for index in (-1, 2):
      source = protocol()
      source.tips[0].steps[0].sample_plate_index = index
      with self.assertRaisesRegex(ValueError, "sample_plate_index"):
        write_bdz(source)
    source = protocol()
    source.tips[0].steps[0].shake_duration = -1
    with self.assertRaisesRegex(ValueError, "duration"):
      write_bdz(source)

  def test_unknown_tip_comb_is_not_assigned_a_random_id(self) -> None:
    source = protocol()
    source.tips[0].plate_type_id = "unknown"
    with self.assertRaisesRegex(ValueError, "tip_id"):
      write_bdz(source)

  def test_protocol_instrument_must_match_properties(self) -> None:
    data = write_bdz(protocol())
    _, exported = _bdz_parts(data)
    data = replace_xml(data, exported=exported.replace(b"9da3c7a3", b"aaaaaaaa"))
    with self.assertRaisesRegex(ValueError, "instrument ID"):
      bdz_variant(data)
