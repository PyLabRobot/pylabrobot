"""The variant-tree reader on streams built in the test: every value kind, objects and references,
the class bodies it knows, and the refusals that keep a file of another format from being misread."""

import struct
import unittest
import uuid

from pylabrobot.beckman_coulter.biomek_i7.biomek_files import variant_tree as vt
from pylabrobot.beckman_coulter.biomek_i7.biomek_files.files_tests_support import (
  Array,
  Writer,
  container,
  stream,
)


def parse_root(value: object) -> object:
  tree = vt.parse(stream(value))
  return vt.plain(tree.root, tree.objects, inline=True)


def without_ids(value: object) -> object:
  if isinstance(value, dict):
    return {k: without_ids(v) for k, v in value.items() if k != "$id"}
  if isinstance(value, list):
    return [without_ids(v) for v in value]
  return value


class ValueTests(unittest.TestCase):
  def test_scalars_round_trip(self) -> None:
    for value in (None, True, False, 0, -7, 2**31 - 1, 1.5, -0.001, "", "Pod1", "µL ≤ 1"):
      with self.subTest(value=value):
        self.assertEqual(parse_root(value), value)

  def test_fixed_size_codes(self) -> None:
    cases = [
      (b"\x04\x2a", 42),
      (b"\x05" + struct.pack("<h", -3), -3),
      (b"\x07" + struct.pack("<f", 0.5), 0.5),
      (b"\x09" + struct.pack("<q", 12345), vt.Currency(12345)),
      (b"\x0a" + struct.pack("<d", 45000.25), vt.Date(45000.25)),
      (b"\x0c" + struct.pack("<i", 7), ("error", 7)),
      (b"\x00", None),
    ]
    for body, value in cases:
      with self.subTest(code=body[0]):
        self.assertEqual(vt.parse(struct.pack("<i", len(body)) + body).root, value)

  def test_dictionaries_and_lists_nest(self) -> None:
    value = {"Devices": {"Pipettor1": {"Pod1": {"Name": "Pod1", "X": [1, 2.5, "three"]}}}}
    self.assertEqual(without_ids(parse_root(value)), value)
    paths = dict(vt.walk(without_ids(parse_root(value))))
    self.assertEqual(paths["/Devices/Pipettor1/Pod1/X[2]"], "three")

  def test_references_name_objects_defined_earlier(self) -> None:
    shared = {"a": 1}
    tree = vt.parse(stream([shared, vt.Ref(1)]))
    self.assertIsInstance(tree.root.items[1], vt.Ref)
    self.assertIs(tree.deref(tree.root.items[1]), tree.objects[1])
    plain = vt.plain(tree.root, tree.objects, inline=True)
    self.assertEqual(without_ids(plain), [shared, shared])

  def test_arrays(self) -> None:
    self.assertEqual(parse_root(Array(0x03, [1, -2, 3], "i")), [1, -2, 3])
    self.assertEqual(parse_root(Array(0x05, [0.25, 2.0], "d")), [0.25, 2.0])
    self.assertEqual(parse_root(Array(0x08, ["a", "bc"])), ["a", "bc"])
    self.assertEqual(parse_root(Array(0x0C, [1, "x", None])), [1, "x", None])

  def test_the_container_is_a_header_then_zlib(self) -> None:
    data = stream({"k": "v"})
    self.assertEqual(vt.decompress(container(data)), data)


