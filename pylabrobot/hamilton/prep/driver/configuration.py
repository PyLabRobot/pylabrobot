"""The Prep's configuration: what the device reports about itself, and reading it back from a file."""

from __future__ import annotations

import dataclasses
import datetime
import json
import typing
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from .features.pipettes import PipettesConfiguration
from .prep_commands import DeckBounds, DeckSiteInfo, WasteSiteInfo

__all__ = ["DeviceConfiguration", "read_configuration", "to_jsonable"]

# The JSON walk is the STAR's: it is written against dataclass field types, and nothing in it is
# particular to that device.


def to_jsonable(value: Any) -> Any:
  """The value as JSON holds it.

  Args:
    value: what to convert - a configuration, or anything one holds.

  Returns:
    The same value in types `json.dump` accepts.
  """
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return {
      field.name: to_jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
    }
  if isinstance(value, datetime.date):
    return value.isoformat()
  if isinstance(value, (list, tuple)):
    return [to_jsonable(item) for item in value]
  if isinstance(value, dict):
    # Keys are written as text because JSON has no other kind. What they were is on the field.
    return {str(key): to_jsonable(item) for key, item in value.items()}
  return value


def _restore(hint: Any, value: Any) -> Any:
  """One value, back in the type its field is declared to hold.

  Args:
    hint: the declared type.
    value: the value as JSON held it.

  Returns:
    The value in the declared type.
  """
  if value is None:
    return None

  origin = typing.get_origin(hint)
  args = typing.get_args(hint)

  if origin is Union:  # Optional[X] is Union[X, None]; the None case returned above.
    declared = [arg for arg in args if arg is not type(None)]
    return _restore(declared[0], value) if len(declared) == 1 else value
  if origin is tuple:
    # Fixed-length tuples name a type per position; `Tuple[X, ...]` names one for all of them.
    if len(args) == 2 and args[1] is Ellipsis:
      return tuple(_restore(args[0], item) for item in value)
    return tuple(_restore(arg, item) for arg, item in zip(args, value))
  if origin is list:
    return [_restore(args[0], item) for item in value]
  if origin is dict:
    key_hint, value_hint = args
    return {_restore(key_hint, key): _restore(value_hint, item) for key, item in value.items()}
  if hint is int and isinstance(value, str):
    # A dict keyed by int: JSON wrote the key as text, and the field says what it was.
    return int(value)
  if hint is datetime.date:
    return datetime.date.fromisoformat(value)
  if dataclasses.is_dataclass(hint) and isinstance(hint, type):
    # A nested configuration: rebuilt field by field against what its own class declares. Names the
    # class does not have are left out, so a file written by a driver that has since dropped a
    # field still loads.
    field_types = typing.get_type_hints(hint)
    named = {field.name for field in dataclasses.fields(hint)}
    return hint(**{n: _restore(field_types[n], v) for n, v in value.items() if n in named})
  return value


@dataclass
class DeviceConfiguration:
  """The device's installed hardware and geometry.

  What MLPrep, MLPrepService, MLPrepCpu and the deck configuration report, read at setup.
  """

  # -- which device this is, and what it is running --
  serial_number: Optional[str] = None
  """What the device calls itself, as MLPrepCpu answers."""
  firmware_version: Optional[str] = None
  """What MLPrepCpu runs."""

  # -- what is fitted --
  num_channels: Optional[int] = None
  """How many independent pipetting channels, 1 or 2, from GetPresentChannels."""
  head8_installed: Optional[bool] = None
  """Whether the 8-channel head is fitted, from GetPresentChannels."""
  has_enclosure: bool = False
  """Whether MLPrep reports an enclosure."""

  # -- how it is set up --
  safe_speeds_enabled: bool = False
  """Whether MLPrep reports its safe speeds on."""
  deck_bounds: Optional[DeckBounds] = None
  """The deck's extent on each axis, from the deck configuration."""
  deck_sites: Tuple[DeckSiteInfo, ...] = ()
  """The deck sites the deck configuration defines."""
  waste_sites: Tuple[WasteSiteInfo, ...] = ()
  """The waste positions the deck configuration defines."""


def read_configuration(path: str) -> Dict[str, Any]:
  """Read a saved configuration back into the dataclasses it was written from.

  Args:
    path: a file `PrepDriver.save_configuration` wrote.

  Returns:
    `{"device": DeviceConfiguration, "pipettes": PipettesConfiguration}`, holding whichever of the
    two the file holds.
  """
  with open(path, encoding="utf-8") as f:
    saved = json.load(f)
  read: Dict[str, Any] = {}
  if "device" in saved:
    read["device"] = _restore(DeviceConfiguration, saved["device"])
  if "pipettes" in saved:
    read["pipettes"] = _restore(PipettesConfiguration, saved["pipettes"])
  return read
