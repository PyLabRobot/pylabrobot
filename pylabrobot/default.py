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
  return value is Default


# Unfortunately this method is needed as the typeguard for is_default does not reveal to mypy that
# the value is T.
def is_not_default(value: Defaultable[T]) -> TypeGuard[T]:
  return not is_default(value)


def get_value(value: Defaultable[T], default: T) -> T:
  if is_not_default(value):
    return value
  return default
