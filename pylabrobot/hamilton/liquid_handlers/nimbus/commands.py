"""Hamilton Nimbus command classes and supporting types.

This module contains all Nimbus-specific Hamilton protocol commands, including
tip management, initialization, door control, ADC, aspirate, and dispense.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.messages import HoiParams, HoiParamsParser
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.tcp.wire_types import (
  Bool,
  BoolArray,
  I16Array,
  I32,
  I32Array,
  U16,
  U16Array,
  U32Array,
)
from pylabrobot.resources import Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize

logger = logging.getLogger(__name__)


class NimbusCommand(TCPCommand):
  """Thin Nimbus command base for namespace clarity."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1

  def _build_structured_parameters(self) -> HoiParams:
    """Serialize wire-annotated dataclass payload fields in declaration order."""
    return HoiParams.from_struct(self)


# ============================================================================
# TIP TYPE ENUM
# ============================================================================


class NimbusTipType(enum.IntEnum):
  """Hamilton Nimbus tip type enumeration.

  Maps tip type names to their integer values used in Hamilton protocol commands.
  """

  STANDARD_300UL = 0  # "300ul Standard Volume Tip"
  STANDARD_300UL_FILTER = 1  # "300ul Standard Volume Tip with filter"
  LOW_VOLUME_10UL = 2  # "10ul Low Volume Tip"
  LOW_VOLUME_10UL_FILTER = 3  # "10ul Low Volume Tip with filter"
  HIGH_VOLUME_1000UL = 4  # "1000ul High Volume Tip"
  HIGH_VOLUME_1000UL_FILTER = 5  # "1000ul High Volume Tip with filter"
  TIP_50UL = 22  # "50ul Tip"
  TIP_50UL_FILTER = 23  # "50ul Tip with filter"
  SLIM_CORE_300UL = 36  # "SLIM CO-RE Tip 300ul"


def _get_tip_type_from_tip(tip: Tip) -> int:
  """Map Tip object characteristics to Hamilton tip type integer.

  Args:
    tip: Tip object with volume and filter information. Must be a HamiltonTip.

  Returns:
    Hamilton tip type integer value.

  Raises:
    ValueError: If tip characteristics don't match any known tip type.
  """

  if not isinstance(tip, HamiltonTip):
    raise ValueError("Tip must be a HamiltonTip to determine tip type.")

  if tip.tip_size == TipSize.LOW_VOLUME:  # 10ul tip
    return NimbusTipType.LOW_VOLUME_10UL_FILTER if tip.has_filter else NimbusTipType.LOW_VOLUME_10UL

  if tip.tip_size == TipSize.STANDARD_VOLUME and tip.maximal_volume < 60:  # 50ul tip
    return NimbusTipType.TIP_50UL_FILTER if tip.has_filter else NimbusTipType.TIP_50UL

  if tip.tip_size == TipSize.STANDARD_VOLUME:  # 300ul tip
    return NimbusTipType.STANDARD_300UL_FILTER if tip.has_filter else NimbusTipType.STANDARD_300UL

  if tip.tip_size == TipSize.HIGH_VOLUME:  # 1000ul tip
    return (
      NimbusTipType.HIGH_VOLUME_1000UL_FILTER
      if tip.has_filter
      else NimbusTipType.HIGH_VOLUME_1000UL
    )

  raise ValueError(
    f"Cannot determine tip type for tip with volume {tip.maximal_volume}uL "
    f"and filter={tip.has_filter}. No matching Hamilton tip type found."
  )


def _get_default_flow_rate(tip: Tip, is_aspirate: bool) -> float:
  """Get default flow rate based on tip type.

  Defaults from Hamilton Nimbus:
    - 1000 ul tip: 250 asp / 400 disp
    - 300 and 50 ul tip: 100 asp / 180 disp
    - 10 ul tip: 100 asp / 75 disp

  Args:
    tip: Tip object to determine default flow rate for.
    is_aspirate: True for aspirate, False for dispense.

  Returns:
    Default flow rate in uL/s.
  """
  tip_type = _get_tip_type_from_tip(tip)

  if tip_type in (NimbusTipType.HIGH_VOLUME_1000UL, NimbusTipType.HIGH_VOLUME_1000UL_FILTER):
    return 250.0 if is_aspirate else 400.0

  if tip_type in (NimbusTipType.LOW_VOLUME_10UL, NimbusTipType.LOW_VOLUME_10UL_FILTER):
    return 100.0 if is_aspirate else 75.0

  # 50 and 300 ul tips
  return 100.0 if is_aspirate else 180.0


