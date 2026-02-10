"""A simple JSON serializer."""

import enum
import inspect
import marshal
import math
import sys
import types
from typing import Any, Dict, List, Optional, Union, cast

if sys.version_info >= (3, 10):
  from typing import TypeAlias
else:
  from typing_extensions import TypeAlias

from pylabrobot.utils.object_parsing import find_subclass

JSON: TypeAlias = Union[Dict[str, "JSON"], List["JSON"], str, int, float, bool, None]


class SerializableMixin:
  """Mixin for classes that can be serialized and deserialized."""

  def serialize(self) -> dict:
    data = {}
    for key, value in vars(self).items():
      if key.startswith("_"):
        continue
      data[key] = serialize(value)
    data["type"] = self.__class__.__name__
    return data


def serialize(obj: Any) -> JSON:
  """Serialize an object."""

  if isinstance(obj, (int, float, str, bool, type(None))):
    # infinities and NaNs are not valid JSON, so we convert them to strings
    if isinstance(obj, float) and not math.isfinite(obj):
      return "nan" if math.isnan(obj) else ("Infinity" if obj > 0 else "-Infinity")
    return obj
  if isinstance(obj, (list, tuple, set)):
    return [serialize(item) for item in obj]
  if isinstance(obj, dict):
    return {k: serialize(v) for k, v in obj.items()}
  if isinstance(obj, enum.Enum):
    return obj.name
  if inspect.isfunction(obj):
    return {
      "type": "function",
      "code": marshal.dumps(obj.__code__).hex(),
      "closure": serialize(obj.__closure__) if obj.__closure__ else None,
    }
  if isinstance(obj, types.CellType):
    return {"type": "cell", "contents": serialize(obj.cell_contents)}
  if isinstance(obj, object):
    if hasattr(obj, "serialize"):  # if the object has a custom serialize method
      return cast(JSON, obj.serialize())
    else:
      data: Dict[str, Any] = {}
      for key, value in vars(obj).items():
        if key.startswith("_"):
          continue
        data[key] = serialize(value)
      data["type"] = obj.__class__.__name__
      return data
  raise TypeError(f"Cannot serialize {obj} of type {type(obj)}")


def deserialize(data: JSON, allow_marshal: bool = False) -> Any:
  """Deserialize an object."""

  if isinstance(data, str):
    if data == "Infinity":
      return math.inf
    if data == "-Infinity":
      return -math.inf
    if data == "nan":
      return math.nan
    return data
  if isinstance(data, (int, float, bool, type(None))):
    return data
  if isinstance(data, list):
    return [deserialize(item, allow_marshal=allow_marshal) for item in data]
  if isinstance(data, dict):
    if "type" in data:  # deserialize a class
      data = data.copy()
      klass_type = cast(str, data.pop("type"))
      if klass_type == "function":
        if allow_marshal:
          assert isinstance(data["code"], str)
          code = marshal.loads(bytes.fromhex(data["code"]))
          closure = (
            tuple(deserialize(data["closure"], allow_marshal=allow_marshal))
            if data["closure"]
            else None
          )
          return types.FunctionType(code, globals(), closure=closure)
        return None
      if klass_type == "cell":
        return types.CellType(deserialize(data["contents"], allow_marshal=allow_marshal))
      klass = find_subclass(klass_type, cls=SerializableMixin)
      if klass is None:
        raise ValueError(f"Could not find class '{klass_type}'")
      params = {k: deserialize(v, allow_marshal=allow_marshal) for k, v in data.items()}
      if "deserialize" in klass.__dict__:
        return klass.deserialize(params)  # type: ignore[attr-defined]
      params = _fill_defaults(klass, params)
      return klass(**params)
    return {k: deserialize(v, allow_marshal=allow_marshal) for k, v in data.items()}
  if isinstance(data, object):
    return data
  raise TypeError(f"Cannot deserialize {data} of type {type(data)}")


