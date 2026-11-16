"""Flatten any resource tree into prototypes and instances.

A resource is split into its model (what it is, shared by every resource that serializes the
same way, sent once) and its instance (a name, a parent, six floats). The split is derived from
`Resource.serialize()` by removing the fields that vary per instance, so no resource type is
known here.
"""

import base64
import functools
import inspect
import json
import struct
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .grids import describe_bands, describe_grid

# Fields of a serialized resource that describe this one resource rather than its kind. Everything
# else is shared by every resource that serializes the same way, and so belongs to the model.
INSTANCE_FIELDS = frozenset({"name", "location", "rotation", "parent_name", "children"})

# Keys that name one particular thing at any depth: a tip spot carries its prototype tip's name,
# a plate the names of its wells. Links, not kind.
NAME_KEYS = frozenset({"name", "parent_name"})

# Fields a resource may declare about itself that `serialize()` does not carry, read straight off
# the resource.
DECLARED_FIELDS = ("reference_point", "window", "mesh", "reference_glb", "appearance")
RESOURCE_LINK = "<resource>"


def _declared(value: Any) -> Any:
  """A declared field as JSON: anything with a `serialize` method is asked for it.

  Read off the resource, so a field may be a live object such as a `Coordinate`.
  """
  serialize = getattr(value, "serialize", None)
  return serialize() if callable(serialize) else value


def _model_of(data: Dict[str, Any], names: FrozenSet[str]) -> Dict[str, Any]:
  """The kind-defining part of a serialized resource, with every identity removed.

  Inside nested values a `NAME_KEYS` key is dropped and a string naming a resource in `names`
  becomes `RESOURCE_LINK`. Top-level scalars are kept as they are: a category may read the same
  as a resource's name (a deck called "deck") without referring to it.
  """

  def strip(value: Any) -> Any:
    if isinstance(value, dict):
      return {k: strip(v) for k, v in value.items() if k not in NAME_KEYS}
    if isinstance(value, list):
      return [strip(v) for v in value]
    if isinstance(value, str) and value in names:
      return RESOURCE_LINK
    return value

  return {
    k: (v if not isinstance(v, (dict, list)) else strip(v))
    for k, v in data.items()
    if k not in INSTANCE_FIELDS
  }


@functools.lru_cache(maxsize=None)
def _public_methods(cls: type) -> Tuple[str, ...]:
  """The public operations of a resource class as signatures, sorted, cached per class."""
  signatures = []
  for name in dir(cls):
    if name.startswith("_"):
      continue
    try:
      attribute = getattr(cls, name, None)
    except Exception:
      continue
    if attribute is None or not callable(attribute) or isinstance(attribute, property):
      continue
    try:
      parameters = [p for p in inspect.signature(attribute).parameters if p != "self"]
      signatures.append(f"{name}({', '.join(parameters)})")
    except (ValueError, TypeError):
      signatures.append(f"{name}()")
  return tuple(sorted(signatures))


def _model_class(model: Dict[str, Any]) -> str:
  """The bucket a model is compared within. Two resources of different types are never the same."""
  return str(model.get("type", ""))


def _location_of(data: Dict[str, Any]) -> Tuple[float, float, float]:
  loc = data.get("location") or {}
  return (float(loc.get("x", 0.0)), float(loc.get("y", 0.0)), float(loc.get("z", 0.0)))


def _rotation_of(data: Dict[str, Any]) -> Tuple[float, float, float]:
  rot = data.get("rotation") or {}
  return (float(rot.get("x", 0.0)), float(rot.get("y", 0.0)), float(rot.get("z", 0.0)))


