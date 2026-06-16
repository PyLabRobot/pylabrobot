"""Nimbus channel topology — typed wrapper around ChannelConfiguration cmd 30 data."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
  from .info import NimbusInstrumentInfo


class ChannelType(enum.IntEnum):
  """Mirrors GlobalObjects.ChannelType from NimbusCORE.dll."""

  NONE = 0
  CHANNEL_300UL = 1
  CHANNEL_1000UL = 2
  CHANNEL_5000UL = 3


class Rail(enum.IntEnum):
  """Mirrors GlobalObjects.Rail from NimbusCORE.dll."""

  LEFT = 0
  RIGHT = 1


_CHANNEL_TYPE_MAX_VOLUME: dict[ChannelType, float] = {
  ChannelType.NONE: 0.0,
  ChannelType.CHANNEL_300UL: 300.0,
  ChannelType.CHANNEL_1000UL: 1000.0,
  ChannelType.CHANNEL_5000UL: 5000.0,
}


@dataclass(frozen=True)
class NimbusChannelConfig:
  """Per-channel hardware configuration decoded from firmware."""

  type: ChannelType
  rail: Rail
  previous_neighbor_spacing: int
  next_neighbor_spacing: int
  can_address: int

  @property
  def max_volume(self) -> float:
    """Maximum aspirate/dispense volume for this channel type in µL."""
    return _CHANNEL_TYPE_MAX_VOLUME.get(self.type, 0.0)


@dataclass(frozen=True)
class NimbusChannelMap:
  """Typed per-channel topology built from :class:`NimbusInstrumentInfo`."""

  channels: List[NimbusChannelConfig]

  @property
  def num_channels(self) -> int:
    return len(self.channels)

  def channel_type(self, index: int) -> ChannelType:
    return self.channels[index].type

  def max_volume_for_channel(self, index: int) -> float:
    return self.channels[index].max_volume

  @staticmethod
  def from_info(info: "NimbusInstrumentInfo") -> "NimbusChannelMap":
    channels = [
      NimbusChannelConfig(
        type=ChannelType(wire.channel_type),
        rail=Rail(wire.rail),
        previous_neighbor_spacing=wire.previous_neighbor_spacing,
        next_neighbor_spacing=wire.next_neighbor_spacing,
        can_address=wire.can_address,
      )
      for wire in info.channel_configurations
    ]
    return NimbusChannelMap(channels=channels)
