"""Prep command dataclasses and wire-type parameter structs.

Pure data definitions for the Hamilton Prep protocol — enums, hardware config,
wire-type annotated parameter structs, and PrepCommand subclasses. No business
logic; used by PrepBackend for command construction and serialization.

Moved from prep_backend.py to separate protocol contracts from domain logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated, Optional, Tuple

from pylabrobot.liquid_handling.backends.hamilton.tcp.commands import HamiltonCommand
from pylabrobot.liquid_handling.backends.hamilton.tcp.messages import HoiParams
from pylabrobot.liquid_handling.backends.hamilton.tcp.packets import Address
from pylabrobot.liquid_handling.backends.hamilton.tcp.protocol import HamiltonProtocol
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  F32,
  I8,
  I16,
  I64,
  U16,
  U32,
  EnumArray,
  I16Array,
  PaddedBool,
  PaddedU8,
  Str,
  Struct,
  StructArray,
  U8Array,
)
from pylabrobot.liquid_handling.backends.hamilton.tcp.wire_types import (
  Enum as WEnum,
)
from pylabrobot.liquid_handling.standard import SingleChannelAspiration

# =============================================================================
# Enums (mirrored from Prep protocol spec)
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


class MonitoringMode(IntEnum):
  """Selects aspirate monitoring vs TADM for pipetting commands."""

  MONITORING = 0  # AspirateMonitoringParameters (default, matches v1 behavior)
  TADM = 1  # TadmParameters


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
  deck_sites: Tuple[DeckSiteInfo, ...]
  waste_sites: Tuple[WasteSiteInfo, ...]
  default_traverse_height: Optional[float] = (
    None  # None if probe failed; user can set via set_default_traverse_height
  )
  num_channels: Optional[int] = None  # 1 or 2 dual-channel pipettor; from GetPresentChannels
  has_mph: Optional[bool] = None  # True if 8MPH present; from GetPresentChannels


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
    op: SingleChannelAspiration,
    prewet_volume: float = 0.0,
    blowout_volume: Optional[float] = None,
  ) -> AspirateParameters:
    return cls(
      default_values=False,
      x_position=loc.x,
      y_position=loc.y,
      prewet_volume=prewet_volume,
      blowout_volume=(op.blow_out_air_volume or 0.0) if blowout_volume is None else blowout_volume,
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
  ) -> DispenseParameters:
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
    z_minimum: float = 5.0,
    z_final: float = 96.97,
    z_liquid_exit_speed: float = 10.0,
    transport_air_volume: float = 0.0,
    cone_height: float = 0.0,
    cone_bottom_radius: float = 0.0,
    settling_time: float = 1.0,
    additional_probes: int = 0,
  ) -> CommonParameters:
    """Build CommonParameters for a single aspirate/dispense op.

    z_minimum is in mm; default 5.0 keeps the head above the deck surface (deck has
    its own size_z). High-level aspirate()/dispense() override with well bottom when None.
    z_liquid_exit_speed is in mm/s; default 10.0 aligns with STAR swap speed.
    """
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
  ) -> NoLldParameters:
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
  search_start_position: F32
  channel_speed: F32
  z_submerge: F32
  z_out_of_liquid: F32

  @classmethod
  def default(cls) -> LldParameters:
    return cls(
      default_values=True,
      search_start_position=0.0,
      channel_speed=0.0,
      z_submerge=0.0,
      z_out_of_liquid=0.0,
    )


@dataclass
class CLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  clot_check_enable: PaddedBool
  z_clot_check: F32
  detect_mode: WEnum

  @classmethod
  def default(cls) -> CLldParameters:
    return cls(
      default_values=True, sensitivity=1, clot_check_enable=False, z_clot_check=0.0, detect_mode=0
    )


@dataclass
class PLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  dispenser_seek_speed: F32
  lld_height_difference: F32
  detect_mode: WEnum

  @classmethod
  def default(cls) -> PLldParameters:
    return cls(
      default_values=True,
      sensitivity=1,
      dispenser_seek_speed=0.0,
      lld_height_difference=0.0,
      detect_mode=0,
    )


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
class ChannelBoundsParameters:
  """Per-channel movement bounds returned by PipettorService.GetChannelBounds."""

  default_values: PaddedBool
  channel: WEnum
  x_min: F32
  x_max: F32
  y_min: F32
  y_max: F32
  z_min: F32
  z_max: F32


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
  ) -> TipPositionParameters:
    """Build from an op location and tip (pickup).

    z_seek default: z_position + fitting_depth + 5mm guard (tip-type-aware,
    comparable to Nimbus/Vantage). z_seek_offset: additive mm on top of
    computed default (None = 0).
    """
    z = loc.z + tip.total_tip_length - tip.fitting_depth
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
    drop_type: Optional[TipDropType] = None,
  ) -> TipDropParameters:
    """Build from an op location and tip (drop).

    z_position uses (total_tip_length - fitting_depth) so the tip bottom lands
    at the spot surface (consistent with STAR and with pickup).
    z_seek default: loc.z + total_tip_length + 5mm so tip bottom clears adjacent tips during
    lateral approach. z_seek_offset: additive mm on top of computed default
    (None = 0).
    """
    z = loc.z + (tip.total_tip_length - tip.fitting_depth)
    z_seek = loc.z + tip.total_tip_length + 10.0 + (z_seek_offset or 0.0)
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
class MphPickupTips(PrepCommand):
  """Pick up tips via MPH coordinator (iface=1 id=9, dest=MphRoot.MPH).

  Resolved introspection signature:
    PickupTips(tipParameters: struct(iface=1), finalZ: f32,
               tipDefinition: struct(iface=1), tadm: bool,
               dispenserVolume: f32, dispenserSpeed: f32,
               tipMask: u32) -> { seekSpeed: List[u16] }

  The MPH takes a SINGLE struct (type_57) for tip_parameters, not a
  StructArray (type_61) like the Pipettor. All 8 probes move as one unit;
  tip_mask selects which channels engage.
  """

  command_id = 9
  tip_parameters: Annotated[TipPositionParameters, Struct()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32
  tip_mask: U32


@dataclass
class MphDropTips(PrepCommand):
  """Drop tips via MPH coordinator (iface=1 id=12, dest=MphRoot.MPH).

  Resolved introspection signature:
    DropTips(dropTipParameters: struct(iface=1), finalZ: f32,
             tipRollOffDistance: f32) -> seekSpeed: List[u16]

  Single struct (type_57) for drop position — all probes drop together.
  """

  command_id = 12
  drop_parameters: Annotated[TipDropParameters, Struct()]
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


# CORE gripper tool definition for PrepPickUpTool (struct); matches instrument id=11.
CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS = TipPickupParameters(
  default_values=False,
  volume=1.0,
  length=22.9,
  tip_type=TipTypes.None_,
  has_filter=False,
  is_needle=False,
  is_tool=True,
)


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
class PrepGetPositions(PrepCommand):
  """GetPositions (cmd=25, dest=Pipettor).

  Returns the current XYZ position of each channel as a StructArray of
  ChannelXYZPositionParameters.
  """

  command_id = 25
  action_code = 0  # STATUS_REQUEST

  @dataclass(frozen=True)
  class Response:
    positions: Annotated[list[ChannelXYZPositionParameters], StructArray()]


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
  action_code = 0  # STATUS_REQUEST

  @dataclass(frozen=True)
  class Response:
    white: PaddedU8
    red: PaddedU8
    green: PaddedU8
    blue: PaddedU8


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
# Config queries (MLPrep / DeckConfiguration) for _get_hardware_config
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


@dataclass
class PrepGetChannelBounds(PrepCommand):
  """GetChannelBounds (cmd=10, dest=PipettorService).

  Returns per-channel movement bounds (x_min, x_max, y_min, y_max, z_min, z_max)
  as a StructArray of ChannelBoundsParameters.
  """

  command_id = 10
  action_code = 0  # STATUS_REQUEST

  @dataclass(frozen=True)
  class Response:
    bounds: Annotated[list[ChannelBoundsParameters], StructArray()]


@dataclass
class PrepGetPresentChannels(_PrepStatusQuery):
  """GetPresentChannels (cmd=17, dest=MLPrepService).

  Returns a list of enum values (iface=1, id=5): which channels are present.
  Map to ChannelIndex: 0=InvalidIndex, 1=FrontChannel, 2=RearChannel, 3=MPHChannel.
  Use this to determine hardware configuration: 1 vs 2 channels, or 8MPH presence.
  """

  command_id = 17

  @dataclass(frozen=True)
  class Response:
    channels: EnumArray  # list of ints: map to ChannelIndex for present channels
