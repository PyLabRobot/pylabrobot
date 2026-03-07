"""Hamilton Prep backend implementation.

Three-layer design:

- **HamiltonTCPClient** (``self.client``): Transport and introspection.
  All device communication goes through ``self.client.send_command()``.
  Address resolution: ``self.client.interfaces.<path>.address``.

- **Command dataclasses** (e.g. ``PrepDropTips``, ``MphPickupTips``): Pure wire shapes.
  ``@dataclass`` with ``dest: Address`` + ``Annotated`` payload fields; no defaults;
  ``build_parameters()`` uses ``HoiParams.from_struct(self)``.

- **PrepBackend methods**: Domain logic and defaults.
  Single source of truth for Prep-specific parameter defaults.

Standalone access: ``lh.backend.client.interfaces.MLPrepRoot.MphRoot.MPH.address``,
``HamiltonIntrospection(lh.backend.client)``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
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
from pylabrobot.liquid_handling.backends.backend import LiquidHandlerBackend

from pylabrobot.liquid_handling.backends.hamilton.tcp_backend import (
  HamiltonTCPClient,
  HamiltonInterfaceResolver,
  InterfaceSpec,
)
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
from pylabrobot.resources.hamilton import HamiltonTip, TipSize
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.well import CrossSectionType, Well

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


class MonitoringMode(IntEnum):
  """Selects aspirate monitoring vs TADM for pipetting commands."""

  MONITORING = 0  # AspirateMonitoringParameters (default, matches v1 behavior)
  TADM = 1        # TadmParameters


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
  default_traverse_height: Optional[float] = None  # None if probe failed; user can set via set_default_traverse_height
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
    z_minimum: float = 5.0,
    z_final: float = 96.97,
    z_liquid_exit_speed: float = 10.0,
    transport_air_volume: float = 0.0,
    cone_height: float = 0.0,
    cone_bottom_radius: float = 0.0,
    settling_time: float = 1.0,
    additional_probes: int = 0,
  ) -> "CommonParameters":
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

  @classmethod
  def default(cls) -> LldParameters:
    return cls(default_values=True, z_seek=0.0, z_seek_speed=0.0, z_submerge=0.0, z_out_of_liquid=0.0)


@dataclass
class CLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  clot_check_enable: PaddedBool
  z_clot_check: F32
  detect_mode: WEnum

  @classmethod
  def default(cls) -> CLldParameters:
    return cls(default_values=True, sensitivity=1, clot_check_enable=False, z_clot_check=0.0, detect_mode=0)


@dataclass
class PLldParameters:
  default_values: PaddedBool
  sensitivity: WEnum
  dispenser_seek_speed: F32
  lld_height_difference: F32
  detect_mode: WEnum

  @classmethod
  def default(cls) -> PLldParameters:
    return cls(default_values=True, sensitivity=1, dispenser_seek_speed=0.0, lld_height_difference=0.0, detect_mode=0)


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
    drop_type: Optional["TipDropType"] = None,
  ) -> "TipDropParameters":
    """Build from an op location and tip (drop).

    z_position uses (total_tip_length - fitting_depth) so the tip bottom lands
    at the spot surface (consistent with STAR and with pickup).
    z_seek default: loc.z + total_tip_length + 5mm so tip bottom clears adjacent tips during
    lateral approach. z_seek_offset: additive mm on top of computed default
    (None = 0).
    """
    z = loc.z + (tip.total_tip_length - tip.fitting_depth)
    z_seek = loc.z + tip.total_tip_length + 5.0 + (z_seek_offset or 0.0)
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


def _effective_radius(resource) -> float:
  """Effective radius for CommonParameters.tube_radius.

  For circular wells uses the actual radius; for rectangular wells computes the
  radius of a circle with equivalent area so tube_radius is meaningful to the
  firmware's conical liquid-following model.
  """
  if isinstance(resource, Well) and resource.cross_section_type == CrossSectionType.RECTANGLE:
    return math.sqrt(resource.get_size_x() * resource.get_size_y() / math.pi)
  return resource.get_size_x() / 2


def _build_container_segments(resource) -> list[SegmentDescriptor]:
  """Derive SegmentDescriptor list from a Well's geometry for liquid-following.

  Each segment is a frustum.  The firmware uses area_bottom/area_top to
  interpolate cross-sectional area A(z) within the segment and computes the
  Z-axis following speed as dz/dt = Q / A(z), where Q is volumetric flow rate.

  Returns [] when geometry cannot be determined; the firmware then falls back to
  the tube_radius / cone model in CommonParameters.
  """
  if not isinstance(resource, Well):
    return []

  size_z = resource.get_size_z()

  if resource.cross_section_type == CrossSectionType.CIRCLE:
    area = math.pi * (resource.get_size_x() / 2) ** 2
  elif resource.cross_section_type == CrossSectionType.RECTANGLE:
    area = resource.get_size_x() * resource.get_size_y()
  else:
    return []

  if resource.supports_compute_height_volume_functions():
    # Non-linear geometry: approximate with N frustum segments by sampling dV/dh.
    n_boundaries = 11  # 10 segments
    heights = [size_z * i / (n_boundaries - 1) for i in range(n_boundaries)]
    eps = size_z / (n_boundaries - 1) * 0.1

    def area_at(h: float) -> float:
      h_lo = max(0.0, h - eps)
      h_hi = min(size_z, h + eps)
      dv = resource.compute_volume_from_height(h_hi) - resource.compute_volume_from_height(h_lo)
      return dv / (h_hi - h_lo)

    return [
      SegmentDescriptor(
        area_top=float(area_at(heights[i + 1])),
        area_bottom=float(area_at(heights[i])),
        height=float(heights[i + 1] - heights[i]),
      )
      for i in range(n_boundaries - 1)
    ]

  # Simple geometry: single segment with constant cross-section.
  return [SegmentDescriptor(area_top=float(area), area_bottom=float(area), height=float(size_z))]


def _absolute_z_from_well(op, z_air_margin_mm: float = 2.0):
  """Compute absolute Z values from well geometry for aspirate/dispense (STAR-aligned).

  Returns (well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z). The resource
  must have get_size_z() (e.g. a well/container); otherwise raises ValueError.
  """
  loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
  well_bottom_z = loc.z + op.offset.z
  liquid_surface_z = well_bottom_z + (op.liquid_height or 0.0)

  if not hasattr(op.resource, "get_size_z"):
    raise ValueError(
      "Resource must have get_size_z() to derive absolute Z (e.g. a Well or Container). "
      "Pass z_minimum, z_fluid, z_air explicitly for this operation."
    )
  size_z = op.resource.get_size_z()
  top_of_well_z = loc.z + size_z
  z_air_z = top_of_well_z + z_air_margin_mm
  return (well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z)


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


# =============================================================================
# PrepBackend
# =============================================================================

_CHANNEL_INDEX = {
  0: ChannelIndex.RearChannel,
  1: ChannelIndex.FrontChannel,
}


# Expected root name from discovery; validated at setup().
_EXPECTED_ROOT = "MLPrepRoot"


class PrepBackend(LiquidHandlerBackend):
  """Backend for Hamilton Prep instruments using the shared TCP stack.

  Uses HamiltonTCPClient (self.client) for communication and introspection;
  implements LiquidHandlerBackend for liquid handling.
  Interfaces resolved lazily via _require() on first use.

  On-demand introspection: ``await self.client.introspect(path)``.
  """

  # Declare known object paths via InterfaceSpec. deck_config required (key positions, traverse height, deck info).
  _INTERFACES: dict[str, InterfaceSpec] = {
    "mlprep":        InterfaceSpec("MLPrepRoot.MLPrep", True, True),
    "pipettor":      InterfaceSpec("MLPrepRoot.PipettorRoot.Pipettor", True, True),
    "coordinator":   InterfaceSpec("MLPrepRoot.ChannelCoordinator", True, True),
    "deck_config":   InterfaceSpec("MLPrepRoot.MLPrepCalibration.DeckConfiguration", True, True),
    "mph":           InterfaceSpec("MLPrepRoot.MphRoot.MPH", False, True),
    "mlprep_service": InterfaceSpec("MLPrepRoot.MLPrepService", False, True),
  }

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 30.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
    default_traverse_height: Optional[float] = None,
  ):
    super().__init__()
    self.client = HamiltonTCPClient(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
    )
    self._config: Optional[InstrumentConfig] = None
    self._user_traverse_height: Optional[float] = default_traverse_height
    self._resolver = HamiltonInterfaceResolver(self.client, self._INTERFACES)
    self._num_channels: Optional[int] = None
    self._has_mph: Optional[bool] = None

  def _has_interface(self, name: str) -> bool:
    """Return True if the interface was resolved and is present."""
    return self._resolver.has_interface(name)

  def set_default_traverse_height(self, value: float) -> None:
    """Set the default traverse height (mm) used when final_z is not passed to pick_up_tips/drop_tips.

    Use this when the instrument did not report a traverse height at setup, or to override
    the probed value.
    """
    self._user_traverse_height = value

  # ---------------------------------------------------------------------------
  # Setup & interface resolution
  # ---------------------------------------------------------------------------

  async def _require(self, name: str) -> Address:
    """Resolve and return an interface address, lazy on first call. Raises RuntimeError if not found."""
    return await self._resolver.require(name)

  async def get_present_channels(self) -> Optional[Tuple[ChannelIndex, ...]]:
    """Query which channels are present (GetPresentChannels on MLPrepService).

    Maps raw enum values to ChannelIndex: 0=InvalidIndex, 1=FrontChannel,
    2=RearChannel, 3=MPHChannel. Returns None if MLPrepService is unavailable
    or the command fails (caller should use defaults).
    """
    if not self._has_interface("mlprep_service"):
      return None
    try:
      service_addr = await self._require("mlprep_service")
      resp = await self.client.send_command(PrepGetPresentChannels(dest=service_addr))
      if resp is None or not getattr(resp, "channels", None):
        return None
      present = tuple(
        ChannelIndex(v) if v in (0, 1, 2, 3) else ChannelIndex.InvalidIndex
        for v in resp.channels
      )
      return present
    except Exception:
      return None

  async def setup(self, smart: bool = True, force_initialize: bool = False):
    """Set up Prep: connect, discover objects, then conditionally initialize MLPrep.

    Interfaces: .address for MLPrep/Pipettor; depth-2 paths resolved in setup.

    Order:
      1. TCP + Protocol 7/3 init, root discovery, and depth-1 interface discovery (self.client.setup())
      2. Lazy-resolve Pipettor (depth-2) for commands
      3. If force_initialize: always run Initialize(smart=smart).
         Else: query GetIsInitialized; only run Initialize(smart=smart) when not initialized.
      4. Mark setup complete.

    Args:
      smart: When we call Initialize, pass this to the firmware (default True).
      force_initialize: If True, always run Initialize. If False, run Initialize only
        when GetIsInitialized reports not initialized (e.g. reconnect-safe).
    """
    await self.client.setup()

    # Validate discovered root matches this backend
    discovered = self.client.discovered_root_name()
    if discovered != _EXPECTED_ROOT:
      raise RuntimeError(
        f"Expected root '{_EXPECTED_ROOT}' (Prep), but discovered '{discovered}'. Wrong instrument?"
      ) from None

    # Resolve all interfaces (required fail-fast; optional log and continue)
    await self._resolver.run_setup_loop()

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

    self._config = await self._get_hardware_config()
    self._num_channels = self._config.num_channels
    self._has_mph = self._config.has_mph
    logger.info(
      "Hardware config: has_enclosure=%s, safe_speeds=%s, traverse_height=%s, "
      "deck_bounds=%s, deck_sites=%d, waste_sites=%d, num_channels=%s, has_mph=%s",
      self._config.has_enclosure,
      self._config.safe_speeds_enabled,
      self._config.default_traverse_height,
      self._config.deck_bounds,
      len(self._config.deck_sites),
      len(self._config.waste_sites),
      self._config.num_channels,
      self._config.has_mph,
    )

    # await self.ensure_spread()
    self.setup_finished = True

  async def _run_initialize(self, smart: bool):
    """Send PrepInitialize to MLPrep (shared by setup)."""
    await self.client.send_command(
      PrepInitialize(
        dest=await self._require("mlprep"),
        smart=smart,
        tip_drop_params=InitTipDropParameters(
          default_values=True,
          x_position=287.0,
          rolloff_distance=3,
          channel_parameters=[],
        ),
      )
    )

  async def _get_hardware_config(self) -> InstrumentConfig:
    """Aggregate getters: query MLPrep, DeckConfiguration, and MLPrepService for hardware config.

    Includes deck/enclosure, deck sites, waste sites, traverse height, and channel
    configuration (num_channels, has_mph) from GetPresentChannels.
    """
    mlprep = await self._require("mlprep")
    enc_resp = await self.client.send_command(PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await self.client.send_command(PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await self.client.send_command(PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else None

    deck_bounds: Optional[DeckBounds] = None
    deck_sites: Tuple[DeckSiteInfo, ...] = ()
    waste_sites: Tuple[WasteSiteInfo, ...] = ()
    deck_addr = await self._require("deck_config")

    bounds_resp = await self.client.send_command(PrepGetDeckBounds(dest=deck_addr))
    if bounds_resp:
      deck_bounds = DeckBounds(
        min_x=bounds_resp.min_x,
        max_x=bounds_resp.max_x,
        min_y=bounds_resp.min_y,
        max_y=bounds_resp.max_y,
        min_z=bounds_resp.min_z,
        max_z=bounds_resp.max_z,
      )

    sites_resp = await self.client.send_command(PrepGetDeckSiteDefinitions(dest=deck_addr))
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

    waste_resp = await self.client.send_command(PrepGetWasteSiteDefinitions(dest=deck_addr))
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

    # Channel configuration (1 vs 2 dual-channel pipettor, 8MPH) from MLPrepService
    present = await self.get_present_channels()
    if present is not None:
      dual = [c for c in present if c in (ChannelIndex.FrontChannel, ChannelIndex.RearChannel)]
      num_channels = len(dual)
      has_mph = ChannelIndex.MPHChannel in present
    else:
      num_channels = 2
      has_mph = False

    return InstrumentConfig(
      deck_bounds=deck_bounds,
      has_enclosure=has_enclosure,
      safe_speeds_enabled=safe_speeds_enabled,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
      default_traverse_height=default_traverse_height,
      num_channels=num_channels,
      has_mph=has_mph,
    )

  # ---------------------------------------------------------------------------
  # Properties
  # ---------------------------------------------------------------------------

  @property
  def num_channels(self) -> int:
    """Number of independent dual-channel pipettor channels (1 or 2). Set at setup from GetPresentChannels."""
    if self._num_channels is None:
      raise RuntimeError("num_channels not set. Call setup() first.")
    return self._num_channels

  @property
  def has_mph(self) -> bool:
    """True if the 8-channel Multi-Pipetting Head (8MPH) is present. Set at setup from GetPresentChannels."""
    return bool(self._has_mph) if self._has_mph is not None else False

  def _validate_position(self, x: float, y: float, z: float) -> None:
    """Raise ValueError if (x, y, z) is outside deck bounds. No-op if config/bounds not set."""
    # Validation disabled for now: deck bounds logic may not match actual deck coordinates
    # (e.g. well cavity_bottom Z can be below reported min_z). The instrument will reject
    # invalid positions.
    return
    # if self._config is None or self._config.deck_bounds is None:
    #   return
    # b = self._config.deck_bounds
    # if not (b.min_x <= x <= b.max_x and b.min_y <= y <= b.max_y and b.min_z <= z <= b.max_z):
    #   raise ValueError(
    #     f"Position ({x}, {y}, {z}) outside deck bounds "
    #     f"(x=[{b.min_x}, {b.max_x}], y=[{b.min_y}, {b.max_y}], z=[{b.min_z}, {b.max_z}])"
    #   )

  def _resolve_traverse_height(self, final_z: Optional[float]) -> float:
    """Resolve final_z: explicit arg > user-set default > probed value. Raises if none available."""
    if final_z is not None:
      return final_z
    if self._user_traverse_height is not None:
      return self._user_traverse_height
    if self._config is not None and self._config.default_traverse_height is not None:
      return self._config.default_traverse_height
    raise RuntimeError(
      "Default traverse height is required for this operation but could not be determined. "
      "Either pass final_z explicitly to this call, or set it via "
      "PrepBackend(..., default_traverse_height=<mm>) or backend.set_default_traverse_height(<mm>). "
      "If the instrument supports it, the value is also probed during setup(); ensure setup() completed successfully."
    ) from None

  async def is_initialized(self) -> bool:
    """Query whether MLPrep reports as initialized (GetIsInitialized, cmd=2).

    Uses MLPrep method from introspection: GetIsInitialized(()) -> value: I64.
    Requires MLPrep to be discovered (e.g. after self.client.setup() and
    _discover_prep_objects()). Call before or after PrepInitialize to test.
    """
    result = await self.client.send_command(PrepGetIsInitialized(dest=await self._require("mlprep")))
    if result is None:
      return False
    return bool(result.value)

  async def get_tip_and_needle_definitions(self) -> Tuple[TipDefinition, ...]:
    """Return tip/needle definitions registered on the instrument (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self.client.send_command(
      PrepGetTipAndNeedleDefinitions(dest=await self._require("mlprep"))
    )
    if result is None or not getattr(result, "definitions", None):
      return ()
    return tuple(result.definitions)

  async def is_parked(self) -> bool:
    """Query whether MLPrep is parked (IsParked, cmd=34)."""
    result = await self.client.send_command(PrepIsParked(dest=await self._require("mlprep")))
    if result is None:
      return False
    return bool(result.value)

  async def is_spread(self) -> bool:
    """Query whether channels are spread (IsSpread, cmd=35). Pipettor commands typically require spread state."""
    result = await self.client.send_command(PrepIsSpread(dest=await self._require("mlprep")))
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
        If None, uses the user-set value (constructor or set_default_traverse_height) or the
        value probed from the instrument at setup. Raises RuntimeError if none is available.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      enable_tadm: Enable tip-adjust during pickup.
      dispenser_volume: Dispenser volume for TADM (if enabled).
      dispenser_speed: Dispenser speed for TADM (if enabled).
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    resolved_final_z = self._resolve_traverse_height(final_z)

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[TipPositionParameters] = []
    for ch in range(self.num_channels):
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

    await self.client.send_command(
      PrepPickUpTips(
        dest=await self._require("pipettor"),
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
    bottom is at z_seek - (total_tip_length - fitting_depth)). z_position uses
    fitting depth so the tip bottom lands at the spot surface; default z_seek =
    z_position + 10mm so the tip bottom stays above adjacent tips in the rack.

    Args:
      final_z: Traverse/safe height (mm) for the move and Z position after command.
        If None, uses the user-set value (constructor or set_default_traverse_height) or the
        value probed from the instrument at setup. Raises RuntimeError if none is available.
      seek_speed: Speed (mm/s) for the seek/approach phase.
      z_seek_offset: Additive mm on top of the geometry-based default. None = 0
        (use default only). Use to raise or lower the approach height if needed.
      drop_type: How the tip is released (FixedHeight, Stall, or CLLDSeek).
      tip_roll_off_distance: Roll-off distance (mm) for tip release.
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    resolved_final_z = self._resolve_traverse_height(final_z)

    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}
    tip_positions: List[TipDropParameters] = []
    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "t")
      tip = op.tip
      # Waste positions (Trash with category waste_position): use drop height directly
      # (z_position = loc.z) instead of loc.z + tip_length, and z_seek just above.
      is_waste = (
        isinstance(op.resource, Trash)
        and getattr(op.resource, "category", "") == "waste_position"
      )
      if is_waste:
        z_position = loc.z
        z_seek = loc.z + 10.0 + (z_seek_offset or 0.0)
        params = TipDropParameters(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          x_position=loc.x,
          y_position=loc.y,
          z_position=z_position,
          z_seek=z_seek,
          drop_type=drop_type,
        )
      else:
        params = TipDropParameters.for_op(
          _CHANNEL_INDEX[ch], loc, tip,
          z_seek_offset=z_seek_offset,
          drop_type=drop_type,
        )
      self._validate_position(loc.x, loc.y, params.z_position)
      tip_positions.append(params)

    await self.client.send_command(
      PrepDropTips(
        dest=await self._require("pipettor"),
        tip_positions=tip_positions,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_roll_off_distance=tip_roll_off_distance,
      )
    )

  # ---------------------------------------------------------------------------
  # MPH head tip operations
  # ---------------------------------------------------------------------------

  async def pick_up_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    tip_mask: int = 0xFF,
    final_z: Optional[float] = None,
    seek_speed: float = 15.0,
    z_seek_offset: Optional[float] = None,
    enable_tadm: bool = False,
    dispenser_volume: float = 0.0,
    dispenser_speed: float = 250.0,
  ) -> None:
    """Pick up tips with the MPH (multi-probe) head.

    Routes to MLPrepRoot.MphRoot.MPH (PickupTips, iface=1 id=9). The MPH
    takes a single reference position (type_57 = single struct) rather than
    a per-channel list (type_61). All 8 probes move as one unit; tip_mask
    selects which channels engage (default 0xFF = all 8).

    The first TipSpot is used as the reference position. For a full column
    pickup, pass tip_rack["A1:H1"] — only the first spot's (x,y,z) is sent,
    all 8 probes engage via tip_mask.

    Args:
      tip_spot: A single TipSpot or a list. The first spot is used as the
        reference position for all probes.
      tip_mask: 8-bit bitmask of active MPH channels (bit 0 = channel 0,
        bit 7 = channel 7). Default 0xFF picks up with all 8 channels.
      final_z: Traverse/safe height (mm) after command. If None, uses the
        probed or user-set default traverse height.
      seek_speed: Speed (mm/s) for the Z approach phase.
      z_seek_offset: Additive mm offset on top of the geometry-based seek Z
        (tip.fitting_depth + 5 mm). None = 0.
      enable_tadm: Enable tip-attachment detection (TADM) during pickup.
      dispenser_volume: Dispenser volume for TADM (ignored when False).
      dispenser_speed: Dispenser speed for TADM (ignored when False).
    """
    if not self.has_mph:
      raise RuntimeError("Instrument does not have an 8MPH head. Cannot use pick_up_tips_mph.")
    if isinstance(tip_spot, list):
      spots = tip_spot
    else:
      spots = [tip_spot]
    if not spots:
      raise ValueError("pick_up_tips_mph: tip_spot list is empty")
    resolved_final_z = self._resolve_traverse_height(final_z)

    ref_spot = spots[0]
    tip = ref_spot.get_tip()
    loc = ref_spot.get_absolute_location("c", "c", "t")
    tip_parameters = TipPositionParameters.for_op(
      ChannelIndex.MPHChannel, loc, tip, z_seek_offset=z_seek_offset
    )
    self._validate_position(loc.x, loc.y, tip_parameters.z_position)

    tip_definition = TipPickupParameters(
      default_values=False,
      volume=tip.maximal_volume,
      length=tip.total_tip_length - tip.fitting_depth,
      tip_type=TipTypes.StandardVolume,
      has_filter=tip.has_filter,
      is_needle=False,
      is_tool=False,
    )

    await self.client.send_command(
      MphPickupTips(
        dest=await self._require("mph"),
        tip_parameters=tip_parameters,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_definition=tip_definition,
        enable_tadm=enable_tadm,
        dispenser_volume=dispenser_volume,
        dispenser_speed=dispenser_speed,
        tip_mask=tip_mask,
      )
    )

  async def drop_tips_mph(
    self,
    tip_spot: Union[TipSpot, List[TipSpot]],
    final_z: Optional[float] = None,
    seek_speed: float = 30.0,
    z_seek_offset: Optional[float] = None,
    drop_type: TipDropType = TipDropType.FixedHeight,
    tip_roll_off_distance: float = 0.0,
  ) -> None:
    """Drop tips held by the MPH head.

    Routes to MLPrepRoot.MphRoot.MPH (DropTips, iface=1 id=12). The MPH
    takes a single reference position (type_57 = single struct); all probes
    drop together at the same location.

    Args:
      tip_spot: Target drop position. The first spot is used as the reference
        position for all probes.
      final_z: Traverse/safe height (mm) after command. If None, uses the
        probed or user-set default traverse height.
      seek_speed: Speed (mm/s) for the Z seek/approach phase.
      z_seek_offset: Additive mm offset on top of the geometry-based seek Z.
        None = 0 (default seeks tip_bottom + total_tip_length + 10 mm).
      drop_type: How tips are released (FixedHeight, Stall, or CLLDSeek).
      tip_roll_off_distance: Roll-off distance (mm) for tip release.
    """
    if not self.has_mph:
      raise RuntimeError("Instrument does not have an 8MPH head. Cannot use drop_tips_mph.")
    if isinstance(tip_spot, list):
      spots = tip_spot
    else:
      spots = [tip_spot]
    if not spots:
      raise ValueError("drop_tips_mph: tip_spot list is empty")
    resolved_final_z = self._resolve_traverse_height(final_z)

    ref_spot = spots[0]
    tip = ref_spot.get_tip()
    loc = ref_spot.get_absolute_location("c", "c", "t")
    drop_parameters = TipDropParameters.for_op(
      ChannelIndex.MPHChannel, loc, tip,
      z_seek_offset=z_seek_offset,
      drop_type=drop_type,
    )
    self._validate_position(loc.x, loc.y, drop_parameters.z_position)

    await self.client.send_command(
      MphDropTips(
        dest=await self._require("mph"),
        drop_parameters=drop_parameters,
        final_z=resolved_final_z,
        seek_speed=seek_speed,
        tip_roll_off_distance=tip_roll_off_distance,
      )
    )

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    z_final: Optional[float] = None,
    z_fluid: Optional[float] = None,
    z_air: Optional[float] = None,
    settling_time: float = 1.0,
    transport_air_volume: float = 0.0,
    z_liquid_exit_speed: float = 10.0,
    z_minimum: Optional[float] = None,
    z_bottom_search_offset: float = 2.0,
    monitoring_mode: MonitoringMode = MonitoringMode.MONITORING,
    use_lld: bool = False,
    lld: Optional[LldParameters] = None,
    p_lld: Optional[PLldParameters] = None,
    c_lld: Optional[CLldParameters] = None,
    tadm: Optional[TadmParameters] = None,
    container_segments: Optional[List[List[SegmentDescriptor]]] = None, # TODO: Doesn't work with No LLD
    auto_container_geometry: bool = False,
  ):
    """Aspirate using v2 commands, dispatching to the appropriate variant.

    Selects the command variant based on ``use_lld`` / ``lld`` (LLD on/off) and
    ``monitoring_mode`` (Monitoring vs TADM).  When z_minimum, z_final, z_fluid,
    or z_air are None, they are derived from well geometry (absolute coordinates,
    STAR-aligned): z_minimum = well bottom, z_fluid = well bottom + op.liquid_height,
    z_air = above labware + 2 mm, z_final = Prep traverse height minus fitted tip length (when None).

    Args:
      z_final: Z after the move (retract height). If None, uses bare-channel traverse height
        minus max fitted tip length across channels (so the instrument ends at traverse height).
      z_fluid: Liquid surface Z when not using LLD. If None, derived as well_bottom + op.liquid_height.
      z_air: Z in air (above liquid). If None, derived as top of well + 2 mm.
      z_minimum: Minimum Z (well floor). If None, derived as well bottom.
      monitoring_mode: Select TADM or Monitoring (default: Monitoring).
      use_lld: Enable LLD aspirate variant.  Also activated if ``lld`` is set.
      lld: LLD seek parameters. When None and use_lld=True, built from labware geometry
        (z_seek = top of well; z_submerge/z_out_of_liquid = relative offsets).
      p_lld: Pressure LLD parameters (LLD variants only).
      c_lld: Capacitive LLD parameters (LLD variants only).
      tadm: TADM parameters (TADM variants only).  Firmware defaults when None.
      container_segments: Per-channel SegmentDescriptor lists for liquid following.
        If None and auto_container_geometry=True, derived from well geometry.
      auto_container_geometry: Automatically build container segments from the
        well's cross-section geometry.  Pass False to use empty segments
        (firmware falls back to the CommonParameters cone model).

    Example::

      await backend.aspirate(ops, [0], z_final=95.0, settling_time=2.0)
      await backend.aspirate(ops, [0], use_lld=True)
      await backend.aspirate(ops, [0], monitoring_mode=MonitoringMode.TADM)
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    effective_lld = use_lld or (lld is not None)
    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    # Resolve z_final: when None, use bare-channel traverse height minus fitted tip length
    # so the instrument (which may add tip length) ends with channel at traverse height.
    raw_traverse = self._resolve_traverse_height(z_final)
    if z_final is None and indexed_ops:
      max_fitted_length = max(
        op.tip.total_tip_length - op.tip.fitting_depth for op in indexed_ops.values()
      )
      resolved_z_final = raw_traverse - max_fitted_length
    else:
      resolved_z_final = raw_traverse

    # Build per-channel segment lists.
    ch_segments: dict[int, list[SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    _p_lld = p_lld or PLldParameters.default()
    _c_lld = c_lld or CLldParameters.default()
    _tadm = tadm or TadmParameters.default()

    params_lld_mon: List[AspirateParametersLldAndMonitoring2] = []
    params_lld_tadm: List[AspirateParametersLldAndTadm2] = []
    params_nolld_mon: List[AspirateParametersNoLldAndMonitoring2] = []
    params_nolld_tadm: List[AspirateParametersNoLldAndTadm2] = []

    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      self._validate_position(loc.x, loc.y, loc.z)
      radius = _effective_radius(op.resource)
      asp = AspirateParameters.for_op(loc, op)
      segs = ch_segments[ch]

      well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z = _absolute_z_from_well(op)
      z_minimum_ch = z_minimum if z_minimum is not None else well_bottom_z
      z_final_ch = resolved_z_final
      z_fluid_ch = z_fluid if z_fluid is not None else liquid_surface_z
      z_air_ch = z_air if z_air is not None else z_air_z

      if effective_lld and lld is None:
        _lld = LldParameters(
          default_values=False,
          z_seek=top_of_well_z,
          z_seek_speed=0.0,
          z_submerge=2.0,
          z_out_of_liquid=0.0,
        )
      else:
        _lld = lld or LldParameters.default()

      common = CommonParameters.for_op(
        op.volume, radius,
        flow_rate=op.flow_rate,
        z_minimum=z_minimum_ch,
        z_final=z_final_ch,
        z_liquid_exit_speed=z_liquid_exit_speed,
        transport_air_volume=transport_air_volume,
        settling_time=settling_time,
      )
      no_lld = NoLldParameters.for_fixed_z(z_fluid_ch, z_air_ch, z_bottom_search_offset=z_bottom_search_offset)

      if effective_lld and monitoring_mode == MonitoringMode.TADM:
        params_lld_tadm.append(AspirateParametersLldAndTadm2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          aspirate=asp,
          container_description=segs,
          common=common,
          lld=_lld, p_lld=_p_lld, c_lld=_c_lld,
          mix=MixParameters.default(),
          tadm=_tadm,
          adc=AdcParameters.default(),
        ))
      elif effective_lld:
        params_lld_mon.append(AspirateParametersLldAndMonitoring2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          aspirate=asp,
          container_description=segs,
          common=common,
          lld=_lld, p_lld=_p_lld, c_lld=_c_lld,
          mix=MixParameters.default(),
          aspirate_monitoring=AspirateMonitoringParameters.default(),
          adc=AdcParameters.default(),
        ))
      elif monitoring_mode == MonitoringMode.TADM:
        params_nolld_tadm.append(AspirateParametersNoLldAndTadm2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          aspirate=asp,
          container_description=segs,
          common=common,
          no_lld=no_lld,
          mix=MixParameters.default(),
          adc=AdcParameters.default(),
          tadm=_tadm,
        ))
      else:
        params_nolld_mon.append(AspirateParametersNoLldAndMonitoring2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          aspirate=asp,
          container_description=segs,
          common=common,
          no_lld=no_lld,
          mix=MixParameters.default(),
          adc=AdcParameters.default(),
          aspirate_monitoring=AspirateMonitoringParameters.default(),
        ))

    dest = await self._require("pipettor")
    if effective_lld and monitoring_mode == MonitoringMode.TADM:
      await self.client.send_command(PrepAspirateWithLldTadmV2(dest=dest, aspirate_parameters=params_lld_tadm))
    elif effective_lld:
      await self.client.send_command(PrepAspirateWithLldV2(dest=dest, aspirate_parameters=params_lld_mon))
    elif monitoring_mode == MonitoringMode.TADM:
      await self.client.send_command(PrepAspirateTadmV2(dest=dest, aspirate_parameters=params_nolld_tadm))
    else:
      await self.client.send_command(PrepAspirateNoLldMonitoringV2(dest=dest, aspirate_parameters=params_nolld_mon))

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    final_z: Optional[float] = None,
    z_fluid: Optional[float] = None,
    z_air: Optional[float] = None,
    settling_time: float = 0.0,
    transport_air_volume: float = 0.0,
    z_liquid_exit_speed: float = 10.0,
    z_minimum: Optional[float] = None,
    z_bottom_search_offset: float = 2.0,
    use_lld: bool = False,
    lld: Optional[LldParameters] = None,
    c_lld: Optional[CLldParameters] = None,
    container_segments: Optional[List[List[SegmentDescriptor]]] = None,
    auto_container_geometry: bool = False, # TODO: Doesn't work with no LLD
  ):
    """Dispense using v2 commands, dispatching to NoLLD or LLD variant.

    When final_z, z_minimum, z_fluid, or z_air are None, they are derived from well
    geometry (absolute coordinates, STAR-aligned): z_minimum = well bottom,
    z_fluid = well bottom + op.liquid_height,     z_air = above labware + 2 mm,
    final_z = Prep traverse height minus fitted tip length (when None).

    Args:
      final_z: Z after the move. If None, uses bare-channel traverse height minus max fitted tip length.
      z_fluid: Liquid surface Z when not using LLD. If None, derived as well_bottom + op.liquid_height.
      z_air: Z in air (above liquid). If None, derived as top of well + 2 mm.
      z_minimum: Minimum Z (well floor). If None, derived as well bottom.
      use_lld: Enable LLD dispense variant.  Also activated if ``lld`` is set.
      lld: LLD seek parameters. When None and use_lld=True, built from labware geometry.
      c_lld: Capacitive LLD parameters (LLD variant only).
      container_segments: Per-channel SegmentDescriptor lists for liquid following.
      auto_container_geometry: Automatically build container segments from well geometry.

    Example::

      await backend.dispense(ops, [0], final_z=95.0, settling_time=0.5)
      await backend.dispense(ops, [0], use_lld=True)
    """
    assert len(ops) == len(use_channels)
    if use_channels:
      assert max(use_channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )

    effective_lld = use_lld or (lld is not None)
    indexed_ops = {ch: op for ch, op in zip(use_channels, ops)}

    # Resolve z_final: when None, use bare-channel traverse height minus fitted tip length.
    raw_traverse = self._resolve_traverse_height(final_z)
    if final_z is None and indexed_ops:
      max_fitted_length = max(
        op.tip.total_tip_length - op.tip.fitting_depth for op in indexed_ops.values()
      )
      resolved_z_final = raw_traverse - max_fitted_length
    else:
      resolved_z_final = raw_traverse

    ch_segments: dict[int, list[SegmentDescriptor]] = {}
    for i, ch in enumerate(use_channels):
      if container_segments is not None and i < len(container_segments):
        ch_segments[ch] = container_segments[i]
      elif auto_container_geometry:
        ch_segments[ch] = _build_container_segments(indexed_ops[ch].resource)
      else:
        ch_segments[ch] = []

    _c_lld = c_lld or CLldParameters.default()

    params_nolld: List[DispenseParametersNoLld2] = []
    params_lld: List[DispenseParametersLld2] = []

    for ch in range(self.num_channels):
      if ch not in indexed_ops:
        continue
      op = indexed_ops[ch]
      loc = op.resource.get_absolute_location("c", "c", "cavity_bottom")
      self._validate_position(loc.x, loc.y, loc.z)
      radius = _effective_radius(op.resource)
      disp = DispenseParameters.for_op(loc)
      segs = ch_segments[ch]

      well_bottom_z, liquid_surface_z, top_of_well_z, z_air_z = _absolute_z_from_well(op)
      z_minimum_ch = z_minimum if z_minimum is not None else well_bottom_z
      z_final_ch = resolved_z_final
      z_fluid_ch = z_fluid if z_fluid is not None else liquid_surface_z
      z_air_ch = z_air if z_air is not None else z_air_z

      if effective_lld and lld is None:
        _lld = LldParameters(
          default_values=False,
          z_seek=top_of_well_z,
          z_seek_speed=0.0,
          z_submerge=2.0,
          z_out_of_liquid=0.0,
        )
      else:
        _lld = lld or LldParameters.default()

      common = CommonParameters.for_op(
        op.volume, radius,
        flow_rate=op.flow_rate,
        z_minimum=z_minimum_ch,
        z_final=z_final_ch,
        z_liquid_exit_speed=z_liquid_exit_speed,
        transport_air_volume=transport_air_volume,
        settling_time=settling_time,
      )

      if effective_lld:
        params_lld.append(DispenseParametersLld2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          dispense=disp,
          container_description=segs,
          common=common,
          lld=_lld,
          c_lld=_c_lld,
          mix=MixParameters.default(),
          adc=AdcParameters.default(),
          tadm=TadmParameters.default(),
        ))
      else:
        params_nolld.append(DispenseParametersNoLld2(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          dispense=disp,
          container_description=segs,
          common=common,
          no_lld=NoLldParameters.for_fixed_z(z_fluid_ch, z_air_ch, z_bottom_search_offset=z_bottom_search_offset),
          mix=MixParameters.default(),
          adc=AdcParameters.default(),
          tadm=TadmParameters.default(),
        ))

    dest = await self._require("pipettor")
    if effective_lld:
      await self.client.send_command(PrepDispenseWithLldV2(dest=dest, dispense_parameters=params_lld))
    else:
      await self.client.send_command(PrepDispenseNoLldV2(dest=dest, dispense_parameters=params_nolld))

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
    """Check if the tip can be picked up by the specified channel.

    Uses the same logic as Nimbus/STAR: only Hamilton tips, no XL tips,
    and channel index must be valid.
    """
    if not isinstance(tip, HamiltonTip):
      return False
    if tip.tip_size in {TipSize.XL}:
      return False
    if self._num_channels is not None and channel_idx >= self._num_channels:
      return False
    return True

  # ---------------------------------------------------------------------------
  # MLPrep convenience methods
  # ---------------------------------------------------------------------------

  async def park(self) -> None:
    """Park the instrument."""
    await self.client.send_command(PrepPark(dest=await self._require("mlprep")))

  async def spread(self) -> None:
    """Spread channels."""
    await self.client.send_command(PrepSpread(dest=await self._require("mlprep")))

  async def method_begin(self, automatic_pause: bool = False) -> None:
    """Signal the start of a liquid-handling method."""
    await self.client.send_command(
      PrepMethodBegin(
        dest=await self._require("mlprep"),
        automatic_pause=automatic_pause,
      )
    )

  async def method_end(self) -> None:
    """Signal the end of a liquid-handling method."""
    await self.client.send_command(PrepMethodEnd(dest=await self._require("mlprep")))

  async def method_abort(self) -> None:
    """Abort the current method."""
    await self.client.send_command(PrepMethodAbort(dest=await self._require("mlprep")))

  async def get_deck_light(self) -> Tuple[int, int, int, int]:
      """Get the current deck LED colour (white, red, green, blue)."""
      result = await self.client.send_command(
        PrepGetDeckLight(dest=await self._require("mlprep"))
      )
      if result is None:
        raise ValueError("No response from GetDeckLight.")
      return (result.white, result.red, result.green, result.blue)

  async def set_deck_light(
    self, white: int, red: int, green: int, blue: int
  ) -> None:
    """Set the deck LED colour."""
    await self.client.send_command(
      PrepSetDeckLight(
        dest=await self._require("mlprep"),
        white=white,
        red=red,
        green=green,
        blue=blue,
      )
    )

  async def disco_mode(self) -> None:
    """Easter egg: cycle deck lights then restore previous state."""
    white, red, green, blue = await self.get_deck_light()
    try:
      for _ in range(69):
        await self.set_deck_light(
          white=random.randint(1, 255),
          red=random.randint(1, 255),
          green=random.randint(1, 255),
          blue=random.randint(1, 255),
        )
        await asyncio.sleep(0.1)
    finally:
      await self.set_deck_light(white=white, red=red, green=green, blue=blue)

  # ---------------------------------------------------------------------------
  # Pipettor convenience methods
  # ---------------------------------------------------------------------------

  async def move_to_position(
    self,
    x: float,
    y: Union[float, List[float]],
    z: Union[float, List[float]],
    use_channels: Union[int, List[int]] = 0,
    *,
    via_lane: bool = False,
  ) -> None:
    """Move pipettor to position (cmd=26 or 27). Same (x,y,z) params; via_lane selects cmd 27.

    use_channels defaults to 0 (rear channel). Pass a single channel index (int) or
    a list of indices; for all channels use list(range(self.num_channels)). For a
    single channel, y and z may be scalars instead of lists.
    """
    channels = [use_channels] if isinstance(use_channels, int) else list(use_channels)
    channels = sorted(channels)
    if channels:
      assert max(channels) < self.num_channels, (
        f"use_channels index out of range (valid: 0..{self.num_channels - 1})"
      )
    if isinstance(y, list):
      assert len(y) == len(channels), "len(y) must equal len(use_channels)"
    if isinstance(z, list):
      assert len(z) == len(channels), "len(z) must equal len(use_channels)"

    axis_parameters: List[ChannelYZMoveParameters] = []
    for i, ch in enumerate(channels):
      y_i = y if isinstance(y, (int, float)) else y[i]
      z_i = z if isinstance(z, (int, float)) else z[i]
      self._validate_position(x, y_i, z_i)
      axis_parameters.append(
        ChannelYZMoveParameters(
          default_values=False,
          channel=_CHANNEL_INDEX[ch],
          y_position=y_i,
          z_position=z_i,
        )
      )
    move_parameters = GantryMoveXYZParameters(
      default_values=False,
      gantry_x_position=x,
      axis_parameters=axis_parameters,
    )

    if via_lane:
      await self.client.send_command(
        PrepMoveToPositionViaLane(
          dest=await self._require("pipettor"),
          move_parameters=move_parameters,
        )
      )
    else:
      await self.client.send_command(
        PrepMoveToPosition(
          dest=await self._require("pipettor"),
          move_parameters=move_parameters,
        )
      )

  async def stop(self) -> None:
    await self.client.stop()
    self.setup_finished = False

  def serialize(self) -> dict:
    return {**super().serialize(), **self.client.serialize()}
