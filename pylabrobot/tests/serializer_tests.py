import math

from pylabrobot.serializer import (
  apply_merge_patch,
  compact,
  create_merge_patch,
  deserialize,
  serialize,
)


def test_serialize_deserialize_closure():
  def outer(x):
    y = 10

    def inner():
      return x + y

    return inner

  closure = outer(5)
  serialized = serialize(closure)
  deserialized = deserialize(serialized, allow_marshal=True)

  assert closure() == deserialized()


def test_serialize_deserialize_cell():
  x = 42

  def func():
    return x

  assert func.__closure__ is not None
  cell = func.__closure__[0]
  serialized = serialize(cell)
  deserialized = deserialize(serialized)

  assert cell.cell_contents == deserialized.cell_contents


def test_serialize_deserialize_function_with_closure():
  x = 10

  def func(y):
    return x + y

  serialized = serialize(func)
  deserialized = deserialize(serialized, allow_marshal=True)

  assert func(5) == deserialized(5)


def test_serialize_deserialize_special_floats():
  assert deserialize(serialize(float("inf"))) == math.inf
  assert deserialize(serialize(float("-inf"))) == -math.inf
  result = deserialize(serialize(float("nan")))
  assert math.isnan(result)


def test_deserialize_calls_custom_deserialize_method():
  """Test that deserialize() calls a class's custom deserialize method when defined."""
  from pylabrobot.resources.tip import Tip
  from pylabrobot.resources.tip_rack import TipSpot

  def make_tip(name):
    return Tip(
      name=name, total_tip_length=50, has_filter=False, maximal_volume=300, fitting_depth=8
    )

  ts = TipSpot(name="A1", size_x=9, size_y=9, make_tip=make_tip)
  data = ts.serialize()

  # TipSpot.serialize() includes 'prototype_tip' which TipSpot.__init__ doesn't accept.
  # This only works because deserialize() calls TipSpot.deserialize() which handles it.
  assert "prototype_tip" in data
  result = deserialize(data)
  assert isinstance(result, TipSpot)
  assert result.name == "A1"


# ---- RFC 7386 JSON Merge Patch tests ----


