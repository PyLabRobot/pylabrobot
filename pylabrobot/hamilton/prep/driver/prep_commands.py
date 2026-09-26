"""Prep command dataclasses and wire-type parameter structs.

Pure data definitions for the Hamilton Prep protocol — enums, hardware config,
wire-type annotated parameter structs, and PrepCommand subclasses. No business
logic; used by Prep channels / head8 peers for command construction and serialization.

Moved from prep_backend.py to separate protocol contracts from domain logic.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated, ClassVar, Optional, Set, Tuple, TypeVar

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.messages import HoiParams, HoiParamsParser, parse_into_struct
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import HamiltonProtocol, Hoi2Action
from pylabrobot.hamilton.transport.tcp.wire_types import (
  F32,
  F64,
  I16,
  U16,
  U32,
  BoolArray,
  EnumArray,
  HcResultEntry,
  I16Array,
  PaddedBool,
  PaddedU8,
  Str,
  Struct,
  StructArray,
  U8Array,
  U32Array,
)
from pylabrobot.hamilton.transport.tcp.wire_types import (
  Enum as WEnum,
)

# Firmware object paths the driver and its features address by name.
PREP_ROOT_NAME = "MLPrepRoot"
MLPREP_OBJECT_PATH = "MLPrepRoot.MLPrep"
PIPETTOR_OBJECT_PATH = "MLPrepRoot.PipettorRoot.Pipettor"
MPH_OBJECT_PATH = "MLPrepRoot.MphRoot.MPH"
MLPREP_SERVICE_OBJECT_PATH = "MLPrepRoot.MLPrepService"
DECK_CONFIGURATION_OBJECT_PATH = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"
MLPREP_CPU_OBJECT_PATH = "MLPrepRoot.MLPrepCpu"
MODULE_INFORMATION_OBJECT_PATH = "MLPrepRoot.PipettorRoot.ModuleInformation"
CHANNEL_XYZ_COORDINATOR_OBJECT_PATH = "MLPrepRoot.ChannelCoordinator.ChannelXYZCoordinator"

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


@dataclass
class HoiDateTime:
  """Hamilton network/built-in dateTime struct (source_id=3, ref_id=3).

  Wire format: 7 DataFragments — year(U16), month(PaddedU8), day(PaddedU8),
  hour(PaddedU8), minute(PaddedU8), second(PaddedU8), millisecond(U16).

  Used by EndCalibration and SetChannelHardwareConfiguration to timestamp
  calibration data. Construct from ``datetime.datetime`` via ``from_datetime()``.
  """

  year: U16
  month: PaddedU8
  day: PaddedU8
  hour: PaddedU8
  minute: PaddedU8
  second: PaddedU8
  millisecond: U16

  @classmethod
  def from_datetime(cls, dt: datetime.datetime) -> "HoiDateTime":
    """Create from a Python datetime (microseconds truncated to milliseconds)."""
    return cls(
      year=dt.year,
      month=dt.month,
      day=dt.day,
      hour=dt.hour,
      minute=dt.minute,
      second=dt.second,
      millisecond=dt.microsecond // 1000,
    )

  @classmethod
  def now(cls) -> "HoiDateTime":
    """Create from the current local time."""
    return cls.from_datetime(datetime.datetime.now())

  def to_datetime(self) -> datetime.datetime:
    """Convert to a Python datetime."""
    return datetime.datetime(
      self.year,
      self.month,
      self.day,
      self.hour,
      self.minute,
      self.second,
      self.millisecond * 1000,
    )

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.year, U16)
      .add(self.month, PaddedU8)
      .add(self.day, PaddedU8)
      .add(self.hour, PaddedU8)
      .add(self.minute, PaddedU8)
      .add(self.second, PaddedU8)
      .add(self.millisecond, U16)
    )


@dataclass(frozen=True)
class CalibrationSiteInfo:
  """A calibration site from DeckConfiguration.GetCalibrationSiteDefinitions."""

  id: int
  left_bottom_front_x: float
  left_bottom_front_y: float
  left_bottom_front_z: float
  length: float
  width: float
  height: float
  post: bool


@dataclass(frozen=True)
class ChannelHardwareConfigInfo:
  """Per-channel hardware config from MLPrepCalibration.GetChannelHardwareConfiguration."""

  channel: int  # ChannelIndex enum value
  hardware: int  # Hardware type enum value


@dataclass(frozen=True)
class ChannelCalibrationValuesInfo:
  """Per-channel calibration values from MLPrepCalibration.GetCalibrationValues."""

  index: int  # ChannelIndex enum value
  y_offset: float
  z_offset: float
  squeeze_position: int
  z_touchoff: int
  pressure_shift: int
  pressure_monitoring_shift: int
  dispenser_return_distance: float
  z_tip_height: float
  core_ii: bool

  def to_pretty_string(self) -> str:
    """Return a stable one-line representation for logging/reporting."""
    return (
      f"index={self.index}, y_offset={self.y_offset}, z_offset={self.z_offset}, "
      f"squeeze_position={self.squeeze_position}, z_touchoff={self.z_touchoff}, "
      f"pressure_shift={self.pressure_shift}, "
      f"pressure_monitoring_shift={self.pressure_monitoring_shift}, "
      f"dispenser_return_distance={self.dispenser_return_distance}, "
      f"z_tip_height={self.z_tip_height}, core_ii={self.core_ii}"
    )


@dataclass(frozen=True)
class CalibrationValues:
  """Full calibration values from MLPrepCalibration.GetCalibrationValues."""

  independent_offset_x: float
  mph_offset_x: float
  channel_values: Tuple["ChannelCalibrationValuesInfo", ...]

  def to_pretty_string(self, sort_by_index: bool = True) -> str:
    """Return deterministic, human-readable calibration output."""
    channels = self.channel_values
    if sort_by_index:
      channels = tuple(sorted(channels, key=lambda cv: cv.index))

    lines = [
      f"Independent offset X: {self.independent_offset_x}",
      f"MPH offset X: {self.mph_offset_x}",
      "Per-channel calibration values:",
    ]
    for cv in channels:
      lines.append(f"  {cv.to_pretty_string()}")
    return "\n".join(lines)

  def __str__(self) -> str:
    return self.to_pretty_string()


@dataclass(frozen=True)
class CalibrationFieldChange:
  field: str
  old: object
  new: object


@dataclass(frozen=True)
class ChannelCalibrationDiff:
  index: int
  state: str  # "added" | "removed" | "changed"
  changes: Tuple[CalibrationFieldChange, ...]
  old: Optional[ChannelCalibrationValuesInfo]
  new: Optional[ChannelCalibrationValuesInfo]


@dataclass(frozen=True)
class CalibrationValuesDiff:
  top_level_changes: Tuple[CalibrationFieldChange, ...]
  channel_diffs: Tuple[ChannelCalibrationDiff, ...]

  @property
  def has_changes(self) -> bool:
    return bool(self.top_level_changes or self.channel_diffs)


def _calibration_value_equal(old: object, new: object, float_tol: float) -> bool:
  if isinstance(old, float) and isinstance(new, float):
    return math.isclose(old, new, rel_tol=0.0, abs_tol=float_tol)
  return old == new


def diff_calibration_values(
  old: CalibrationValues,
  new: CalibrationValues,
  float_tol: float = 1e-6,
) -> CalibrationValuesDiff:
  """Return structured diff between two calibration snapshots."""

  top_level_changes = []
  for field_name, old_value, new_value in (
    ("independent_offset_x", old.independent_offset_x, new.independent_offset_x),
    ("mph_offset_x", old.mph_offset_x, new.mph_offset_x),
  ):
    if not _calibration_value_equal(old_value, new_value, float_tol=float_tol):
      top_level_changes.append(
        CalibrationFieldChange(field=field_name, old=old_value, new=new_value)
      )

  old_channels = {cv.index: cv for cv in old.channel_values}
  new_channels = {cv.index: cv for cv in new.channel_values}
  channel_diffs = []
  for idx in sorted(set(old_channels) | set(new_channels)):
    old_cv = old_channels.get(idx)
    new_cv = new_channels.get(idx)
    if old_cv is None and new_cv is not None:
      channel_diffs.append(
        ChannelCalibrationDiff(
          index=idx,
          state="added",
          changes=(),
          old=None,
          new=new_cv,
        )
      )
      continue
    if old_cv is not None and new_cv is None:
      channel_diffs.append(
        ChannelCalibrationDiff(
          index=idx,
          state="removed",
          changes=(),
          old=old_cv,
          new=None,
        )
      )
      continue
    if old_cv is None or new_cv is None:
      raise RuntimeError("a channel present on both sides has no value on one of them")

    field_changes = []
    for field_name, old_value, new_value in (
      ("index", old_cv.index, new_cv.index),
      ("y_offset", old_cv.y_offset, new_cv.y_offset),
      ("z_offset", old_cv.z_offset, new_cv.z_offset),
      ("squeeze_position", old_cv.squeeze_position, new_cv.squeeze_position),
      ("z_touchoff", old_cv.z_touchoff, new_cv.z_touchoff),
      ("pressure_shift", old_cv.pressure_shift, new_cv.pressure_shift),
      (
        "pressure_monitoring_shift",
        old_cv.pressure_monitoring_shift,
        new_cv.pressure_monitoring_shift,
      ),
      (
        "dispenser_return_distance",
        old_cv.dispenser_return_distance,
        new_cv.dispenser_return_distance,
      ),
      ("z_tip_height", old_cv.z_tip_height, new_cv.z_tip_height),
      ("core_ii", old_cv.core_ii, new_cv.core_ii),
    ):
      if not _calibration_value_equal(old_value, new_value, float_tol=float_tol):
        field_changes.append(CalibrationFieldChange(field=field_name, old=old_value, new=new_value))
    if field_changes:
      channel_diffs.append(
        ChannelCalibrationDiff(
          index=idx,
          state="changed",
          changes=tuple(field_changes),
          old=old_cv,
          new=new_cv,
        )
      )

  return CalibrationValuesDiff(
    top_level_changes=tuple(top_level_changes),
    channel_diffs=tuple(channel_diffs),
  )


def format_calibration_diff(diff: CalibrationValuesDiff) -> str:
  """Return a concise, human-readable diff summary."""
  if not diff.has_changes:
    return "No calibration differences."

  lines = ["Calibration differences:"]
  if diff.top_level_changes:
    lines.append("Top-level:")
    for change in diff.top_level_changes:
      lines.append(f"  {change.field}: {change.old} -> {change.new}")

  if diff.channel_diffs:
    lines.append("Per-channel:")
    for channel_diff in diff.channel_diffs:
      if channel_diff.state == "added":
        if channel_diff.new is None:
          raise RuntimeError("an added channel has no new value")
        lines.append(f"  index={channel_diff.index}: added ({channel_diff.new.to_pretty_string()})")
        continue
      if channel_diff.state == "removed":
        if channel_diff.old is None:
          raise RuntimeError("a removed channel has no old value")
        lines.append(
          f"  index={channel_diff.index}: removed ({channel_diff.old.to_pretty_string()})"
        )
        continue
      changed_fields = ", ".join(
        f"{change.field}: {change.old} -> {change.new}" for change in channel_diff.changes
      )
      lines.append(f"  index={channel_diff.index}: {changed_fields}")

  return "\n".join(lines)


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.x_start, F32)
      .add(self.y_start, F32)
      .add(self.z_start, F32)
      .add(self.distance, F32)
      .add(self.expected_position, F32)
    )


@dataclass
class XYZCoord:
  default_values: PaddedBool
  x_position: F32
  y_position: F32
  z_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
    )


@dataclass
class XYCoord:
  default_values: PaddedBool
  x_position: F32
  y_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
    )


@dataclass
class ChannelYPositionParameters:
  default_values: PaddedBool
  channel: WEnum
  y_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool).add(self.channel, WEnum).add(self.y_position, F32)
    )


@dataclass
class ChannelZPositionParameters:
  default_values: PaddedBool
  channel: WEnum
  z_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool).add(self.channel, WEnum).add(self.z_position, F32)
    )


@dataclass
class ChannelYZMoveParameters:
  default_values: PaddedBool
  channel: WEnum
  y_position: F32
  z_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
    )


@dataclass
class GantryMoveXYZParameters:
  default_values: PaddedBool
  gantry_x_position: F32
  axis_parameters: Annotated[list[ChannelYZMoveParameters], StructArray()]

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.gantry_x_position, F32)
      .add(self.axis_parameters, StructArray())
    )


@dataclass
class PlateDimensions:
  default_values: PaddedBool
  length: F32
  width: F32
  height: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.length, F32)
      .add(self.width, F32)
      .add(self.height, F32)
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.id, PaddedU8)
      .add(self.volume, F32)
      .add(self.length, F32)
      .add(self.tip_type, WEnum)
      .add(self.has_filter, PaddedBool)
      .add(self.is_needle, PaddedBool)
      .add(self.is_tool, PaddedBool)
      .add(self.label, Str)
    )


@dataclass(frozen=True)
class HeldTipDefinition:
  """`Pipettor.GetTipDefinitionHeld`'s answer: the definition of the tip the channels hold."""

  value: Annotated[TipDefinition, Struct()]


