import enum
import sys


class Strictness(enum.Enum):
  """ Strictness level for liquid handling. """

  IGNORE = 0
  WARN = 1
  STRICT = 2

this = sys.modules[__name__]
this.strictness = Strictness.WARN # type: ignore


def set_strictness(strictness: Strictness) -> None:
  """ Set the strictness level for liquid handling. """
  this.strictness = strictness # type: ignore


def get_strictness() -> Strictness:
  """ Get the strictness level for liquid handling. """
  return this.strictness # type: ignore