class TestApplyMergePatch:
  """Test RFC 7386 apply_merge_patch."""

  def test_scalar_replace(self):
    assert apply_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}

  def test_null_removes_key(self):
    assert apply_merge_patch({"a": 1, "b": 2}, {"a": None}) == {"b": 2}

  def test_add_new_key(self):
    assert apply_merge_patch({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

  def test_nested_merge(self):
    target = {"a": {"b": 1, "c": 2}}
    patch = {"a": {"b": 3}}
    assert apply_merge_patch(target, patch) == {"a": {"b": 3, "c": 2}}

  def test_nested_null_removes(self):
    target = {"a": {"b": 1, "c": 2}}
    patch = {"a": {"c": None}}
    assert apply_merge_patch(target, patch) == {"a": {"b": 1}}

  def test_non_dict_patch_replaces(self):
    assert apply_merge_patch({"a": 1}, "hello") == "hello"

  def test_non_dict_target_becomes_dict(self):
    assert apply_merge_patch("hello", {"a": 1}) == {"a": 1}

  def test_rfc7386_test_vectors(self):
    """Test cases from RFC 7386 Section 3."""
    assert apply_merge_patch({"a": "b"}, {"a": "c"}) == {"a": "c"}
    assert apply_merge_patch({"a": "b"}, {"b": "c"}) == {"a": "b", "b": "c"}
    assert apply_merge_patch({"a": "b"}, {"a": None}) == {}
    assert apply_merge_patch({"a": "b", "b": "c"}, {"a": None}) == {"b": "c"}
    assert apply_merge_patch({"a": ["b"]}, {"a": "c"}) == {"a": "c"}
    assert apply_merge_patch({"a": "c"}, {"a": ["b"]}) == {"a": ["b"]}
    assert apply_merge_patch({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}) == {"a": {"b": "d"}}
    assert apply_merge_patch({"a": [{"b": "c"}]}, {"a": [1]}) == {"a": [1]}
    assert apply_merge_patch(["a", "b"], {"a": "c"}) == {"a": "c"}  # array replaced by object
    assert apply_merge_patch({}, {"a": {"bb": {"ccc": None}}}) == {"a": {"bb": {}}}

  def test_empty_patch(self):
    assert apply_merge_patch({"a": 1}, {}) == {"a": 1}


class TestCreateMergePatch:
  """Test create_merge_patch."""

  def test_equal_dicts(self):
    assert create_merge_patch({"a": 1}, {"a": 1}) is None

  def test_changed_value(self):
    assert create_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}

  def test_removed_key(self):
    assert create_merge_patch({"a": 1, "b": 2}, {"a": 1}) == {"b": None}

  def test_added_key(self):
    assert create_merge_patch({"a": 1}, {"a": 1, "b": 2}) == {"b": 2}

  def test_nested_change(self):
    source = {"a": {"b": 1, "c": 2}}
    target = {"a": {"b": 3, "c": 2}}
    assert create_merge_patch(source, target) == {"a": {"b": 3}}

  def test_roundtrip(self):
    source = {"a": 1, "b": {"c": 3, "d": 4}, "e": [1, 2]}
    target = {"a": 2, "b": {"c": 3}, "f": 6}
    patch = create_merge_patch(source, target)
    assert patch is not None
    result = apply_merge_patch(source, patch)
    assert result == target

  def test_non_dict_values(self):
    assert create_merge_patch("a", "b") == "b"
    assert create_merge_patch("a", "a") is None
    assert create_merge_patch(1, 2) == 2
    assert create_merge_patch(1, 1) is None


class TestCompact:
  """Test compact() strips default values."""

  def test_strips_none_defaults(self):
    """Resource fields like category=None, model=None should be stripped."""
    from pylabrobot.resources import Resource

    r = Resource(name="test", size_x=10, size_y=20, size_z=30)
    data = r.serialize()
    compacted = compact(data)

    # None-default fields should be stripped
    assert "category" not in compacted
    assert "model" not in compacted
    assert "barcode" not in compacted
    assert "preferred_pickup_location" not in compacted

    # Required fields should be kept
    assert compacted["name"] == "test"
    assert compacted["size_x"] == 10
    assert compacted["size_y"] == 20
    assert compacted["size_z"] == 30
    assert compacted["type"] == "Resource"

  def test_keeps_non_default_values(self):
    """Non-default values should be kept."""
    from pylabrobot.resources import Resource

    r = Resource(name="test", size_x=10, size_y=20, size_z=30, category="plate", model="xyz")
    data = r.serialize()
    compacted = compact(data)

    assert compacted["category"] == "plate"
    assert compacted["model"] == "xyz"

  def test_strips_empty_children(self):
    """Empty children list should be stripped."""
    from pylabrobot.resources import Resource

    r = Resource(name="test", size_x=10, size_y=20, size_z=30)
    data = r.serialize()
    compacted = compact(data)

    assert "children" not in compacted

  def test_keeps_nonempty_children(self):
    """Non-empty children list should be kept."""
    from pylabrobot.resources import Resource
    from pylabrobot.resources.coordinate import Coordinate

    parent = Resource(name="parent", size_x=100, size_y=100, size_z=100)
    child = Resource(name="child", size_x=10, size_y=10, size_z=10)
    parent.assign_child_resource(child, location=Coordinate(0, 0, 0))

    data = parent.serialize()
    compacted = compact(data)

    assert "children" in compacted
    assert len(compacted["children"]) == 1

  def test_default_rotation_compacted(self):
    """Default rotation (0,0,0) should have its default fields stripped.

    The rotation field itself is kept because the Resource __init__ default is None,
    but the Rotation's own x=0, y=0, z=0 defaults are stripped.
    """
    from pylabrobot.resources import Resource

    r = Resource(name="test", size_x=10, size_y=20, size_z=30)
    data = r.serialize()
    compacted = compact(data)

    # rotation is kept (because Resource default is None, not Rotation(0,0,0))
    # but Rotation(0,0,0) fields are compacted (x,y,z stripped since they match defaults)
    assert "rotation" in compacted
    assert compacted["rotation"] == {"type": "Rotation"}

  def test_keeps_non_default_rotation(self):
    """Non-default rotation should be kept."""
    from pylabrobot.resources import Resource
    from pylabrobot.resources.rotation import Rotation

    r = Resource(name="test", size_x=10, size_y=20, size_z=30, rotation=Rotation(z=90))
    data = r.serialize()
    compacted = compact(data)

    assert "rotation" in compacted

  def test_recursive_compaction(self):
    """Compact should recurse into children."""
    from pylabrobot.resources import Resource
    from pylabrobot.resources.coordinate import Coordinate

    parent = Resource(name="parent", size_x=100, size_y=100, size_z=100)
    child = Resource(name="child", size_x=10, size_y=10, size_z=10)
    parent.assign_child_resource(child, location=Coordinate(0, 0, 0))

    data = parent.serialize()
    compacted = compact(data)

    child_data = compacted["children"][0]
    assert "category" not in child_data  # None default stripped
    assert "model" not in child_data  # None default stripped

  def test_unknown_type_passes_through(self):
    """Types not found in PLR should pass through unchanged."""
    data = {"type": "UnknownType12345", "foo": "bar", "children": []}
    compacted = compact(data)
    assert compacted == {"type": "UnknownType12345", "foo": "bar", "children": []}


class TestCompactDeserializeRoundTrip:
  """Test that compact JSON round-trips through deserialization."""

  def test_resource_roundtrip(self):
    """serialize -> compact -> deserialize should produce an equal resource."""
    from pylabrobot.resources import Resource

    r = Resource(name="test", size_x=10, size_y=20, size_z=30)
    data = r.serialize()
    compacted = compact(data)
    restored = Resource.deserialize(compacted)

    assert restored.name == r.name
    assert restored._size_x == r._size_x
    assert restored._size_y == r._size_y
    assert restored._size_z == r._size_z
    assert restored.category == r.category
    assert restored.model == r.model

  def test_resource_with_children_roundtrip(self):
    """Resources with children should round-trip through compact."""
    from pylabrobot.resources import Resource
    from pylabrobot.resources.coordinate import Coordinate

    parent = Resource(name="parent", size_x=100, size_y=100, size_z=100)
    child = Resource(name="child", size_x=10, size_y=10, size_z=10)
    parent.assign_child_resource(child, location=Coordinate(5, 5, 0))

    data = parent.serialize()
    compacted = compact(data)
    restored = Resource.deserialize(compacted)

    assert restored.name == "parent"
    assert len(restored.children) == 1
    assert restored.children[0].name == "child"

  def test_old_full_format_still_works(self):
    """Old full-format JSON (with all fields) should still deserialize."""
    from pylabrobot.resources import Resource

    full_data = {
      "name": "test",
      "type": "Resource",
      "size_x": 10,
      "size_y": 20,
      "size_z": 30,
      "location": None,
      "rotation": {"x": 0, "y": 0, "z": 0},
      "category": None,
      "model": None,
      "barcode": None,
      "preferred_pickup_location": None,
      "children": [],
      "parent_name": None,
    }
    r = Resource.deserialize(full_data)
    assert r.name == "test"
    assert r._size_x == 10

  def test_compact_format_works(self):
    """Compact JSON (missing optional fields) should deserialize."""
    from pylabrobot.resources import Resource

    compact_data = {
      "name": "test",
      "type": "Resource",
      "size_x": 10,
      "size_y": 20,
      "size_z": 30,
    }
    r = Resource.deserialize(compact_data)
    assert r.name == "test"
    assert r._size_x == 10
    assert r.category is None
    assert r.model is None
    assert r.barcode is None
    assert len(r.children) == 0

  def test_resource_with_rotation_roundtrip(self):
    """Resource with non-default rotation round-trips."""
    from pylabrobot.resources import Resource
    from pylabrobot.resources.rotation import Rotation

    r = Resource(name="rotated", size_x=10, size_y=20, size_z=30, rotation=Rotation(z=90))
    data = r.serialize()
    compacted = compact(data)
    restored = Resource.deserialize(compacted)

    assert restored.rotation.z == 90
