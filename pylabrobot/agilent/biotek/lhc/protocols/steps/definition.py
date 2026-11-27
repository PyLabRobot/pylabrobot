"""Reading the delimited text a protocol file stores a step as.

Every step in a protocol file is one ``|``-separated string. The first field is an optional format
marker, the field after it is the step type, and the rest belong to that type. These helpers cover
what all step types share: splitting, locating the step-type field and converting a single field
with the width the format uses for it.
"""

from __future__ import annotations

from typing import Callable

from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import TRAVEL_RATE_TO_BYTE, TravelRate
from pylabrobot.agilent.biotek.lhc.enums.steps.wash_format import (
  WASH_FORMAT_TO_BYTE,
  WashFormat,
)

FORMAT_MARKER = "DV103"
"""The format marker written on every step this package produces."""

_MARKER_PREFIX = "DV"
_MARKER_VERSION = 103.0


def version_of(found: list[str]) -> float | None:
  """Which format version a definition's marker names.

  Args:
    found: The fields of a definition.

  Returns:
    The version, or None when the definition carries no marker. An unmarked definition predates the
    marker, so it is older than any version that has one.
  """
  if not found or not found[0].startswith(_MARKER_PREFIX):
    return None
  return float(found[0][len(_MARKER_PREFIX) :])


def is_older_format(found: list[str]) -> bool:
  """Whether a definition was written by a release older than the one this package writes.

  Only such a definition may be short: each version appended fields to the end of a layout, so an
  older one is missing a tail. A definition of the current version that is short is not missing a
  tail -- it is another product's layout, whose fields do not line up at all.

  Args:
    found: The fields of a definition.

  Returns:
    Whether it is older.
  """
  version = version_of(found)
  return version is None or version < _MARKER_VERSION


def fields(text: str, empty_ok: bool = False) -> tuple[list[str], int]:
  """Split a step definition and say where its step-type field sits.

  Args:
    text: The step definition.
    empty_ok: Whether an empty field holds its place. Some step types write empty fields and count
      them; the rest drop them.

  Returns:
    The fields, and the index of the step-type field among them.

  Raises:
    ValueError: If the definition is empty, or carries a newer format marker than this package
      reads.
  """
  found = text.split("|")
  if not empty_ok:
    found = [field for field in found if field]
  if not found:
    raise ValueError("empty step definition")
  if not found[0].startswith(_MARKER_PREFIX):
    return found, 0
  if float(found[0][len(_MARKER_PREFIX) :]) > _MARKER_VERSION:
    raise ValueError(f"step definition {found[0]} is newer than {FORMAT_MARKER}")
  return found, 1


def own_fields(
  text: str,
  step_type: StepType,
  count: int,
  empty_ok: bool = False,
  defaults: Callable[[], str] | None = None,
) -> list[str]:
  """The fields belonging to a step type whose layout is a fixed length.

  Args:
    text: The step definition.
    step_type: The step type the layout belongs to, named in the error message.
    count: How many fields the layout has, counting the step-type field.
    empty_ok: Whether an empty field holds its place.
    defaults: What the type's defaults look like as a definition, for filling in the tail an older
      release did not write. Without it a short definition is an error.

  Returns:
    The fields after the step-type field, ``count - 1`` of them.

  Raises:
    ValueError: If the definition has more fields than the layout, or fewer without being an older
      release's.
  """
  found, start = fields(text, empty_ok)
  present = len(found) - start
  if present == count:
    return found[start + 1 :]
  if present < count and defaults is not None and is_older_format(found):
    return _filled(found, start, count, step_type, defaults, empty_ok, text)
  raise ValueError(f"{step_type.name} expects {count} fields, got {present}: {text!r}")


