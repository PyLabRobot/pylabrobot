"""Flatten any resource tree into prototypes and instances.

The current visualizer sends one JSON object per resource, so a 96-well plate repeats the same
geometry ninety-six times. Here a resource is split in two: what it *is* (its model, shared by
every resource serializing to the same thing) and where it *is* (its instance: a name, a parent,
and a transform). Models go over the wire once; instances are six floats in a typed array.

Nothing here knows about any resource type. The split is derived from `Resource.serialize()` by
removing the fields that vary per instance, so a resource type that does not exist yet flattens
correctly the day it is written.
"""

import base64
import functools
import inspect
import json
import struct
from typing import Any, Dict, List, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .grids import describe_bands, describe_grid

# Fields of a serialized resource that describe this one resource rather than its kind. Everything
# else is shared by every resource that serializes the same way, and so belongs to the model.
INSTANCE_FIELDS = frozenset({"name", "location", "rotation", "parent_name", "children"})

# Identity also leaks below the top level: a tip spot carries the name of its prototype tip, and a
# plate carries a map of identifier to the name of the well it holds. Both are this resource's
# links, not its kind, and leaving them in gives every plate and every tip spot its own model.
NAME_KEYS = frozenset({"name", "parent_name"})

# Fields a resource may declare about itself that `serialize()` does not yet carry. Upstream these
# belong in the resource's own serialization; passing them through here keeps the viewer free of
# any device's constants in the meantime.
DECLARED_FIELDS = ("reference_point", "window", "mesh", "reference_glb", "appearance")
RESOURCE_LINK = "<resource>"


def _declared(value: Any) -> Any:
  """A declared field as JSON.

  Read straight off the resource rather than out of `serialize()`, so a field may still be a live
  object - a pipette states its reference point as a `Coordinate` where an arm states one as a word.
  Anything that knows its own serialized form is asked for it.
  """
  serialize = getattr(value, "serialize", None)
  return serialize() if callable(serialize) else value


def _model_of(data: Dict[str, Any], names: frozenset) -> Dict[str, Any]:
  """The kind-defining part of a serialized resource, with every identity removed.

  Two rules, both general. A key called `name` anywhere names one particular thing. And inside a
  nested structure, a string that matches a resource in this tree is a link to it. Ordered maps
  such as a plate's item ordering keep their keys, which is the part that does describe the kind.

  The link rule deliberately stops at the top level: a resource's own descriptive fields are
  scalars there, and a category can legitimately read the same as some resource's name (a deck
  called "deck", a bench called "bench") without being a reference to it.
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
  """The public operations of a resource class, as signatures.

  A model is already one per class, so the operations belong on the model. The existing visualizer
  keeps them in a separate registry keyed by type name and re-sends it with every assignment; here
  they ride along with the thing they describe and are sent once.
  """
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
  """A flattened resource tree: models, instances, and the measurements that justify the split."""

  def __init__(self, names: Optional[frozenset] = None) -> None:
    self.names_in_tree = names or frozenset()
    # Every model derived this pass, by resource name, so the next pass can skip the work.
    self.derived: Dict[str, Dict[str, Any]] = {}
    self.models: List[Dict[str, Any]] = []
    # Candidate models per serialized type. Interning compares dictionaries directly rather than
    # serializing each one to a key: dict equality is a C-level compare, and a tree has only a
    # handful of distinct models per type, so the scan is short.
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

    Deriving a model means serializing a resource in full and then throwing nearly all of it away:
    profiling a facility of three hundred plates, that was over half the flattening time, and
    twenty-eight thousand wells were serialized to produce one model. A resource that has not
    changed has the model it had last time, and its position is readable directly.
    """
    index = len(self.names)
    self.names.append(resource.name)
    self.model_of_instance.append(self._intern(model))
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
    self.derived[resource_name] = model
    self.model_of_instance.append(self._intern(model))
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

  def stats(self, scene_bytes: Optional[int] = None) -> Dict[str, Any]:
    """What the split cost and what it saved, in bytes actually sent."""
    new_bytes = scene_bytes if scene_bytes is not None else len(json.dumps(self.serialize()))
    return {
      "instances": self.num_instances,
      "models": len(self.models),
      "legacy_bytes": self.legacy_bytes,
      "scene_bytes": new_bytes,
      "ratio": round(self.legacy_bytes / new_bytes, 2) if new_bytes else 0.0,
    }