@dataclass
class TipPickupParameters:
  default_values: PaddedBool
  volume: F32
  length: F32
  tip_type: WEnum
  has_filter: PaddedBool
  is_needle: PaddedBool
  is_tool: PaddedBool

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.volume, F32)
      .add(self.length, F32)
      .add(self.tip_type, WEnum)
      .add(self.has_filter, PaddedBool)
      .add(self.is_needle, PaddedBool)
      .add(self.is_tool, PaddedBool)
    )


@dataclass
class AspirateParameters:
  default_values: PaddedBool
  x_position: F32
  y_position: F32
  prewet_volume: F32
  blowout_volume: F32

  @classmethod
  def from_location(
    cls,
    loc,
    *,
    prewet_volume: float = 0.0,
    blowout_volume: float = 0.0,
  ) -> AspirateParameters:
    return cls(
      default_values=False,
      x_position=loc.x,
      y_position=loc.y,
      prewet_volume=prewet_volume,
      blowout_volume=blowout_volume,
    )

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.prewet_volume, F32)
      .add(self.blowout_volume, F32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.stop_back_volume, F32)
      .add(self.cutoff_speed, F32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.empty, PaddedBool)
      .add(self.z_minimum, F32)
      .add(self.z_final, F32)
      .add(self.z_liquid_exit_speed, F32)
      .add(self.liquid_volume, F32)
      .add(self.liquid_speed, F32)
      .add(self.transport_air_volume, F32)
      .add(self.tube_radius, F32)
      .add(self.cone_height, F32)
      .add(self.cone_bottom_radius, F32)
      .add(self.settling_time, F32)
      .add(self.additional_probes, U32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.z_fluid, F32)
      .add(self.z_air, F32)
      .add(self.bottom_search, PaddedBool)
      .add(self.z_bottom_search_offset, F32)
      .add(self.z_bottom_offset, F32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.search_start_position, F32)
      .add(self.channel_speed, F32)
      .add(self.z_submerge, F32)
      .add(self.z_out_of_liquid, F32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.sensitivity, WEnum)
      .add(self.clot_check_enable, PaddedBool)
      .add(self.z_clot_check, F32)
      .add(self.detect_mode, WEnum)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.sensitivity, WEnum)
      .add(self.dispenser_seek_speed, F32)
      .add(self.lld_height_difference, F32)
      .add(self.detect_mode, WEnum)
    )


@dataclass
class TadmReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  entries: U32
  error: PaddedBool
  data: I16Array

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.entries, U32)
      .add(self.error, PaddedBool)
      .add(self.data, I16Array)
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.limit_curve_index, U16)
      .add(self.recording_mode, WEnum)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.c_lld_enable, PaddedBool)
      .add(self.p_lld_enable, PaddedBool)
      .add(self.minimum_differential, U16)
      .add(self.maximum_differential, U16)
      .add(self.clot_threshold, U16)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.z_offset, F32)
      .add(self.volume, F32)
      .add(self.cycles, PaddedU8)
      .add(self.speed, F32)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.errors, PaddedBool)
      .add(self.maximum_volume, F32)
    )


@dataclass
class ChannelBoundsParameters:
  """Per-channel movement bounds returned by PipettorService.GetChannelBounds."""

  channel: WEnum
  x_min: F32
  x_max: F32
  y_min: F32
  y_max: F32
  z_min: F32
  z_max: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.channel, WEnum)
      .add(self.x_min, F32)
      .add(self.x_max, F32)
      .add(self.y_min, F32)
      .add(self.y_max, F32)
      .add(self.z_min, F32)
      .add(self.z_max, F32)
    )


@dataclass
class ChannelXYZPositionParameters:
  default_values: PaddedBool
  channel: WEnum
  position_x: F32
  position_y: F32
  position_z: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.position_x, F32)
      .add(self.position_y, F32)
      .add(self.position_z, F32)
    )


@dataclass
class PressureReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  pressure: U16

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool).add(self.channel, WEnum).add(self.pressure, U16)
    )


@dataclass
class LiquidHeightReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  c_lld_detected: PaddedBool
  c_lld_liquid_height: F32
  p_lld_detected: PaddedBool
  p_lld_liquid_height: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.c_lld_detected, PaddedBool)
      .add(self.c_lld_liquid_height, F32)
      .add(self.p_lld_detected, PaddedBool)
      .add(self.p_lld_liquid_height, F32)
    )


@dataclass
class DispenserVolumeReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  volume: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool).add(self.channel, WEnum).add(self.volume, F32)
    )


@dataclass
class PotentiometerParameters:
  default_values: PaddedBool
  channel: WEnum
  gain: PaddedU8
  offset: PaddedU8

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.gain, PaddedU8)
      .add(self.offset, PaddedU8)
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.start_position_x, F32)
      .add(self.start_position_y, F32)
      .add(self.start_position_z, F32)
      .add(self.seek_position_y, F32)
      .add(self.seek_velocity_y, F32)
      .add(self.lld_sensitivity, WEnum)
      .add(self.detect_mode, WEnum)
    )


@dataclass
class ChannelSeekParameters:
  default_values: PaddedBool
  channel: WEnum
  seek_position_x: F32
  seek_position_y: F32
  seek_height: F32
  min_seek_height: F32
  final_position_z: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.seek_position_x, F32)
      .add(self.seek_position_y, F32)
      .add(self.seek_height, F32)
      .add(self.min_seek_height, F32)
      .add(self.final_position_z, F32)
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.seek_position_x, F32)
      .add(self.seek_position_y, F32)
      .add(self.seek_velocity_z, F32)
      .add(self.seek_height, F32)
      .add(self.min_seek_height, F32)
      .add(self.final_position_z, F32)
      .add(self.lld_sensitivity, WEnum)
      .add(self.detect_mode, WEnum)
    )


@dataclass
class SeekResultParameters:
  default_values: PaddedBool
  channel: WEnum
  detected: PaddedBool
  position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.detected, PaddedBool)
      .add(self.position, F32)
    )


@dataclass
class ChannelCounterParameters:
  default_values: PaddedBool
  channel: WEnum
  tip_pickup_counter: U32
  tip_eject_counter: U32
  aspirate_counter: U32
  dispense_counter: U32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.tip_pickup_counter, U32)
      .add(self.tip_eject_counter, U32)
      .add(self.aspirate_counter, U32)
      .add(self.dispense_counter, U32)
    )


@dataclass
class ChannelCalibrationParameters:
  default_values: PaddedBool
  channel: WEnum
  dispenser_return_steps: U32
  squeeze_position: F32
  z_touchoff: F32
  z_tip_height: F32
  pressure_monitoring_shift: U32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.dispenser_return_steps, U32)
      .add(self.squeeze_position, F32)
      .add(self.z_touchoff, F32)
      .add(self.z_tip_height, F32)
      .add(self.pressure_monitoring_shift, U32)
    )


@dataclass
class LeakCheckSimpleParameters:
  default_values: PaddedBool
  channel: WEnum
  time: F32
  high_pressure: PaddedBool

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.time, F32)
      .add(self.high_pressure, PaddedBool)
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.start_position_x, F32)
      .add(self.start_position_y, F32)
      .add(self.start_position_z, F32)
      .add(self.seek_distance_y, F32)
      .add(self.pre_load_distance_y, F32)
      .add(self.final_z, F32)
      .add(self.tip_definition_id, PaddedU8)
      .add(self.test_time, F32)
      .add(self.high_pressure, PaddedBool)
    )


@dataclass
class DriveStatus:
  initialized: PaddedBool
  position: F32
  encoder_position: F32
  in_home_sensor: PaddedBool

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.initialized, PaddedBool)
      .add(self.position, F32)
      .add(self.encoder_position, F32)
      .add(self.in_home_sensor, PaddedBool)
    )


