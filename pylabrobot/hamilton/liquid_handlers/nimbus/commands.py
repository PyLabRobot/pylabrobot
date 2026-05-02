"""Hamilton Nimbus command classes and supporting types.

This module contains all Nimbus-specific Hamilton protocol commands, including
tip management, initialization, door control, ADC, aspirate, and dispense.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, List, Optional, Set

from pylabrobot.hamilton.tcp.commands import TCPCommand

from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.hamilton.tcp.wire_types import (
  I32,
  U8,
  U16,
  Bool,
  BoolArray,
  Enum,
  I16Array,
  I32Array,
  Struct,
  StructArray,
  U16Array,
  U32Array,
)
from pylabrobot.resources import Tip
from pylabrobot.resources.hamilton import HamiltonTip, TipSize

logger = logging.getLogger(__name__)

_UNRESOLVED = Address(-1, -1, -1)


@dataclass
class NimbusCommand(TCPCommand):
  """Base for all Nimbus instrument commands.

  Subclasses are dataclasses with optional ``Annotated`` payload fields.
  ``dest`` is inherited here as kw_only so concrete subclasses can freely
  declare required positional wire fields without default-ordering conflicts.
  ``build_parameters()`` is inherited from ``TCPCommand`` and serialises all
  ``Annotated`` fields via ``HoiParams.from_struct(self)`` automatically.

  Firmware target is declared via class-level ``firmware_path`` and resolved
  JIT in :meth:`NimbusDriver._send_raw` when ``dest`` is the unresolved
  sentinel. Set ``firmware_path = None`` for polymorphic commands that require
  explicit ``dest=`` at construction.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1

  firmware_path: ClassVar[Optional[str]] = None
  _ALL_PATHS: ClassVar[Set[str]] = set()

  dest: Address = field(default=_UNRESOLVED, kw_only=True)

  def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    path = cls.__dict__.get("firmware_path")
    if path is not None:
      NimbusCommand._ALL_PATHS.add(path)

  def __post_init__(self):
    super().__init__(self.dest)


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


@dataclass
class LockDoor(NimbusCommand):
  """Lock door command (DoorLock at 1:1:268, interface_id=1, command_id=1)."""

  command_id = 1
  firmware_path = "NimbusCORE.DoorLock"


@dataclass
class UnlockDoor(NimbusCommand):
  """Unlock door command (DoorLock at 1:1:268, interface_id=1, command_id=2)."""

  command_id = 2
  firmware_path = "NimbusCORE.DoorLock"


@dataclass
class IsDoorLocked(NimbusCommand):
  """Check if door is locked (DoorLock at 1:1:268, interface_id=1, command_id=3)."""

  command_id = 3
  firmware_path = "NimbusCORE.DoorLock"
  action_code = 0

  @dataclass
  class Response:
    locked: Bool


@dataclass
class PreInitializeSmart(NimbusCommand):
  """Pre-initialize smart command (Pipette at 1:1:257, interface_id=1, command_id=32)."""

  command_id = 32
  firmware_path = "NimbusCORE.Pipette"


@dataclass
class InitializeSmartRoll(NimbusCommand):
  """Initialize smart roll command (NimbusCore, cmd=29).

  Units:
    - positions/distances: 0.01 mm
  """

  command_id = 29
  firmware_path = "NimbusCORE"

  x_positions: I32Array
  y_positions: I32Array
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  roll_distances: I32Array


@dataclass
class IsInitialized(NimbusCommand):
  """Check if instrument is initialized (NimbusCore at 1:1:48896, interface_id=1, command_id=14)."""

  command_id = 14
  firmware_path = "NimbusCORE"
  action_code = 0

  @dataclass
  class Response:
    initialized: Bool


@dataclass
class IsTipPresent(NimbusCommand):
  """Check tip presence (Pipette at 1:1:257, interface_id=1, command_id=16)."""

  command_id = 16
  firmware_path = "NimbusCORE.Pipette"
  action_code = 0

  @dataclass
  class Response:
    tip_present: I16Array


