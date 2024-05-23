""" A simple JSON serializer. """

import enum
import inspect
import sys
from typing import Any, Dict, List, Union, cast

if sys.version_info >= (3, 10):
  from typing import TypeAlias
else:
  from typing_extensions import TypeAlias

# pylint: disable=invalid-name
JSON: TypeAlias = Union[Dict[str, "JSON"], List["JSON"], str, int, float, bool, None]


def get_plr_class_from_string(klass_type: str):
  # pylint: disable=import-outside-toplevel, cyclic-import
  import pylabrobot.resources as resource_module
  import pylabrobot.liquid_handling as lh_module
  for name, obj in inspect.getmembers(resource_module) + inspect.getmembers(lh_module):
    if inspect.isclass(obj) and name == klass_type:
      return obj
  raise ValueError(f"Could not find class {klass_type}")


def serialize(obj: Any) -> JSON:
  """ Serialize an object. """

  if isinstance(obj, (int, float, str, bool, type(None))):
    return obj
  if isinstance(obj, (list, tuple, set)):
    return [serialize(item) for item in obj]
  if isinstance(obj, dict):
    return {k: serialize(v) for k, v in obj.items()}
  if isinstance(obj, enum.Enum):
    return obj.name
  if isinstance(obj, object):
    if hasattr(obj, "serialize"): # if the object has a custom serialize method
      return cast(JSON, obj.serialize())
    else:
      data: Dict[str, Any] = {}
      for key, value in obj.__dict__.items():
        if key.startswith("_"):
          continue
        data[key] = serialize(value)
      data["type"] = obj.__class__.__name__
      return data
  raise TypeError(f"Cannot serialize {obj} of type {type(obj)}")


def deserialize(data: JSON) -> Any:
  """ Deserialize an object. """

  if isinstance(data, (int, float, str, bool, type(None))):
    return data
  if isinstance(data, list):
    return [deserialize(item) for item in data]
  if isinstance(data, dict):
    if "type" in data: # deserialize a class
      data = data.copy()
      klass_type = cast(str, data.pop("type"))
      klass = get_plr_class_from_string(klass_type)
      params = {k: deserialize(v) for k, v in data.items()}
      return klass(**params)
    return {k: deserialize(v) for k, v in data.items()}
  raise TypeError(f"Cannot deserialize {data} of type {type(data)}")