@dataclass
class ChannelDriveStatus:
  default_values: PaddedBool
  channel: WEnum
  y_axis_drive_status: Annotated[DriveStatus, Struct()]
  z_axis_drive_status: Annotated[DriveStatus, Struct()]
  dispenser_drive_status: Annotated[DriveStatus, Struct()]
  squeeze_drive_status: Annotated[DriveStatus, Struct()]

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.y_axis_drive_status, Struct())
      .add(self.z_axis_drive_status, Struct())
      .add(self.dispenser_drive_status, Struct())
      .add(self.squeeze_drive_status, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.aspirate_monitoring, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.p_lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.aspirate_monitoring, Struct())
      .add(self.adc, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.p_lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.tadm, Struct())
      .add(self.adc, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.dispense, Struct())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.dispense, Struct())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.y_position, F32)
      .add(self.z_seek, F32)
      .add(self.z_tip, F32)
      .add(self.z_final, F32)
      .add(self.z_seek_speed, F32)
      .add(self.drop_type, WEnum)
    )


@dataclass
class InitTipDropParameters:
  default_values: PaddedBool
  x_position: F32
  rolloff_distance: F32
  channel_parameters: Annotated[list[DropTipParameters], StructArray()]

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.rolloff_distance, F32)
      .add(self.channel_parameters, StructArray())
    )


@dataclass
class DispenseInitToWasteParameters:
  default_values: PaddedBool
  channel: WEnum
  x_position: F32
  y_position: F32
  z_position: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
    )


@dataclass
class MoveAxisAbsoluteParameters:
  default_values: PaddedBool
  channel: WEnum
  axis: WEnum
  position: F32
  delay: U32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.axis, WEnum)
      .add(self.position, F32)
      .add(self.delay, U32)
    )


@dataclass
class MoveAxisRelativeParameters:
  default_values: PaddedBool
  channel: WEnum
  axis: WEnum
  distance: F32
  delay: U32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.axis, WEnum)
      .add(self.distance, F32)
      .add(self.delay, U32)
    )


@dataclass
class LimitCurveEntry:
  default_values: PaddedBool
  sample: U16
  pressure: I16

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return params.add(self.default_values, PaddedBool).add(self.sample, U16).add(self.pressure, I16)


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

    A tip spot is the hole its tip hangs in, so `loc` is where the collar rests and where the
    channel ends up - the same height whatever the tip's length. The seek starts a collar height
    and 5 mm above it, clear of the tips' tops; z_seek_offset adds to that (None = 0).
    """
    collar = tip.collar_height if tip.has_collar_height else 0.0
    z = loc.z
    z_seek = loc.z + collar + 5.0 + (z_seek_offset or 0.0)
    return cls(
      default_values=False,
      channel=channel,
      x_position=loc.x,
      y_position=loc.y,
      z_position=z,
      z_seek=z_seek,
    )

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
      .add(self.z_seek, F32)
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

    The tip is let go a fitting depth below where its collar comes to rest, so that it is off the
    channel by the time the collar is seated. The seek starts a collar height and 2 mm above the
    spot, clear of the tips standing in the rack; z_seek_offset adds to that (None = 0).
    """
    collar = tip.collar_height if tip.has_collar_height else 0.0
    z = loc.z + collar - tip.fitting_depth
    z_seek = loc.z + collar + 2.0 + (z_seek_offset or 0.0)
    return cls(
      default_values=False,
      channel=channel,
      x_position=loc.x,
      y_position=loc.y,
      z_position=z,
      z_seek=z_seek,
      drop_type=drop_type if drop_type is not None else TipDropType.FixedHeight,
    )

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
      .add(self.z_seek, F32)
      .add(self.drop_type, WEnum)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_start, F32)
      .add(self.z_stop, F32)
      .add(self.z_final, F32)
      .add(self.volume, F32)
      .add(self.tip_type, WEnum)
    )


@dataclass
class DispenserVolumeEntry:
  default_values: PaddedBool
  type: WEnum
  volume: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return params.add(self.default_values, PaddedBool).add(self.type, WEnum).add(self.volume, F32)


@dataclass
class DispenserVolumeStackReturnParameters:
  default_values: PaddedBool
  channel: WEnum
  total_volume: F32
  volumes: Annotated[list[DispenserVolumeEntry], StructArray()]

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.total_volume, F32)
      .add(self.volumes, StructArray())
    )


@dataclass
class SegmentDescriptor:
  area_top: F32
  area_bottom: F32
  height: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return params.add(self.area_top, F32).add(self.area_bottom, F32).add(self.height, F32)


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.aspirate_monitoring, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.p_lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.aspirate_monitoring, Struct())
      .add(self.adc, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.aspirate, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.p_lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.tadm, Struct())
      .add(self.adc, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.dispense, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.no_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.channel, WEnum)
      .add(self.dispense, Struct())
      .add(self.container_description, StructArray())
      .add(self.common, Struct())
      .add(self.lld, Struct())
      .add(self.c_lld, Struct())
      .add(self.mix, Struct())
      .add(self.adc, Struct())
      .add(self.tadm, Struct())
    )


# =============================================================================
# PrepCommand base class
# =============================================================================


# An unresolved command is bound to a firmware address by PrepDriver.send_command for each execution.
_UNRESOLVED = Address(-1, -1, -1)
# The pipetting channels of a legacy Prep, back to front: its firmware's ChannelIndex names a rear and a
# front channel. `Pipettes` orders the connected device's own channels at discovery and falls back to this.
channel_order_legacy_prep: Tuple[ChannelIndex, ...] = (
  ChannelIndex.RearChannel,
  ChannelIndex.FrontChannel,
)
# A firmware error names a channel by its ChannelIndex. A command cannot reach the driver's channel order, so
# errors are attributed with the legacy one.
_CHANNEL_TO_INDEX = {int(channel): index for index, channel in enumerate(channel_order_legacy_prep)}


def _plr_channel_index(channel: int, entry_index: int) -> Optional[int]:
  """Map pipettor channel enums or ordered MPH probe entries to PLR indices."""
  if channel == ChannelIndex.MPHChannel:
    return entry_index
  return _CHANNEL_TO_INDEX.get(channel)


ResponseT = TypeVar("ResponseT", covariant=True)


@dataclass(frozen=True)
class PrepCommand(TCPCommand[ResponseT]):
  """Immutable Prep request, with its destination resolved at execution time.

  Concrete commands explicitly encode their wire fields and decode their declared
  response. Commands targeting multiple firmware objects declare a constructor
  ``dest`` field; fixed-target commands declare ``firmware_path``.
  """

  dest: Address = field(default=_UNRESOLVED, init=False)
  protocol = HamiltonProtocol.OBJECT_DISCOVERY
  interface_id = 1
  firmware_path: ClassVar[Optional[str]] = None
  _ALL_PATHS: ClassVar[Set[str]] = set()

  def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    if cls.firmware_path is not None:
      PrepCommand._ALL_PATHS.add(cls.firmware_path)


@dataclass(frozen=True)
class PrepStatusRequest(PrepCommand[ResponseT]):
  """Prep status request; decoding follows the concrete command's response type."""

  action_code = Hoi2Action.STATUS_REQUEST


@dataclass(frozen=True)
class PrepProbeRequest(PrepCommand[bytes]):
  """Ad-hoc STATUS_REQUEST with runtime command_id and interface_id.

  Use with :meth:`~PrepDriver.exchange` when the target command_id is only
  known at runtime. Always supply ``dest=`` explicitly; the JIT firmware-path
  resolver is bypassed because ``firmware_path = None``.

  ``command_id`` and ``interface_id`` are dataclass instance fields that shadow
  the class-level defaults in :class:`~pylabrobot.hamilton.transport.tcp.commands.TCPCommand`,
  so :meth:`TCPCommand.build` picks up the per-instance values correctly.
  """

  action_code = Hoi2Action.STATUS_REQUEST
  firmware_path = None
  dest: Address
  command_id: int  # type: ignore[misc]  # Runtime identity on an immutable probe request.
  interface_id: int = 3  # type: ignore[misc]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> bytes:
    """Decode the declared success response."""
    return data


# =============================================================================
# Pipettor / ChannelCoordinator command classes
# =============================================================================