def build_scene(
  root: Resource,
  measure_legacy: bool = False,
  known: Optional[Dict[str, Dict[str, Any]]] = None,
  known_names: Optional[frozenset] = None,
) -> Scene:
  """Flatten `root` and every descendant into models and instances.

  Traversal is depth-first and parents are emitted before children, so a client can resolve world
  transforms in one forward pass without sorting.

  Args:
    root: the resource to flatten.
    measure_legacy: also serialize the tree the way the existing visualizer sends it, to report
      what the split saves. It costs a second full serialization, so it is off by default and
      belongs in a benchmark rather than in a running viewer.
    known: models derived by an earlier pass, by resource name. A name found here is taken to have
      the model it had then, which is what makes a rebuild cost only what actually changed.
    known_names: the names that pass saw. Reuse is only sound while the tree holds the same names,
      because whether a string in a model counts as a reference to another resource depends on
      which names exist. Different names, and `known` is ignored and everything derived again.
  """
  names = frozenset(_all_names(root))
  scene = Scene(names=names)
  reuse = known if (known and known_names == names) else {}

  def walk(resource: Resource, parent: int) -> None:
    model = reuse.get(resource.name)
    if model is not None:
      index = scene.add_known(resource, parent, model)
      scene.derived[resource.name] = model
      for child in resource.children:
        walk(child, index)
      return

    data = resource.serialize()
    data.pop("children", None)
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
    for child in resource.children:
      walk(child, index)

  walk(root, -1)
  if measure_legacy:
    scene.legacy_bytes = len(json.dumps(_serialize_tree(root)))
  return scene


def _all_names(resource: Resource) -> List[str]:
  """Every resource name in the tree, so a link to one can be recognised as a link."""
  found = [resource.name]
  for child in resource.children:
    found.extend(_all_names(child))
  return found


def _serialize_tree(resource: Resource) -> Dict[str, Any]:
  """The existing visualizer's payload shape, for the comparison only."""
  data = resource.serialize()
  data["children"] = [_serialize_tree(child) for child in resource.children]
  return data


# A rotation nobody has turned. It is published by every resource that publishes anything at all,
# and on a scene of mostly unrotated geometry it is the single largest repeated value.
IDENTITY_ROTATION = {"type": "Rotation", "x": 0, "y": 0, "z": 0}

# Fields that say which resource a state came from rather than what the state is. The message
# already answers that with its key, and a prototype cannot be shared while it carries the identity
# of one instance - the same reason `_model_of` strips names out of models.
STATE_IDENTITY_KEYS = frozenset({"name", "thing"})


def _without_identity(value: Any) -> Any:
  """A state value with the fields that name one instance taken out, at any depth."""
  if isinstance(value, dict):
    return {k: _without_identity(v) for k, v in value.items() if k not in STATE_IDENTITY_KEYS}
  if isinstance(value, list):
    return [_without_identity(v) for v in value]
  return value


# What a viewer can see. Firmware reports position to 0.1 mm and a tenth of a microlitre is below
# anything a well can show, so a change smaller than this is not news. Rounding to it rather than
# comparing against a threshold means two values that look the same *are* the same: they produce one
# signature, so they suppress a resend and share one entry in the table below. One rule, both jobs.
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
  """The publishable form of one resource's state, and a key that is equal when it looks equal."""
  cleaned = _visible(
    _without_identity(
      {k: v for k, v in published.items() if not (k == "rotation" and v == IDENTITY_ROTATION)}
    )
  )
  return cleaned, json.dumps(cleaned, sort_keys=True, default=str)


def pack_state(states: Dict[str, Dict[str, Any]], epoch: int = 0) -> Dict[str, Any]:
  """Pack `{name: state}` into distinct states plus an index per name.

  State is overwhelmingly repetitive: every empty well publishes the same zeros, every unused tip
  spot the same tip. Sending each one in full costs a few hundred bytes per resource and dominates
  the message on any facility with more than a device or two in it, so the same prototype-and-
  instance split the geometry uses is applied here.

  Every name that went in comes out, including one whose state cleans down to nothing. Dropping it
  would leave a client that had already been told about a full well believing it was still full.
  """
  distinct: Dict[str, int] = {}
  table: List[Dict[str, Any]] = []
  index: Dict[str, int] = {}

  for name, published in states.items():
    cleaned, key = state_signature(published)
    if key not in distinct:
      distinct[key] = len(table)
      table.append(cleaned)
    index[name] = distinct[key]

  # Addressed by name, deliberately. Addressing by scene index would be smaller, and was tried: it
  # is wrong, because the order instances are emitted in is not stable. Resources are created
  # lazily during setup and reassigned by code that has nothing to do with the viewer, which
  # reorders a parent's children, and an index that means one resource in one scene means a
  # different one in the next. A name means the same thing in every scene.
  return {"epoch": epoch, "states": table, "of": index}


def collect_state(root: Resource) -> Dict[str, Dict[str, Any]]:
  """Tracker state for every resource that publishes any, keyed by name.

  Only resources whose state says something is included, so a scene of mostly static geometry
  sends a small message. A rotation that is the identity says nothing - but one that is not says
  where a joint is pointing, which for an arm's links is the whole of what they have to report.
  """
  state: Dict[str, Dict[str, Any]] = {}

  def walk(resource: Resource) -> None:
    published, _ = state_signature(resource.serialize_state())
    if published:
      state[resource.name] = resource.serialize_state()
    for child in resource.children:
      walk(child)

  walk(root)
  return state


def find(root: Resource, name: str) -> Optional[Resource]:
  """The descendant called `name`, or None."""
  if root.name == name:
    return root
  for child in root.children:
    hit = find(child, name)
    if hit is not None:
      return hit
  return None
