"""The Prep's configuration: what the device reports about itself, and reading it back from a file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# The JSON walk is the STAR's: it is written against dataclass field types, and nothing in it is
# particular to that device.
from pylabrobot.hamilton.star.driver.configuration import _restore, to_jsonable

from .prep_commands import DeckBounds, DeckSiteInfo, WasteSiteInfo

__all__ = ["DeviceConfiguration", "read_configuration", "to_jsonable"]


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
  has_mph: Optional[bool] = None
  """Whether the 8-channel head is fitted, from GetPresentChannels."""
  has_enclosure: bool = False
  """Whether MLPrep reports an enclosure."""

  # -- how it is set up --
  safe_speeds_enabled: bool = False
  """Whether MLPrep reports its safe speeds on."""
  default_traverse_height: Optional[float] = None
  """The height the channels travel at between positions, in mm. None if it could not be read."""
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
    `{"device": DeviceConfiguration}`, or an empty dict when the file holds none.
  """
  with open(path, encoding="utf-8") as f:
    saved = json.load(f)
  read: Dict[str, Any] = {}
  if "device" in saved:
    read["device"] = _restore(DeviceConfiguration, saved["device"])
  return read
