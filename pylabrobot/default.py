import sys
from typing import Union, TypeVar

if sys.version_info < (3, 10):
  from typing_extensions import TypeGuard
else:
  from typing import TypeGuard


class _DefaultType:
  """ Default values for any parameter, defined per robot. """
  def __repr__(self) -> str:
    return "Default"

  def __bool__(self) -> bool:
    return False


Default = _DefaultType()


T = TypeVar("T")
Defaultable = Union[T, _DefaultType]

def is_default(value: Defaultable[T]) -> TypeGuard[_DefaultType]:
  """ Returns True if the value is the default value. This serves as a typeguard. """
  return value is Default


def is_not_default(value: Defaultable[T]) -> TypeGuard[T]:
  """ Returns True if the value is not the default value. This serves as a typeguard.

  Unfortunately this method is needed as the typeguard for is_default does not reveal to mypy that
  the value is T.
  """
  return not is_default(value)


def get_value(value: Defaultable[T], default: T) -> T:
  """ Returns the value if it is not the default value, otherwise returns the default value. """
  if is_not_default(value):
    return value
  return default
