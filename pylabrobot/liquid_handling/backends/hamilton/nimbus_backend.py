"""Hamilton Nimbus backend implementation.

This module provides the NimbusBackend class for controlling Hamilton Nimbus
instruments via TCP communication using the Hamilton protocol.
"""

from __future__ import annotations

import enum
import logging
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar, Union

from pylabrobot.liquid_handling.backends.hamilton.common import fill_in_defaults
from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
  HamiltonIntrospection,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import (
  HoiParams,
  HoiParamsParser,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonProtocol,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp_backend import HamiltonTCPBackend
from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  PipettingOp,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Tip
from pylabrobot.resources.container import Container
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)


T = TypeVar("T")


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


class LockDoor(HamiltonCommand):
  """Lock door command (DoorLock at 1:1:268, interface_id=1, command_id=1)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 1


class UnlockDoor(HamiltonCommand):
  """Unlock door command (DoorLock at 1:1:268, interface_id=1, command_id=2)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 2


class IsDoorLocked(HamiltonCommand):
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


class PreInitializeSmart(HamiltonCommand):
  """Pre-initialize smart command (Pipette at 1:1:257, interface_id=1, command_id=32)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 32


class InitializeSmartRoll(HamiltonCommand):
  """Initialize smart roll command (NimbusCore at 1:1:48896, interface_id=1, command_id=29)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 29

  def __init__(
    self,
    dest: Address,
    x_positions: List[int],
    y_positions: List[int],
    begin_tip_deposit_process: List[int],
    end_tip_deposit_process: List[int],
    z_position_at_end_of_a_command: List[int],
    roll_distances: List[int],
  ):
    """Initialize InitializeSmartRoll command.

    Args:
      dest: Destination address (NimbusCore)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      begin_tip_deposit_process: Z start positions in 0.01mm units
      end_tip_deposit_process: Z stop positions in 0.01mm units
      z_position_at_end_of_a_command: Z position at end of command in 0.01mm units
      roll_distances: Roll distances in 0.01mm units
    """
    super().__init__(dest)
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.begin_tip_deposit_process = begin_tip_deposit_process
    self.end_tip_deposit_process = end_tip_deposit_process
    self.z_position_at_end_of_a_command = z_position_at_end_of_a_command
    self.roll_distances = roll_distances

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32_array(self.begin_tip_deposit_process)
      .i32_array(self.end_tip_deposit_process)
      .i32_array(self.z_position_at_end_of_a_command)
      .i32_array(self.roll_distances)
    )


class IsInitialized(HamiltonCommand):
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


class IsTipPresent(HamiltonCommand):
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


class GetChannelConfiguration_1(HamiltonCommand):
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


class SetChannelConfiguration(HamiltonCommand):
  """Set channel configuration (Pipette at 1:1:257, interface_id=1, command_id=67)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 67

  def __init__(
    self,
    dest: Address,
    channel: int,
    indexes: List[int],
    enables: List[bool],
  ):
    """Initialize SetChannelConfiguration command.

    Args:
      dest: Destination address (Pipette)
      channel: Channel number (1-based)
      indexes: List of configuration indexes (e.g., [1, 3, 4])
        1: Tip Recognition, 2: Aspirate and clot monitoring pLLD,
        3: Aspirate monitoring with cLLD, 4: Clot monitoring with cLLD
      enables: List of enable flags (e.g., [True, False, False, False])
    """
    super().__init__(dest)
    self.channel = channel
    self.indexes = indexes
    self.enables = enables

  def build_parameters(self) -> HoiParams:
    return HoiParams().u16(self.channel).i16_array(self.indexes).bool_array(self.enables)


class Park(HamiltonCommand):
  """Park command (NimbusCore at 1:1:48896, interface_id=1, command_id=3)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 3


class PickupTips(HamiltonCommand):
  """Pick up tips command (Pipette at 1:1:257, interface_id=1, command_id=4)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 4

  def __init__(
    self,
    dest: Address,
    channels_involved: List[int],
    x_positions: List[int],
    y_positions: List[int],
    minimum_traverse_height_at_beginning_of_a_command: int,
    begin_tip_pick_up_process: List[int],
    end_tip_pick_up_process: List[int],
    tip_types: List[int],
  ):
    """Initialize PickupTips command.

    Args:
      dest: Destination address (Pipette)
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in 0.01mm units
      begin_tip_pick_up_process: Z start positions in 0.01mm units
      end_tip_pick_up_process: Z stop positions in 0.01mm units
      tip_types: Tip type integers for each channel
    """
    super().__init__(dest)
    self.channels_involved = channels_involved
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.minimum_traverse_height_at_beginning_of_a_command = (
      minimum_traverse_height_at_beginning_of_a_command
    )
    self.begin_tip_pick_up_process = begin_tip_pick_up_process
    self.end_tip_pick_up_process = end_tip_pick_up_process
    self.tip_types = tip_types

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .u16_array(self.channels_involved)
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32(self.minimum_traverse_height_at_beginning_of_a_command)
      .i32_array(self.begin_tip_pick_up_process)
      .i32_array(self.end_tip_pick_up_process)
      .u16_array(self.tip_types)
    )


class DropTips(HamiltonCommand):
  """Drop tips command (Pipette at 1:1:257, interface_id=1, command_id=5)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 5

  def __init__(
    self,
    dest: Address,
    channels_involved: List[int],
    x_positions: List[int],
    y_positions: List[int],
    minimum_traverse_height_at_beginning_of_a_command: int,
    begin_tip_deposit_process: List[int],
    end_tip_deposit_process: List[int],
    z_position_at_end_of_a_command: List[int],
    default_waste: bool,
  ):
    """Initialize DropTips command.

    Args:
      dest: Destination address (Pipette)
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in 0.01mm units
      begin_tip_deposit_process: Z start positions in 0.01mm units
      end_tip_deposit_process: Z stop positions in 0.01mm units
      z_position_at_end_of_a_command: Z position at end of command in 0.01mm units
      default_waste: If True, drop to default waste (positions may be ignored)
    """
    super().__init__(dest)
    self.channels_involved = channels_involved
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.minimum_traverse_height_at_beginning_of_a_command = (
      minimum_traverse_height_at_beginning_of_a_command
    )
    self.begin_tip_deposit_process = begin_tip_deposit_process
    self.end_tip_deposit_process = end_tip_deposit_process
    self.z_position_at_end_of_a_command = z_position_at_end_of_a_command
    self.default_waste = default_waste

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .u16_array(self.channels_involved)
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32(self.minimum_traverse_height_at_beginning_of_a_command)
      .i32_array(self.begin_tip_deposit_process)
      .i32_array(self.end_tip_deposit_process)
      .i32_array(self.z_position_at_end_of_a_command)
      .bool_value(self.default_waste)
    )


class DropTipsRoll(HamiltonCommand):
  """Drop tips with roll command (Pipette at 1:1:257, interface_id=1, command_id=82)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 82

  def __init__(
    self,
    dest: Address,
    channels_involved: List[int],
    x_positions: List[int],
    y_positions: List[int],
    minimum_traverse_height_at_beginning_of_a_command: int,
    begin_tip_deposit_process: List[int],
    end_tip_deposit_process: List[int],
    z_position_at_end_of_a_command: List[int],
    roll_distances: List[int],
  ):
    """Initialize DropTipsRoll command.

    Args:
      dest: Destination address (Pipette)
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in 0.01mm units
      begin_tip_deposit_process: Z start positions in 0.01mm units
      end_tip_deposit_process: Z stop positions in 0.01mm units
      z_position_at_end_of_a_command: Z position at end of command in 0.01mm units
      roll_distances: Roll distance for each channel in 0.01mm units
    """
    super().__init__(dest)
    self.channels_involved = channels_involved
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.minimum_traverse_height_at_beginning_of_a_command = (
      minimum_traverse_height_at_beginning_of_a_command
    )
    self.begin_tip_deposit_process = begin_tip_deposit_process
    self.end_tip_deposit_process = end_tip_deposit_process
    self.z_position_at_end_of_a_command = z_position_at_end_of_a_command
    self.roll_distances = roll_distances

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .u16_array(self.channels_involved)
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32(self.minimum_traverse_height_at_beginning_of_a_command)
      .i32_array(self.begin_tip_deposit_process)
      .i32_array(self.end_tip_deposit_process)
      .i32_array(self.z_position_at_end_of_a_command)
      .i32_array(self.roll_distances)
    )


class EnableADC(HamiltonCommand):
  """Enable ADC command (Pipette at 1:1:257, interface_id=1, command_id=43)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 43

  def __init__(
    self,
    dest: Address,
    channels_involved: List[int],
  ):
    """Initialize EnableADC command.

    Args:
      dest: Destination address (Pipette)
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
    """
    super().__init__(dest)
    self.channels_involved = channels_involved

  def build_parameters(self) -> HoiParams:
    return HoiParams().u16_array(self.channels_involved)


