from typing import List, Optional, TypeVar

T = TypeVar("T")


def fill_in_defaults(val: Optional[List[T]], default: List[T]) -> List[T]:
  """Util for converting an argument to the appropriate format for low level star methods."""
  # if the val is None, use the default.
  if val is None:
    return default
  # if the val is a list, it must be of the correct length.
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {val}")
  # replace None values in list with default values.
  val = [v if v is not None else d for v, d in zip(val, default)]
  # the value is ready to be used.
  return val