class Scene:
  """A flattened resource tree: models, instances, and the measurements of the split."""

  def __init__(self, names: Optional[FrozenSet[str]] = None) -> None:
    self.names_in_tree = names or frozenset()
    # Every model derived this pass, by resource name, so the next pass can skip the work.
    self.derived: Dict[str, Dict[str, Any]] = {}
    self.models: List[Dict[str, Any]] = []
    # Candidate model indices per serialized type: interning compares dicts directly, and a tree
    # has only a handful of distinct models per type, so the scan is short.
    self._candidates: Dict[str, List[int]] = {}
    self.names: List[str] = []
    self.model_of_instance: List[int] = []
    self.parent_of_instance: List[int] = []
    self.transforms: List[float] = []  # 6 floats per instance: x, y, z, rx, ry, rz
    self.legacy_bytes = 0

  def _intern(self, model: Dict[str, Any]) -> int:
    bucket = self._candidates.setdefault(_model_class(model), [])
    for index in bucket:
      if self.models[index] == model:
        return index
    index = len(self.models)
    self.models.append(model)
    bucket.append(index)
    return index

  def add_known(self, resource: Resource, parent: int, model: Dict[str, Any]) -> int:
    """Add a resource whose model is already known, without serializing it again.

    Its position is read off the resource.
    """
    index = len(self.names)
    self.names.append(resource.name)
    model_index = self._intern(model)
    self.model_of_instance.append(model_index)
    self.derived[resource.name] = self.models[model_index]
    self.parent_of_instance.append(parent)
    location = resource.location or Coordinate.zero()
    rotation = resource.rotation
    self.transforms.extend((float(location.x), float(location.y), float(location.z)))
    self.transforms.extend((float(rotation.x), float(rotation.y), float(rotation.z)))
    return index

  def add(
    self,
    data: Dict[str, Any],
    parent: int,
    cls: Optional[type] = None,
    grid: Optional[Dict[str, Any]] = None,
    bands: Optional[List[Dict[str, Any]]] = None,
    declared: Optional[Dict[str, Any]] = None,
  ) -> int:
    """Add one serialized resource, returning its instance index."""
    index = len(self.names)
    resource_name = data["name"]
    self.names.append(resource_name)
    model = _model_of(data, self.names_in_tree)
    if cls is not None:
      model["methods"] = list(_public_methods(cls))
    if grid is not None:
      model["grid"] = grid
    if bands is not None:
      model["bands"] = bands
    for field, value in (declared or {}).items():
      model[field] = value
    # The interned dict, so every instance of one model shares one object.
    model_index = self._intern(model)
    self.model_of_instance.append(model_index)
    self.derived[resource_name] = self.models[model_index]
    self.parent_of_instance.append(parent)
    self.transforms.extend(_location_of(data))
    self.transforms.extend(_rotation_of(data))
    return index

  @property
  def num_instances(self) -> int:
    return len(self.names)

  def serialize(self) -> Dict[str, Any]:
    """The scene message. Transforms ride as base64 little-endian float32, not as JSON numbers."""
    packed = struct.pack(f"<{len(self.transforms)}f", *self.transforms)
    return {
      "models": self.models,
      "instances": {
        "names": self.names,
        "model": self.model_of_instance,
        "parent": self.parent_of_instance,
        "transforms": base64.b64encode(packed).decode("ascii"),
      },
    }

  def stats(self, scene_bytes: int) -> Dict[str, Any]:
    """What the split cost and what it saved, in bytes actually sent."""
    return {
      "instances": self.num_instances,
      "models": len(self.models),
      "legacy_bytes": self.legacy_bytes,
      "scene_bytes": scene_bytes,
      "ratio": round(self.legacy_bytes / scene_bytes, 2) if scene_bytes else 0.0,
    }


def all_names(resource: Resource) -> List[str]:
  """Every resource name in the tree, so a link to one can be recognised as a link."""
  found = [resource.name]
  for child in resource.children:
    found.extend(all_names(child))
  return found