class DisableADC(HamiltonCommand):
  """Disable ADC command (Pipette at 1:1:257, interface_id=1, command_id=44)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 44

  def __init__(
    self,
    dest: Address,
    channels_involved: List[int],
  ):
    """Initialize DisableADC command.

    Args:
      dest: Destination address (Pipette)
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
    """
    super().__init__(dest)
    self.channels_involved = channels_involved

  def build_parameters(self) -> HoiParams:
    return HoiParams().u16_array(self.channels_involved)


class GetChannelConfiguration(HamiltonCommand):
  """Get channel configuration command (Pipette at 1:1:257, interface_id=1, command_id=66)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 66
  action_code = 0  # Must be 0 (STATUS_REQUEST), default is 3 (COMMAND_REQUEST)

  def __init__(
    self,
    dest: Address,
    channel: int,
    indexes: List[int],
  ):
    """Initialize GetChannelConfiguration command.

    Args:
      dest: Destination address (Pipette)
      channel: Channel number (1-based)
      indexes: List of configuration indexes (e.g., [2] for "Aspirate monitoring with cLLD")
    """
    super().__init__(dest)
    self.channel = channel
    self.indexes = indexes

  def build_parameters(self) -> HoiParams:
    return HoiParams().u16(self.channel).i16_array(self.indexes)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> dict:
    """Parse GetChannelConfiguration response.

    Returns: { enabled: List[bool] }
    """
    parser = HoiParamsParser(data)
    _, enabled = parser.parse_next()
    return {"enabled": enabled}