@dataclass
class NimbusChannelConfigWire:
  """Wire-format struct for one entry in the ChannelConfiguration[] array (cmd 30).

  Members (in wire order, from GlobalObjects.ConvertProtocolStructToChannelConfigurationStruct):
    channel_type  — Enum 0=None 1=300uL 2=1000uL 3=5000uL
    rail          — Enum 0=Left 1=Right
    previous_neighbor_spacing — U16
    next_neighbor_spacing     — U16
    can_address               — U8
  """

  channel_type: Enum
  rail: Enum
  previous_neighbor_spacing: U16
  next_neighbor_spacing: U16
  can_address: U8


@dataclass
class ChannelConfiguration(NimbusCommand):
  """Channel configuration (NimbusCORE root, interface_id=1, command_id=30).

  Replaces the obsolete GetChannelConfiguration (cmd 15). Returns one entry
  per physical channel with type, rail, spacing, and CAN address.
  """

  command_id = 30
  firmware_path = "NimbusCORE"
  action_code = 0

  @dataclass
  class Response:
    configurations: Annotated[List[NimbusChannelConfigWire], StructArray()]


@dataclass
class SetChannelConfiguration(NimbusCommand):
  """Set channel configuration (Pipette, cmd=67).

  Field meanings:
    - `channel`: 1-based physical channel index.
    - `indexes`: firmware config slots (e.g. tip recognition / LLD monitors).
    - `enables`: booleans matching `indexes` order.
  """

  command_id = 67
  firmware_path = "NimbusCORE.Pipette"

  channel: U16
  indexes: I16Array
  enables: BoolArray


@dataclass
class GetChannelConfiguration(NimbusCommand):
  """Get channel configuration command (Pipette, cmd=66).

  Field meanings:
    - `channel`: 1-based physical channel index.
    - `indexes`: firmware config slots to query.
  """

  command_id = 66
  firmware_path = "NimbusCORE.Pipette"
  action_code = 0

  channel: U16
  indexes: I16Array

  @dataclass
  class Response:
    enabled: BoolArray


@dataclass
class PickupGripperTool(NimbusCommand):
  """Pick up CoRe gripper tool (Pipette, cmd=9).

  Units:
    - positions/heights/toolWidth: 0.01 mm
  """

  command_id = 9
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32
  y_position_1st_channel: I32
  y_position_2nd_channel: I32
  traverse_height: I32
  z_start_position: I32
  z_stop_position: I32
  tip_type: U16
  first_channel_number: U16
  second_channel_number: U16
  tool_width: I32


@dataclass
class DropGripperTool(NimbusCommand):
  """Drop CoRe gripper tool (Pipette, cmd=10).

  Units:
    - positions/heights: 0.01 mm
  """

  command_id = 10
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32
  y_position_1st_channel: I32
  y_position_2nd_channel: I32
  traverse_height: I32
  z_start_position: I32
  z_stop_position: I32
  z_final: I32
  first_channel_number: U16
  second_channel_number: U16


@dataclass
class PickupPlate(NimbusCommand):
  """Pick up plate with CoRe gripper (Pipette, cmd=11).

  Units:
    - positions/heights: 0.01 mm
    - yPlateWidth, yGripStrength: 0.01 mm (U32)
    - yGripSpeed, zSpeed: 0.01 mm/s (U32)
  """

  command_id = 11
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32
  y_plate_center_position: I32
  y_plate_width: U32
  y_open_position: I32
  y_grip_speed: U32
  y_grip_strength: U32
  traverse_height: I32
  z_grip_height: I32
  z_final: I32
  z_speed: U32


@dataclass
class DropPlate(NimbusCommand):
  """Drop plate with CoRe gripper (Pipette, cmd=12).

  Units:
    - positions/heights: 0.01 mm
    - xAcceleration: scale 1–100 (U32)
    - zSpeed: 0.01 mm/s (U32)
  """

  command_id = 12
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32
  x_acceleration: U32
  y_plate_center_position: I32
  y_open_position: I32
  traverse_height: I32
  z_drop_height: I32
  z_press_distance: I32
  z_final: I32
  z_speed: U32


