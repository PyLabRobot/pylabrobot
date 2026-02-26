"""Hamilton Prep backend implementation.

Uses HamiltonTCPBackend (Protocol 7/3, introspection) and shares the same
TCP codec as NimbusBackend. Discovers MLPrep, Pipettor, and ChannelCoordinator
via introspection and sends commands as HamiltonCommand subclasses.

Three-layer design:

- **Inner structs** (e.g. ``CommonParameters``, ``NoLldParameters``): reusable
  wire protocol building blocks, no defaults.  ``.default()`` only where it
  means "tell firmware to use its own defaults".
- **Command classes** (e.g. ``PrepDropTips``): pure wire shape + identity.
  ``@dataclass`` with ``dest: Address`` + ``Annotated`` payload fields, no
  defaults.  ``build_parameters()`` uses ``HoiParams.from_struct(self)``.
- **PrepBackend methods**: single source of truth for all Prep-specific
  defaults.  Flat, named, typed kwargs with defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Annotated, List, Optional, Tuple, Union

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  EnumArray,
  F32,
  I8,
  I16,
  I16Array,
  I64,
  PaddedBool,
  PaddedU8,
  Str,
  Struct,
  StructArray,
  U16,
  U32,
  U8Array,
  Enum as WEnum,
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
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Tip

if TYPE_CHECKING:
  pass

logger = logging.getLogger(__name__)


# =============================================================================
# Enums (mirrored from Prep protocol spec, independent of prep.py)
# =============================================================================


class ChannelIndex(IntEnum):
  InvalidIndex = 0
  FrontChannel = 1
  RearChannel = 2
  MPHChannel = 3


class TipDropType(IntEnum):
  FixedHeight = 0
  Stall = 1
  CLLDSeek = 2


class TipTypes(IntEnum):
  None_ = 0
  LowVolume = 1
  StandardVolume = 2
  HighVolume = 3


class TadmRecordingModes(IntEnum):
  NoRecording = 0
  Errors = 1
  All = 2


# =============================================================================
# Hardware config (probed from instrument, immutable)
# =============================================================================


@dataclass(frozen=True)
class DeckBounds:
  """Deck axis bounds in mm (from GetDeckBounds / DeckConfiguration)."""

  min_x: float
  max_x: float
  min_y: float
  max_y: float
  min_z: float
  max_z: float


@dataclass(frozen=True)
class DeckSiteInfo:
  """A deck slot read from DeckConfiguration.GetDeckSiteDefinitions."""

  id: int
  left_bottom_front_x: float
  left_bottom_front_y: float
  left_bottom_front_z: float
  length: float
  width: float
  height: float


@dataclass(frozen=True)
class WasteSiteInfo:
  """A waste position read from DeckConfiguration.GetWasteSiteDefinitions."""

  index: int
  x_position: float
  y_position: float
  z_position: float
  z_seek: float


@dataclass(frozen=True)
class InstrumentConfig:
  """Instrument hardware configuration probed at setup."""

  deck_bounds: Optional[DeckBounds]
  has_enclosure: bool
  safe_speeds_enabled: bool
  default_traverse_height: float
  deck_sites: Tuple[DeckSiteInfo, ...]
  waste_sites: Tuple[WasteSiteInfo, ...]


# =============================================================================
# Inner parameter dataclasses (wire-type annotated, serialized via from_struct)
# =============================================================================


@dataclass
class SeekParameters:
  x_start: F32
  y_start: F32
  z_start: F32
  distance: F32
  expected_position: F32


@dataclass
class XYZCoord:
  default_values: PaddedBool
  x_position: F32
  y_position: F32
  z_position: F32


@dataclass
class XYCoord:
  default_values: PaddedBool
  x_position: F32
  y_position: F32


@dataclass
class ChannelYZMoveParameters:
  default_values: PaddedBool
  channel: WEnum
  y_position: F32
  z_position: F32


@dataclass
class GantryMoveXYZParameters:
  default_values: PaddedBool
  gantry_x_position: F32
  axis_parameters: Annotated[list[ChannelYZMoveParameters], StructArray()]


@dataclass
class PlateDimensions:
  default_values: PaddedBool
  length: F32
  width: F32
  height: F32


@dataclass
class TipDefinition:
  default_values: PaddedBool
  id: PaddedU8
  volume: F32
  length: F32
  tip_type: WEnum
  has_filter: PaddedBool
  is_needle: PaddedBool
  is_tool: PaddedBool
  label: Str


@dataclass
class TipPickupParameters:
  default_values: PaddedBool
  volume: F32
  length: F32
  tip_type: WEnum
  has_filter: PaddedBool
  is_needle: PaddedBool
  is_tool: PaddedBool


@dataclass
class AspirateParameters:
  default_values: PaddedBool
  x_position: F32
  y_position: F32
  prewet_volume: F32
  blowout_volume: F32

  @classmethod
  def for_op(
    cls,
    loc,
    op: "SingleChannelAspiration",
    prewet_volume: float = 0.0,
  ) -> "AspirateParameters":
    return cls(
      default_values=False,
      x_position=loc.x,
      y_position=loc.y,
      prewet_volume=prewet_volume,
      blowout_volume=op.blow_out_air_volume or 0.0,
    )


@dataclass
class DispenseParameters:
  default_values: PaddedBool
  x_position: F32
  y_position: F32
  stop_back_volume: F32
  cutoff_speed: F32

  @classmethod
  def for_op(
    cls,
    loc,
    stop_back_volume: float = 0.0,
    cutoff_speed: float = 100.0,
  ) -> "DispenseParameters":
    return cls(
      default_values=False,
      x_position=loc.x,
      y_position=loc.y,
      stop_back_volume=stop_back_volume,
      cutoff_speed=cutoff_speed,
    )


@dataclass
class CommonParameters:
  default_values: PaddedBool
  empty: PaddedBool
  z_minimum: F32
  z_final: F32
  z_liquid_exit_speed: F32
  liquid_volume: F32
  liquid_speed: F32
  transport_air_volume: F32
  tube_radius: F32
  cone_height: F32
  cone_bottom_radius: F32
  settling_time: F32
  additional_probes: U32

  @classmethod
  def for_op(
    cls,
    volume: float,
    radius: float,
    *,
    flow_rate: Optional[float] = None,
    empty: bool = True,
    z_minimum: float = -5.03,
    z_final: float = 96.97,
    z_liquid_exit_speed: float = 2.0,
    transport_air_volume: float = 0.0,
    cone_height: float = 0.0,
    cone_bottom_radius: float = 0.0,
    settling_time: float = 1.0,
    additional_probes: int = 0,
  ) -> "CommonParameters":
    return cls(
      default_values=False,
      empty=empty,
      z_minimum=z_minimum,
      z_final=z_final,
      z_liquid_exit_speed=z_liquid_exit_speed,
      liquid_volume=volume,
      liquid_speed=flow_rate or 100.0,
      transport_air_volume=transport_air_volume,
      tube_radius=radius,
      cone_height=cone_height,
      cone_bottom_radius=cone_bottom_radius,
      settling_time=settling_time,
      additional_probes=additional_probes,
    )


@dataclass
class NoLldParameters:
  default_values: PaddedBool
  z_fluid: F32
  z_air: F32
  bottom_search: PaddedBool
  z_bottom_search_offset: F32
  z_bottom_offset: F32

  @classmethod
  def for_fixed_z(
    cls,
    z_fluid: float = 94.97,
    z_air: float = 96.97,
    *,
    z_bottom_search_offset: float = 2.0,
    z_bottom_offset: float = 0.0,
  ) -> "NoLldParameters":
    return cls(
      default_values=False,
      z_fluid=z_fluid,
      z_air=z_air,
      bottom_search=False,
      z_bottom_search_offset=z_bottom_search_offset,
      z_bottom_offset=z_bottom_offset,
    )


@dataclass
class LldParameters:
  default_values: PaddedBool
  z_seek: F32
  z_seek_speed: F32
  z_submerge: F32
  z_out_of_liquid: F32


@dataclass
class CLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  clot_check_enable: PaddedBool
  z_clot_check: F32
  detect_mode: WEnum


@dataclass
class PLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  dispenser_seek_speed: F32
  lld_height_difference: F32
  detect_mode: WEnum


@dataclass
class TadmReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  entries: U32
  error: PaddedBool
  data: I16Array


@dataclass
class TadmParameters:
  default_values: PaddedBool
  limit_curve_index: U16
  recording_mode: WEnum

  @classmethod
  def default(cls) -> TadmParameters:
    return cls(
      default_values=True,
      limit_curve_index=0,
      recording_mode=TadmRecordingModes.Errors,
    )


@dataclass
class AspirateMonitoringParameters:
  default_values: PaddedBool
  c_lld_enable: PaddedBool
  p_lld_enable: PaddedBool
  minimum_differential: U16
  maximum_differential: U16
  clot_threshold: U16

  @classmethod
  def default(cls) -> AspirateMonitoringParameters:
    return cls(
      default_values=True,
      c_lld_enable=False,
      p_lld_enable=False,
      minimum_differential=30,
      maximum_differential=30,
      clot_threshold=20,
    )


@dataclass
class MixParameters:
  default_values: PaddedBool
  z_offset: F32
  volume: F32
  cycles: PaddedU8
  speed: F32

  @classmethod
  def default(cls) -> MixParameters:
    return cls(
      default_values=True,
      z_offset=0.0,
      volume=0.0,
      cycles=0,
      speed=250.0,
    )


@dataclass
class AdcParameters:
  default_values: PaddedBool
  errors: PaddedBool
  maximum_volume: F32

  @classmethod
  def default(cls) -> AdcParameters:
    return cls(
      default_values=True,
      errors=True,
      maximum_volume=4.5,
    )


@dataclass
class ChannelXYZPositionParameters:
  default_values: PaddedBool
  channel: WEnum
  position_x: F32
  position_y: F32
  position_z: F32


@dataclass
class PressureReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  pressure: U16


@dataclass
class LiquidHeightReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  c_lld_detected: PaddedBool
  c_lld_liquid_height: F32
  p_lld_detected: PaddedBool
  p_lld_liquid_height: F32


@dataclass
class DispenserVolumeReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  volume: F32


@dataclass
class PotentiometerParameters:
  default_values: PaddedBool
  channel: WEnum
  gain: PaddedU8
  offset: PaddedU8


@dataclass
class YLLDSeekParameters:
  default_values: PaddedBool
  channel: WEnum
  start_position_x: F32
  start_position_y: F32
  start_position_z: F32
  seek_position_y: F32
  seek_velocity_y: F32
  lld_sensitivity: WEnum
  detect_mode: WEnum


@dataclass
class ChannelSeekParameters:
  default_values: PaddedBool
  channel: WEnum
  seek_position_x: F32
  seek_position_y: F32
  seek_height: F32
  min_seek_height: F32
  final_position_z: F32


@dataclass
class LLDChannelSeekParameters:
  default_values: PaddedBool
  channel: WEnum
  seek_position_x: F32
  seek_position_y: F32
  seek_velocity_z: F32
  seek_height: F32
  min_seek_height: F32
  final_position_z: F32
  lld_sensitivity: WEnum
  detect_mode: WEnum


@dataclass
class SeekResultParameters:
  default_values: PaddedBool
  channel: WEnum
  detected: PaddedBool
  position: F32


@dataclass
class ChannelCounterParameters:
  default_values: PaddedBool
  channel: WEnum
  tip_pickup_counter: U32
  tip_eject_counter: U32
  aspirate_counter: U32
  dispense_counter: U32


@dataclass
class ChannelCalibrationParameters:
  default_values: PaddedBool
  channel: WEnum
  dispenser_return_steps: U32
  squeeze_position: F32
  z_touchoff: F32
  z_tip_height: F32
  pressure_monitoring_shift: U32


@dataclass
class LeakCheckSimpleParameters:
  default_values: PaddedBool
  channel: WEnum
  time: F32
  high_pressure: PaddedBool


@dataclass
class LeakCheckParameters:
  default_values: PaddedBool
  channel: WEnum
  start_position_x: F32
  start_position_y: F32
  start_position_z: F32
  seek_distance_y: F32
  pre_load_distance_y: F32
  final_z: F32
  tip_definition_id: PaddedU8
  test_time: F32
  high_pressure: PaddedBool


@dataclass
class DriveStatus:
  initialized: PaddedBool
  position: F32
  encoder_position: F32
  in_home_sensor: PaddedBool


@dataclass
class ChannelDriveStatus:
  default_values: PaddedBool
  channel: WEnum
  y_axis_drive_status: Annotated[DriveStatus, Struct()]
  z_axis_drive_status: Annotated[DriveStatus, Struct()]
  dispenser_drive_status: Annotated[DriveStatus, Struct()]
  squeeze_drive_status: Annotated[DriveStatus, Struct()]


@dataclass
class AspirateParametersNoLldAndMonitoring:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  aspirate_monitoring: Annotated[AspirateMonitoringParameters, Struct()]


@dataclass
class AspirateParametersNoLldAndTadm:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


@dataclass
class AspirateParametersLldAndMonitoring:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  p_lld: Annotated[PLldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  aspirate_monitoring: Annotated[AspirateMonitoringParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]


@dataclass
class AspirateParametersLldAndTadm:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  p_lld: Annotated[PLldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]


@dataclass
class DispenseParametersNoLld:
  default_values: PaddedBool
  channel: WEnum
  dispense: Annotated[DispenseParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


@dataclass
class DispenseParametersLld:
  default_values: PaddedBool
  channel: WEnum
  dispense: Annotated[DispenseParameters, Struct()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


@dataclass
class DropTipParameters:
  default_values: PaddedBool
  channel: WEnum
  y_position: F32
  z_seek: F32
  z_tip: F32
  z_final: F32
  z_seek_speed: F32
  drop_type: WEnum


@dataclass
class InitTipDropParameters:
  default_values: PaddedBool
  x_position: F32
  rolloff_distance: F32
  channel_parameters: Annotated[list[DropTipParameters], StructArray()]


@dataclass
class DispenseInitToWasteParameters:
  default_values: PaddedBool
  channel: WEnum
  x_position: F32
  y_position: F32
  z_position: F32


@dataclass
class MoveAxisAbsoluteParameters:
  default_values: PaddedBool
  channel: WEnum
  axis: WEnum
  position: F32
  delay: U32


@dataclass
class MoveAxisRelativeParameters:
  default_values: PaddedBool
  channel: WEnum
  axis: WEnum
  distance: F32
  delay: U32


@dataclass
class LimitCurveEntry:
  default_values: PaddedBool
  sample: U16
  pressure: I16


@dataclass
class TipPositionParameters:
  default_values: PaddedBool
  channel: WEnum
  x_position: F32
  y_position: F32
  z_position: F32
  z_seek: F32

  @classmethod
  def for_op(
    cls,
    channel: WEnum,
    loc,
    tip,
    *,
    z_seek_offset: Optional[float] = None,
  ) -> "TipPositionParameters":
    """Build from an op location and tip (pickup).

    z_seek default: z_position + fitting_depth + 5mm guard (tip-type-aware,
    comparable to Nimbus/Vantage). z_seek_offset: additive mm on top of
    computed default (None = 0).
    """
    z = loc.z + tip.total_tip_length
    z_seek = z + tip.fitting_depth + 5.0 + (z_seek_offset or 0.0)
    return cls(
      default_values=False,
      channel=channel,
      x_position=loc.x,
      y_position=loc.y,
      z_position=z,
      z_seek=z_seek,
    )


@dataclass
class TipDropParameters:
  default_values: PaddedBool
  channel: WEnum
  x_position: F32
  y_position: F32
  z_position: F32
  z_seek: F32
  drop_type: WEnum

  @classmethod
  def for_op(
    cls,
    channel: WEnum,
    loc,
    tip,
    *,
    z_seek_offset: Optional[float] = None,
    drop_type: Optional["TipDropType"] = None,
  ) -> "TipDropParameters":
    """Build from an op location and tip (drop).

    z_seek default: z_position + total_tip_length + 10mm so tip bottom clears
    adjacent tips during lateral approach. z_seek_offset: additive mm on top
    of computed default (None = 0).
    """
    z = loc.z + tip.total_tip_length
    z_seek = z + tip.total_tip_length + 10.0 + (z_seek_offset or 0.0)
    return cls(
      default_values=False,
      channel=channel,
      x_position=loc.x,
      y_position=loc.y,
      z_position=z,
      z_seek=z_seek,
      drop_type=drop_type if drop_type is not None else TipDropType.FixedHeight,
    )


@dataclass
class TipHeightCalibrationParameters:
  default_values: PaddedBool
  channel: WEnum
  x_position: F32
  y_position: F32
  z_start: F32
  z_stop: F32
  z_final: F32
  volume: F32
  tip_type: WEnum


@dataclass
class DispenserVolumeEntry:
  default_values: PaddedBool
  type: WEnum
  volume: F32


@dataclass
class DispenserVolumeStackReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  total_volume: F32
  volumes: Annotated[list[DispenserVolumeEntry], StructArray()]


@dataclass
class SegmentDescriptor:
  area_top: F32
  area_bottom: F32
  height: F32


@dataclass
class AspirateParametersNoLldAndMonitoring2:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  aspirate_monitoring: Annotated[AspirateMonitoringParameters, Struct()]


@dataclass
class AspirateParametersNoLldAndTadm2:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


@dataclass
class AspirateParametersLldAndMonitoring2:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  p_lld: Annotated[PLldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  aspirate_monitoring: Annotated[AspirateMonitoringParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]


@dataclass
class AspirateParametersLldAndTadm2:
  default_values: PaddedBool
  channel: WEnum
  aspirate: Annotated[AspirateParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  p_lld: Annotated[PLldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]


@dataclass
class DispenseParametersNoLld2:
  default_values: PaddedBool
  channel: WEnum
  dispense: Annotated[DispenseParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  no_lld: Annotated[NoLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


@dataclass
class DispenseParametersLld2:
  default_values: PaddedBool
  channel: WEnum
  dispense: Annotated[DispenseParameters, Struct()]
  container_description: Annotated[list[SegmentDescriptor], StructArray()]
  common: Annotated[CommonParameters, Struct()]
  lld: Annotated[LldParameters, Struct()]
  c_lld: Annotated[CLldParameters, Struct()]
  mix: Annotated[MixParameters, Struct()]
  adc: Annotated[AdcParameters, Struct()]
  tadm: Annotated[TadmParameters, Struct()]


# =============================================================================
# PrepCommand base class
# =============================================================================


@dataclass
class PrepCommand(HamiltonCommand):
  """Base for all Prep instrument commands.

  Subclasses are dataclasses with ``dest: Address`` (inherited) plus any
  ``Annotated`` payload fields.  ``build_parameters()`` calls
  ``HoiParams.from_struct(self)`` which serialises only ``Annotated`` fields,
  so ``dest`` is automatically excluded from the wire payload.
  """

  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1

  dest: Address

  def __post_init__(self):
    super().__init__(self.dest)

  def build_parameters(self) -> HoiParams:
    return HoiParams.from_struct(self)


# =============================================================================
# Pipettor / ChannelCoordinator command classes
# =============================================================================


@dataclass
class PrepAspirateNoLldMonitoring(PrepCommand):
  """Aspirate without LLD or monitoring (cmd=1, dest=Pipettor)."""

  command_id = 1
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring], StructArray()]


@dataclass
class PrepAspirateTadm(PrepCommand):
  """Aspirate with TADM, no LLD (cmd=2, dest=Pipettor)."""

  command_id = 2
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm], StructArray()]


@dataclass
class PrepAspirateWithLld(PrepCommand):
  """Aspirate with LLD and monitoring (cmd=3, dest=Pipettor)."""

  command_id = 3
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring], StructArray()]


@dataclass
class PrepAspirateWithLldTadm(PrepCommand):
  """Aspirate with LLD and TADM (cmd=4, dest=Pipettor)."""

  command_id = 4
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm], StructArray()]


@dataclass
class PrepDispenseNoLld(PrepCommand):
  """Dispense without LLD (cmd=5, dest=Pipettor)."""

  command_id = 5
  dispense_parameters: Annotated[list[DispenseParametersNoLld], StructArray()]


@dataclass
class PrepDispenseWithLld(PrepCommand):
  """Dispense with LLD (cmd=6, dest=Pipettor)."""

  command_id = 6
  dispense_parameters: Annotated[list[DispenseParametersLld], StructArray()]


@dataclass
class PrepDispenseInitToWaste(PrepCommand):
  """Dispense initialize to waste (cmd=7, dest=Pipettor)."""

  command_id = 7
  waste_parameters: Annotated[list[DispenseInitToWasteParameters], StructArray()]


@dataclass
class PrepPickUpTipsById(PrepCommand):
  """Pick up tips by tip-definition ID (cmd=8, dest=Pipettor)."""

  command_id = 8
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition_id: PaddedU8
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32


@dataclass
class PrepPickUpTips(PrepCommand):
  """Pick up tips by tip-definition struct (cmd=9, dest=Pipettor)."""

  command_id = 9
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32


@dataclass
class PrepPickUpNeedlesById(PrepCommand):
  """Pick up needles by tip-definition ID (cmd=10, dest=Pipettor)."""

  command_id = 10
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition_id: PaddedU8
  blowout_offset: F32
  blowout_speed: F32
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32


@dataclass
class PrepPickUpNeedles(PrepCommand):
  """Pick up needles by tip-definition struct (cmd=11, dest=Pipettor)."""

  command_id = 11
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  blowout_offset: F32
  blowout_speed: F32
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32


@dataclass
class PrepDropTips(PrepCommand):
  """Drop tips (cmd=12, dest=Pipettor)."""

  command_id = 12
  tip_positions: Annotated[list[TipDropParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_roll_off_distance: F32


@dataclass
class PrepPickUpToolById(PrepCommand):
  """Pick up tool by tip-definition ID (cmd=14, dest=Pipettor)."""

  command_id = 14
  tip_definition_id: PaddedU8
  tool_position_x: F32
  tool_position_z: F32
  front_channel_position_y: F32
  rear_channel_position_y: F32
  tool_seek: F32
  tool_x_radius: F32
  tool_y_radius: F32


@dataclass
class PrepPickUpTool(PrepCommand):
  """Pick up tool by tip-definition struct (cmd=15, dest=Pipettor)."""

  command_id = 15
  tip_definition: Annotated[TipPickupParameters, Struct()]
  tool_position_x: F32
  tool_position_z: F32
  front_channel_position_y: F32
  rear_channel_position_y: F32
  tool_seek: F32
  tool_x_radius: F32
  tool_y_radius: F32


@dataclass
class PrepDropTool(PrepCommand):
  """Drop tool (cmd=16, dest=Pipettor)."""

  command_id = 16


@dataclass
class PrepPickUpPlate(PrepCommand):
  """Pick up plate (cmd=17, dest=Pipettor)."""

  command_id = 17
  plate_top_center: Annotated[XYZCoord, Struct()]
  plate: Annotated[PlateDimensions, Struct()]
  clearance_y: F32
  grip_speed_y: F32
  grip_distance: F32
  grip_height: F32


@dataclass
class PrepDropPlate(PrepCommand):
  """Drop plate (cmd=18, dest=Pipettor)."""

  command_id = 18
  plate_top_center: Annotated[XYZCoord, Struct()]
  clearance_y: F32
  acceleration_scale_x: PaddedU8


@dataclass
class PrepMovePlate(PrepCommand):
  """Move plate to position (cmd=19, dest=Pipettor)."""

  command_id = 19
  plate_top_center: Annotated[XYZCoord, Struct()]
  acceleration_scale_x: PaddedU8


@dataclass
class PrepTransferPlate(PrepCommand):
  """Transfer plate from source to destination (cmd=20, dest=Pipettor)."""

  command_id = 20
  plate_source_top_center: Annotated[XYZCoord, Struct()]
  plate_destination_top_center: Annotated[XYZCoord, Struct()]
  plate: Annotated[PlateDimensions, Struct()]
  clearance_y: F32
  grip_speed_y: F32
  grip_distance: F32
  grip_height: F32
  acceleration_scale_x: PaddedU8


@dataclass
class PrepReleasePlate(PrepCommand):
  """Release plate / open gripper (cmd=21, dest=Pipettor)."""

  command_id = 21


@dataclass
class PrepEmptyDispenser(PrepCommand):
  """Empty dispenser (cmd=23, dest=Pipettor)."""

  command_id = 23
  channels: EnumArray


@dataclass
class PrepMoveToPosition(PrepCommand):
  """Move to position (cmd=26, dest=Pipettor or ChannelCoordinator)."""

  command_id = 26
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]


@dataclass
class PrepMoveToPositionViaLane(PrepCommand):
  """Move to position via lane (cmd=27, dest=Pipettor or ChannelCoordinator)."""

  command_id = 27
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]


@dataclass
class PrepMoveZUpToSafe(PrepCommand):
  """Move Z axes up to safe height (cmd=28, dest=Pipettor)."""

  command_id = 28
  channels: EnumArray


@dataclass
class PrepZSeekLldPosition(PrepCommand):
  """Z-seek LLD position (cmd=29, dest=Pipettor)."""

  command_id = 29
  seek_parameters: Annotated[list[LLDChannelSeekParameters], StructArray()]


@dataclass
class PrepCreateTadmLimitCurve(PrepCommand):
  """Create TADM limit curve (cmd=31, dest=Pipettor)."""

  command_id = 31
  channel: U32
  name: Str
  lower_limit: Annotated[list[LimitCurveEntry], StructArray()]
  upper_limit: Annotated[list[LimitCurveEntry], StructArray()]


@dataclass
class PrepEraseTadmLimitCurves(PrepCommand):
  """Erase TADM limit curves for a channel (cmd=32, dest=Pipettor)."""

  command_id = 32
  channel: U32


@dataclass
class PrepGetTadmLimitCurveNames(PrepCommand):
  """Get TADM limit curve names for a channel (cmd=33, dest=Pipettor)."""

  command_id = 33
  channel: U32


@dataclass
class PrepGetTadmLimitCurveInfo(PrepCommand):
  """Get TADM limit curve info (cmd=34, dest=Pipettor)."""

  command_id = 34
  channel: U32
  name: Str


@dataclass
class PrepRetrieveTadmData(PrepCommand):
  """Retrieve TADM data for a channel (cmd=35, dest=Pipettor)."""

  command_id = 35
  channel: U32


@dataclass
class PrepResetTadmFifo(PrepCommand):
  """Reset TADM FIFO (cmd=36, dest=Pipettor)."""

  command_id = 36
  channels: EnumArray


@dataclass
class PrepAspirateNoLldMonitoringV2(PrepCommand):
  """Aspirate v2 without LLD or monitoring (cmd=38, dest=Pipettor)."""

  command_id = 38
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring2], StructArray()]


@dataclass
class PrepAspirateTadmV2(PrepCommand):
  """Aspirate v2 with TADM, no LLD (cmd=39, dest=Pipettor)."""

  command_id = 39
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm2], StructArray()]


@dataclass
class PrepAspirateWithLldV2(PrepCommand):
  """Aspirate v2 with LLD and monitoring (cmd=40, dest=Pipettor)."""

  command_id = 40
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring2], StructArray()]


@dataclass
class PrepAspirateWithLldTadmV2(PrepCommand):
  """Aspirate v2 with LLD and TADM (cmd=41, dest=Pipettor)."""

  command_id = 41
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm2], StructArray()]


@dataclass
class PrepDispenseNoLldV2(PrepCommand):
  """Dispense v2 without LLD (cmd=42, dest=Pipettor)."""

  command_id = 42
  dispense_parameters: Annotated[list[DispenseParametersNoLld2], StructArray()]


@dataclass
class PrepDispenseWithLldV2(PrepCommand):
  """Dispense v2 with LLD (cmd=43, dest=Pipettor)."""

  command_id = 43
  dispense_parameters: Annotated[list[DispenseParametersLld2], StructArray()]


# =============================================================================
# MLPrep command classes
# =============================================================================


@dataclass
class PrepInitialize(PrepCommand):
  """Initialize MLPrep (cmd=1, dest=MLPrep)."""

  command_id = 1
  smart: PaddedBool
  tip_drop_params: Annotated[InitTipDropParameters, Struct()]


@dataclass
class PrepGetIsInitialized(PrepCommand):
  """Query whether MLPrep is initialized.

  From introspection (MLPrepRoot.MLPrep): iface=1 id=2 GetIsInitialized(()) -> value: I64.
  Sent as STATUS_REQUEST (0); response is STATUS_RESPONSE (1) with one I64.
  """

  command_id = 2  # GetIsInitialized per introspection_output/MLPrepRoot_MLPrep.txt
  action_code = 0  # STATUS_REQUEST (query methods use 0, like Nimbus IsInitialized)

  @dataclass(frozen=True)
  class Response:
    value: I64


@dataclass
class PrepPark(PrepCommand):
  """Park MLPrep (cmd=3, dest=MLPrep)."""

  command_id = 3


@dataclass
class PrepSpread(PrepCommand):
  """Spread channels (cmd=4, dest=MLPrep)."""

  command_id = 4


@dataclass
class PrepAddTipAndNeedleDefinition(PrepCommand):
  """Add tip/needle definition (cmd=12, dest=MLPrep)."""

  command_id = 12
  tip_definition: Annotated[TipDefinition, Struct()]


@dataclass
class PrepRemoveTipAndNeedleDefinition(PrepCommand):
  """Remove tip/needle definition by ID (cmd=13, dest=MLPrep)."""

  command_id = 13
  id_: WEnum


@dataclass
class PrepReadStorage(PrepCommand):
  """Read from instrument storage (cmd=14, dest=MLPrep)."""

  command_id = 14
  offset: U32
  length: U32


@dataclass
class PrepWriteStorage(PrepCommand):
  """Write to instrument storage (cmd=15, dest=MLPrep)."""

  command_id = 15
  offset: U32
  data: U8Array


@dataclass
class PrepPowerDownRequest(PrepCommand):
  """Request power down (cmd=17, dest=MLPrep)."""

  command_id = 17


@dataclass
class PrepConfirmPowerDown(PrepCommand):
  """Confirm power down (cmd=18, dest=MLPrep)."""

  command_id = 18


@dataclass
class PrepCancelPowerDown(PrepCommand):
  """Cancel power down (cmd=19, dest=MLPrep)."""

  command_id = 19


@dataclass
class PrepRemoveChannelPower(PrepCommand):
  """Remove channel power for head swap (cmd=23, dest=MLPrep)."""

  command_id = 23


@dataclass
class PrepRestoreChannelPower(PrepCommand):
  """Restore channel power after head swap (cmd=24, dest=MLPrep)."""

  command_id = 24
  delay_ms: U32


@dataclass
class PrepSetDeckLight(PrepCommand):
  """Set deck LED colour (cmd=25, dest=MLPrep)."""

  command_id = 25
  white: PaddedU8
  red: PaddedU8
  green: PaddedU8
  blue: PaddedU8


@dataclass
class PrepGetDeckLight(PrepCommand):
  """Get deck LED colour (cmd=26, dest=MLPrep)."""

  command_id = 26


@dataclass
class PrepSuspendedPark(PrepCommand):
  """Suspended park / move to load position (cmd=29, dest=MLPrep)."""

  command_id = 29
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]


@dataclass
class PrepMethodBegin(PrepCommand):
  """Begin method (cmd=30, dest=MLPrep)."""

  command_id = 30
  automatic_pause: PaddedBool


@dataclass
class PrepMethodEnd(PrepCommand):
  """End method (cmd=31, dest=MLPrep)."""

  command_id = 31


@dataclass
class PrepMethodAbort(PrepCommand):
  """Abort method (cmd=33, dest=MLPrep)."""

  command_id = 33


@dataclass
class PrepIsParked(PrepCommand):
  """Query parked status (cmd=34, dest=MLPrep). Introspection: IsParked(()) -> parked: I64."""

  command_id = 34
  action_code = 0  # STATUS_REQUEST

  @dataclass(frozen=True)
  class Response:
    value: I64


@dataclass
class PrepIsSpread(PrepCommand):
  """Query spread status (cmd=35, dest=MLPrep). Introspection: IsSpread(()) -> parked: I64."""

  command_id = 35
  action_code = 0  # STATUS_REQUEST

  @dataclass(frozen=True)
  class Response:
    value: I64


# -----------------------------------------------------------------------------
# Wire structs for config responses (used by nested Response and InstrumentConfig)
# -----------------------------------------------------------------------------


@dataclass
class _DeckSiteDefinitionWire:
  """Wire shape for one DeckSiteDefinition (GetDeckSiteDefinitions element)."""

  default_values: PaddedBool
  id: U32
  left_bottom_front_x: F32
  left_bottom_front_y: F32
  left_bottom_front_z: F32
  length: F32
  width: F32
  height: F32


@dataclass
class _WasteSiteDefinitionWire:
  """Wire shape for one WasteSiteDefinition (GetWasteSiteDefinitions element)."""

  default_values: PaddedBool
  index: WEnum
  x_position: I8
  y_position: U16
  z_position: F32
  z_seek: F32


# -----------------------------------------------------------------------------
# Config queries (MLPrep / DeckConfiguration) for _probe_hardware_config
# -----------------------------------------------------------------------------


@dataclass
class _PrepStatusQuery(PrepCommand):
  """Base for MLPrep status queries: STATUS_REQUEST (0), no params."""

  action_code = 0


@dataclass
class PrepGetIsEnclosurePresent(_PrepStatusQuery):
  """GetIsEnclosurePresent (cmd=21, dest=MLPrep). Returns I64 as bool."""

  command_id = 21

  @dataclass(frozen=True)
  class Response:
    value: I64


@dataclass
class PrepGetSafeSpeedsEnabled(_PrepStatusQuery):
  """GetSafeSpeedsEnabled (cmd=28, dest=MLPrep). Returns I64 as bool."""

  command_id = 28

  @dataclass(frozen=True)
  class Response:
    value: I64


@dataclass
class PrepGetDefaultTraverseHeight(_PrepStatusQuery):
  """GetDefaultTraverseHeight (cmd=10, dest=MLPrep). Returns F32."""

  command_id = 10

  @dataclass(frozen=True)
  class Response:
    value: F32


@dataclass
class PrepGetTipAndNeedleDefinitions(_PrepStatusQuery):
  """GetTipAndNeedleDefinitions (cmd=11, dest=MLPrep).

  Returns the list of tip/needle definitions registered on the instrument.
  Introspection: iface=1 id=11 GetTipAndNeedleDefinitions(value: type_64) -> void
  (response carries STRUCTURE_ARRAY of tip definition structs).
  """

  command_id = 11

  @dataclass(frozen=True)
  class Response:
    definitions: Annotated[list[TipDefinition], StructArray()]


@dataclass
class PrepGetDeckBounds(_PrepStatusQuery):
  """GetDeckBounds (cmd=1, dest=DeckConfiguration). Returns 6× F32 (min/max x,y,z)."""

  command_id = 1

  @dataclass(frozen=True)
  class Response:
    min_x: F32
    max_x: F32
    min_y: F32
    max_y: F32
    min_z: F32
    max_z: F32


@dataclass
class PrepGetDeckSiteDefinitions(_PrepStatusQuery):
  """GetDeckSiteDefinitions (cmd=7, dest=DeckConfiguration).

  Response is a STRUCTURE_ARRAY of DeckSiteDefinition structs:
    DefaultValues: BOOL, Id: U32, LeftBottomFrontX: F32, LeftBottomFrontY: F32,
    LeftBottomFrontZ: F32, Length: F32, Width: F32, Height: F32
  """

  command_id = 7

  @dataclass(frozen=True)
  class Response:
    sites: Annotated[list[_DeckSiteDefinitionWire], StructArray()]


@dataclass
class PrepGetWasteSiteDefinitions(_PrepStatusQuery):
  """GetWasteSiteDefinitions (cmd=12, dest=DeckConfiguration).

  Response is a STRUCTURE_ARRAY of WasteSiteDefinition structs:
    DefaultValues: BOOL, Index: ENUM, XPosition: I8, YPosition: U16,
    ZPosition: F32, ZSeek: F32
  """

  command_id = 12

  @dataclass(frozen=True)
  class Response:
    sites: Annotated[list[_WasteSiteDefinitionWire], StructArray()]


# =============================================================================
# PrepBackend
# =============================================================================

_CHANNEL_INDEX = {
  0: ChannelIndex.RearChannel,
  1: ChannelIndex.FrontChannel,
}


class PrepBackend(HamiltonTCPBackend):
  """Backend for Hamilton Prep instruments using the shared TCP stack.

  Discovers MLPrep, Pipettor, and ChannelCoordinator via Protocol-7/3
  introspection, then sends typed PrepCommand instances. All parameter
  serialisation is handled by HoiParams.from_struct() -- no manual builder
  calls.
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
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )
    self._config: Optional[InstrumentConfig] = None

  # ---------------------------------------------------------------------------
  # Setup & discovery
  # ---------------------------------------------------------------------------

  async def setup(self, smart: bool = True, force_initialize: bool = False):
    """Set up Prep: connect, discover objects, then conditionally initialize MLPrep.

    Order:
      1. TCP + Protocol 7/3 init, root discovery, and depth-1 interface discovery (super().setup())
      2. Lazy-resolve Pipettor (depth-2) for commands
      3. If force_initialize: always run Initialize(smart=smart).
         Else: query GetIsInitialized; only run Initialize(smart=smart) when not initialized.
      4. Mark setup complete.

    Args:
      smart: When we call Initialize, pass this to the firmware (default True).
      force_initialize: If True, always run Initialize. If False, run Initialize only
        when GetIsInitialized reports not initialized (e.g. reconnect-safe).
    """
    await super().setup()
    await self._registry.resolve("MLPrepRoot.PipettorRoot.Pipettor")

    if not self._registry.has("MLPrepRoot.MLPrep"):
      raise RuntimeError("MLPrep object not discovered. Cannot proceed with setup.")

    if force_initialize:
      await self._run_initialize(smart=smart)
      logger.info("Prep initialization complete (force_initialize=True)")
    else:
      try:
        already = await self.is_initialized()
      except Exception as e:
        logger.error("GetIsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.info("MLPrep already initialized, skipping Initialize")
      else:
        await self._run_initialize(smart=smart)
        logger.info("Prep initialization complete")

    self._config = await self._probe_hardware_config()
    logger.info(
      "Hardware config: has_enclosure=%s, safe_speeds=%s, traverse_height=%s, "
      "deck_bounds=%s, deck_sites=%d, waste_sites=%d",
      self._config.has_enclosure,
      self._config.safe_speeds_enabled,
      self._config.default_traverse_height,
      self._config.deck_bounds,
      len(self._config.deck_sites),
      len(self._config.waste_sites),
    )

    # await self.ensure_spread()
    self.setup_finished = True

  async def _run_initialize(self, smart: bool):
    """Send PrepInitialize to MLPrep (shared by setup)."""
    await self.send_command(
      PrepInitialize(
        dest=self._mlprep_dest(),
        smart=smart,
        tip_drop_params=InitTipDropParameters(
          default_values=True,
          x_position=287.0,
          rolloff_distance=3,
          channel_parameters=[],
        ),
      )
    )

  async def _probe_hardware_config(self) -> InstrumentConfig:
    """Query MLPrep and DeckConfiguration for hardware config, deck sites, and waste sites."""
    mlprep = self._mlprep_dest()
    enc_resp = await self.send_command(PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await self.send_command(PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await self.send_command(PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else 0.0

    deck_bounds: Optional[DeckBounds] = None
    deck_sites: Tuple[DeckSiteInfo, ...] = ()
    waste_sites: Tuple[WasteSiteInfo, ...] = ()
    try:
      deck_addr = await self._registry.resolve("MLPrepRoot.MLPrepCalibration.DeckConfiguration")

      bounds_resp = await self.send_command(PrepGetDeckBounds(dest=deck_addr))
      if bounds_resp:
        deck_bounds = DeckBounds(
          min_x=bounds_resp.min_x,
          max_x=bounds_resp.max_x,
          min_y=bounds_resp.min_y,
          max_y=bounds_resp.max_y,
          min_z=bounds_resp.min_z,
          max_z=bounds_resp.max_z,
        )

      sites_resp = await self.send_command(PrepGetDeckSiteDefinitions(dest=deck_addr))
      if sites_resp and sites_resp.sites:
        deck_sites = tuple(
          DeckSiteInfo(
            id=int(s.id),
            left_bottom_front_x=float(s.left_bottom_front_x),
            left_bottom_front_y=float(s.left_bottom_front_y),
            left_bottom_front_z=float(s.left_bottom_front_z),
            length=float(s.length),
            width=float(s.width),
            height=float(s.height),
          )
          for s in sites_resp.sites
        )
        logger.info("Discovered %d deck sites", len(deck_sites))

      waste_resp = await self.send_command(PrepGetWasteSiteDefinitions(dest=deck_addr))
      if waste_resp and waste_resp.sites:
        waste_sites = tuple(
          WasteSiteInfo(
            index=int(s.index),
            x_position=float(s.x_position),
            y_position=float(s.y_position),
            z_position=float(s.z_position),
            z_seek=float(s.z_seek),
          )
          for s in waste_resp.sites
        )
        logger.info("Discovered %d waste sites: %s", len(waste_sites), waste_sites)

    except (KeyError, RuntimeError) as e:
      logger.debug("DeckConfiguration not available: %s", e)

    return InstrumentConfig(
      deck_bounds=deck_bounds,
      has_enclosure=has_enclosure,
      safe_speeds_enabled=safe_speeds_enabled,
      default_traverse_height=default_traverse_height,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
    )

  # ---------------------------------------------------------------------------
  # Properties
  # ---------------------------------------------------------------------------

  @property
  def mlprep_address(self) -> Optional[Address]:
    try:
      return self._registry.address("MLPrepRoot.MLPrep") if self._registry.has("MLPrepRoot.MLPrep") else None
    except KeyError:
      return None

  @property
  def pipettor_address(self) -> Optional[Address]:
    for path in ("MLPrepRoot.PipettorRoot.Pipettor", "MLPrepRoot.ChannelCoordinator"):
      if self._registry.has(path):
        return self._registry.address(path)
    return None

  @property
  def coordinator_address(self) -> Optional[Address]:
    try:
      return self._registry.address("MLPrepRoot.ChannelCoordinator") if self._registry.has("MLPrepRoot.ChannelCoordinator") else None
    except KeyError:
      return None

  @property
  def num_channels(self) -> int:
    """Prep has 2 channels (front and rear)."""
    return 2

  def _pipettor_dest(self) -> Address:
    for path in ("MLPrepRoot.PipettorRoot.Pipettor", "MLPrepRoot.ChannelCoordinator"):
      if self._registry.has(path):
        return self._registry.address(path)
    raise RuntimeError("Pipettor/Coordinator not discovered. Call setup() first.")

  def _mlprep_dest(self) -> Address:
    return self._registry.address("MLPrepRoot.MLPrep")

  def _validate_position(self, x: float, y: float, z: float) -> None:
    """Raise ValueError if (x, y, z) is outside deck bounds. No-op if config/bounds not set."""
    if self._config is None or self._config.deck_bounds is None:
      return
    b = self._config.deck_bounds
    if not (b.min_x <= x <= b.max_x and b.min_y <= y <= b.max_y and b.min_z <= z <= b.max_z):
      raise ValueError(
        f"Position ({x}, {y}, {z}) outside deck bounds "
        f"(x=[{b.min_x}, {b.max_x}], y=[{b.min_y}, {b.max_y}], z=[{b.min_z}, {b.max_z}])"
      )

  async def is_initialized(self) -> bool:
    """Query whether MLPrep reports as initialized (GetIsInitialized, cmd=2).

    Uses MLPrep method from introspection: GetIsInitialized(()) -> value: I64.
    Requires MLPrep to be discovered (e.g. after super().setup() and
    _discover_prep_objects()). Call before or after PrepInitialize to test.
    """
    result = await self.send_command(PrepGetIsInitialized(dest=self._mlprep_dest()))
    if result is None:
      return False
    return bool(result.value)

  async def get_tip_and_needle_definitions(self) -> Tuple[TipDefinition, ...]:
    """Return tip/needle definitions registered on the instrument (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self.send_command(
      PrepGetTipAndNeedleDefinitions(dest=self._mlprep_dest())
    )
    if result is None or not getattr(result, "definitions", None):
      return ()
    return tuple(result.definitions)

  async def is_parked(self) -> bool:
    """Query whether MLPrep is parked (IsParked, cmd=34)."""
    result = await self.send_command(PrepIsParked(dest=self._mlprep_dest()))
    if result is None:
      return False
    return bool(result.value)

  async def is_spread(self) -> bool:
    """Query whether channels are spread (IsSpread, cmd=35). Pipettor commands typically require spread state."""
    result = await self.send_command(PrepIsSpread(dest=self._mlprep_dest()))
    if result is None:
      return False
    return bool(result.value)

  # ---------------------------------------------------------------------------
  # LiquidHandlerBackend abstract methods
  # ---------------------------------------------------------------------------

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
  ):
    """Pick up tips.

    The arm moves to z_seek during lateral XY approach, then descends to z_position
    to engage the tip. Default z_seek = z_position + fitting_depth + 5mm (tip-type-
    aware; avoids descending into the rack during approach).

    Args:
      final_z: Traverse/safe height (mm) for the move and Z position after command.
        Defaults to the instrument's configured traverse height from setup.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      enable_tadm: Enable tip-adjust during pickup.
      dispenser_volume: Dispenser volume for TADM (if enabled).
      dispenser_speed: Dispenser speed for TADM (if enabled).
    """
    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported"

    resolved_final_z = final_z if final_z is not None else self._config.default_traverse_height

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[TipPositionParameters] = []
    for ch in range(2):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "t")
      params = TipPositionParameters.for_op(
        _CHANNEL_INDEX[ch], loc, op.resource.get_tip(),
        z_seek_offset=z_seek_offset,
      )
      self._validate_position(loc.x, loc.y, params.z_position)
      tip_positions.append(params)

    assert len(set(op.tip for op in ops)) == 1, "All ops must use the same tip type"
    tip = ops[0].tip
    tip_definition = TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.total_tip_length - tip.fitting_depth,
      tip_type=TipTypes.StandardVolume,
      has_filter=tip.has_filter,
      is_needle=False,
      is_tool=False,
    )

    await self.send_command(
      PrepPickUpTips(
        dest=self._pipettor_dest(),
        tip_positions=tip_positions,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_definition=tip_definition,
        enable_tadm=enable_tadm,
        dispenser_volume=dispenser_volume,
        dispenser_speed=dispenser_speed,
      )
    )

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    final_z: Optional[float] = None,
    seek_speed: float = 30.0,
    z_seek_offset: Optional[float] = None,
    drop_type: TipDropType = TipDropType.FixedHeight,
    tip_roll_off_distance: float = 0.0,
  ):
    """Drop tips.

    The arm moves to z_seek during lateral XY approach (tip is on pipette, so tip
    bottom is at z_seek - total_tip_length). Default z_seek = z_position +
    total_tip_length + 10mm so the tip bottom stays above adjacent tips in the
    rack during approach.

    Args:
      final_z: Traverse/safe height (mm) for the move and Z position after command.
        Defaults to the instrument's configured traverse height from setup.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      drop_type: How the tip is released (FixedHeight, Stall, or CLLDSeek).
      tip_roll_off_distance: Roll-off distance (mm) for tip release.
    """
    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported"

    resolved_final_z = final_z if final_z is not None else self._config.default_traverse_height

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[TipDropParameters] = []
    for ch in range(2):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "t")
      params = TipDropParameters.for_op(
        _CHANNEL_INDEX[ch], loc, op.resource.get_tip(),
        z_seek_offset=z_seek_offset,
        drop_type=drop_type,
      )
      self._validate_position(loc.x, loc.y, params.z_position)
      tip_positions.append(params)

    await self.send_command(
      PrepDropTips(
        dest=self._pipettor_dest(),
        tip_positions=tip_positions,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_roll_off_distance=tip_roll_off_distance,
      )
    )

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    z_final: float = 96.97,
    z_fluid: float = 94.97,
    z_air: float = 96.97,
    settling_time: float = 1.0,
    transport_air_volume: float = 0.0,
    z_liquid_exit_speed: float = 2.0,
    z_minimum: float = -5.03,
    z_bottom_search_offset: float = 2.0,
  ):
    """Aspirate from the given resources (NoLLD path).

    All optional kwargs override wire-protocol defaults and are passed through
    to CommonParameters and NoLldParameters. Example::

      await backend.aspirate(ops, [0], z_final=95.0, settling_time=2.0)
    """
    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported"

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    aspirate_parameters: List[AspirateParametersNoLldAndMonitoring] = []
    for ch in range(2):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      self._validate_position(loc.x, loc.y, loc.z)
      assert op.resource.get_size_x() == op.resource.get_size_y(), "Only round wells supported"
      radius = op.resource.get_size_x() / 2
      aspirate_parameters.append(
        AspirateParametersNoLldAndMonitoring(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          aspirate=AspirateParameters.for_op(loc, op),
          common=CommonParameters.for_op(
            op.volume, radius,
            flow_rate=op.flow_rate,
            z_minimum=z_minimum,
            z_final=z_final,
            z_liquid_exit_speed=z_liquid_exit_speed,
            transport_air_volume=transport_air_volume,
            settling_time=settling_time,
          ),
          no_lld=NoLldParameters.for_fixed_z(
            z_fluid, z_air,
            z_bottom_search_offset=z_bottom_search_offset,
          ),
          mix=MixParameters.default(),
          adc=AdcParameters.default(),
          aspirate_monitoring=AspirateMonitoringParameters.default(),
        )
      )

    await self.send_command(
      PrepAspirateNoLldMonitoring(
        dest=self._pipettor_dest(),
        aspirate_parameters=aspirate_parameters,
      )
    )

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    final_z: float = 96.97,
    z_fluid: float = 94.97,
    z_air: float = 99.08,
    settling_time: float = 0.0,
    transport_air_volume: float = 0.0,
    z_liquid_exit_speed: float = 2.0,
    z_minimum: float = -5.03,
    z_bottom_search_offset: float = 2.0,
  ):
    """Dispense to the given resources (NoLLD path).

    All optional kwargs override wire-protocol defaults and are passed through
    to CommonParameters and NoLldParameters. Example::

      await backend.dispense(ops, [0], final_z=95.0, settling_time=0.5)
    """
    assert len(ops) == len(use_channels)
    assert max(use_channels) <= 2, "Only two channels are supported"

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    dispense_parameters: List[DispenseParametersNoLld] = []
    for ch in range(2):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      self._validate_position(loc.x, loc.y, loc.z)
      assert op.resource.get_size_x() == op.resource.get_size_y(), "Only round wells supported"
      radius = op.resource.get_size_x() / 2
      dispense_parameters.append(
        DispenseParametersNoLld(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          dispense=DispenseParameters.for_op(loc),
          common=CommonParameters.for_op(
            op.volume, radius,
            flow_rate=op.flow_rate,
            z_minimum=z_minimum,
            z_final=final_z,
            z_liquid_exit_speed=z_liquid_exit_speed,
            transport_air_volume=transport_air_volume,
            settling_time=settling_time,
          ),
          no_lld=NoLldParameters.for_fixed_z(
            z_fluid, z_air,
            z_bottom_search_offset=z_bottom_search_offset,
          ),
          mix=MixParameters.default(),
          tadm=TadmParameters.default(),
          adc=AdcParameters.default(),
        )
      )

    await self.send_command(
      PrepDispenseNoLld(
        dest=self._pipettor_dest(),
        dispense_parameters=dispense_parameters,
      )
    )

  async def pick_up_tips96(self, pickup: PickupTipRack):
    raise NotImplementedError("pick_up_tips96 is not supported on the Prep")

  async def drop_tips96(self, drop: DropTipRack):
    raise NotImplementedError("drop_tips96 is not supported on the Prep")

  async def aspirate96(
    self, aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer]
  ):
    raise NotImplementedError("aspirate96 is not supported on the Prep")

  async def dispense96(
    self, dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer]
  ):
    raise NotImplementedError("dispense96 is not supported on the Prep")

  async def pick_up_resource(self, pickup: ResourcePickup):
    raise NotImplementedError("pick_up_resource is not yet implemented on the Prep")

  async def move_picked_up_resource(self, move: ResourceMove):
    raise NotImplementedError("move_picked_up_resource is not yet implemented on the Prep")

  async def drop_resource(self, drop: ResourceDrop):
    raise NotImplementedError("drop_resource is not yet implemented on the Prep")

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    return True

  # ---------------------------------------------------------------------------
  # MLPrep convenience methods
  # ---------------------------------------------------------------------------

  async def park(self) -> None:
    """Park the instrument."""
    await self.send_command(PrepPark(dest=self._mlprep_dest()))

  async def spread(self) -> None:
    """Spread channels."""
    await self.send_command(PrepSpread(dest=self._mlprep_dest()))

  async def method_begin(self, automatic_pause: bool = False) -> None:
    """Signal the start of a liquid-handling method."""
    await self.send_command(
      PrepMethodBegin(
        dest=self._mlprep_dest(),
        automatic_pause=automatic_pause,
      )
    )

  async def method_end(self) -> None:
    """Signal the end of a liquid-handling method."""
    await self.send_command(PrepMethodEnd(dest=self._mlprep_dest()))

  async def method_abort(self) -> None:
    """Abort the current method."""
    await self.send_command(PrepMethodAbort(dest=self._mlprep_dest()))

  async def set_deck_light(
    self, white: int, red: int, green: int, blue: int
  ) -> None:
    """Set the deck LED colour."""
    await self.send_command(
      PrepSetDeckLight(
        dest=self._mlprep_dest(),
        white=white,
        red=red,
        green=green,
        blue=blue,
      )
    )

  # ---------------------------------------------------------------------------
  # Pipettor convenience methods
  # ---------------------------------------------------------------------------

  async def move_to_position(self, move_parameters: GantryMoveXYZParameters) -> None:
    """Move to position (cmd=26)."""
    for ax in move_parameters.axis_parameters:
      self._validate_position(
        move_parameters.gantry_x_position, ax.y_position, ax.z_position
      )
    await self.send_command(
      PrepMoveToPosition(
        dest=self._pipettor_dest(),
        move_parameters=move_parameters,
      )
    )

  async def move_to_position_via_lane(self, move_parameters: GantryMoveXYZParameters) -> None:
    """Move to position via lane (cmd=27)."""
    for ax in move_parameters.axis_parameters:
      self._validate_position(
        move_parameters.gantry_x_position, ax.y_position, ax.z_position
      )
    await self.send_command(
      PrepMoveToPositionViaLane(
        dest=self._pipettor_dest(),
        move_parameters=move_parameters,
      )
    )
