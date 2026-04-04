"""Hamilton Nimbus protocol command definitions.

Contains all HamiltonCommand subclasses, tip type enum, and helpers
for the Nimbus liquid handler protocol.
"""

from __future__ import annotations

import enum
from typing import List

from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.messages import (
  HoiParams,
  HoiParamsParser,
)
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.legacy.liquid_handling.backends.hamilton.tcp.protocol import (
  HamiltonProtocol,
)
from pylabrobot.resources import Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize


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
      mix_position_from_liquid_surface: Mix position from liquid surface in 0.01mm units
      mix_surface_following_distance: Mix follow distance in 0.01mm units
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
      surface_following_distance: Surface following distance in 0.01mm units
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
      mix_position_from_liquid_surface: Mix position from liquid surface in 0.01mm units
      mix_surface_following_distance: Mix follow distance in 0.01mm units
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