@dataclass
class MovePlate(NimbusCommand):
  """Move plate with CoRe gripper (Pipette, cmd=13).

  Units:
    - positions/heights: 0.01 mm
    - xAcceleration: scale 1–100 (U32)
    - zSpeed: 0.01 mm/s (U32)
  """

  command_id = 13
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32
  x_acceleration: U32
  y_plate_center_position: I32
  traverse_height: I32
  z_final: I32
  z_speed: U32


@dataclass
class ReleasePlate(NimbusCommand):
  """Release plate (open CoRe gripper) (Pipette, cmd=14)."""

  command_id = 14
  firmware_path = "NimbusCORE.Pipette"

  first_channel_number: U16
  second_channel_number: U16


@dataclass
class IsCoreGripperToolHeld(NimbusCommand):
  """Check if CoRe gripper tool is held (Pipette, cmd=17)."""

  command_id = 17
  firmware_path = "NimbusCORE.Pipette"
  action_code = 0

  @dataclass
  class Response:
    gripped: Bool
    tip_type: U16Array


@dataclass
class IsCoreGripperPlateGripped(NimbusCommand):
  """Check if CoRe gripper plate is gripped (Pipette, cmd=18)."""

  command_id = 18
  firmware_path = "NimbusCORE.Pipette"
  action_code = 0

  @dataclass
  class Response:
    gripped: Bool


@dataclass
class GetPosition(NimbusCommand):
  """Query current pipette position (Pipette, cmd=20).

  Units:
    - x_position: 0.01 mm
    - y_position: 0.01 mm per channel
    - z_position: 0.01 mm per channel
  """

  command_id = 20
  firmware_path = "NimbusCORE.Pipette"
  action_code = 0

  @dataclass
  class Response:
    x_position: I32
    y_position: I32Array
    z_position: I32Array


@dataclass
class ParkPipette(NimbusCommand):
  """Park the pipette head (Pipette, cmd=21)."""

  command_id = 21
  firmware_path = "NimbusCORE.Pipette"


@dataclass
class MoveOver(NimbusCommand):
  """Move to position above a location, traversing at traverse_height (Pipette, cmd=22).

  Units:
    - positions/heights: 0.01 mm
  """

  command_id = 22
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  x_position: I32
  y_position: I32Array
  traverse_height: I32
  z_position: I32Array


@dataclass
class MoveToPosition(NimbusCommand):
  """Move to absolute XYZ position (Pipette, cmd=23).

  Units:
    - positions: 0.01 mm
  """

  command_id = 23
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  x_position: I32
  y_position: I32Array
  z_position: I32Array


@dataclass
class MoveToPositionViaLane(NimbusCommand):
  """Move to XY position via lane (traverse then lower) (Pipette, cmd=24).

  Units:
    - positions/heights: 0.01 mm
  """

  command_id = 24
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  x_position: I32
  y_position: I32Array
  traverse_height: I32


@dataclass
class MoveAbsoluteXY(NimbusCommand):
  """Move to absolute XY position at traverse height (Pipette, cmd=25).

  Units:
    - positions: 0.01 mm
  """

  command_id = 25
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  x_position: I32
  y_position: I32Array


@dataclass
class MoveAbsoluteX(NimbusCommand):
  """Move X axis to absolute position (Pipette, cmd=26).

  Units:
    - x_position: 0.01 mm
  """

  command_id = 26
  firmware_path = "NimbusCORE.Pipette"

  x_position: I32


@dataclass
class MoveRelativeX(NimbusCommand):
  """Move X axis by relative distance (Pipette, cmd=27).

  Units:
    - x_distance: 0.01 mm
  """

  command_id = 27
  firmware_path = "NimbusCORE.Pipette"

  x_distance: I32


@dataclass
class MoveAbsoluteY(NimbusCommand):
  """Move channels to absolute Y positions — the channel spread mechanism (Pipette, cmd=28).

  Units:
    - y_position: 0.01 mm per channel
  """

  command_id = 28
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  y_position: I32Array


@dataclass
class MoveRelativeY(NimbusCommand):
  """Move channels by relative Y distances (Pipette, cmd=29).

  Units:
    - y_distance: 0.01 mm per channel
  """

  command_id = 29
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  y_distance: I32Array


