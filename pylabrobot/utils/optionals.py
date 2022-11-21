from typing import TypeVar, Optional

T = TypeVar("T")


def force_unwrap(v: Optional[T]) -> T: # like in Swift
  """ Force unwrap an optional value. """
  if v is None:
    raise ValueError("Optional is None")
  return v