# ============================================================================
# COMMAND CLASSES
# ============================================================================


class LockDoor(NimbusCommand):
  """Lock door command (DoorLock at 1:1:268, interface_id=1, command_id=1)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 1


class UnlockDoor(NimbusCommand):
  """Unlock door command (DoorLock at 1:1:268, interface_id=1, command_id=2)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 2


class IsDoorLocked(NimbusCommand):
  """Check if door is locked (DoorLock at 1:1:268, interface_id=1, command_id=3)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 3
  action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse IsDoorLocked response."""
    parser = HoiParamsParser(data)
    _, locked = parser.parse_next()
    return {"locked": bool(locked)}


class PreInitializeSmart(NimbusCommand):
  """Pre-initialize smart command (Pipette at 1:1:257, interface_id=1, command_id=32)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 32


@dataclass
class InitializeSmartRoll(NimbusCommand):
  """Initialize smart roll command (NimbusCore, cmd=29).

  Units:
    - positions/distances: 0.01 mm
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 29

  dest: Address
  x_positions: I32Array
  y_positions: I32Array
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  roll_distances: I32Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


class IsInitialized(NimbusCommand):
  """Check if instrument is initialized (NimbusCore at 1:1:48896, interface_id=1, command_id=14)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 14
  action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse IsInitialized response."""
    parser = HoiParamsParser(data)
    _, initialized = parser.parse_next()
    return {"initialized": bool(initialized)}


class IsTipPresent(NimbusCommand):
  """Check tip presence (Pipette at 1:1:257, interface_id=1, command_id=16)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 16
  action_code = 0

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse IsTipPresent response - returns List[i16]."""
    parser = HoiParamsParser(data)
    # Parse array of i16 values representing tip presence per channel
    _, tip_presence = parser.parse_next()
    return {"tip_present": tip_presence}


class GetChannelConfiguration_1(NimbusCommand):
  """Get channel configuration (NimbusCore root, interface_id=1, command_id=15)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 15
  action_code = 0

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse GetChannelConfiguration_1 response.

    Returns: (channels: u16, channel_types: List[i16])
    """
    parser = HoiParamsParser(data)
    _, channels = parser.parse_next()
    _, channel_types = parser.parse_next()
    return {"channels": channels, "channel_types": channel_types}


@dataclass
class SetChannelConfiguration(NimbusCommand):
  """Set channel configuration (Pipette, cmd=67).

  Field meanings:
    - `channel`: 1-based physical channel index.
    - `indexes`: firmware config slots (e.g. tip recognition / LLD monitors).
    - `enables`: booleans matching `indexes` order.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 67

  dest: Address
  channel: U16
  indexes: I16Array
  enables: BoolArray

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class GetChannelConfiguration(NimbusCommand):
  """Get channel configuration command (Pipette, cmd=66).

  Field meanings:
    - `channel`: 1-based physical channel index.
    - `indexes`: firmware config slots to query.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 66
  action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

  dest: Address
  channel: U16
  indexes: I16Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse GetChannelConfiguration response.

    Returns: { enabled: List[bool] }
    """
    parser = HoiParamsParser(data)
    _, enabled = parser.parse_next()
    return {"enabled": enabled}


class Park(NimbusCommand):
  """Park command (NimbusCore at 1:1:48896, interface_id=1, command_id=3)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 3