def _serialized_children(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
  """A serialized resource's children by name. A parent may leave one out: a tip spot its tip."""
  return {child["name"]: child for child in data.get("children", ())}


def _legacy_shape(resource: Resource, data: Dict[str, Any]) -> Dict[str, Any]:
  """One JSON node per resource, listing every child, including those `serialize()` leaves out."""
  serialized = _serialized_children(data)
  data["children"] = [
    _legacy_shape(child, serialized.get(child.name) or child.serialize())
    for child in resource.children
  ]
  return data


def legacy_size(root: Resource) -> int:
  """The size of the tree serialized as one JSON node per resource."""
  return len(json.dumps(_legacy_shape(root, root.serialize())))


def _links_to(model: Dict[str, Any], names: FrozenSet[str]) -> bool:
  """Whether a nested value of `model` is a link, or a string naming one of `names`."""

  def found(value: Any) -> bool:
    if isinstance(value, dict):
      return any(found(v) for v in value.values())
    if isinstance(value, list):
      return any(found(v) for v in value)
    return isinstance(value, str) and (value == RESOURCE_LINK or value in names)

  return any(found(v) for v in model.values() if isinstance(v, (dict, list)))


def build_scene(
  root: Resource,
  measure_legacy: bool = False,
  known: Optional[Dict[str, Dict[str, Any]]] = None,
  known_names: Optional[FrozenSet[str]] = None,
) -> Scene:
  """Flatten `root` and every descendant into models and instances.

  Depth-first, parents before children, so world transforms resolve in one forward pass. A node
  is serialized once and its nested children are handed down the walk.

  Args:
    root: the resource to flatten.
    measure_legacy: also measure the per-resource tree, for `Scene.legacy_bytes`.
    known: models derived by an earlier pass, by resource name, reused unless stale.
    known_names: the names that pass saw; a model that links to, or names, a resource that has
      since appeared or gone is derived again.
  """
  names = frozenset(all_names(root))
  scene = Scene(names=names)
  changed = names ^ (known_names or frozenset())
  models = {id(model): model for model in (known or {}).values()}
  stale = {i for i, model in models.items() if changed and _links_to(model, changed)}
  reuse = {name: model for name, model in (known or {}).items() if id(model) not in stale}

  def walk(resource: Resource, parent: int, data: Optional[Dict[str, Any]] = None) -> None:
    model = reuse.get(resource.name)
    if model is not None:
      index = scene.add_known(resource, parent, model)
      for child in resource.children:
        walk(child, index)
      return

    if data is None:
      data = resource.serialize()
    # Read off the resource, as `add_known` does: a tip's serialized data has no location.
    if resource.location is not None:
      data["location"] = resource.location.serialize()
    declared = {
      field: _declared(getattr(resource, field))
      for field in DECLARED_FIELDS
      if getattr(resource, field, None) is not None
    }
    index = scene.add(
      data, parent, type(resource), describe_grid(resource), describe_bands(resource), declared
    )
    serialized = _serialized_children(data)
    for child in resource.children:
      walk(child, index, serialized.get(child.name))

  walk(root, -1)
  if measure_legacy:
    scene.legacy_bytes = legacy_size(root)
  return scene


# A rotation nobody has turned. It is published by every resource that publishes anything at all,
# and on a scene of mostly unrotated geometry it is the single largest repeated value.
IDENTITY_ROTATION = {"type": "Rotation", "x": 0, "y": 0, "z": 0}

# Fields that say which resource a state came from, not what it is: the key already says. A tip
# names the spot it stands in, so without `parent_name` here every full spot's state was distinct.
STATE_IDENTITY_KEYS = frozenset({"name", "thing", "parent_name"})


def _without_identity(value: Any) -> Any:
  """A state value with the fields that name one instance taken out, at any depth."""
  if isinstance(value, dict):
    return {k: _without_identity(v) for k, v in value.items() if k not in STATE_IDENTITY_KEYS}
  if isinstance(value, list):
    return [_without_identity(v) for v in value]
  return value


# What a viewer can see: firmware reports position to 0.1 mm. Rounding rather than thresholding
# makes two values that look the same produce one signature and share one table entry.
STATE_DECIMALS = 1


def _visible(value: Any) -> Any:
  """A state value with changes too small to see rounded away."""
  if isinstance(value, float):
    return round(value, STATE_DECIMALS)
  if isinstance(value, dict):
    return {k: _visible(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_visible(v) for v in value]
  return value


def state_signature(published: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
  """The publishable form of one resource's state, and a key that is equal when it looks equal.

  The key leaves out `location`; the cleaned state keeps it as published.
  """
  cleaned = _visible(
    _without_identity(
      {
        k: v
        for k, v in published.items()
        if k != "location" and not (k == "rotation" and v == IDENTITY_ROTATION)
      }
    )
  )
  key = json.dumps(cleaned, sort_keys=True, default=str)
  if "location" in published:
    cleaned["location"] = published["location"]
  return cleaned, key


def pack_state(states: Dict[str, Tuple[Dict[str, Any], str]], epoch: int = 0) -> Dict[str, Any]:
  """Pack `{name: (cleaned state, key)}` into a table of distinct states plus an index per name.

  Every name that went in comes out, including one whose state cleaned down to nothing. A
  `location` travels under the name in `locations`, not in the shared table.
  """
  distinct: Dict[str, int] = {}
  table: List[Dict[str, Any]] = []
  index: Dict[str, int] = {}
  locations: Dict[str, Any] = {}

  for name, (cleaned, key) in states.items():
    if "location" in cleaned:
      locations[name] = cleaned["location"]
      cleaned = {k: v for k, v in cleaned.items() if k != "location"}
    if key not in distinct:
      distinct[key] = len(table)
      table.append(cleaned)
    index[name] = distinct[key]

  # Addressed by name: instance order is not stable across rebuilds, since setup creates resources
  # lazily and a reassignment reorders a parent's children.
  return {"epoch": epoch, "states": table, "of": index, "locations": locations}


def collect_state(root: Resource) -> Dict[str, Tuple[Dict[str, Any], str]]:
  """The cleaned state and key of every resource that publishes any, keyed by name.

  A resource whose state cleans down to nothing is left out; a rotation that is not the identity
  counts.
  """
  state: Dict[str, Tuple[Dict[str, Any], str]] = {}

  def walk(resource: Resource) -> None:
    cleaned, key = state_signature(resource.serialize_state())
    if cleaned:
      state[resource.name] = (cleaned, key)
    for child in resource.children:
      walk(child)

  walk(root)
  return state