class Aspirate(HamiltonCommand):
  """Aspirate command (Pipette at 1:1:257, interface_id=1, command_id=6)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 6

  def __init__(
    self,
    dest: Address,
    aspirate_type: List[int],
    channels_involved: List[int],
    x_positions: List[int],
    y_positions: List[int],
    minimum_traverse_height_at_beginning_of_a_command: int,
    lld_search_height: List[int],
    liquid_height: List[int],
    immersion_depth: List[int],
    surface_following_distance: List[int],
    minimum_height: List[int],
    clot_detection_height: List[int],
    min_z_endpos: int,
    swap_speed: List[int],
    blow_out_air_volume: List[int],
    pre_wetting_volume: List[int],
    aspirate_volume: List[int],
    transport_air_volume: List[int],
    aspiration_speed: List[int],
    settling_time: List[int],
    mix_volume: List[int],
    mix_cycles: List[int],
    mix_position_from_liquid_surface: List[int],
    mix_surface_following_distance: List[int],
    mix_speed: List[int],
    tube_section_height: List[int],
    tube_section_ratio: List[int],
    lld_mode: List[int],
    gamma_lld_sensitivity: List[int],
    dp_lld_sensitivity: List[int],
    lld_height_difference: List[int],
    tadm_enabled: bool,
    limit_curve_index: List[int],
    recording_mode: int,
  ):
    """Initialize Aspirate command.

    Args:
      dest: Destination address (Pipette)
      aspirate_type: Aspirate type for each channel (List[i16])
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in 0.01mm units
      lld_search_height: LLD search height for each channel in 0.01mm units
      liquid_height: Liquid height for each channel in 0.01mm units
      immersion_depth: Immersion depth for each channel in 0.01mm units
      surface_following_distance: Surface following distance for each channel in 0.01mm units
      minimum_height: Minimum height for each channel in 0.01mm units
      clot_detection_height: Clot detection height for each channel in 0.01mm units
      min_z_endpos: Minimum Z end position in 0.01mm units
      swap_speed: Swap speed (on leaving liquid) for each channel in 0.1uL/s units
      blow_out_air_volume: Blowout volume for each channel in 0.1uL units
      pre_wetting_volume: Pre-wetting volume for each channel in 0.1uL units
      aspirate_volume: Aspirate volume for each channel in 0.1uL units
      transport_air_volume: Transport air volume for each channel in 0.1uL units
      aspiration_speed: Aspirate speed for each channel in 0.1uL/s units
      settling_time: Settling time for each channel in 0.1s units
      mix_volume: Mix volume for each channel in 0.1uL units
      mix_cycles: Mix cycles for each channel
      mix_position_from_liquid_surface: Mix position from liquid surface for each channel in 0.01mm units
      mix_surface_following_distance: Mix follow distance for each channel in 0.01mm units
      mix_speed: Mix speed for each channel in 0.1uL/s units
      tube_section_height: Tube section height for each channel in 0.01mm units
      tube_section_ratio: Tube section ratio for each channel
      lld_mode: LLD mode for each channel (List[i16])
      gamma_lld_sensitivity: Gamma LLD sensitivity for each channel (List[i16])
      dp_lld_sensitivity: DP LLD sensitivity for each channel (List[i16])
      lld_height_difference: LLD height difference for each channel in 0.01mm units
      tadm_enabled: TADM enabled flag
      limit_curve_index: Limit curve index for each channel
      recording_mode: Recording mode (u16)
    """
    super().__init__(dest)
    self.aspirate_type = aspirate_type
    self.channels_involved = channels_involved
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.minimum_traverse_height_at_beginning_of_a_command = (
      minimum_traverse_height_at_beginning_of_a_command
    )
    self.lld_search_height = lld_search_height
    self.liquid_height = liquid_height
    self.immersion_depth = immersion_depth
    self.surface_following_distance = surface_following_distance
    self.minimum_height = minimum_height
    self.clot_detection_height = clot_detection_height
    self.min_z_endpos = min_z_endpos
    self.swap_speed = swap_speed
    self.blow_out_air_volume = blow_out_air_volume
    self.pre_wetting_volume = pre_wetting_volume
    self.aspirate_volume = aspirate_volume
    self.transport_air_volume = transport_air_volume
    self.aspiration_speed = aspiration_speed
    self.settling_time = settling_time
    self.mix_volume = mix_volume
    self.mix_cycles = mix_cycles
    self.mix_position_from_liquid_surface = mix_position_from_liquid_surface
    self.mix_surface_following_distance = mix_surface_following_distance
    self.mix_speed = mix_speed
    self.tube_section_height = tube_section_height
    self.tube_section_ratio = tube_section_ratio
    self.lld_mode = lld_mode
    self.gamma_lld_sensitivity = gamma_lld_sensitivity
    self.dp_lld_sensitivity = dp_lld_sensitivity
    self.lld_height_difference = lld_height_difference
    self.tadm_enabled = tadm_enabled
    self.limit_curve_index = limit_curve_index
    self.recording_mode = recording_mode

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .i16_array(self.aspirate_type)
      .u16_array(self.channels_involved)
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32(self.minimum_traverse_height_at_beginning_of_a_command)
      .i32_array(self.lld_search_height)
      .i32_array(self.liquid_height)
      .i32_array(self.immersion_depth)
      .i32_array(self.surface_following_distance)
      .i32_array(self.minimum_height)
      .i32_array(self.clot_detection_height)
      .i32(self.min_z_endpos)
      .u32_array(self.swap_speed)
      .u32_array(self.blow_out_air_volume)
      .u32_array(self.pre_wetting_volume)
      .u32_array(self.aspirate_volume)
      .u32_array(self.transport_air_volume)
      .u32_array(self.aspiration_speed)
      .u32_array(self.settling_time)
      .u32_array(self.mix_volume)
      .u32_array(self.mix_cycles)
      .i32_array(self.mix_position_from_liquid_surface)
      .i32_array(self.mix_surface_following_distance)
      .u32_array(self.mix_speed)
      .i32_array(self.tube_section_height)
      .i32_array(self.tube_section_ratio)
      .i16_array(self.lld_mode)
      .i16_array(self.gamma_lld_sensitivity)
      .i16_array(self.dp_lld_sensitivity)
      .i32_array(self.lld_height_difference)
      .bool_value(self.tadm_enabled)
      .u32_array(self.limit_curve_index)
      .u16(self.recording_mode)
    )


class Dispense(HamiltonCommand):
  """Dispense command (Pipette at 1:1:257, interface_id=1, command_id=7)."""

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  command_id = 7

  def __init__(
    self,
    dest: Address,
    dispense_type: List[int],
    channels_involved: List[int],
    x_positions: List[int],
    y_positions: List[int],
    minimum_traverse_height_at_beginning_of_a_command: int,
    lld_search_height: List[int],
    liquid_height: List[int],
    immersion_depth: List[int],
    surface_following_distance: List[int],
    minimum_height: List[int],
    min_z_endpos: int,
    swap_speed: List[int],
    transport_air_volume: List[int],
    dispense_volume: List[int],
    stop_back_volume: List[int],
    blow_out_air_volume: List[int],
    dispense_speed: List[int],
    cut_off_speed: List[int],
    settling_time: List[int],
    mix_volume: List[int],
    mix_cycles: List[int],
    mix_position_from_liquid_surface: List[int],
    mix_surface_following_distance: List[int],
    mix_speed: List[int],
    side_touch_off_distance: int,
    dispense_offset: List[int],
    tube_section_height: List[int],
    tube_section_ratio: List[int],
    lld_mode: List[int],
    gamma_lld_sensitivity: List[int],
    tadm_enabled: bool,
    limit_curve_index: List[int],
    recording_mode: int,
  ):
    """Initialize Dispense command.

    Args:
      dest: Destination address (Pipette)
      dispense_type: Dispense type for each channel (List[i16])
      channels_involved: Tip pattern (1 for active channels, 0 for inactive)
      x_positions: X positions in 0.01mm units
      y_positions: Y positions in 0.01mm units
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in 0.01mm units
      lld_search_height: LLD search height for each channel in 0.01mm units
      liquid_height: Liquid height for each channel in 0.01mm units
      immersion_depth: Immersion depth for each channel in 0.01mm units
      surface_following_distance: Surface following distance for each channel in 0.01mm units
      minimum_height: Minimum height for each channel in 0.01mm units
      min_z_endpos: Minimum Z end position in 0.01mm units
      swap_speed: Swap speed (on leaving liquid) for each channel in 0.1uL/s units
      transport_air_volume: Transport air volume for each channel in 0.1uL units
      dispense_volume: Dispense volume for each channel in 0.1uL units
      stop_back_volume: Stop back volume for each channel in 0.1uL units
      blow_out_air_volume: Blowout volume for each channel in 0.1uL units
      dispense_speed: Dispense speed for each channel in 0.1uL/s units
      cut_off_speed: Cut off speed for each channel in 0.1uL/s units
      settling_time: Settling time for each channel in 0.1s units
      mix_volume: Mix volume for each channel in 0.1uL units
      mix_cycles: Mix cycles for each channel
      mix_position_from_liquid_surface: Mix position from liquid surface for each channel in 0.01mm units
      mix_surface_following_distance: Mix follow distance for each channel in 0.01mm units
      mix_speed: Mix speed for each channel in 0.1uL/s units
      side_touch_off_distance: Side touch off distance in 0.01mm units
      dispense_offset: Dispense offset for each channel in 0.01mm units
      tube_section_height: Tube section height for each channel in 0.01mm units
      tube_section_ratio: Tube section ratio for each channel
      lld_mode: LLD mode for each channel (List[i16])
      gamma_lld_sensitivity: Gamma LLD sensitivity for each channel (List[i16])
      tadm_enabled: TADM enabled flag
      limit_curve_index: Limit curve index for each channel
      recording_mode: Recording mode (u16)
    """
    super().__init__(dest)
    self.dispense_type = dispense_type
    self.channels_involved = channels_involved
    self.x_positions = x_positions
    self.y_positions = y_positions
    self.minimum_traverse_height_at_beginning_of_a_command = (
      minimum_traverse_height_at_beginning_of_a_command
    )
    self.lld_search_height = lld_search_height
    self.liquid_height = liquid_height
    self.immersion_depth = immersion_depth
    self.surface_following_distance = surface_following_distance
    self.minimum_height = minimum_height
    self.min_z_endpos = min_z_endpos
    self.swap_speed = swap_speed
    self.transport_air_volume = transport_air_volume
    self.dispense_volume = dispense_volume
    self.stop_back_volume = stop_back_volume
    self.blow_out_air_volume = blow_out_air_volume
    self.dispense_speed = dispense_speed
    self.cut_off_speed = cut_off_speed
    self.settling_time = settling_time
    self.mix_volume = mix_volume
    self.mix_cycles = mix_cycles
    self.mix_position_from_liquid_surface = mix_position_from_liquid_surface
    self.mix_surface_following_distance = mix_surface_following_distance
    self.mix_speed = mix_speed
    self.side_touch_off_distance = side_touch_off_distance
    self.dispense_offset = dispense_offset
    self.tube_section_height = tube_section_height
    self.tube_section_ratio = tube_section_ratio
    self.lld_mode = lld_mode
    self.gamma_lld_sensitivity = gamma_lld_sensitivity
    self.tadm_enabled = tadm_enabled
    self.limit_curve_index = limit_curve_index
    self.recording_mode = recording_mode

  def build_parameters(self) -> HoiParams:
    return (
      HoiParams()
      .i16_array(self.dispense_type)
      .u16_array(self.channels_involved)
      .i32_array(self.x_positions)
      .i32_array(self.y_positions)
      .i32(self.minimum_traverse_height_at_beginning_of_a_command)
      .i32_array(self.lld_search_height)
      .i32_array(self.liquid_height)
      .i32_array(self.immersion_depth)
      .i32_array(self.surface_following_distance)
      .i32_array(self.minimum_height)
      .i32(self.min_z_endpos)
      .u32_array(self.swap_speed)
      .u32_array(self.transport_air_volume)
      .u32_array(self.dispense_volume)
      .u32_array(self.stop_back_volume)
      .u32_array(self.blow_out_air_volume)
      .u32_array(self.dispense_speed)
      .u32_array(self.cut_off_speed)
      .u32_array(self.settling_time)
      .u32_array(self.mix_volume)
      .u32_array(self.mix_cycles)
      .i32_array(self.mix_position_from_liquid_surface)
      .i32_array(self.mix_surface_following_distance)
      .u32_array(self.mix_speed)
      .i32(self.side_touch_off_distance)
      .i32_array(self.dispense_offset)
      .i32_array(self.tube_section_height)
      .i32_array(self.tube_section_ratio)
      .i16_array(self.lld_mode)
      .i16_array(self.gamma_lld_sensitivity)
      .bool_value(self.tadm_enabled)
      .u32_array(self.limit_curve_index)
      .u16(self.recording_mode)
    )


# ============================================================================
# MAIN BACKEND CLASS
# ============================================================================


class NimbusBackend(HamiltonTCPBackend):
  """Backend for Hamilton Nimbus liquid handling instruments.

  This backend uses TCP communication with the Hamilton protocol to control
  Nimbus instruments. It inherits from both TCPBackend (for communication)
  and LiquidHandlerBackend (for liquid handling interface).

  Attributes:
    _door_lock_available: Whether door lock is available on this instrument.
  """

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
  ):
    """Initialize Nimbus backend.

    Args:
      host: Hamilton instrument IP address
      port: Hamilton instrument port (default: 2000)
      read_timeout: Read timeout in seconds
      write_timeout: Write timeout in seconds
      auto_reconnect: Enable automatic reconnection
      max_reconnect_attempts: Maximum reconnection attempts
    """
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )

    self._num_channels: Optional[int] = None
    self._pipette_address: Optional[Address] = None
    self._door_lock_address: Optional[Address] = None
    self._nimbus_core_address: Optional[Address] = None
    self._is_initialized: Optional[bool] = None
    self._channel_configurations: Optional[Dict[int, Dict[int, bool]]] = None

    self._channel_traversal_height: float = 146.0  # Default traversal height in mm

  async def setup(self, unlock_door: bool = False, force_initialize: bool = False):
    """Set up the Nimbus backend.

    This method:
    1. Establishes TCP connection and performs protocol initialization
    2. Discovers instrument objects
    3. Queries channel configuration to get num_channels
    4. Queries tip presence
    5. Queries initialization status
    6. Locks door if available
    7. Conditionally initializes NimbusCore with InitializeSmartRoll (only if not initialized)
    8. Optionally unlocks door after initialization

    Args:
      unlock_door: If True, unlock door after initialization (default: False)
      force_initialize: If True, force initialization even if already initialized
    """
    # Call parent setup (TCP connection, Protocol 7 init, Protocol 3 registration)
    await super().setup()

    # Discover instrument objects
    await self._discover_instrument_objects()

    # Ensure required objects are discovered
    if self._pipette_address is None:
      raise RuntimeError("Pipette object not discovered. Cannot proceed with setup.")
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore root object not discovered. Cannot proceed with setup.")

    # Query channel configuration to get num_channels (use discovered address only)
    try:
      config = await self.send_command(GetChannelConfiguration_1(self._nimbus_core_address))
      assert config is not None, "GetChannelConfiguration_1 command returned None"
      self._num_channels = config["channels"]
      logger.info(f"Channel configuration: {config['channels']} channels")
    except Exception as e:
      logger.error(f"Failed to query channel configuration: {e}")
      raise

    # Query tip presence (use discovered address only)
    try:
      tip_present = await self.request_tip_presence()
      logger.info(f"Tip presence: {tip_present}")
    except Exception as e:
      logger.warning(f"Failed to query tip presence: {e}")

    # Query initialization status (use discovered address only)
    try:
      init_status = await self.send_command(IsInitialized(self._nimbus_core_address))
      assert init_status is not None, "IsInitialized command returned None"
      self._is_initialized = init_status.get("initialized", False)
      logger.info(f"Instrument initialized: {self._is_initialized}")
    except Exception as e:
      logger.error(f"Failed to query initialization status: {e}")
      raise

    # Lock door if available (optional - no error if not found)
    # This happens before initialization
    if self._door_lock_address is not None:
      try:
        if not await self.is_door_locked():
          await self.lock_door()
        else:
          logger.info("Door already locked")
      except RuntimeError:
        # Door lock not available or not set up - this is okay
        logger.warning("Door lock operations skipped (not available or not set up)")
      except Exception as e:
        logger.warning(f"Failed to lock door: {e}")

    # Conditional initialization - only if not already initialized
    if not self._is_initialized or force_initialize:
      # Set channel configuration for each channel (required before InitializeSmartRoll)
      try:
        # Configure all channels (1 to num_channels) - one SetChannelConfiguration call per channel
        # Parameters: channel (1-based), indexes=[1, 3, 4], enables=[True, False, False, False]
        for channel in range(1, self.num_channels + 1):
          await self.send_command(
            SetChannelConfiguration(
              dest=self._pipette_address,
              channel=channel,
              indexes=[1, 3, 4],
              enables=[True, False, False, False],
            )
          )
        logger.info(f"Channel configuration set for {self.num_channels} channels")
      except Exception as e:
        logger.error(f"Failed to set channel configuration: {e}")
        raise

      # Initialize NimbusCore with InitializeSmartRoll using waste positions
      try:
        # Build waste position parameters using helper method
        # Use all channels (0 to num_channels-1) for setup
        all_channels = list(range(self.num_channels))

        # Use same logic as DropTipsRoll: z_start = waste_z + 4.0mm, z_stop = waste_z, z_position_at_end = minimum_traverse_height_at_beginning_of_a_command
        (
          x_positions_full,
          y_positions_full,
          begin_tip_deposit_process_full,
          end_tip_deposit_process_full,
          z_position_at_end_of_a_command_full,
          roll_distances_full,
        ) = self._build_waste_position_params(
          use_channels=all_channels,
          z_position_at_end_of_a_command=None,  # Will default to minimum_traverse_height_at_beginning_of_a_command
          roll_distance=None,  # Will default to 9.0mm
        )

        await self.send_command(
          InitializeSmartRoll(
            dest=self._nimbus_core_address,
            x_positions=x_positions_full,
            y_positions=y_positions_full,
            begin_tip_deposit_process=begin_tip_deposit_process_full,
            end_tip_deposit_process=end_tip_deposit_process_full,
            z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
            roll_distances=roll_distances_full,
          )
        )
        logger.info("NimbusCore initialized with InitializeSmartRoll successfully")
        self._is_initialized = True
      except Exception as e:
        logger.error(f"Failed to initialize NimbusCore with InitializeSmartRoll: {e}")
        raise
    else:
      logger.info("Instrument already initialized, skipping initialization")

    # Unlock door if requested (optional - no error if not found)
    if unlock_door and self._door_lock_address is not None:
      try:
        await self.unlock_door()
      except RuntimeError:
        # Door lock not available or not set up - this is okay
        logger.warning("Door unlock requested but not available or not set up")
      except Exception as e:
        logger.warning(f"Failed to unlock door: {e}")

  async def _discover_instrument_objects(self):
    """Discover instrument-specific objects using introspection."""
    introspection = HamiltonIntrospection(self)

    # Get root objects (already discovered in setup)
    root_objects = self._discovered_objects.get("root", [])
    if not root_objects:
      logger.warning("No root objects discovered")
      return

    # Use first root object as NimbusCore
    nimbus_core_addr = root_objects[0]
    self._nimbus_core_address = nimbus_core_addr

    try:
      # Get NimbusCore object info
      core_info = await introspection.get_object(nimbus_core_addr)

      # Discover subobjects to find Pipette and DoorLock
      for i in range(core_info.subobject_count):
        try:
          sub_addr = await introspection.get_subobject_address(nimbus_core_addr, i)
          sub_info = await introspection.get_object(sub_addr)

          # Check if this is the Pipette by interface name
          if sub_info.name == "Pipette":
            self._pipette_address = sub_addr
            logger.info(f"Found Pipette at {sub_addr}")

          # Check if this is the DoorLock by interface name
          if sub_info.name == "DoorLock":
            self._door_lock_address = sub_addr
            logger.info(f"Found DoorLock at {sub_addr}")

        except Exception as e:
          logger.debug(f"Failed to get subobject {i}: {e}")

    except Exception as e:
      logger.warning(f"Failed to discover instrument objects: {e}")

    # If door lock not found via introspection, it's not available
    if self._door_lock_address is None:
      logger.info("DoorLock not available on this instrument")

  def _fill_by_channels(self, values: List[T], use_channels: List[int], default: T) -> List[T]:
    """Returns a full-length list of size `num_channels` where positions in `channels`
    are filled from `values` in order; all others are `default`. Similar to one-hot encoding."""
    if len(values) != len(use_channels):
      raise ValueError(
        f"values and channels must have same length (got {len(values)} vs {len(use_channels)})"
      )

    out = [default] * self.num_channels
    for ch, v in zip(use_channels, values):
      out[ch] = v
    return out

  @property
  def num_channels(self) -> int:
    """The number of channels that the robot has."""
    if self._num_channels is None:
      raise RuntimeError("num_channels not set. Call setup() first to query from instrument.")
    return self._num_channels

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the channels.

    This value will be used as the default value for the
    `minimal_traverse_height_at_begin_of_command` and `minimal_height_at_command_end` parameters
    for all commands, unless they are explicitly set in the command call.
    """

    if not 0 < traversal_height < 146:
      raise ValueError(f"Traversal height must be between 0 and 146 mm (got {traversal_height})")

    self._channel_traversal_height = traversal_height

  async def park(self):
    """Park the instrument.

    Raises:
      RuntimeError: If NimbusCore address was not discovered during setup.
    """
    if self._nimbus_core_address is None:
      raise RuntimeError("NimbusCore address not discovered. Call setup() first.")

    try:
      await self.send_command(Park(self._nimbus_core_address))
      logger.info("Instrument parked successfully")
    except Exception as e:
      logger.error(f"Failed to park instrument: {e}")
      raise

  async def is_door_locked(self) -> bool:
    """Check if the door is locked.

    Returns:
      True if door is locked, False if unlocked.

    Raises:
      RuntimeError: If door lock is not available on this instrument, or if setup() has not been called yet.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )

    try:
      status = await self.send_command(IsDoorLocked(self._door_lock_address))
      assert status is not None, "IsDoorLocked command returned None"
      return bool(status["locked"])
    except Exception as e:
      logger.error(f"Failed to check door lock status: {e}")
      raise

  async def lock_door(self) -> None:
    """Lock the door.

    Raises:
      RuntimeError: If door lock is not available on this instrument, or if setup() has not been called yet.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )

    try:
      await self.send_command(LockDoor(self._door_lock_address))
      logger.info("Door locked successfully")
    except Exception as e:
      logger.error(f"Failed to lock door: {e}")
      raise

  async def unlock_door(self) -> None:
    """Unlock the door.

    Raises:
      RuntimeError: If door lock is not available on this instrument, or if setup() has not been called yet.
    """
    if self._door_lock_address is None:
      raise RuntimeError(
        "Door lock is not available on this instrument or setup() has not been called."
      )

    try:
      await self.send_command(UnlockDoor(self._door_lock_address))
      logger.info("Door unlocked successfully")
    except Exception as e:
      logger.error(f"Failed to unlock door: {e}")
      raise

  async def stop(self):
    """Stop the backend and close connection."""
    await HamiltonTCPBackend.stop(self)

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Request tip presence on each channel.

    Returns:
      A list of length `num_channels` where each element is `True` if a tip is mounted,
      `False` if not, or `None` if unknown.
    """
    if self._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")
    tip_status = await self.send_command(IsTipPresent(self._pipette_address))
    assert tip_status is not None, "IsTipPresent command returned None"
    tip_present = tip_status.get("tip_present", [])
    return [bool(v) for v in tip_present]

  def _build_waste_position_params(
    self,
    use_channels: List[int],
    z_position_at_end_of_a_command: Optional[float] = None,
    roll_distance: Optional[float] = None,
  ) -> Tuple[List[int], List[int], List[int], List[int], List[int], List[int]]:
    """Build waste position parameters for InitializeSmartRoll or DropTipsRoll.

    Args:
      use_channels: List of channel indices to use
      z_position_at_end_of_a_command: Z final position in mm (absolute, optional, defaults to minimum_traverse_height_at_beginning_of_a_command)
      roll_distance: Roll distance in mm (optional, defaults to 9.0 mm)

    Returns:
      x_positions, y_positions, begin_tip_deposit_process_full, end_tip_deposit_process_full, z_position_at_end_of_a_command, roll_distances (all in 0.01mm units as lists matching num_channels)

    Raises:
      RuntimeError: If deck is not set or waste position not found
    """

    # Validate we have a NimbusDeck for coordinate conversion
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    # Extract coordinates for each channel
    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []
    z_positions_mm: List[float] = []

    for channel_idx in use_channels:
      # Get waste position from deck based on channel index
      # Use waste_type attribute from deck to construct waste position name
      if not hasattr(self.deck, "waste_type") or self.deck.waste_type is None:
        raise RuntimeError(
          f"Deck does not have waste_type attribute or waste_type is None. "
          f"Cannot determine waste position name for channel {channel_idx}."
        )
      waste_pos_name = f"{self.deck.waste_type}_{channel_idx + 1}"
      try:
        waste_pos = self.deck.get_resource(waste_pos_name)
        abs_location = waste_pos.get_location_wrt(self.deck)
      except Exception as e:
        raise RuntimeError(
          f"Failed to get waste position {waste_pos_name} for channel {channel_idx}: {e}"
        )

      # Convert to Hamilton coordinates (returns in mm)
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)

      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)
      z_positions_mm.append(hamilton_coord.z)

    # Convert positions to 0.01mm units (multiply by 100)
    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    # Calculate Z positions from waste position coordinates
    max_z_hamilton = max(z_positions_mm)  # Highest waste position Z in Hamilton coordinates
    waste_z_hamilton = max_z_hamilton

    # Calculate from waste position: start above waste position
    z_start_absolute_mm = waste_z_hamilton + 4.0  # Start 4mm above waste position

    # Calculate from waste position: stop at waste position
    z_stop_absolute_mm = waste_z_hamilton  # Stop at waste position

    if z_position_at_end_of_a_command is None:
      z_position_at_end_of_a_command = (
        self._channel_traversal_height
      )  # Use traverse height as final position

    if roll_distance is None:
      roll_distance = 9.0  # Default roll distance from log

    # Use absolute Z positions (same for all channels)
    begin_tip_deposit_process = [round(z_start_absolute_mm * 100)] * len(use_channels)
    end_tip_deposit_process = [round(z_stop_absolute_mm * 100)] * len(use_channels)
    z_position_at_end_of_a_command_list = [round(z_position_at_end_of_a_command * 100)] * len(
      use_channels
    )
    roll_distances = [round(roll_distance * 100)] * len(use_channels)

    # Ensure arrays match num_channels length (with zeros for inactive channels)
    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)
    begin_tip_deposit_process_full = self._fill_by_channels(
      begin_tip_deposit_process, use_channels, default=0
    )
    end_tip_deposit_process_full = self._fill_by_channels(
      end_tip_deposit_process, use_channels, default=0
    )
    z_position_at_end_of_a_command_full = self._fill_by_channels(
      z_position_at_end_of_a_command_list, use_channels, default=0
    )
    roll_distances_full = self._fill_by_channels(roll_distances, use_channels, default=0)

    return (
      x_positions_full,
      y_positions_full,
      begin_tip_deposit_process_full,
      end_tip_deposit_process_full,
      z_position_at_end_of_a_command_full,
      roll_distances_full,
    )

  # ============== Abstract methods from LiquidHandlerBackend ==============

  def _compute_ops_xy_locations(
    self, ops: Sequence[PipettingOp], use_channels: List[int]
  ) -> Tuple[List[int], List[int]]:
    """Compute X and Y positions in Hamilton coordinates for the given operations."""
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    x_positions_mm: List[float] = []
    y_positions_mm: List[float] = []

    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck)
      final_location = abs_location + op.offset
      hamilton_coord = self.deck.to_hamilton_coordinate(final_location)

      x_positions_mm.append(hamilton_coord.x)
      y_positions_mm.append(hamilton_coord.y)

    # Convert positions to 0.01mm units (multiply by 100)
    x_positions = [round(x * 100) for x in x_positions_mm]
    y_positions = [round(y * 100) for y in y_positions_mm]

    x_positions_full = self._fill_by_channels(x_positions, use_channels, default=0)
    y_positions_full = self._fill_by_channels(y_positions, use_channels, default=0)

    return x_positions_full, y_positions_full

  def _compute_tip_handling_parameters(
    self,
    ops: Sequence[Union[Pickup, Drop]],
    use_channels: List[int],
    use_fixed_offset: bool = False,
    fixed_offset_mm: float = 10.0,
  ):
    """Calculate Z positions for tip pickup/drop operations.

    Pickup (use_fixed_offset=False): Z based on tip length
      z_start = max_z + max_total_tip_length, z_stop = max_z + max_tip_length
    Drop (use_fixed_offset=True): Z based on fixed offset (matches VantageBackend default)
      z_start = max_z + fixed_offset_mm (default 10.0mm), z_stop = max_z

    Returns: (begin_position, end_position) in 0.01mm units
    """
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    z_positions_mm: List[float] = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck) + op.offset
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)
      z_positions_mm.append(hamilton_coord.z)

    max_z_hamilton = max(z_positions_mm)  # Highest resource Z in Hamilton coordinates

    if use_fixed_offset:
      # For drop operations: use fixed offsets relative to resource surface
      begin_position_mm = max_z_hamilton + fixed_offset_mm
      end_position_mm = max_z_hamilton
    else:
      # For pickup operations: use tip length
      # Similar to STAR backend: z_start = max_z + max_total_tip_length, z_stop = max_z + max_tip_length
      max_total_tip_length = max(op.tip.total_tip_length for op in ops)
      max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)
      begin_position_mm = max_z_hamilton + max_total_tip_length
      end_position_mm = max_z_hamilton + max_tip_length

    # Convert to 0.01mm units
    begin_position = [round(begin_position_mm * 100)] * len(ops)
    end_position = [round(end_position_mm * 100)] * len(ops)

    begin_position_full = self._fill_by_channels(begin_position, use_channels, default=0)
    end_position_full = self._fill_by_channels(end_position, use_channels, default=0)

    return begin_position_full, end_position_full

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    """Pick up tips from the specified resource.

    TODO: evaluate this doc:
    Z positions and traverse height are calculated from the resource locations and tip
    properties if not explicitly provided:
    - minimum_traverse_height_at_beginning_of_a_command: Uses deck z_max if not provided
    - z_start_offset: Calculated as max(resource Z) + max(tip total_tip_length)
    - z_stop_offset: Calculated as max(resource Z) + max(tip total_tip_length - tip fitting_depth)

    Args:
      ops: List of Pickup operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm (optional, defaults to _channel_traversal_height)

    Raises:
      RuntimeError: If pipette address or deck is not set
      ValueError: If deck is not a NimbusDeck and minimum_traverse_height_at_beginning_of_a_command is not provided
    """
    if self._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")

    # Validate we have a NimbusDeck for coordinate conversion
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    # Check tip presence before picking up tips
    try:
      tip_present = await self.request_tip_presence()
      channels_with_tips = [
        i for i, present in enumerate(tip_present) if i in use_channels and present
      ]
      if channels_with_tips:
        raise RuntimeError(
          f"Cannot pick up tips: channels {channels_with_tips} already have tips mounted. "
          f"Drop existing tips first."
        )
    except RuntimeError:
      raise
    except Exception as e:
      # If tip presence check fails, log warning but continue
      logger.warning(f"Could not check tip presence before pickup: {e}")

    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)
    begin_tip_pick_up_process, end_tip_pick_up_process = self._compute_tip_handling_parameters(
      ops, use_channels
    )

    # Build tip pattern array (True for active channels, False for inactive)
    channels_involved = [int(ch in use_channels) for ch in range(self.num_channels)]

    # Ensure arrays match num_channels length (pad with 0s for inactive channels)
    tip_types = [_get_tip_type_from_tip(op.tip) for op in ops]
    tip_types_full = self._fill_by_channels(tip_types, use_channels, default=0)

    # Traverse height: use default value
    if minimum_traverse_height_at_beginning_of_a_command is None:
      minimum_traverse_height_at_beginning_of_a_command = self._channel_traversal_height
    minimum_traverse_height_at_beginning_of_a_command_units = round(
      minimum_traverse_height_at_beginning_of_a_command * 100
    )  # Convert to 0.01mm units

    # Create and send command
    command = PickupTips(
      dest=self._pipette_address,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command_units,
      begin_tip_pick_up_process=begin_tip_pick_up_process,
      end_tip_pick_up_process=end_tip_pick_up_process,
      tip_types=tip_types_full,
    )

    try:
      await self.send_command(command)
      logger.info(f"Picked up tips on channels {use_channels}")
    except Exception as e:
      logger.error(f"Failed to pick up tips: {e}")
      raise

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    default_waste: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
    roll_distance: Optional[float] = None,
  ):
    """Drop tips to the specified resource.

    Auto-detects waste positions and uses appropriate command:
    - If resource is a waste position (Trash with category="waste_position"), uses DropTipsRoll
    - Otherwise, uses DropTips command

    Z positions are calculated from resource locations:
    - For waste positions: Fixed Z positions (135.39 mm start, 131.39 mm stop) via _build_waste_position_params
    - For regular resources: Fixed offsets relative to resource surface (max_z + 10mm start, max_z stop)
      Note: Z positions use fixed offsets, NOT tip length, because the tip is already mounted on the pipette.
      This works for all tip sizes (300ul, 1000ul, etc.) without additional configuration.
    - z_position_at_end_of_a_command: Calculated from resources (defaults to minimum_traverse_height_at_beginning_of_a_command)
    - roll_distance: Defaults to 9.0 mm for waste positions

    Args:
      ops: List of Drop operations, one per channel
      use_channels: List of channel indices to use
      default_waste: For DropTips command, if True, drop to default waste (positions may be ignored)
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm (optional, defaults to self._channel_traversal_height)
      z_position_at_end_of_a_command: Z final position in mm (absolute, optional, calculated from resources)
      roll_distance: Roll distance in mm (optional, defaults to 9.0 mm for waste positions)

    Raises:
      RuntimeError: If pipette address or deck is not set
      ValueError: If operations mix waste and regular resources
    """
    if self._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")

    # Validate we have a NimbusDeck for coordinate conversion
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    # Check if resources are waste positions (Trash objects)
    is_waste_positions = [isinstance(op.resource, Trash) for op in ops]
    all_waste = all(is_waste_positions)
    all_regular = not any(is_waste_positions)

    if not (all_waste or all_regular):
      raise ValueError(
        "Cannot mix waste positions and regular resources in a single drop_tips call. "
        "All operations must be either waste positions or regular resources."
      )

    # Build tip pattern array (1 for active channels, 0 for inactive)
    channels_involved = [int(ch in use_channels) for ch in range(self.num_channels)]

    # Traverse height: use provided value (defaults to class attribute)
    if minimum_traverse_height_at_beginning_of_a_command is None:
      minimum_traverse_height_at_beginning_of_a_command = self._channel_traversal_height
    minimum_traverse_height_at_beginning_of_a_command_units = round(
      minimum_traverse_height_at_beginning_of_a_command * 100
    )

    # Type annotation for command variable (can be either DropTips or DropTipsRoll)
    command: Union[DropTips, DropTipsRoll]

    if all_waste:
      # Use DropTipsRoll for waste positions
      # Build waste position parameters using helper method
      (
        x_positions_full,
        y_positions_full,
        begin_tip_deposit_process_full,
        end_tip_deposit_process_full,
        z_position_at_end_of_a_command_full,
        roll_distances_full,
      ) = self._build_waste_position_params(
        use_channels=use_channels,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command,
        roll_distance=roll_distance,
      )

      command = DropTipsRoll(
        dest=self._pipette_address,
        channels_involved=channels_involved,
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command_units,
        begin_tip_deposit_process=begin_tip_deposit_process_full,
        end_tip_deposit_process=end_tip_deposit_process_full,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
        roll_distances=roll_distances_full,
      )

    else:
      # Compute x and y positions for regular resources
      x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

      # Compute Z positions using fixed offsets (not tip length) for drop operations
      begin_tip_deposit_process, end_tip_deposit_process = self._compute_tip_handling_parameters(
        ops, use_channels, use_fixed_offset=True
      )

      # Compute final Z positions. Use the traverse height if not provided. Fill to num_channels.
      if z_position_at_end_of_a_command is None:
        z_position_at_end_of_a_command_value = (
          minimum_traverse_height_at_beginning_of_a_command  # Use traverse height as final position
        )
        z_position_at_end_of_a_command_list = [
          round(z_position_at_end_of_a_command_value * 100)
        ] * len(ops)  # in 0.01mm units
        z_position_at_end_of_a_command_full = self._fill_by_channels(
          z_position_at_end_of_a_command_list, use_channels, default=0
        )

      command = DropTips(
        dest=self._pipette_address,
        channels_involved=channels_involved,
        x_positions=x_positions_full,
        y_positions=y_positions_full,
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command_units,
        begin_tip_deposit_process=begin_tip_deposit_process,
        end_tip_deposit_process=end_tip_deposit_process,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command_full,
        default_waste=default_waste,
      )

    try:
      await self.send_command(command)
      logger.info(f"Dropped tips on channels {use_channels}")
    except Exception as e:
      logger.error(f"Failed to drop tips: {e}")
      raise

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    adc_enabled: bool = False,
    # Advanced kwargs (Optional, default to zeros/nulls)
    lld_mode: Optional[List[int]] = None,
    lld_search_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    tadm_enabled: bool = False,
  ):
    """Aspirate liquid from the specified resource using pip.

    Args:
      ops: List of SingleChannelAspiration operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm (optional, defaults to self._channel_traversal_height)
      adc_enabled: If True, enable ADC (Automatic Drip Control), else disable (default: False)
      lld_mode: LLD mode (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL), default: [0] * n
      lld_search_height: Relative offset from well bottom for LLD search start position (mm).
        This is a RELATIVE OFFSET, not an absolute coordinate. The instrument adds this to
        minimum_height (well bottom) to determine where to start the LLD search.
        If None, defaults to the well's size_z (depth), meaning "start search at top of well".
        When provided, should be a list of offsets in mm, one per channel.
      immersion_depth: Depth to submerge into liquid (mm), default: [0.0] * n
      surface_following_distance: Distance to follow liquid surface (mm), default: [0.0] * n
      gamma_lld_sensitivity: Gamma LLD sensitivity (1-4), default: [0] * n
      dp_lld_sensitivity: DP LLD sensitivity (1-4), default: [0] * n
      settling_time: Settling time (s), default: [1.0] * n
      transport_air_volume: Transport air volume (uL), default: [5.0] * n
      pre_wetting_volume: Pre-wetting volume (uL), default: [0.0] * n
      swap_speed: Swap speed on leaving liquid (uL/s), default: [20.0] * n
      mix_position_from_liquid_surface: Mix position from liquid surface (mm), default: [0.0] * n
      limit_curve_index: Limit curve index, default: [0] * n
      tadm_enabled: TADM enabled flag, default: False

    Raises:
      RuntimeError: If pipette address or deck is not set
    """
    if self._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")

    # Validate we have a NimbusDeck for coordinate conversion
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    n = len(ops)

    # Build tip pattern array (1 for active channels, 0 for inactive)
    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      if channel_idx >= self.num_channels:
        raise ValueError(f"Channel index {channel_idx} exceeds num_channels {self.num_channels}")
      channels_involved[channel_idx] = 1

    # Call ADC command (EnableADC or DisableADC)
    if adc_enabled:
      await self.send_command(EnableADC(self._pipette_address, channels_involved))
      logger.info("Enabled ADC before aspirate")
    else:
      await self.send_command(DisableADC(self._pipette_address, channels_involved))
      logger.info("Disabled ADC before aspirate")

    # Call GetChannelConfiguration for each active channel (index 2 = "Aspirate monitoring with cLLD")
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1  # Convert to 1-based
      try:
        config = await self.send_command(
          GetChannelConfiguration(
            self._pipette_address,
            channel=channel_num,
            indexes=[2],  # Index 2 = "Aspirate monitoring with cLLD"
          )
        )
        assert config is not None, "GetChannelConfiguration returned None"
        enabled = config["enabled"][0] if config["enabled"] else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
        logger.debug(f"Channel {channel_num} configuration (index 2): enabled={enabled}")
      except Exception as e:
        logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

    # ========================================================================
    # MINIMAL SET: Calculate from resources (NOT kwargs)
    # ========================================================================

    # Extract coordinates and convert to Hamilton coordinates
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    # Traverse height: use provided value or default
    if minimum_traverse_height_at_beginning_of_a_command is None:
      minimum_traverse_height_at_beginning_of_a_command = self._channel_traversal_height
    minimum_traverse_height_at_beginning_of_a_command_units = round(
      minimum_traverse_height_at_beginning_of_a_command * 100
    )

    # Calculate well_bottoms: resource Z + offset Z + material_z_thickness in Hamilton coords
    well_bottoms = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck) + op.offset
      if isinstance(op.resource, Container):
        abs_location.z += op.resource.material_z_thickness
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)
      well_bottoms.append(hamilton_coord.z)

    # Calculate liquid_height: well_bottom + (op.liquid_height or 0)
    # This is the fixed Z-height when LLD is OFF
    liquid_heights_mm = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]

    # Calculate lld_search_height if not provided as kwarg
    #
    # IMPORTANT: lld_search_height is a RELATIVE OFFSET (in mm), not an absolute coordinate.
    # It represents the height offset from the well bottom where the LLD (Liquid Level Detection)
    # search should start. The Hamilton instrument will add this offset to minimum_height
    # (well bottom) to determine the absolute Z position where the search begins.
    #
    # Default behavior: Use the well's size_z (depth) as the offset, which means
    # "start the LLD search at the top of the well" (well_bottom + well_size).
    # This is a reasonable default since we want to search from the top downward.
    #
    # When provided as a kwarg, it should be a list of relative offsets in mm.
    # The instrument will internally add these to minimum_height to get absolute coordinates.
    if lld_search_height is None:
      lld_search_height = [op.resource.get_absolute_size_z() for op in ops]

    # Calculate minimum_height: default to well_bottom
    minimum_heights_mm = well_bottoms.copy()

    # Extract volumes and speeds from operations
    volumes = [op.volume for op in ops]  # in uL
    flow_rates: List[float] = [
      op.flow_rate if op.flow_rate is not None else _get_default_flow_rate(op.tip, is_aspirate=True)
      for op in ops
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops
    ]  # in uL, default 40

    # Extract mix parameters from op.mix if available. Otherwise use None.
    mix_volume: List[float] = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles: List[int] = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    # Default mix_speed to aspirate speed (flow_rates) when no mix operation
    # This matches the working version behavior
    mix_speed: List[float] = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else _get_default_flow_rate(op.tip, is_aspirate=True)
      )
      for op in ops
    ]

    # ========================================================================
    # ADVANCED PARAMETERS: Fill in defaults using fill_in_defaults()
    # ========================================================================

    lld_mode = fill_in_defaults(lld_mode, [0] * n)
    immersion_depth = fill_in_defaults(immersion_depth, [0.0] * n)
    surface_following_distance = fill_in_defaults(surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = fill_in_defaults(gamma_lld_sensitivity, [0] * n)
    dp_lld_sensitivity = fill_in_defaults(dp_lld_sensitivity, [0] * n)
    settling_time = fill_in_defaults(settling_time, [1.0] * n)
    transport_air_volume = fill_in_defaults(transport_air_volume, [5.0] * n)
    pre_wetting_volume = fill_in_defaults(pre_wetting_volume, [0.0] * n)
    swap_speed = fill_in_defaults(swap_speed, [20.0] * n)
    mix_position_from_liquid_surface = fill_in_defaults(mix_position_from_liquid_surface, [0.0] * n)
    limit_curve_index = fill_in_defaults(limit_curve_index, [0] * n)

    # ========================================================================
    # CONVERT UNITS AND BUILD FULL ARRAYS
    # Hamilton uses units of 0.1uL and 0.1mm and 0.1s etc. for most parameters
    # Some are in 0.01.
    # PLR units are uL, mm, s etc.
    # ========================================================================

    aspirate_volumes = [round(vol * 10) for vol in volumes]
    blow_out_air_volumes_units = [round(vol * 10) for vol in blow_out_air_volumes]
    aspiration_speeds = [round(fr * 10) for fr in flow_rates]
    lld_search_height_units = [round(h * 100) for h in lld_search_height]
    liquid_height_units = [round(h * 100) for h in liquid_heights_mm]
    immersion_depth_units = [round(d * 100) for d in immersion_depth]
    surface_following_distance_units = [round(d * 100) for d in surface_following_distance]
    minimum_height_units = [round(z * 100) for z in minimum_heights_mm]
    settling_time_units = [round(t * 10) for t in settling_time]
    transport_air_volume_units = [round(v * 10) for v in transport_air_volume]
    pre_wetting_volume_units = [round(v * 10) for v in pre_wetting_volume]
    swap_speed_units = [round(s * 10) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]

    # Build arrays for all channels (pad with 0s for inactive channels)
    aspirate_volumes_full = self._fill_by_channels(aspirate_volumes, use_channels, default=0)
    blow_out_air_volumes_full = self._fill_by_channels(
      blow_out_air_volumes_units, use_channels, default=0
    )
    aspiration_speeds_full = self._fill_by_channels(aspiration_speeds, use_channels, default=0)
    lld_search_height_full = self._fill_by_channels(
      lld_search_height_units, use_channels, default=0
    )
    liquid_height_full = self._fill_by_channels(liquid_height_units, use_channels, default=0)
    immersion_depth_full = self._fill_by_channels(immersion_depth_units, use_channels, default=0)
    surface_following_distance_full = self._fill_by_channels(
      surface_following_distance_units, use_channels, default=0
    )
    minimum_height_full = self._fill_by_channels(minimum_height_units, use_channels, default=0)
    settling_time_full = self._fill_by_channels(settling_time_units, use_channels, default=0)
    transport_air_volume_full = self._fill_by_channels(
      transport_air_volume_units, use_channels, default=0
    )
    pre_wetting_volume_full = self._fill_by_channels(
      pre_wetting_volume_units, use_channels, default=0
    )
    swap_speed_full = self._fill_by_channels(swap_speed_units, use_channels, default=0)
    mix_volume_full = self._fill_by_channels(mix_volume_units, use_channels, default=0)
    mix_cycles_full = self._fill_by_channels(mix_cycles, use_channels, default=0)
    mix_speed_full = self._fill_by_channels(mix_speed_units, use_channels, default=0)
    mix_position_from_liquid_surface_full = self._fill_by_channels(
      mix_position_from_liquid_surface_units, use_channels, default=0
    )
    gamma_lld_sensitivity_full = self._fill_by_channels(
      gamma_lld_sensitivity, use_channels, default=0
    )
    dp_lld_sensitivity_full = self._fill_by_channels(dp_lld_sensitivity, use_channels, default=0)
    limit_curve_index_full = self._fill_by_channels(limit_curve_index, use_channels, default=0)
    lld_mode_full = self._fill_by_channels(lld_mode, use_channels, default=0)

    # Default values for remaining parameters
    aspirate_type = [0] * self.num_channels
    clot_detection_height = [0] * self.num_channels
    min_z_endpos = minimum_traverse_height_at_beginning_of_a_command_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    lld_height_difference = [0] * self.num_channels
    recording_mode = 0

    # Create and send Aspirate command
    command = Aspirate(
      dest=self._pipette_address,
      aspirate_type=aspirate_type,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command_units,
      lld_search_height=lld_search_height_full,
      liquid_height=liquid_height_full,
      immersion_depth=immersion_depth_full,
      surface_following_distance=surface_following_distance_full,
      minimum_height=minimum_height_full,
      clot_detection_height=clot_detection_height,
      min_z_endpos=min_z_endpos,
      swap_speed=swap_speed_full,
      blow_out_air_volume=blow_out_air_volumes_full,
      pre_wetting_volume=pre_wetting_volume_full,
      aspirate_volume=aspirate_volumes_full,
      transport_air_volume=transport_air_volume_full,
      aspiration_speed=aspiration_speeds_full,
      settling_time=settling_time_full,
      mix_volume=mix_volume_full,
      mix_cycles=mix_cycles_full,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface_full,
      mix_surface_following_distance=mix_surface_following_distance,
      mix_speed=mix_speed_full,
      tube_section_height=tube_section_height,
      tube_section_ratio=tube_section_ratio,
      lld_mode=lld_mode_full,
      gamma_lld_sensitivity=gamma_lld_sensitivity_full,
      dp_lld_sensitivity=dp_lld_sensitivity_full,
      lld_height_difference=lld_height_difference,
      tadm_enabled=tadm_enabled,
      limit_curve_index=limit_curve_index_full,
      recording_mode=recording_mode,
    )

    try:
      await self.send_command(command)
      logger.info(f"Aspirated on channels {use_channels}")
    except Exception as e:
      logger.error(f"Failed to aspirate: {e}")
      raise

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    adc_enabled: bool = False,
    # Advanced kwargs (Optional, default to zeros/nulls)
    lld_mode: Optional[List[int]] = None,
    lld_search_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    surface_following_distance: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    settling_time: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    tadm_enabled: bool = False,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    side_touch_off_distance: float = 0.0,
    dispense_offset: Optional[List[float]] = None,
  ):
    """Dispense liquid from the specified resource using pip.

    Args:
      ops: List of SingleChannelDispense operations, one per channel
      use_channels: List of channel indices to use
      minimum_traverse_height_at_beginning_of_a_command: Traverse height in mm (optional, defaults to self._channel_traversal_height)
      adc_enabled: If True, enable ADC (Automatic Drip Control), else disable (default: False)
      lld_mode: LLD mode (0=OFF, 1=cLLD, 2=pLLD, 3=DUAL), default: [0] * n
      lld_search_height: Override calculated LLD search height (mm). If None, calculated from well_bottom + resource size
      immersion_depth: Depth to submerge into liquid (mm), default: [0.0] * n
      surface_following_distance: Distance to follow liquid surface (mm), default: [0.0] * n
      gamma_lld_sensitivity: Gamma LLD sensitivity (1-4), default: [0] * n
      settling_time: Settling time (s), default: [1.0] * n
      transport_air_volume: Transport air volume (uL), default: [5.0] * n
      swap_speed: Swap speed on leaving liquid (uL/s), default: [20.0] * n
      mix_position_from_liquid_surface: Mix position from liquid surface (mm), default: [0.0] * n
      limit_curve_index: Limit curve index, default: [0] * n
      tadm_enabled: TADM enabled flag, default: False
      cut_off_speed: Cut off speed (uL/s), default: [25.0] * n
      stop_back_volume: Stop back volume (uL), default: [0.0] * n
      side_touch_off_distance: Side touch off distance (mm), default: 0.0
      dispense_offset: Dispense offset (mm), default: [0.0] * n

    Raises:
      RuntimeError: If pipette address or deck is not set
    """
    if self._pipette_address is None:
      raise RuntimeError("Pipette address not discovered. Call setup() first.")

    # Validate we have a NimbusDeck for coordinate conversion
    if not isinstance(self.deck, NimbusDeck):
      raise RuntimeError("Deck must be a NimbusDeck for coordinate conversion")

    n = len(ops)

    # Build tip pattern array (1 for active channels, 0 for inactive)
    channels_involved = [0] * self.num_channels
    for channel_idx in use_channels:
      if channel_idx >= self.num_channels:
        raise ValueError(f"Channel index {channel_idx} exceeds num_channels {self.num_channels}")
      channels_involved[channel_idx] = 1

    # Call ADC command (EnableADC or DisableADC)
    if adc_enabled:
      await self.send_command(EnableADC(self._pipette_address, channels_involved))
      logger.info("Enabled ADC before dispense")
    else:
      await self.send_command(DisableADC(self._pipette_address, channels_involved))
      logger.info("Disabled ADC before dispense")

    # Call GetChannelConfiguration for each active channel (index 2 = "Aspirate monitoring with cLLD")
    if self._channel_configurations is None:
      self._channel_configurations = {}
    for channel_idx in use_channels:
      channel_num = channel_idx + 1  # Convert to 1-based
      try:
        config = await self.send_command(
          GetChannelConfiguration(
            self._pipette_address,
            channel=channel_num,
            indexes=[2],  # Index 2 = "Aspirate monitoring with cLLD"
          )
        )
        assert config is not None, "GetChannelConfiguration returned None"
        enabled = config["enabled"][0] if config["enabled"] else False
        if channel_num not in self._channel_configurations:
          self._channel_configurations[channel_num] = {}
        self._channel_configurations[channel_num][2] = enabled
        logger.debug(f"Channel {channel_num} configuration (index 2): enabled={enabled}")
      except Exception as e:
        logger.warning(f"Failed to get channel configuration for channel {channel_num}: {e}")

    # ========================================================================
    # MINIMAL SET: Calculate from resources (NOT kwargs)
    # ========================================================================

    # Extract coordinates and convert to Hamilton coordinates
    x_positions_full, y_positions_full = self._compute_ops_xy_locations(ops, use_channels)

    # Traverse height: use provided value or default
    if minimum_traverse_height_at_beginning_of_a_command is None:
      minimum_traverse_height_at_beginning_of_a_command = self._channel_traversal_height
    minimum_traverse_height_at_beginning_of_a_command_units = round(
      minimum_traverse_height_at_beginning_of_a_command * 100
    )

    # Calculate well_bottoms: resource Z + offset Z + material_z_thickness in Hamilton coords
    well_bottoms = []
    for op in ops:
      abs_location = op.resource.get_location_wrt(self.deck) + op.offset
      if isinstance(op.resource, Container):
        abs_location.z += op.resource.material_z_thickness
      hamilton_coord = self.deck.to_hamilton_coordinate(abs_location)
      well_bottoms.append(hamilton_coord.z)

    # Calculate liquid_height: well_bottom + (op.liquid_height or 0)
    # This is the fixed Z-height when LLD is OFF
    liquid_heights_mm = [wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)]

    # Calculate lld_search_height if not provided as kwarg
    #
    # IMPORTANT: lld_search_height is a RELATIVE OFFSET (in mm), not an absolute coordinate.
    # It represents the height offset from the well bottom where the LLD (Liquid Level Detection)
    # search should start. The Hamilton instrument will add this offset to minimum_height
    # (well bottom) to determine the absolute Z position where the search begins.
    #
    # Default behavior: Use the well's size_z (depth) as the offset, which means
    # "start the LLD search at the top of the well" (well_bottom + well_size).
    # This is a reasonable default since we want to search from the top downward.
    #
    # When provided as a kwarg, it should be a list of relative offsets in mm.
    # The instrument will internally add these to minimum_height to get absolute coordinates.
    if lld_search_height is None:
      lld_search_height = [op.resource.get_absolute_size_z() for op in ops]

    # Calculate minimum_height: default to well_bottom
    minimum_heights_mm = well_bottoms.copy()

    # Extract volumes and speeds from operations
    volumes = [op.volume for op in ops]  # in uL
    flow_rates: List[float] = [
      op.flow_rate
      if op.flow_rate is not None
      else _get_default_flow_rate(op.tip, is_aspirate=False)
      for op in ops
    ]
    blow_out_air_volumes = [
      op.blow_out_air_volume if op.blow_out_air_volume is not None else 40.0 for op in ops
    ]  # in uL, default 40

    # Extract mix parameters from op.mix if available
    mix_volume: List[float] = [op.mix.volume if op.mix is not None else 0.0 for op in ops]
    mix_cycles: List[int] = [op.mix.repetitions if op.mix is not None else 0 for op in ops]
    # Default mix_speed to dispense speed (flow_rates) when no mix operation
    # This matches the working version behavior
    mix_speed: List[float] = [
      op.mix.flow_rate
      if op.mix is not None
      else (
        op.flow_rate
        if op.flow_rate is not None
        else _get_default_flow_rate(op.tip, is_aspirate=False)
      )
      for op in ops
    ]

    # ========================================================================
    # ADVANCED PARAMETERS: Fill in defaults using fill_in_defaults()
    # ========================================================================

    lld_mode = fill_in_defaults(lld_mode, [0] * n)
    immersion_depth = fill_in_defaults(immersion_depth, [0.0] * n)
    surface_following_distance = fill_in_defaults(surface_following_distance, [0.0] * n)
    gamma_lld_sensitivity = fill_in_defaults(gamma_lld_sensitivity, [0] * n)
    settling_time = fill_in_defaults(settling_time, [1.0] * n)
    transport_air_volume = fill_in_defaults(transport_air_volume, [5.0] * n)
    swap_speed = fill_in_defaults(swap_speed, [20.0] * n)
    mix_position_from_liquid_surface = fill_in_defaults(mix_position_from_liquid_surface, [0.0] * n)
    limit_curve_index = fill_in_defaults(limit_curve_index, [0] * n)
    cut_off_speed = fill_in_defaults(cut_off_speed, [25.0] * n)
    stop_back_volume = fill_in_defaults(stop_back_volume, [0.0] * n)
    dispense_offset = fill_in_defaults(dispense_offset, [0.0] * n)

    # ========================================================================
    # CONVERT UNITS AND BUILD FULL ARRAYS
    # Hamilton uses units of 0.1uL and 0.1mm and 0.1s etc. for most parameters
    # Some are in 0.01.
    # PLR units are uL, mm, s etc.
    # ========================================================================

    dispense_volumes = [round(vol * 10) for vol in volumes]
    blow_out_air_volumes_units = [round(vol * 10) for vol in blow_out_air_volumes]
    dispense_speeds = [round(fr * 10) for fr in flow_rates]
    lld_search_height_units = [round(h * 100) for h in lld_search_height]
    liquid_height_units = [round(h * 100) for h in liquid_heights_mm]
    immersion_depth_units = [round(d * 100) for d in immersion_depth]
    surface_following_distance_units = [round(d * 100) for d in surface_following_distance]
    minimum_height_units = [round(z * 100) for z in minimum_heights_mm]
    settling_time_units = [round(t * 10) for t in settling_time]
    transport_air_volume_units = [round(v * 10) for v in transport_air_volume]
    swap_speed_units = [round(s * 10) for s in swap_speed]
    mix_volume_units = [round(v * 10) for v in mix_volume]
    mix_speed_units = [round(s * 10) for s in mix_speed]
    mix_position_from_liquid_surface_units = [
      round(p * 100) for p in mix_position_from_liquid_surface
    ]
    cut_off_speed_units = [round(s * 10) for s in cut_off_speed]
    stop_back_volume_units = [round(v * 10) for v in stop_back_volume]
    dispense_offset_units = [round(o * 100) for o in dispense_offset]
    side_touch_off_distance_units = round(side_touch_off_distance * 100)

    # Build arrays for all channels (pad with 0s for inactive channels)
    dispense_volumes_full = self._fill_by_channels(dispense_volumes, use_channels, default=0)
    blow_out_air_volumes_full = self._fill_by_channels(
      blow_out_air_volumes_units, use_channels, default=0
    )
    dispense_speeds_full = self._fill_by_channels(dispense_speeds, use_channels, default=0)
    lld_search_height_full = self._fill_by_channels(
      lld_search_height_units, use_channels, default=0
    )
    liquid_height_full = self._fill_by_channels(liquid_height_units, use_channels, default=0)
    immersion_depth_full = self._fill_by_channels(immersion_depth_units, use_channels, default=0)
    surface_following_distance_full = self._fill_by_channels(
      surface_following_distance_units, use_channels, default=0
    )
    minimum_height_full = self._fill_by_channels(minimum_height_units, use_channels, default=0)
    settling_time_full = self._fill_by_channels(settling_time_units, use_channels, default=0)
    transport_air_volume_full = self._fill_by_channels(
      transport_air_volume_units, use_channels, default=0
    )
    swap_speed_full = self._fill_by_channels(swap_speed_units, use_channels, default=0)
    mix_volume_full = self._fill_by_channels(mix_volume_units, use_channels, default=0)
    mix_cycles_full = self._fill_by_channels(mix_cycles, use_channels, default=0)
    mix_speed_full = self._fill_by_channels(mix_speed_units, use_channels, default=0)
    mix_position_from_liquid_surface_full = self._fill_by_channels(
      mix_position_from_liquid_surface_units, use_channels, default=0
    )
    gamma_lld_sensitivity_full = self._fill_by_channels(
      gamma_lld_sensitivity, use_channels, default=0
    )
    limit_curve_index_full = self._fill_by_channels(limit_curve_index, use_channels, default=0)
    lld_mode_full = self._fill_by_channels(lld_mode, use_channels, default=0)
    cut_off_speed_full = self._fill_by_channels(cut_off_speed_units, use_channels, default=0)
    stop_back_volume_full = self._fill_by_channels(stop_back_volume_units, use_channels, default=0)
    dispense_offset_full = self._fill_by_channels(dispense_offset_units, use_channels, default=0)

    # Default values for remaining parameters
    dispense_type = [0] * self.num_channels
    min_z_endpos = minimum_traverse_height_at_beginning_of_a_command_units
    mix_surface_following_distance = [0] * self.num_channels
    tube_section_height = [0] * self.num_channels
    tube_section_ratio = [0] * self.num_channels
    recording_mode = 0

    # Create and send Dispense command
    command = Dispense(
      dest=self._pipette_address,
      dispense_type=dispense_type,
      channels_involved=channels_involved,
      x_positions=x_positions_full,
      y_positions=y_positions_full,
      minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command_units,
      lld_search_height=lld_search_height_full,
      liquid_height=liquid_height_full,
      immersion_depth=immersion_depth_full,
      surface_following_distance=surface_following_distance_full,
      minimum_height=minimum_height_full,
      min_z_endpos=min_z_endpos,
      swap_speed=swap_speed_full,
      transport_air_volume=transport_air_volume_full,
      dispense_volume=dispense_volumes_full,
      stop_back_volume=stop_back_volume_full,
      blow_out_air_volume=blow_out_air_volumes_full,
      dispense_speed=dispense_speeds_full,
      cut_off_speed=cut_off_speed_full,
      settling_time=settling_time_full,
      mix_volume=mix_volume_full,
      mix_cycles=mix_cycles_full,
      mix_position_from_liquid_surface=mix_position_from_liquid_surface_full,
      mix_surface_following_distance=mix_surface_following_distance,
      mix_speed=mix_speed_full,
      side_touch_off_distance=side_touch_off_distance_units,
      dispense_offset=dispense_offset_full,
      tube_section_height=tube_section_height,
      tube_section_ratio=tube_section_ratio,
      lld_mode=lld_mode_full,
      gamma_lld_sensitivity=gamma_lld_sensitivity_full,
      tadm_enabled=tadm_enabled,
      limit_curve_index=limit_curve_index_full,
      recording_mode=recording_mode,
    )

    try:
      await self.send_command(command)
      logger.info(f"Dispensed on channels {use_channels}")
    except Exception as e:
      logger.error(f"Failed to dispense: {e}")
      raise

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("pick_up_tips96 not yet implemented")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("drop_tips96 not yet implemented")

  async def aspirate96(self, aspiration: MultiHeadAspirationPlate | MultiHeadAspirationContainer):
    raise NotImplementedError("aspirate96 not yet implemented")

  async def dispense96(self, dispense: MultiHeadDispensePlate | MultiHeadDispenseContainer):
    raise NotImplementedError("dispense96 not yet implemented")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("pick_up_resource not yet implemented")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("move_picked_up_resource not yet implemented")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("drop_resource not yet implemented")

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel.

    Args:
      channel_idx: Channel index (0-based)
      tip: Tip object to check

    Returns:
      True if the tip can be picked up, False otherwise
    """
    # Only Hamilton tips are supported
    if not isinstance(tip, HamiltonTip):
      return False

    # XL tips are not supported on Nimbus
    if tip.tip_size in {TipSize.XL}:
      return False

    # Check if channel index is valid
    if self._num_channels is not None and channel_idx >= self._num_channels:
      return False

    return True