@dataclass
class MoveAbsoluteZ(NimbusCommand):
  """Move channels to absolute Z positions (Pipette, cmd=30).

  Units:
    - z_position: 0.01 mm per channel
  """

  command_id = 30
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  z_position: I32Array


@dataclass
class MoveRelativeZ(NimbusCommand):
  """Move channels by relative Z distances (Pipette, cmd=31).

  Units:
    - z_distance: 0.01 mm per channel
  """

  command_id = 31
  firmware_path = "NimbusCORE.Pipette"

  tips_used: U16Array
  z_distance: I32Array


@dataclass
class Park(NimbusCommand):
  """Park command (NimbusCore at 1:1:48896, interface_id=1, command_id=3)."""

  command_id = 3
  firmware_path = "NimbusCORE"


@dataclass
class PickupTips(NimbusCommand):
  """Pick up tips command (Pipette, cmd=4).

  Units:
    - positions/heights: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `tip_types`: per-channel Nimbus tip type IDs.
  """

  command_id = 4
  firmware_path = "NimbusCORE.Pipette"

  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_pick_up_process: I32Array
  end_tip_pick_up_process: I32Array
  tip_types: U16Array


@dataclass
class DropTips(NimbusCommand):
  """Drop tips command (Pipette, cmd=5).

  Units:
    - positions/heights: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `default_waste`: when true, firmware default waste position is used.
  """

  command_id = 5
  firmware_path = "NimbusCORE.Pipette"

  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  default_waste: Bool


@dataclass
class DropTipsRoll(NimbusCommand):
  """Drop tips with roll command (Pipette, cmd=82).

  Units:
    - positions/heights/distances: 0.01 mm

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  command_id = 82
  firmware_path = "NimbusCORE.Pipette"

  channels_involved: U16Array
  x_positions: I32Array
  y_positions: I32Array
  minimum_traverse_height_at_beginning_of_a_command: I32
  begin_tip_deposit_process: I32Array
  end_tip_deposit_process: I32Array
  z_position_at_end_of_a_command: I32Array
  roll_distances: I32Array


@dataclass
class EnableADC(NimbusCommand):
  """Enable ADC command (Pipette, cmd=43).

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  command_id = 43
  firmware_path = "NimbusCORE.Pipette"

  channels_involved: U16Array


@dataclass
class DisableADC(NimbusCommand):
  """Disable ADC command (Pipette, cmd=44).

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
  """

  command_id = 44
  firmware_path = "NimbusCORE.Pipette"

  channels_involved: U16Array


@dataclass
class Aspirate(NimbusCommand):
  """Aspirate command (Pipette, cmd=6).

  Units:
    - linear positions/heights: 0.01 mm
    - volumes: 0.1 uL
    - aspiration/dispense/mix flow parameters: 0.1 uL/s (piston motion)
    - swap_speed: 0.01 mm/s per wire unit (leave-liquid Z speed — not uL/s)
    - settling time: 0.1 s

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `aspirate_type`: firmware aspirate mode per channel.
    - `lld_mode`: 0=off, 1=cLLD, 2=pLLD, 3=dual.
    - `tadm_enabled`: enable Total Aspiration/Dispense Monitoring.
  """

  command_id = 6
  firmware_path = "NimbusCORE.Pipette"

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


@dataclass
class Dispense(NimbusCommand):
  """Dispense command (Pipette, cmd=7).

  Units:
    - linear positions/heights: 0.01 mm
    - volumes: 0.1 uL
    - dispense/mix/cut-off flow parameters: 0.1 uL/s where applicable
    - swap_speed: 0.01 mm/s per wire unit (leave-liquid Z speed — not uL/s)
    - settling time: 0.1 s

  Field meanings:
    - `channels_involved`: 1=active, 0=inactive.
    - `dispense_type`: firmware dispense mode per channel.
    - `lld_mode`: 0=off, 1=cLLD, 2=pLLD, 3=dual.
    - `tadm_enabled`: enable Total Aspiration/Dispense Monitoring.
  """

  command_id = 7
  firmware_path = "NimbusCORE.Pipette"

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