@dataclass(frozen=True)
class PrepAspirateNoLldMonitoring(PrepCommand[None]):
  """Aspirate without LLD or monitoring (cmd=1, dest=Pipettor)."""

  command_id = 1
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateTadm(PrepCommand[None]):
  """Aspirate with TADM, no LLD (cmd=2, dest=Pipettor)."""

  command_id = 2
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateWithLld(PrepCommand[None]):
  """Aspirate with LLD and monitoring (cmd=3, dest=Pipettor)."""

  command_id = 3
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateWithLldTadm(PrepCommand[None]):
  """Aspirate with LLD and TADM (cmd=4, dest=Pipettor)."""

  command_id = 4
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDispenseNoLld(PrepCommand[None]):
  """Dispense without LLD (cmd=5, dest=Pipettor)."""

  command_id = 5
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  dispense_parameters: Annotated[list[DispenseParametersNoLld], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDispenseWithLld(PrepCommand[None]):
  """Dispense with LLD (cmd=6, dest=Pipettor)."""

  command_id = 6
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  dispense_parameters: Annotated[list[DispenseParametersLld], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDispenseInitToWaste(PrepCommand[None]):
  """Dispense initialize to waste (cmd=7, dest=Pipettor)."""

  command_id = 7
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  waste_parameters: Annotated[list[DispenseInitToWasteParameters], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.waste_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.waste_parameters):
      return None
    return _plr_channel_index(int(self.waste_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepPickUpTipsById(PrepCommand[None]):
  """Pick up tips by tip-definition ID (cmd=8, dest=Pipettor)."""

  command_id = 8
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition_id: PaddedU8
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_positions, StructArray())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_definition_id, PaddedU8)
      .add(self.enable_tadm, PaddedBool)
      .add(self.dispenser_volume, F32)
      .add(self.dispenser_speed, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.tip_positions):
      return None
    return _plr_channel_index(int(self.tip_positions[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepPickUpTips(PrepCommand[None]):
  """Pick up tips by tip-definition struct (cmd=9, dest=Pipettor)."""

  command_id = 9
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_positions, StructArray())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_definition, Struct())
      .add(self.enable_tadm, PaddedBool)
      .add(self.dispenser_volume, F32)
      .add(self.dispenser_speed, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.tip_positions):
      return None
    return _plr_channel_index(int(self.tip_positions[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepPickUpNeedlesById(PrepCommand[None]):
  """Pick up needles by tip-definition ID (cmd=10, dest=Pipettor)."""

  command_id = 10
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition_id: PaddedU8
  blowout_offset: F32
  blowout_speed: F32
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_positions, StructArray())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_definition_id, PaddedU8)
      .add(self.blowout_offset, F32)
      .add(self.blowout_speed, F32)
      .add(self.enable_tadm, PaddedBool)
      .add(self.dispenser_volume, F32)
      .add(self.dispenser_speed, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.tip_positions):
      return None
    return _plr_channel_index(int(self.tip_positions[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepPickUpNeedles(PrepCommand[None]):
  """Pick up needles by tip-definition struct (cmd=11, dest=Pipettor)."""

  command_id = 11
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_positions: Annotated[list[TipPositionParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  blowout_offset: F32
  blowout_speed: F32
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_positions, StructArray())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_definition, Struct())
      .add(self.blowout_offset, F32)
      .add(self.blowout_speed, F32)
      .add(self.enable_tadm, PaddedBool)
      .add(self.dispenser_volume, F32)
      .add(self.dispenser_speed, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.tip_positions):
      return None
    return _plr_channel_index(int(self.tip_positions[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDropTips(PrepCommand[None]):
  """Drop tips (cmd=12, dest=Pipettor)."""

  command_id = 12
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_positions: Annotated[list[TipDropParameters], StructArray()]
  final_z: F32
  seek_speed: F32
  tip_roll_off_distance: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_positions, StructArray())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_roll_off_distance, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.tip_positions):
      return None
    return _plr_channel_index(int(self.tip_positions[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphPickupTips(PrepCommand[None]):
  """Pick up tips via MPH coordinator (iface=1 id=9, dest=MphRoot.MPH).

  Resolved introspection signature:
    PickupTips(tipParameters: struct(iface=1), finalZ: f32,
               tipDefinition: struct(iface=1), tadm: bool,
               dispenserVolume: f32, dispenserSpeed: f32,
               tipMask: u32) -> { seekSpeed: List[u16] }

  The MPH takes a SINGLE struct (type_57) for tip_position, not a
  StructArray (type_61) like the Pipettor. All 8 probes move as one unit;
  tip_mask selects which channels engage.
  """

  command_id = 9
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  tip_position: Annotated[TipPositionParameters, Struct()]
  final_z: F32
  seek_speed: F32
  tip_definition: Annotated[TipPickupParameters, Struct()]
  enable_tadm: PaddedBool
  dispenser_volume: F32
  dispenser_speed: F32
  tip_mask: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_position, Struct())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_definition, Struct())
      .add(self.enable_tadm, PaddedBool)
      .add(self.dispenser_volume, F32)
      .add(self.dispenser_speed, F32)
      .add(self.tip_mask, U32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class MphMoveToPosition(PrepCommand[None]):
  """Move MPH gantry to absolute XYZ on IMph (cmd=17, dest=MphRoot.MPH).

  Wire matches vendor ``MoveToPosition(positionX, positionY, positionZ)`` as three
  plain ``f32`` scalars (see mph.yaml) — not :class:`GantryMoveXYZParameters`, which
  is Pipettor-only. Use :class:`MphMoveToPosition` / :class:`MphMoveToPositionViaLane`
  for MPH motion; :class:`PrepMoveToPosition` targets PipettorRoot only.
  """

  command_id = 17
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  x_position: F32
  y_position: F32
  z_position: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.x_position, F32).add(self.y_position, F32).add(self.z_position, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class MphMoveToPositionViaLane(PrepCommand[None]):
  """Move MPH gantry to absolute XYZ via lane (cmd=18, dest=MphRoot.MPH).

  Same payload as :class:`MphMoveToPosition`; vendor ``MoveToPositionViaLane``.
  """

  command_id = 18
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  x_position: F32
  y_position: F32
  z_position: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.x_position, F32).add(self.y_position, F32).add(self.z_position, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class MphDropTips(PrepCommand[None]):
  """Drop tips via MPH coordinator (iface=1 id=12, dest=MphRoot.MPH).

  Resolved introspection signature:
    DropTips(dropTipParameters: struct(iface=1), finalZ: f32,
             tipRollOffDistance: f32) -> seekSpeed: List[u16]

  Single struct (type_57) for drop position — all probes drop together.
  """

  command_id = 12
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  tip_position: Annotated[TipDropParameters, Struct()]
  final_z: F32
  seek_speed: F32
  tip_roll_off_distance: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_position, Struct())
      .add(self.final_z, F32)
      .add(self.seek_speed, F32)
      .add(self.tip_roll_off_distance, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class MphAspirateNoLldMonitoring(PrepCommand[None]):
  """Aspirate without LLD via MPH coordinator (cmd=1, dest=MphRoot.MPH).

  One AspirateParametersNoLldAndMonitoring struct per active probe — each with
  its own explicit x/y position. ``channel`` is ChannelIndex.MPHChannel for all
  entries. The array length equals the number of active probes (not necessarily 8).
  """

  command_id = 1
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphDispenseNoLld(PrepCommand[None]):
  """Dispense without LLD via MPH coordinator (cmd=5, dest=MphRoot.MPH).

  One DispenseParametersNoLld struct per active probe — each with its own
  explicit x/y position. ``channel`` is ChannelIndex.MPHChannel for all entries.
  """

  command_id = 5
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  dispense_parameters: Annotated[list[DispenseParametersNoLld], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateNoLldMonitoring2(PrepCommand[None]):
  """Aspirate V2 with liquid-following via MPH coordinator (cmd=29, dest=MphRoot.MPH).

  Uses ``AspirateParametersNoLldAndMonitoring2`` which includes a
  ``ContainerDescription`` frustum-segment array for Z-axis liquid-following.
  One entry per active probe; array length equals the number of active probes.
  """

  command_id = 29
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphDispenseNoLld2(PrepCommand[None]):
  """Dispense V2 without LLD via MPH coordinator (cmd=33, dest=MphRoot.MPH).

  Uses ``DispenseParametersNoLld2`` which includes a ``ContainerDescription``
  frustum-segment array for Z-axis liquid-following.
  One entry per active probe; array length equals the number of active probes.
  """

  command_id = 33
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  dispense_parameters: Annotated[list[DispenseParametersNoLld2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateTadm(PrepCommand[None]):
  """Aspirate with TADM, no LLD via MPH coordinator (cmd=2, dest=MphRoot.MPH)."""

  command_id = 2
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateWithLld(PrepCommand[None]):
  """Aspirate with LLD and monitoring via MPH coordinator (cmd=3, dest=MphRoot.MPH)."""

  command_id = 3
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateWithLldTadm(PrepCommand[None]):
  """Aspirate with LLD and TADM via MPH coordinator (cmd=4, dest=MphRoot.MPH)."""

  command_id = 4
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphDispenseWithLld(PrepCommand[None]):
  """Dispense with LLD via MPH coordinator (cmd=6, dest=MphRoot.MPH)."""

  command_id = 6
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  dispense_parameters: Annotated[list[DispenseParametersLld], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateTadm2(PrepCommand[None]):
  """Aspirate V2 with TADM, no LLD via MPH coordinator (cmd=30, dest=MphRoot.MPH)."""

  command_id = 30
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateWithLld2(PrepCommand[None]):
  """Aspirate V2 with LLD and monitoring via MPH coordinator (cmd=31, dest=MphRoot.MPH)."""

  command_id = 31
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphAspirateWithLldTadm2(PrepCommand[None]):
  """Aspirate V2 with LLD and TADM via MPH coordinator (cmd=32, dest=MphRoot.MPH)."""

  command_id = 32
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class MphDispenseWithLld2(PrepCommand[None]):
  """Dispense V2 with LLD via MPH coordinator (cmd=34, dest=MphRoot.MPH)."""

  command_id = 34
  firmware_path = "MLPrepRoot.MphRoot.MPH"
  dispense_parameters: Annotated[list[DispenseParametersLld2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepPickUpToolById(PrepCommand[None]):
  """Pick up tool by tip-definition ID (cmd=14, dest=Pipettor)."""

  command_id = 14
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_definition_id: PaddedU8
  tool_position_x: F32
  tool_position_z: F32
  front_channel_position_y: F32
  rear_channel_position_y: F32
  tool_seek: F32
  tool_x_radius: F32
  tool_y_radius: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_definition_id, PaddedU8)
      .add(self.tool_position_x, F32)
      .add(self.tool_position_z, F32)
      .add(self.front_channel_position_y, F32)
      .add(self.rear_channel_position_y, F32)
      .add(self.tool_seek, F32)
      .add(self.tool_x_radius, F32)
      .add(self.tool_y_radius, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepPickUpTool(PrepCommand[None]):
  """Pick up tool by tip-definition struct (cmd=15, dest=Pipettor)."""

  command_id = 15
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  tip_definition: Annotated[TipPickupParameters, Struct()]
  tool_position_x: F32
  tool_position_z: F32
  front_channel_position_y: F32
  rear_channel_position_y: F32
  tool_seek: F32
  tool_x_radius: F32
  tool_y_radius: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.tip_definition, Struct())
      .add(self.tool_position_x, F32)
      .add(self.tool_position_z, F32)
      .add(self.front_channel_position_y, F32)
      .add(self.rear_channel_position_y, F32)
      .add(self.tool_seek, F32)
      .add(self.tool_x_radius, F32)
      .add(self.tool_y_radius, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepDropTool(PrepCommand[None]):
  """Drop tool (cmd=16, dest=Pipettor)."""

  command_id = 16
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepPickUpPlate(PrepCommand[None]):
  """Pick up plate (cmd=17, dest=Pipettor)."""

  command_id = 17
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  plate_top_center: Annotated[XYZCoord, Struct()]
  plate: Annotated[PlateDimensions, Struct()]
  clearance_y: F32
  grip_speed_y: F32
  grip_distance: F32
  grip_height: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.plate_top_center, Struct())
      .add(self.plate, Struct())
      .add(self.clearance_y, F32)
      .add(self.grip_speed_y, F32)
      .add(self.grip_distance, F32)
      .add(self.grip_height, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepDropPlate(PrepCommand[None]):
  """Drop plate (cmd=18, dest=Pipettor)."""

  command_id = 18
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  plate_top_center: Annotated[XYZCoord, Struct()]
  clearance_y: F32
  acceleration_scale_x: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.plate_top_center, Struct())
      .add(self.clearance_y, F32)
      .add(self.acceleration_scale_x, PaddedU8)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMovePlate(PrepCommand[None]):
  """Move plate to position (cmd=19, dest=Pipettor)."""

  command_id = 19
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  plate_top_center: Annotated[XYZCoord, Struct()]
  acceleration_scale_x: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.plate_top_center, Struct()).add(self.acceleration_scale_x, PaddedU8)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepTransferPlate(PrepCommand[None]):
  """Transfer plate from source to destination (cmd=20, dest=Pipettor)."""

  command_id = 20
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  plate_source_top_center: Annotated[XYZCoord, Struct()]
  plate_destination_top_center: Annotated[XYZCoord, Struct()]
  plate: Annotated[PlateDimensions, Struct()]
  clearance_y: F32
  grip_speed_y: F32
  grip_distance: F32
  grip_height: F32
  acceleration_scale_x: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.plate_source_top_center, Struct())
      .add(self.plate_destination_top_center, Struct())
      .add(self.plate, Struct())
      .add(self.clearance_y, F32)
      .add(self.grip_speed_y, F32)
      .add(self.grip_distance, F32)
      .add(self.grip_height, F32)
      .add(self.acceleration_scale_x, PaddedU8)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepReleasePlate(PrepCommand[None]):
  """Release plate / open gripper (cmd=21, dest=Pipettor)."""

  command_id = 21
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetPlateHeld(PrepStatusRequest["PrepGetPlateHeld.Response"]):
  """GetPlateHeld (cmd=22, dest=Pipettor): whether the pipettor records a plate held.

  Set by a finished pick-up and cleared by a drop or a release, and kept across a power cycle. Not a
  sensor: a pick-up that closed on nothing sets it too.
  """

  command_id = 22
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetPlateHeld.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


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


@dataclass(frozen=True)
class PrepEmptyDispenser(PrepCommand[None]):
  """Empty dispenser (cmd=23, dest=Pipettor)."""

  command_id = 23
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channels: EnumArray

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, EnumArray)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMoveToPosition(PrepCommand[None]):
  """Move pipettor gantry to position (cmd=26, dest=PipettorRoot only).

  Payload is :class:`GantryMoveXYZParameters` with ``FrontChannel`` / ``RearChannel``
  only in ``axis_parameters``. MPH motion must use :class:`MphMoveToPosition` on
  ``MLPrepRoot.MphRoot.MPH``, not this command.
  """

  command_id = 26
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.move_parameters, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMoveToPositionViaLane(PrepCommand[None]):
  """Move pipettor gantry via lane (cmd=27, dest=PipettorRoot only).

  Same constraints as :class:`PrepMoveToPosition`. MPH: use
  :class:`MphMoveToPositionViaLane`.
  """

  command_id = 27
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.move_parameters, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetPositions(PrepStatusRequest["PrepGetPositions.Response"]):
  """GetPositions (cmd=25, dest=Pipettor).

  Returns the current XYZ position of each channel as a StructArray of
  ChannelXYZPositionParameters.
  """

  command_id = 25
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  @dataclass(frozen=True)
  class Response:
    positions: Annotated[list[ChannelXYZPositionParameters], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetPositions.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetTipDefinitionHeld(PrepStatusRequest["PrepGetTipDefinitionHeld.Response"]):
  """GetTipDefinitionHeld (cmd=13, dest=Pipettor): the definition of the tip the pipettor holds."""

  command_id = 13
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  @dataclass(frozen=True)
  class Response:
    value: Annotated[TipDefinition, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetTipDefinitionHeld.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class ChannelDispenserVolumeParameters:
  default_values: PaddedBool
  channel: WEnum
  volume: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool).add(self.channel, WEnum).add(self.volume, F32)
    )


@dataclass(frozen=True)
class PrepGetCurrentDispenserVolume(PrepStatusRequest["PrepGetCurrentDispenserVolume.Response"]):
  """GetCurrentDispenserVolume (cmd=24, dest=Pipettor): each channel's dispensing drive, in uL."""

  command_id = 24
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"

  @dataclass(frozen=True)
  class Response:
    volumes: Annotated[list[ChannelDispenserVolumeParameters], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetCurrentDispenserVolume.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepMoveYAbsolute(PrepCommand[None]):
  """Move channels along Y (cmd=10, dest=ChannelXYZCoordinator)."""

  command_id = 10
  firmware_path = CHANNEL_XYZ_COORDINATOR_OBJECT_PATH
  channels: Annotated[list[ChannelYPositionParameters], StructArray()]
  velocity: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, StructArray()).add(self.velocity, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMoveZAbsolute(PrepCommand[None]):
  """Move channels' tool bottoms along Z (cmd=12, dest=ChannelXYZCoordinator)."""

  command_id = 12
  firmware_path = CHANNEL_XYZ_COORDINATOR_OBJECT_PATH
  channels: Annotated[list[ChannelZPositionParameters], StructArray()]
  velocity: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, StructArray()).add(self.velocity, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepZDriveGetAcceleration(PrepStatusRequest["PrepZDriveGetAcceleration.Response"]):
  """Get a channel's Z drive acceleration in mm/s2 (cmd=16, dest=ZAxis.ZDrive)."""

  command_id = 16
  firmware_path = None
  dest: Address  # type: ignore[misc]

  @dataclass(frozen=True)
  class Response:
    value: F32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZDriveGetAcceleration.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepZDriveSetAcceleration(PrepCommand[None]):
  """Set a channel's Z drive acceleration in mm/s2 (cmd=15, dest=ZAxis.ZDrive)."""

  command_id = 15
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # A default only because `dest` comes first; every caller names the acceleration.
  value: F32 = math.nan

  def __post_init__(self) -> None:
    if not self.value > 0:
      raise ValueError(f"a Z drive acceleration must be above 0 mm/s2, is {self.value}")

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMoveZUpToSafe(PrepCommand[None]):
  """Move Z axes up to safe height (cmd=28, dest=Pipettor)."""

  command_id = 28
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channels: EnumArray

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, EnumArray)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepZSeekLldPosition(PrepCommand["PrepZSeekLldPosition.Response"]):
  """Z-seek LLD position (cmd=29, dest=Pipettor)."""

  command_id = 29
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  seek_parameters: Annotated[list[LLDChannelSeekParameters], StructArray()]

  @dataclass(frozen=True)
  class Response:
    results: Annotated[list[SeekResultParameters], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.seek_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZSeekLldPosition.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.seek_parameters):
      return None
    return _plr_channel_index(int(self.seek_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepYSeekLldPosition(PrepCommand["PrepYSeekLldPosition.Response"]):
  """Seek one channel along Y until its cLLD triggers (cmd=19, dest=ChannelCoordinator)."""

  command_id = 19
  firmware_path = "MLPrepRoot.ChannelCoordinator"
  seek_parameters: Annotated[YLLDSeekParameters, Struct()]

  @dataclass(frozen=True)
  class Response:
    result: Annotated[SeekResultParameters, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.seek_parameters, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepYSeekLldPosition.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepYAxisSeekCapacitiveLld(PrepCommand["PrepYAxisSeekCapacitiveLld.Response"]):
  """Seek a channel along Y, in its drive frame, until cLLD triggers (cmd=9, dest=YAxis)."""

  command_id = 9
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # Defaults only because `dest` comes first; every caller names them.
  position: F32 = math.nan
  velocity: F32 = math.nan
  detect_mode: WEnum = 0
  sensitivity: WEnum = 0

  @dataclass(frozen=True)
  class Response:
    lld_detected: PaddedBool
    detect_position: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.position, F32)
      .add(self.velocity, F32)
      .add(self.detect_mode, WEnum)
      .add(self.sensitivity, WEnum)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepYAxisSeekCapacitiveLld.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepZAxisSeekCapacitiveLld(PrepCommand["PrepZAxisSeekCapacitiveLld.Response"]):
  """Seek a channel down, in its Z drive frame, until cLLD triggers (cmd=12, dest=ZAxis)."""

  command_id = 12
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # Defaults only because `dest` comes first; every caller names them.
  position: F32 = math.nan
  velocity: F32 = math.nan
  detect_mode: WEnum = 0
  sensitivity: WEnum = 0

  @dataclass(frozen=True)
  class Response:
    lld_detected: PaddedBool
    detect_position: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.position, F32)
      .add(self.velocity, F32)
      .add(self.detect_mode, WEnum)
      .add(self.sensitivity, WEnum)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZAxisSeekCapacitiveLld.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepYDriveGetPosition(PrepStatusRequest["PrepYDriveGetPosition.Response"]):
  """Get a channel's Y drive position in its drive frame, in mm (cmd=9, dest=YDrive)."""

  command_id = 9
  firmware_path = None
  dest: Address  # type: ignore[misc]

  @dataclass(frozen=True)
  class Response:
    position: F32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepYDriveGetPosition.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepZAxisMoveRelative(PrepCommand[None]):
  """Move a channel along Z by a distance, in mm, positive up (cmd=5, dest=ZAxis). Runs before
  initializing."""

  command_id = 5
  firmware_path = None
  dest: Address  # type: ignore[misc]
  distance: F32 = math.nan

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.distance, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepYAxisMoveRelative(PrepCommand[None]):
  """Move a channel along Y by a distance, in mm (cmd=3, dest=YAxis). Runs before initializing."""

  command_id = 3
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # A default only because `dest` comes first; every caller names it.
  distance: F32 = math.nan

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.distance, F32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepZAxisSeekObstacle(PrepCommand["PrepZAxisSeekObstacle.Response"]):
  """Seek a channel down, in its Z drive frame, until it meets an obstacle (cmd=14, dest=ZAxis)."""

  command_id = 14
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # Defaults only because `dest` comes first; every caller names them.
  start_position: F32 = math.nan
  end_position: F32 = math.nan
  final_position: F32 = math.nan
  velocity: F32 = math.nan

  @dataclass(frozen=True)
  class Response:
    obstacle_detected: PaddedBool
    position: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.start_position, F32)
      .add(self.end_position, F32)
      .add(self.final_position, F32)
      .add(self.velocity, F32)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZAxisSeekObstacle.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepZDriveSetPwm(PrepCommand[None]):
  """Set a channel's Z drive PWM, the limit on how hard it pushes (cmd=19, dest=ZAxis.ZDrive)."""

  command_id = 19
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # A default only because `dest` comes first; every caller names the value.
  value: int = 125

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams().add(self.value, U16)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepZDriveGetPwm(PrepStatusRequest["PrepZDriveGetPwm.Response"]):
  """Get a channel's Z drive PWM (cmd=20, dest=ZAxis.ZDrive)."""

  command_id = 20
  firmware_path = None
  dest: Address  # type: ignore[misc]

  @dataclass(frozen=True)
  class Response:
    value: U16

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZDriveGetPwm.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepZDriveGetPosition(PrepStatusRequest["PrepZDriveGetPosition.Response"]):
  """Get a channel's Z drive position in its drive frame, in mm (cmd=12, dest=ZDrive)."""

  command_id = 12
  firmware_path = None
  dest: Address  # type: ignore[misc]

  @dataclass(frozen=True)
  class Response:
    position: F32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepZDriveGetPosition.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepChannelStartCLldDetection(PrepCommand[None]):
  """Start a channel's continuous cLLD detection (cmd=14, dest=Calibration)."""

  command_id = 14
  firmware_path = None
  dest: Address  # type: ignore[misc]
  # Defaults only because `dest` comes first; every caller names them.
  detect_mode: WEnum = 0
  sensitivity: WEnum = 0

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.detect_mode, WEnum).add(self.sensitivity, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepChannelStopCLldDetection(PrepCommand[None]):
  """Stop a channel's continuous cLLD detection (cmd=15, dest=Calibration)."""

  command_id = 15
  firmware_path = None
  dest: Address  # type: ignore[misc]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepCLldGetStatus(PrepStatusRequest["PrepCLldGetStatus.Response"]):
  """Get a channel's cLLD detection status (cmd=1, dest=CLld)."""

  command_id = 1
  firmware_path = None
  dest: Address  # type: ignore[misc]

  @dataclass(frozen=True)
  class Response:
    detected: BoolArray
    detect_index: U32Array
    length: U32Array
    sample_rate: U32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCLldGetStatus.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepCreateTadmLimitCurve(PrepCommand[None]):
  """Create TADM limit curve (cmd=31, dest=Pipettor)."""

  command_id = 31
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channel: U32
  name: Str
  lower_limit: Annotated[list[LimitCurveEntry], StructArray()]
  upper_limit: Annotated[list[LimitCurveEntry], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.channel, U32)
      .add(self.name, Str)
      .add(self.lower_limit, StructArray())
      .add(self.upper_limit, StructArray())
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepEraseTadmLimitCurves(PrepCommand[None]):
  """Erase TADM limit curves for a channel (cmd=32, dest=Pipettor)."""

  command_id = 32
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channel: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetTadmLimitCurveNames(PrepCommand[None]):
  """Get TADM limit curve names for a channel (cmd=33, dest=Pipettor)."""

  command_id = 33
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channel: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetTadmLimitCurveInfo(PrepCommand[None]):
  """Get TADM limit curve info (cmd=34, dest=Pipettor)."""

  command_id = 34
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channel: U32
  name: Str

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, U32).add(self.name, Str)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepRetrieveTadmData(PrepCommand[None]):
  """Retrieve TADM data for a channel (cmd=35, dest=Pipettor)."""

  command_id = 35
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channel: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepResetTadmFifo(PrepCommand[None]):
  """Reset TADM FIFO (cmd=36, dest=Pipettor)."""

  command_id = 36
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  channels: EnumArray

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, EnumArray)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepAspirateNoLldMonitoringV2(PrepCommand[None]):
  """Aspirate v2 without LLD or monitoring (cmd=38, dest=Pipettor)."""

  command_id = 38
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndMonitoring2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateTadmV2(PrepCommand[None]):
  """Aspirate v2 with TADM, no LLD (cmd=39, dest=Pipettor)."""

  command_id = 39
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersNoLldAndTadm2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateWithLldV2(PrepCommand[None]):
  """Aspirate v2 with LLD and monitoring (cmd=40, dest=Pipettor)."""

  command_id = 40
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndMonitoring2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepAspirateWithLldTadmV2(PrepCommand[None]):
  """Aspirate v2 with LLD and TADM (cmd=41, dest=Pipettor)."""

  command_id = 41
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  aspirate_parameters: Annotated[list[AspirateParametersLldAndTadm2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.aspirate_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.aspirate_parameters):
      return None
    return _plr_channel_index(int(self.aspirate_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDispenseNoLldV2(PrepCommand[None]):
  """Dispense v2 without LLD (cmd=42, dest=Pipettor)."""

  command_id = 42
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  dispense_parameters: Annotated[list[DispenseParametersNoLld2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepDispenseWithLldV2(PrepCommand[None]):
  """Dispense v2 with LLD (cmd=43, dest=Pipettor)."""

  command_id = 43
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor"
  dispense_parameters: Annotated[list[DispenseParametersLld2], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.dispense_parameters, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.dispense_parameters):
      return None
    return _plr_channel_index(int(self.dispense_parameters[entry_index].channel), entry_index)


# =============================================================================
# MLPrep command classes
# =============================================================================


@dataclass(frozen=True)
class PrepInitialize(PrepCommand[None]):
  """Initialize MLPrep (cmd=1, dest=MLPrep)."""

  command_id = 1
  firmware_path = "MLPrepRoot.MLPrep"
  smart: PaddedBool
  tip_drop_params: Annotated[InitTipDropParameters, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.smart, PaddedBool).add(self.tip_drop_params, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetIsInitialized(PrepStatusRequest["PrepGetIsInitialized.Response"]):
  """Query whether MLPrep is initialized. Firmware yaml: [1:2] GetIsInitialized(void) -> value: bool."""

  command_id = 2
  firmware_path = "MLPrepRoot.MLPrep"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetIsInitialized.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepPark(PrepCommand[None]):
  """Park MLPrep (cmd=3, dest=MLPrep)."""

  command_id = 3
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepSpread(PrepCommand[None]):
  """Spread channels (cmd=4, dest=MLPrep)."""

  command_id = 4
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepAddTipAndNeedleDefinition(PrepCommand[None]):
  """Add tip/needle definition (cmd=12, dest=MLPrep)."""

  command_id = 12
  firmware_path = "MLPrepRoot.MLPrep"
  tip_definition: Annotated[TipDefinition, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.tip_definition, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepRemoveTipAndNeedleDefinition(PrepCommand[None]):
  """Remove tip/needle definition by ID (cmd=13, dest=MLPrep)."""

  command_id = 13
  firmware_path = "MLPrepRoot.MLPrep"
  id_: WEnum

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.id_, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepReadStorage(PrepCommand[None]):
  """Read from instrument storage (cmd=14, dest=MLPrep)."""

  command_id = 14
  firmware_path = "MLPrepRoot.MLPrep"
  offset: U32
  length: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.offset, U32).add(self.length, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepWriteStorage(PrepCommand[None]):
  """Write to instrument storage (cmd=15, dest=MLPrep)."""

  command_id = 15
  firmware_path = "MLPrepRoot.MLPrep"
  offset: U32
  data: U8Array

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.offset, U32).add(self.data, U8Array)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepPowerDownRequest(PrepCommand[None]):
  """Request power down (cmd=17, dest=MLPrep)."""

  command_id = 17
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepConfirmPowerDown(PrepCommand[None]):
  """Confirm power down (cmd=18, dest=MLPrep)."""

  command_id = 18
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepCancelPowerDown(PrepCommand[None]):
  """Cancel power down (cmd=19, dest=MLPrep)."""

  command_id = 19
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepRemoveChannelPower(PrepCommand[None]):
  """Remove channel power for head swap (cmd=23, dest=MLPrep)."""

  command_id = 23
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepRestoreChannelPower(PrepCommand[None]):
  """Restore channel power after head swap (cmd=24, dest=MLPrep)."""

  command_id = 24
  firmware_path = "MLPrepRoot.MLPrep"
  delay_ms: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.delay_ms, U32)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepSetDeckLight(PrepCommand[None]):
  """Set deck LED colour (cmd=25, dest=MLPrep)."""

  command_id = 25
  firmware_path = "MLPrepRoot.MLPrep"
  white: PaddedU8
  red: PaddedU8
  green: PaddedU8
  blue: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.white, PaddedU8)
      .add(self.red, PaddedU8)
      .add(self.green, PaddedU8)
      .add(self.blue, PaddedU8)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetDeckLight(PrepStatusRequest["PrepGetDeckLight.Response"]):
  """Get deck LED colour (cmd=26, dest=MLPrep)."""

  command_id = 26
  firmware_path = "MLPrepRoot.MLPrep"

  @dataclass(frozen=True)
  class Response:
    white: PaddedU8
    red: PaddedU8
    green: PaddedU8
    blue: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetDeckLight.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepSetXSpeedScale(PrepCommand[None]):
  """Set the X speed scale, in percent (cmd=5, dest=MLPrep). Declared as a u8."""

  command_id = 5
  firmware_path = "MLPrepRoot.MLPrep"
  value: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, PaddedU8)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetXSpeedScale(PrepStatusRequest["PrepGetXSpeedScale.Response"]):
  """Get the X speed scale, in percent (cmd=6, dest=MLPrep). Answers a padded u8."""

  command_id = 6
  firmware_path = "MLPrepRoot.MLPrep"

  @dataclass(frozen=True)
  class Response:
    value: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetXSpeedScale.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepSetZSpeedScale(PrepCommand[None]):
  """Set the Z speed scale, in percent (cmd=7, dest=MLPrep). Declared as a u8."""

  command_id = 7
  firmware_path = "MLPrepRoot.MLPrep"
  value: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, PaddedU8)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetZSpeedScale(PrepStatusRequest["PrepGetZSpeedScale.Response"]):
  """Get the Z speed scale, in percent (cmd=8, dest=MLPrep). Answers a padded u8."""

  command_id = 8
  firmware_path = "MLPrepRoot.MLPrep"

  @dataclass(frozen=True)
  class Response:
    value: PaddedU8

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetZSpeedScale.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepXAxisSetVelocity(PrepCommand[None]):
  """Set the X axis velocity, in mm/s (cmd=9, dest=XAxis). Volatile."""

  command_id = 9
  firmware_path = "MLPrepRoot.XAxis"
  value: F64

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, F64)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepXAxisSetAcceleration(PrepCommand[None]):
  """Set the X axis acceleration, in mm/s2 (cmd=11, dest=XAxis). Volatile."""

  command_id = 11
  firmware_path = "MLPrepRoot.XAxis"
  value: F64

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, F64)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepXAxisGetCommandedPosition(PrepStatusRequest["PrepXAxisGetCommandedPosition.Response"]):
  """Get the X axis commanded position, in the axis's own frame, in mm (cmd=7, dest=XAxis)."""

  command_id = 7
  firmware_path = "MLPrepRoot.XAxis"

  @dataclass(frozen=True)
  class Response:
    value: F64

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepXAxisGetCommandedPosition.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepXAxisGetVelocity(PrepStatusRequest["PrepXAxisGetVelocity.Response"]):
  """Get the X axis velocity, in mm/s (cmd=10, dest=XAxis)."""

  command_id = 10
  firmware_path = "MLPrepRoot.XAxis"

  @dataclass(frozen=True)
  class Response:
    value: F64

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepXAxisGetVelocity.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepXAxisGetAcceleration(PrepStatusRequest["PrepXAxisGetAcceleration.Response"]):
  """Get the X axis acceleration, in mm/s2 (cmd=12, dest=XAxis)."""

  command_id = 12
  firmware_path = "MLPrepRoot.XAxis"

  @dataclass(frozen=True)
  class Response:
    value: F64

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepXAxisGetAcceleration.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepXAxisSeekToHomeFlag(PrepCommand["PrepXAxisSeekToHomeFlag.Response"]):
  """Move the X axis until its home flag sensor trips (cmd=5, dest=XAxis).

  `distance` is relative, in mm; `trip_sense` is the firmware's TripSense {Sensor0=0, Sensor1=1,
  SensorToggle=2}. Answers where the sensor tripped, in mm in the axis's own frame.
  """

  command_id = 5
  firmware_path = "MLPrepRoot.XAxis"
  distance: F64
  travel_limits_enable: PaddedBool
  trip_sense: WEnum

  @dataclass(frozen=True)
  class Response:
    value: F64

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.distance, F64)
      .add(self.travel_limits_enable, PaddedBool)
      .add(self.trip_sense, WEnum)
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepXAxisSeekToHomeFlag.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepXAxisMoveRelative(PrepCommand[None]):
  """Move the X axis by a distance, in mm, positive to the right (cmd=4, dest=XAxis)."""

  command_id = 4
  firmware_path = "MLPrepRoot.XAxis"
  distance: F64

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.distance, F64)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepXAxisMoveAbsolute(PrepCommand[None]):
  """Move the X axis to a position in its own frame, in mm (cmd=3, dest=XAxis).

  Addressed to the axis rather than the channel coordinator.
  """

  command_id = 3
  firmware_path = "MLPrepRoot.XAxis"
  position: F64

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.position, F64)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepSuspendedPark(PrepCommand[None]):
  """Suspended park / move to load position (cmd=29, dest=MLPrep).

  Reuses :class:`GantryMoveXYZParameters` on the **MLPrep** coordinator, not
  PipettorRoot — distinct from :class:`PrepMoveToPosition` and from MPH moves
  (:class:`MphMoveToPosition`).
  """

  command_id = 29
  firmware_path = "MLPrepRoot.MLPrep"
  move_parameters: Annotated[GantryMoveXYZParameters, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.move_parameters, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMethodBegin(PrepCommand[None]):
  """Begin method (cmd=30, dest=MLPrep)."""

  command_id = 30
  firmware_path = "MLPrepRoot.MLPrep"
  automatic_pause: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.automatic_pause, PaddedBool)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMethodEnd(PrepCommand[None]):
  """End method (cmd=31, dest=MLPrep)."""

  command_id = 31
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepMethodAbort(PrepCommand[None]):
  """Abort method (cmd=33, dest=MLPrep)."""

  command_id = 33
  firmware_path = "MLPrepRoot.MLPrep"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepIsParked(PrepStatusRequest["PrepIsParked.Response"]):
  """Query parked status (cmd=34, dest=MLPrep). Firmware yaml: IsParked(void) -> parked: bool."""

  command_id = 34
  firmware_path = "MLPrepRoot.MLPrep"

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepIsParked.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepIsSpread(PrepStatusRequest["PrepIsSpread.Response"]):
  """Query spread status (cmd=35, dest=MLPrep). Same HOI pattern as :class:`PrepIsParked`."""

  command_id = 35
  firmware_path = "MLPrepRoot.MLPrep"

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepIsSpread.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


# -----------------------------------------------------------------------------
# Wire structs for config responses (used by nested Response and DeviceConfiguration)
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

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.id, U32)
      .add(self.left_bottom_front_x, F32)
      .add(self.left_bottom_front_y, F32)
      .add(self.left_bottom_front_z, F32)
      .add(self.length, F32)
      .add(self.width, F32)
      .add(self.height, F32)
    )


@dataclass
class _CalibrationSiteDefinitionWire:
  """Wire shape for one CalibrationSiteDefinition (GetCalibrationSiteDefinitions element).

  Same fields as DeckSiteDefinition plus trailing Post (BOOL).
  """

  default_values: PaddedBool
  id: U32
  left_bottom_front_x: F32
  left_bottom_front_y: F32
  left_bottom_front_z: F32
  length: F32
  width: F32
  height: F32
  post: PaddedBool

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.id, U32)
      .add(self.left_bottom_front_x, F32)
      .add(self.left_bottom_front_y, F32)
      .add(self.left_bottom_front_z, F32)
      .add(self.length, F32)
      .add(self.width, F32)
      .add(self.height, F32)
      .add(self.post, PaddedBool)
    )


@dataclass
class _ChannelHardwareConfigWire:
  """Wire shape for ChannelHardwareConfig (GetChannelHardwareConfiguration element)."""

  channel: WEnum  # ChannelIndex
  hardware: WEnum  # Hardware type enum (interface 2, id 1)

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return params.add(self.channel, WEnum).add(self.hardware, WEnum)


@dataclass
class _ChannelCalibrationValuesWire:
  """Wire shape for ChannelCalibrationValues (GetCalibrationValues element)."""

  index: WEnum  # ChannelIndex
  y_offset: F32
  z_offset: F32
  squeeze_position: U32
  z_touchoff: U32
  pressure_shift: U32
  pressure_monitoring_shift: U32
  dispenser_return_distance: F32
  z_tip_height: F32
  core_ii: PaddedBool

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.index, WEnum)
      .add(self.y_offset, F32)
      .add(self.z_offset, F32)
      .add(self.squeeze_position, U32)
      .add(self.z_touchoff, U32)
      .add(self.pressure_shift, U32)
      .add(self.pressure_monitoring_shift, U32)
      .add(self.dispenser_return_distance, F32)
      .add(self.z_tip_height, F32)
      .add(self.core_ii, PaddedBool)
    )


@dataclass
class _WasteSiteDefinitionWire:
  """Wire shape for one WasteSiteDefinition (GetWasteSiteDefinitions element)."""

  default_values: PaddedBool
  index: WEnum
  x_position: F32
  y_position: F32
  z_position: F32
  z_seek: F32

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.index, WEnum)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_position, F32)
      .add(self.z_seek, F32)
    )