class ClassBodyTests(unittest.TestCase):
  """The bodies of the classes besides dictionaries and lists, as `EXTENSIONS` / `OBJECTS` read
  them, on bodies laid out as each reader states."""

  @staticmethod
  def extended(class_id: bytes, body: bytes) -> object:
    """A dictionary with an extension of `class_id`, its body `body`, and one entry."""
    w = Writer()
    data = w.define(vt.DICTIONARY) + b"\x01" + class_id + body + struct.pack("<i", 1)
    data += w.string("Name") + w.variant("x")
    tree = vt.parse(struct.pack("<i", len(data)) + data)
    return vt.plain(tree.root, tree.objects)

  def test_data_set_manager_and_data_set(self) -> None:
    w = Writer()
    body = struct.pack("<i", 2) + w.string("Liquid") + w.string("Volume")
    plain = self.extended(vt.DATA_SET_MANAGER, body)
    assert isinstance(plain, dict)
    self.assertEqual(
      (plain["$class"], plain["$extension"], plain["Name"]),
      ("data set manager", ["Liquid", "Volume"], "x"),
    )
    plain = self.extended(vt.DATA_SET, b"")
    assert isinstance(plain, dict)
    self.assertEqual(plain["$class"], "data set")

  def test_well_values(self) -> None:
    w = Writer()
    body = w.string("Liquid") + struct.pack("<ii", 2, 1) + w.variant("Water") + w.variant(None)
    plain = self.extended(vt.WELL_VALUES, body)
    assert isinstance(plain, dict)
    self.assertEqual(
      plain["$extension"], {"name": "Liquid", "columns": 2, "rows": 1, "values": ["Water", None]}
    )

  def test_labware(self) -> None:
    w = Writer()
    pair = w.variant(Array(0x05, [0.0, 1.0], "d")) + struct.pack("<i", 2)
    body = pair * 4 + b"\x00" + b"\x00"
    plain = self.extended(vt.LABWARE, body)
    assert isinstance(plain, dict)
    self.assertEqual(len(plain["$extension"]["arrays"]), 4)
    with self.assertRaisesRegex(vt.ParseError, "does not hold"):
      self.extended(vt.LABWARE, w.variant(Array(0x05, [0.0], "d")) + struct.pack("<i", 2))
    with self.assertRaisesRegex(vt.ParseError, "closing flag"):
      self.extended(vt.LABWARE, pair * 4 + b"\x00" + b"\x01")

  def test_workspace_and_promise(self) -> None:
    plain = self.extended(vt.WORKSPACE, struct.pack("<9i", 3, *range(8)))
    assert isinstance(plain, dict)
    self.assertEqual(plain["$extension"], {"version": 3, "records": [[0, 1, 2, 3], [4, 5, 6, 7]]})
    w = Writer()
    data = w.define(vt.PROMISE) + struct.pack("<3d", 1.0, 2.0, 3.0) + w.variant(5)
    data += struct.pack("<iB", 9, 1)
    tree = vt.parse(struct.pack("<i", len(data)) + data)
    self.assertEqual(tree.root.extension, {"values": [1.0, 2.0, 3.0], "status": 5, "tail": (9, 1)})


class RefusalTests(unittest.TestCase):
  def body(self, data: bytes) -> bytes:
    return struct.pack("<i", len(data)) + data

  def test_a_stream_must_be_read_exactly(self) -> None:
    good = stream({"a": 1})
    with self.assertRaisesRegex(vt.ParseError, "stream says"):
      vt.parse(good + b"\x00")
    with self.assertRaisesRegex(vt.ParseError, "left unparsed"):
      vt.parse(self.body(good[4:] + b"\x01"))
    with self.assertRaisesRegex(vt.ParseError, "past the end"):
      vt.parse(self.body(b"\x0b" + struct.pack("<i", 50)))

  def test_unknown_codes_classes_and_references(self) -> None:
    with self.assertRaisesRegex(vt.ParseError, "unknown stream code"):
      vt.parse(self.body(b"\x30"))
    with self.assertRaisesRegex(vt.ParseError, "undefined object reference"):
      vt.parse(self.body(b"\x0d" + struct.pack("<i", 0)))
    with self.assertRaisesRegex(vt.ParseError, "invalid object definition"):
      vt.parse(self.body(b"\x0e" + struct.pack("<i", 4) + vt.LIST + struct.pack("<i", 0)))
    unknown = uuid.uuid4().bytes_le
    with self.assertRaisesRegex(vt.ParseError, "no reader for class"):
      vt.parse(self.body(b"\x0e" + struct.pack("<i", 0) + unknown))
    with self.assertRaisesRegex(vt.ParseError, "no reader for extension"):
      vt.parse(self.body(b"\x0e" + struct.pack("<i", 0) + vt.DICTIONARY + b"\x01" + unknown))
    with self.assertRaisesRegex(vt.ParseError, "unknown dictionary flag"):
      vt.parse(self.body(b"\x0e" + struct.pack("<i", 0) + vt.DICTIONARY + b"\x02" + unknown))
    with self.assertRaisesRegex(vt.ParseError, "invalid array vartype"):
      vt.parse(self.body(b"\x0f" + struct.pack("<BHIi", 0x42, 1, 0, 0)))


if __name__ == "__main__":
  unittest.main()