def own_fields_at_least(
  text: str,
  step_type: StepType,
  minimum: int,
  empty_ok: bool = False,
  defaults: Callable[[], str] | None = None,
) -> list[str]:
  """The fields belonging to a step type whose layout ends in an optional tail.

  What a surplus means depends on how much of it there is, so the caller decides.

  Args:
    text: The step definition.
    step_type: The step type the layout belongs to, named in the error message.
    minimum: The shortest the layout can be, counting the step-type field.
    empty_ok: Whether an empty field holds its place.
    defaults: What the type's defaults look like as a definition, for filling in the tail an older
      release did not write. Without it a short definition is an error.

  Returns:
    The fields after the step-type field, at least ``minimum - 1`` of them.

  Raises:
    ValueError: If the definition has fewer fields than that without being an older release's.
  """
  found, start = fields(text, empty_ok)
  present = len(found) - start
  if present >= minimum:
    return found[start + 1 :]
  if defaults is not None and is_older_format(found):
    return _filled(found, start, minimum, step_type, defaults, empty_ok, text)
  raise ValueError(f"{step_type.name} expects at least {minimum} fields, got {present}: {text!r}")


def _filled(
  found: list[str],
  start: int,
  count: int,
  step_type: StepType,
  defaults: Callable[[], str],
  empty_ok: bool,
  text: str,
) -> list[str]:
  """Fill a definition written by an older release out to the current layout's full length.

  Each version appended its new fields to the end of a layout, so a definition an older release
  wrote is the current one minus a tail: the fields it does carry mean what they mean at the
  positions they are in, and the rest are taken from the type's defaults. That is why only an older
  definition may be filled -- a short definition of the *current* version is another product's
  layout, where a field can be missing from the middle and nothing after it lines up.

  Args:
    found: The fields the definition carries.
    start: Where its step-type field sits.
    count: How many fields the layout has, counting the step-type field.
    step_type: The step type the layout belongs to, named in the error message.
    defaults: What the type's defaults look like as a definition.
    empty_ok: Whether an empty field holds its place.
    text: The definition, named in the error message.

  Returns:
    The fields after the step-type field, ``count - 1`` of them.

  Raises:
    ValueError: If the defaults are themselves shorter than the layout, which means they cannot say
      what the absent fields are.
  """
  present = len(found) - start
  filled, filled_start = fields(defaults(), empty_ok)
  if len(filled) - filled_start < count:
    raise ValueError(
      f"{step_type.name} expects {count} fields, got {present}, and its defaults describe "
      f"{len(filled) - filled_start}: {text!r}"
    )
  return found[start + 1 :] + filled[filled_start + present : filled_start + count]


def flag(field: str) -> bool:
  """Read a boolean field.

  Args:
    field: The field text, ``"True"`` or ``"False"`` in any case.

  Returns:
    What it says.

  Raises:
    ValueError: If it says anything else.
  """
  lowered = field.strip().lower()
  if lowered in ("true", "false"):
    return lowered == "true"
  raise ValueError(f"not a boolean: {field!r}")


def number(field: str, bits: int) -> int:
  """Read an unsigned field of a given width.

  Args:
    field: The field text.
    bits: How many bits the value is stored in.

  Returns:
    The value.

  Raises:
    ValueError: If it does not fit, which makes the whole definition unreadable rather than
      merely invalid.
  """
  value = int(field)
  if not 0 <= value < 1 << bits:
    raise ValueError(f"{value} does not fit in {bits} unsigned bits")
  return value


def signed(field: str, bits: int) -> int:
  """Read a signed field of a given width, as the offsets are stored.

  Args:
    field: The field text.
    bits: How many bits the value is stored in.

  Returns:
    The value.

  Raises:
    ValueError: If it does not fit.
  """
  value = int(field)
  if not -(1 << (bits - 1)) <= value < 1 << (bits - 1):
    raise ValueError(f"{value} does not fit in {bits} signed bits")
  return value


BUFFERS: dict[str, Buffer] = {"A": "A", "B": "B", "C": "C", "D": "D"}
"""The buffer inlet each buffer field names."""

TRAVEL_RATES: dict[str, TravelRate] = {rate: rate for rate in TRAVEL_RATE_TO_BYTE}
"""The travel rate each travel-rate field names."""

WASH_FORMATS: dict[str, WashFormat] = {value: value for value in WASH_FORMAT_TO_BYTE}
"""The wash format each format field names."""