# -----------------------------------------------------------------------------
# Config queries (MLPrep / DeckConfiguration) for _get_hardware_config
# (inherit :class:`PrepStatusRequest`, defined above)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepGetIsEnclosurePresent(PrepStatusRequest["PrepGetIsEnclosurePresent.Response"]):
  """GetIsEnclosurePresent (cmd=21, dest=MLPrep). Firmware yaml: -> value: bool."""

  command_id = 21
  firmware_path = "MLPrepRoot.MLPrep"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetIsEnclosurePresent.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetSafeSpeedsEnabled(PrepStatusRequest["PrepGetSafeSpeedsEnabled.Response"]):
  """GetSafeSpeedsEnabled (cmd=28, dest=MLPrep). Firmware yaml: -> value: bool."""

  command_id = 28
  firmware_path = "MLPrepRoot.MLPrep"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    value: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetSafeSpeedsEnabled.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepReleaseTips(PrepCommand[None]):
  """ReleaseTips(channel) (dest=PipettorService): the squeeze drive lets go where the channel stands.

  The id is read from the device's method table by name, as it differs between firmware versions.
  """

  firmware_path = None
  dest: Address
  command_id: int  # type: ignore[misc]
  interface_id: int = 1  # type: ignore[misc]
  channel: WEnum = 0

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepSetSafeSpeedsEnabled(PrepCommand[None]):
  """SetSafeSpeedsEnabled(value) (dest=MLPrep), at the ids the device's method table gives.

  The id differs between firmware versions, so the caller reads it by name and passes it in.
  """

  firmware_path = None
  dest: Address
  command_id: int  # type: ignore[misc]
  interface_id: int = 1  # type: ignore[misc]
  value: PaddedBool = False

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.value, PaddedBool)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepGetDefaultTraverseHeight(PrepStatusRequest["PrepGetDefaultTraverseHeight.Response"]):
  """GetDefaultTraverseHeight (cmd=10, dest=MLPrep). Returns F32."""

  command_id = 10
  firmware_path = "MLPrepRoot.MLPrep"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    value: F32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetDefaultTraverseHeight.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetTipAndNeedleDefinitions(PrepStatusRequest["PrepGetTipAndNeedleDefinitions.Response"]):
  """GetTipAndNeedleDefinitions (cmd=11, dest=MLPrep).

  Returns the list of tip/needle definitions registered on the instrument.
  Introspection: iface=1 id=11 GetTipAndNeedleDefinitions(value: type_64) -> void
  (response carries STRUCTURE_ARRAY of tip definition structs).
  """

  command_id = 11
  firmware_path = "MLPrepRoot.MLPrep"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    definitions: Annotated[list[TipDefinition], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetTipAndNeedleDefinitions.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetDeckBounds(PrepStatusRequest["PrepGetDeckBounds.Response"]):
  """GetDeckBounds (cmd=1, dest=DeckConfiguration). Returns 6× F32 (min/max x,y,z)."""

  command_id = 1
  firmware_path = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    min_x: F32
    max_x: F32
    min_y: F32
    max_y: F32
    min_z: F32
    max_z: F32

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetDeckBounds.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetCalibrationSiteDefinitions(
  PrepStatusRequest["PrepGetCalibrationSiteDefinitions.Response"]
):
  """GetCalibrationSiteDefinitions (cmd=3, dest=DeckConfiguration).

  Response is a STRUCTURE_ARRAY of CalibrationSiteDefinition structs:
    DefaultValues: BOOL, Id: U32, LeftBottomFrontX/Y/Z: F32, Length, Width, Height: F32, Post: BOOL
  """

  command_id = 3
  firmware_path = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"

  @dataclass(frozen=True)
  class Response:
    sites: Annotated[list[_CalibrationSiteDefinitionWire], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetCalibrationSiteDefinitions.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetDeckSiteDefinitions(PrepStatusRequest["PrepGetDeckSiteDefinitions.Response"]):
  """GetDeckSiteDefinitions (cmd=7, dest=DeckConfiguration).

  Response is a STRUCTURE_ARRAY of DeckSiteDefinition structs:
    DefaultValues: BOOL, Id: U32, LeftBottomFrontX: F32, LeftBottomFrontY: F32,
    LeftBottomFrontZ: F32, Length: F32, Width: F32, Height: F32
  """

  command_id = 7
  firmware_path = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    sites: Annotated[list[_DeckSiteDefinitionWire], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetDeckSiteDefinitions.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetWasteSiteDefinitions(PrepStatusRequest["PrepGetWasteSiteDefinitions.Response"]):
  """GetWasteSiteDefinitions (cmd=12, dest=DeckConfiguration).

  Response is a STRUCTURE_ARRAY of WasteSiteDefinition structs:
    DefaultValues: BOOL, Index: ENUM, XPosition: I8, YPosition: U16,
    ZPosition: F32, ZSeek: F32
  """

  command_id = 12
  firmware_path = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    sites: Annotated[list[_WasteSiteDefinitionWire], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetWasteSiteDefinitions.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetChannelBounds(PrepStatusRequest["PrepGetChannelBounds.Response"]):
  """GetChannelBounds (cmd=10, dest=PipettorService).

  Returns per-channel movement bounds (x_min, x_max, y_min, y_max, z_min, z_max)
  as a StructArray of ChannelBoundsParameters.
  """

  command_id = 10
  firmware_path = "MLPrepRoot.PipettorRoot.Pipettor.PipettorService"

  @dataclass(frozen=True)
  class Response:
    bounds: Annotated[list[ChannelBoundsParameters], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetChannelBounds.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetPresentChannels(PrepStatusRequest["PrepGetPresentChannels.Response"]):
  """GetPresentChannels (cmd=17, dest=MLPrepService).

  Returns a list of enum values (iface=1, id=5): which channels are present.
  Map to ChannelIndex: 0=InvalidIndex, 1=FrontChannel, 2=RearChannel, 3=MPHChannel.
  Use this to determine hardware configuration: 1 vs 2 channels, or 8MPH presence.
  """

  command_id = 17
  firmware_path = "MLPrepRoot.MLPrepService"
  dest: Address = _UNRESOLVED

  @dataclass(frozen=True)
  class Response:
    channels: EnumArray  # list of ints: map to ChannelIndex for present channels

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetPresentChannels.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


# -----------------------------------------------------------------------------
# MLPrepCalibration commands
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepBeginCalibration(PrepCommand[None]):
  """BeginCalibration (cmd=1, dest=MLPrepCalibration). Enter calibration mode."""

  command_id = 1
  firmware_path = "MLPrepRoot.MLPrepCalibration"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepCancelCalibration(PrepCommand[None]):
  """CancelCalibration (cmd=2, dest=MLPrepCalibration). Cancel active calibration session."""

  command_id = 2
  firmware_path = "MLPrepRoot.MLPrepCalibration"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepEndCalibration(PrepCommand[None]):
  """EndCalibration (cmd=3, dest=MLPrepCalibration). End calibration and store results with timestamp."""

  command_id = 3
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  date_time: Annotated[HoiDateTime, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.date_time, Struct())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepResetCalibration(PrepCommand[None]):
  """ResetCalibration (cmd=4, dest=MLPrepCalibration). Reset calibration data, optionally storing."""

  command_id = 4
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  store: PaddedBool

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.store, PaddedBool)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepCalibrationInitialize(PrepCommand[None]):
  """CalibrationInitialize (cmd=5, dest=MLPrepCalibration). Initialize calibration hardware."""

  command_id = 5
  firmware_path = "MLPrepRoot.MLPrepCalibration"

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass
class NeedleDefinition:
  """Wire shape for NeedleDefinition (MLPrepCalibration local struct, id=2).

  When default_values=True the firmware uses stored defaults for all fields.
  TipDefinition is nested (global pool source_id=1, ref_id=8).
  """

  default_values: PaddedBool
  x_position: F32
  y_position: F32
  z_start: F32
  z_stop: F32
  tip_definition: Annotated[TipDefinition, Struct()]
  tip_mask: U32

  @classmethod
  def defaults(cls) -> "NeedleDefinition":
    """Return an all-defaults instance (firmware fills in stored values)."""
    return cls(
      default_values=True,
      x_position=0.0,
      y_position=0.0,
      z_start=0.0,
      z_stop=0.0,
      tip_definition=TipDefinition(
        default_values=True,
        id=0,
        volume=0.0,
        length=0.0,
        tip_type=0,
        has_filter=False,
        is_needle=False,
        is_tool=False,
        label="",
      ),
      tip_mask=0,
    )

  def encode_into(self, params: HoiParams) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      params.add(self.default_values, PaddedBool)
      .add(self.x_position, F32)
      .add(self.y_position, F32)
      .add(self.z_start, F32)
      .add(self.z_stop, F32)
      .add(self.tip_definition, Struct())
      .add(self.tip_mask, U32)
    )


@dataclass(frozen=True)
class PrepSelfCalibrate(PrepCommand[None]):
  """SelfCalibrate (cmd=6, dest=MLPrepCalibration).

  Runs a full self-calibration sequence. Set individual booleans to select
  which calibration phases to run. Pass NeedleDefinition.defaults() to use
  firmware-stored needle parameters.
  """

  command_id = 6
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  site_index: U32
  channels: WEnum  # ChannelIndex
  axis: PaddedBool
  pressure: PaddedBool
  touchoff: PaddedBool
  needle: Annotated[NeedleDefinition, Struct()]

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return (
      HoiParams()
      .add(self.site_index, U32)
      .add(self.channels, WEnum)
      .add(self.axis, PaddedBool)
      .add(self.pressure, PaddedBool)
      .add(self.touchoff, PaddedBool)
      .add(self.needle, Struct())
    )

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> None:
    """Decode the declared success response."""
    return None


@dataclass(frozen=True)
class PrepCalibrateXAxis(PrepCommand["PrepCalibrateXAxis.Response"]):
  """CalibrateXAxis (cmd=7, dest=MLPrepCalibration). Returns offset: F32."""

  command_id = 7
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  site_index: U32
  channel: WEnum  # ChannelIndex

  @dataclass(frozen=True)
  class Response:
    offset: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.site_index, U32).add(self.channel, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCalibrateXAxis.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepCalibrateYAxis(PrepCommand["PrepCalibrateYAxis.Response"]):
  """CalibrateYAxis (cmd=8, dest=MLPrepCalibration). Returns offset: F32."""

  command_id = 8
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  site_index: U32
  channel: WEnum  # ChannelIndex

  @dataclass(frozen=True)
  class Response:
    offset: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.site_index, U32).add(self.channel, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCalibrateYAxis.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepCalibrateZAxis(PrepCommand["PrepCalibrateZAxis.Response"]):
  """CalibrateZAxis (cmd=9, dest=MLPrepCalibration). Returns offset: F32."""

  command_id = 9
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  site_index: U32
  channel: WEnum  # ChannelIndex

  @dataclass(frozen=True)
  class Response:
    offset: F32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.site_index, U32).add(self.channel, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCalibrateZAxis.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepCalibrateSqueeze(PrepCommand["PrepCalibrateSqueeze.Response"]):
  """CalibrateSqueeze (cmd=14, dest=MLPrepCalibration). Returns position: U32."""

  command_id = 14
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  channel: WEnum  # ChannelIndex

  @dataclass(frozen=True)
  class Response:
    position: U32

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channel, WEnum)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCalibrateSqueeze.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepCalibrateSqueezeTips(PrepCommand["PrepCalibrateSqueezeTips.Response"]):
  """CalibrateSqueezeTips (cmd=15, dest=MLPrepCalibration).

  Takes per-channel TipPositionParameters (same struct as pick_up_tips) and
  returns per-channel squeeze positions as a list of u32.
  """

  command_id = 15
  firmware_path = "MLPrepRoot.MLPrepCalibration"
  channels: Annotated[list[TipPositionParameters], StructArray()]

  @dataclass(frozen=True)
  class Response:
    positions: U32Array

  def build_parameters(self) -> HoiParams:
    """Encode fields in firmware-defined order."""
    return HoiParams().add(self.channels, StructArray())

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepCalibrateSqueezeTips.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)

  uses_physical_channels = True

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map the firmware result ordinal to the requested PLR channel."""
    if entry_index >= len(self.channels):
      return None
    return _plr_channel_index(int(self.channels[entry_index].channel), entry_index)


@dataclass(frozen=True)
class PrepGetCalibrationValues(PrepStatusRequest["PrepGetCalibrationValues.Response"]):
  """GetCalibrationValues (cmd=16, dest=MLPrepCalibration).

  Returns independentOffsetX (F32), mphOffsetX (F32), and per-channel
  calibration values as a StructArray of ChannelCalibrationValues.
  """

  command_id = 16
  firmware_path = "MLPrepRoot.MLPrepCalibration"

  @dataclass(frozen=True)
  class Response:
    independent_offset_x: F32
    mph_offset_x: F32
    channel_values: Annotated[list[_ChannelCalibrationValuesWire], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetCalibrationValues.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)


@dataclass(frozen=True)
class PrepGetChannelHardwareConfiguration(
  PrepStatusRequest["PrepGetChannelHardwareConfiguration.Response"]
):
  """GetChannelHardwareConfiguration (cmd=24, dest=MLPrepCalibration).

  Response is a StructArray of ChannelHardwareConfig: Channel (enum) + Hardware (enum).
  """

  command_id = 24
  firmware_path = "MLPrepRoot.MLPrepCalibration"

  @dataclass(frozen=True)
  class Response:
    channels: Annotated[list[_ChannelHardwareConfigWire], StructArray()]

  def build_parameters(self) -> HoiParams:
    """Encode the request payload."""
    return HoiParams()

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> PrepGetChannelHardwareConfiguration.Response:
    """Decode the declared success response."""
    return parse_into_struct(HoiParamsParser(data), cls.Response)