@dataclass
class PickupTips(NimbusCommand):
  """Pick up tips command (Pipette, cmd=4).

  Units:
    - positions/heights: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `tip_types`: per-channel Nimbus tip type IDs.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 4

  dest: Address
  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_pick_up_process: I32Array
  end_tip_pick_up_process: I32Array
  tip_types: U16Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class DropTips(NimbusCommand):
  """Drop tips command (Pipette, cmd=5).

  Units:
    - positions/heights: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `default_waste`: when true, firmware default waste position is used.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 5

  dest: Address
  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  default_waste: Bool

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class DropTipsRoll(NimbusCommand):
  """Drop tips with roll command (Pipette, cmd=82).

  Units:
    - positions/heights/distances: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 82

  dest: Address
  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  roll_distances: I32Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class EnableADC(NimbusCommand):
  """Enable ADC command (Pipette, cmd=43).

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 43

  dest: Address
  channels_involved: U16Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class DisableADC(NimbusCommand):
  """Disable ADC command (Pipette, cmd=44).

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 44

  dest: Address
  channels_involved: U16Array

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class Aspirate(NimbusCommand):
  """Aspirate command (Pipette, cmd=6).

  Units:
    - linear positions/heights: 0.01 mm
    - volumes: 0.1 uL
    - flow rates: 0.1 uL/s
    - settling time: 0.1 s

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `aspirate_type`: firmware aspirate mode per channel.
    - `lld_mode`: 0=off, 1=cLLD, 2=pLLD, 3=dual.
    - `tadm_enabled`: enable Total Aspiration/Dispense Monitoring.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 6

  dest: Address
  # Channel selectors/modes.
  aspirate_type: I16Array
  channels_involved: U16Array
  # Motion and level tracking.
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  lld_search_height: I32Array
  liquid_height: I32Array
  immersion_depth: I32Array
  surface_following_distance: I32Array
  minimum_height: I32Array
  clot_detection_height: I32Array
  min_z_endpos: I32
  # Volumetric profile.
  swap_speed: U32Array
  blow_out_air_volume: U32Array
  pre_wetting_volume: U32Array
  aspirate_volume: U32Array
  transport_air_volume: U32Array
  aspiration_speed: U32Array
  settling_time: U32Array
  # Mixing profile.
  mix_volume: U32Array
  mix_cycles: U32Array
  mix_position_from_liquid_surface: I32Array
  mix_surface_following_distance: I32Array
  mix_speed: U32Array
  # Advanced monitoring/firmware controls.
  tube_section_height: I32Array
  tube_section_ratio: I32Array
  lld_mode: I16Array
  gamma_lld_sensitivity: I16Array
  dp_lld_sensitivity: I16Array
  lld_height_difference: I32Array
  tadm_enabled: Bool
  limit_curve_index: U32Array
  recording_mode: U16

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()


@dataclass
class Dispense(NimbusCommand):
  """Dispense command (Pipette, cmd=7).

  Units:
    - linear positions/heights: 0.01 mm
    - volumes: 0.1 uL
    - flow rates: 0.1 uL/s
    - settling time: 0.1 s

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `dispense_type`: firmware dispense mode per channel.
    - `lld_mode`: 0=off, 1=cLLD, 2=pLLD, 3=dual.
    - `tadm_enabled`: enable Total Aspiration/Dispense Monitoring.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 7

  dest: Address
  # Channel selectors/modes.
  dispense_type: I16Array
  channels_involved: U16Array
  # Motion and level tracking.
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  lld_search_height: I32Array
  liquid_height: I32Array
  immersion_depth: I32Array
  surface_following_distance: I32Array
  minimum_height: I32Array
  min_z_endpos: I32
  # Volumetric profile.
  swap_speed: U32Array
  transport_air_volume: U32Array
  dispense_volume: U32Array
  stop_back_volume: U32Array
  blow_out_air_volume: U32Array
  dispense_speed: U32Array
  cut_off_speed: U32Array
  settling_time: U32Array
  # Mixing profile.
  mix_volume: U32Array
  mix_cycles: U32Array
  mix_position_from_liquid_surface: I32Array
  mix_surface_following_distance: I32Array
  mix_speed: U32Array
  # Dispense-specific offsets and advanced controls.
  side_touch_off_distance: I32
  dispense_offset: I32Array
  tube_section_height: I32Array
  tube_section_ratio: I32Array
  lld_mode: I16Array
  gamma_lld_sensitivity: I16Array
  tadm_enabled: Bool
  limit_curve_index: U32Array
  recording_mode: U16

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return self._build_structured_parameters()