def apply_merge_patch(target: JSON, patch: JSON) -> JSON:
  """Apply a JSON Merge Patch (RFC 7386) to a target document.

  Rules:
  - If patch is not a dict, it replaces the target entirely.
  - If a patch value is None (JSON null), the key is removed from target.
  - If a patch value is a dict, recurse.
  - Otherwise, replace the key.
  """
  if not isinstance(patch, dict):
    return patch

  if not isinstance(target, dict):
    target = {}

  result = dict(target)
  for key, value in patch.items():
    if value is None:
      result.pop(key, None)
    else:
      result[key] = apply_merge_patch(result.get(key), value)
  return result


def create_merge_patch(source: JSON, target: JSON) -> Optional[JSON]:
  """Create a JSON Merge Patch (RFC 7386) that transforms source into target.

  Returns None if source and target are equal (no patch needed).
  """
  if isinstance(source, dict) and isinstance(target, dict):
    patch: Dict[str, JSON] = {}
    all_keys = set(source.keys()) | set(target.keys())
    for key in all_keys:
      if key not in target:
        patch[key] = None  # remove
      elif key not in source:
        patch[key] = target[key]  # add
      else:
        sub_patch = create_merge_patch(source[key], target[key])
        if sub_patch is not None:
          patch[key] = sub_patch
    return patch if patch else None

  if source == target:
    # Handle NaN: NaN != NaN, but we treat string "nan" as equal
    if isinstance(source, float) and isinstance(target, float):
      if math.isnan(source) and math.isnan(target):
        return None
    else:
      return None
  return target


def compact(data: JSON) -> JSON:
  """Strip fields from a serialized dict tree that match constructor defaults.

  For every dict with a ``"type"`` field, looks up the class via
  :func:`get_plr_class_from_string`, inspects ``__init__`` defaults, serializes
  each default, and omits fields where the serialized value matches.

  Meta keys (``type``, ``location``, ``parent_name``) are always kept.
  ``children`` is kept only when non-empty.
  """
  if isinstance(data, list):
    return [compact(item) for item in data]
  if not isinstance(data, dict):
    return data

  if "type" not in data:
    return {k: compact(v) for k, v in data.items()}

  type_name = data["type"]

  # Try to look up the class; if not found, just recurse children
  try:
    klass = get_plr_class_from_string(type_name)
  except ValueError:
    return {k: compact(v) for k, v in data.items()}

  # Get constructor defaults
  defaults = _get_init_defaults(klass)

  meta_keys = {"type", "location", "parent_name", "name"}
  result: Dict[str, JSON] = {}

  for key, value in data.items():
    # Always keep meta keys
    if key in meta_keys:
      result[key] = compact(value) if isinstance(value, (dict, list)) else value
      continue

    # children: keep only when non-empty
    if key == "children":
      if isinstance(value, list) and len(value) > 0:
        result[key] = compact(value)
      continue

    # If we have a default for this key and it matches, skip it
    if key in defaults:
      default_serialized = serialize(defaults[key])
      if _json_equal(value, default_serialized):
        continue

    # Recurse into nested dicts/lists
    result[key] = compact(value)

  return result


def _get_init_defaults(klass: type) -> Dict[str, Any]:
  """Get the default values for a class's __init__ parameters."""
  try:
    sig = inspect.signature(klass.__init__)
  except (ValueError, TypeError):
    return {}

  defaults: Dict[str, Any] = {}
  for name, param in sig.parameters.items():
    if name == "self":
      continue
    if param.default is not inspect.Parameter.empty:
      defaults[name] = param.default
  return defaults


def _fill_defaults(klass: type, params: Dict[str, Any]) -> Dict[str, Any]:
  """Fill in missing params from constructor defaults."""
  defaults = _get_init_defaults(klass)
  for key, default_value in defaults.items():
    if key not in params:
      params[key] = default_value
  return params


def _json_equal(a: JSON, b: JSON) -> bool:
  """Compare two JSON values for equality, handling NaN."""
  if isinstance(a, float) and isinstance(b, float):
    if math.isnan(a) and math.isnan(b):
      return True
  if type(a) != type(b):
    return False
  if isinstance(a, dict) and isinstance(b, dict):
    if set(a.keys()) != set(b.keys()):
      return False
    return all(_json_equal(a[k], b[k]) for k in a)
  if isinstance(a, list) and isinstance(b, list):
    if len(a) != len(b):
      return False
    return all(_json_equal(x, y) for x, y in zip(a, b))
  return a == b
