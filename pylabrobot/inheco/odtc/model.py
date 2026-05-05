"""ODTC domain types, hardware constants, and XML field metadata.

This module defines:
- Hardware constraints and variant normalization
- XML field annotation helpers (used by odtc_xml.py)
- ODTCPID, ODTCMethodSet, ODTCSensorValues, ODTCProgress
- ODTCProtocol: device-native protocol (Protocol subclass) for upload/roundtrip

XML serialization lives in odtc_xml.py.
Protocol conversion lives in odtc_protocol.py.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.thermocycling.standard import Protocol, Stage, Step

ODTCVariant = Literal[96, 384]


# =============================================================================
# Timestamp
# =============================================================================


def generate_odtc_timestamp() -> str:
  """Generate ISO 8601 timestamp in ODTC format (microsecond precision)."""
  return datetime.now().isoformat(timespec="microseconds")


# =============================================================================
# Volume / fluid quantity
# =============================================================================


class FluidQuantity(enum.IntEnum):
  """ODTC fluid volume range for thermal compensation.

  Select based on the maximum sample volume in the wells.
  VERIFICATION_TOOL disables volume-based overshoot calculation.
  """

  VERIFICATION_TOOL = -1  # calibration / dry run
  UL_10_TO_29  = 0        # 10–29 µL
  UL_30_TO_74  = 1        # 30–74 µL
  UL_75_TO_100 = 2        # 75–100 µL


def volume_to_fluid_quantity(volume_ul: float) -> FluidQuantity:
  """Map volume in µL to ODTC FluidQuantity.

  Args:
    volume_ul: Volume in microliters (must be > 0 and ≤ 100 µL).

  Returns:
    FluidQuantity matching the volume range.

  Raises:
    ValueError: If volume_ul is <= 0 or > 100.
  """
  if volume_ul <= 0:
    raise ValueError(
      f"Volume must be > 0 µL, got {volume_ul} µL."
    )
  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL."
    )
  if volume_ul <= 29:
    return FluidQuantity.UL_10_TO_29
  if volume_ul <= 74:
    return FluidQuantity.UL_30_TO_74
  return FluidQuantity.UL_75_TO_100


# =============================================================================
# Hardware Constraints
# =============================================================================


@dataclass(frozen=True)
class ODTCDimensions:
  """ODTC footprint dimensions (mm)."""

  x: float
  y: float
  z: float


ODTC_DIMENSIONS = ODTCDimensions(x=156.5, y=248.0, z=124.3)

PREMETHOD_ESTIMATED_DURATION_SECONDS: float = 600.0


@dataclass(frozen=True)
class ODTCHardwareConstraints:
  """Hardware limits for ODTC variants."""

  min_lid_temp: float = 30.0
  max_lid_temp: float = 115.0
  min_slope: float = 0.1
  max_heating_slope: float = 4.4
  max_cooling_slope: float = 2.2
  valid_fluid_quantities: Tuple[int, ...] = tuple(int(v) for v in FluidQuantity)
  valid_plate_types: Tuple[int, ...] = (0,)


def get_constraints(variant: ODTCVariant) -> ODTCHardwareConstraints:
  if variant == 96:
    return ODTCHardwareConstraints(max_heating_slope=4.4, max_lid_temp=110.0)
  if variant == 384:
    return ODTCHardwareConstraints(
      max_heating_slope=5.0, max_lid_temp=115.0, valid_plate_types=(0, 2)
    )
  raise ValueError(f"Unknown variant {variant}. Valid: [96, 384]")


def normalize_variant(variant: int) -> ODTCVariant:
  """Normalize variant to 96 or 384 (accepts device codes too)."""
  if variant in (96, 960000):
    return 96
  if variant in (384, 384000, 3840000):
    return 384
  raise ValueError(f"Unknown variant {variant}. Expected 96, 384, 960000, 384000, or 3840000.")


def _variant_to_device_code(variant: ODTCVariant) -> int:
  """Convert variant (96/384) to ODTC device code for XML serialization."""
  return {96: 960000, 384: 384000}[variant]


# =============================================================================
# XML Field Metadata
# =============================================================================


class XMLFieldType(Enum):
  ELEMENT = "element"
  ATTRIBUTE = "attribute"
  CHILD_LIST = "child_list"


@dataclass(frozen=True)
class XMLField:
  """Metadata for XML field mapping."""

  tag: Optional[str] = None
  field_type: XMLFieldType = XMLFieldType.ELEMENT
  default: Any = None
  scale: float = 1.0


def xml_field(
  tag: Optional[str] = None,
  field_type: XMLFieldType = XMLFieldType.ELEMENT,
  default: Any = None,
  scale: float = 1.0,
) -> Any:
  metadata = {"xml": XMLField(tag=tag, field_type=field_type, default=default, scale=scale)}
  if default is None:
    return field(default=None, metadata=metadata)
  return field(default=default, metadata=metadata)


def xml_attr(tag: Optional[str] = None, default: Any = None) -> Any:
  return xml_field(tag=tag, field_type=XMLFieldType.ATTRIBUTE, default=default)


def xml_child_list(tag: Optional[str] = None) -> Any:
  metadata = {"xml": XMLField(tag=tag, field_type=XMLFieldType.CHILD_LIST, default=None)}
  return field(default_factory=list, metadata=metadata)


# =============================================================================
# ODTC Data Classes
# =============================================================================


@dataclass
class ODTCPID:
  """PID controller parameters."""

  number: int = xml_attr(tag="number", default=1)
  p_heating: float = xml_field(tag="PHeating", default=60.0)
  p_cooling: float = xml_field(tag="PCooling", default=80.0)
  i_heating: float = xml_field(tag="IHeating", default=250.0)
  i_cooling: float = xml_field(tag="ICooling", default=100.0)
  d_heating: float = xml_field(tag="DHeating", default=10.0)
  d_cooling: float = xml_field(tag="DCooling", default=10.0)
  p_lid: float = xml_field(tag="PLid", default=100.0)
  i_lid: float = xml_field(tag="ILid", default=70.0)


@dataclass
class ODTCBackendParams(BackendParams):
  """ODTC-specific backend parameters. Single source of truth for compilation defaults.

  Use as backend_params in run_protocol() or as params in ODTCProtocol.from_protocol().
  Both paths accept this object — defaults are defined here and nowhere else.

  Device variant is fixed at ODTC construction time and is not a per-call field.
  fluid_quantity takes precedence over the volume_ul arg on run_protocol() when both
  are provided. If neither is set, defaults to FluidQuantity.UL_30_TO_74.
  """

  fluid_quantity: Optional[FluidQuantity] = None
  plate_type: int = 0
  post_heating: bool = True
  pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])
  default_heating_slope: Optional[float] = None   # None = hardware max
  default_cooling_slope: Optional[float] = None   # None = hardware max
  name: Optional[str] = None
  creator: Optional[str] = None
  apply_overshoot: bool = True


@dataclass
class ODTCMethodSet:
  """Container for all methods and premethods uploaded as a set."""

  delete_all_methods: bool = False
  premethods: List[ODTCProtocol] = field(default_factory=list)
  methods: List[ODTCProtocol] = field(default_factory=list)

  def get(self, name: str) -> Optional[ODTCProtocol]:
    return next((p for p in self.methods + self.premethods if p.name == name), None)

  def __str__(self) -> str:
    lines: List[str] = ["Methods (runnable protocols):"]
    if self.methods:
      for m in self.methods:
        lines.append(f"  - {m.name} ({len(m.stages)} stage(s))")
    else:
      lines.append("  (none)")
    lines.append("PreMethods (setup-only):")
    if self.premethods:
      for p in self.premethods:
        lines.append(
          f"  - {p.name} (block={p.target_block_temperature:.1f}°C,"
          f" lid={p.target_lid_temperature:.1f}°C)"
        )
    else:
      lines.append("  (none)")
    return "\n".join(lines)


@dataclass
class ODTCSensorValues:
  """Temperature sensor readings from ODTC (values in °C)."""

  timestamp: Optional[str] = xml_attr(tag="timestamp", default=None)
  mount: float = xml_field(tag="Mount", scale=0.01, default=0.0)
  mount_monitor: float = xml_field(tag="Mount_Monitor", scale=0.01, default=0.0)
  lid: float = xml_field(tag="Lid", scale=0.01, default=0.0)
  lid_monitor: float = xml_field(tag="Lid_Monitor", scale=0.01, default=0.0)
  ambient: float = xml_field(tag="Ambient", scale=0.01, default=0.0)
  pcb: float = xml_field(tag="PCB", scale=0.01, default=0.0)
  heatsink: float = xml_field(tag="Heatsink", scale=0.01, default=0.0)
  heatsink_tec: float = xml_field(tag="Heatsink_TEC", scale=0.01, default=0.0)

  def __str__(self) -> str:
    lines = [
      "ODTCSensorValues:",
      f"  Mount={self.mount:.1f}°C  Mount_Monitor={self.mount_monitor:.1f}°C",
      f"  Lid={self.lid:.1f}°C  Lid_Monitor={self.lid_monitor:.1f}°C",
      f"  Ambient={self.ambient:.1f}°C  PCB={self.pcb:.1f}°C",
      f"  Heatsink={self.heatsink:.1f}°C  Heatsink_TEC={self.heatsink_tec:.1f}°C",
    ]
    if self.timestamp:
      lines.insert(1, f"  timestamp={self.timestamp}")
    return "\n".join(lines)

  def format_compact(self) -> str:
    parts = [
      f"Mount={self.mount:.1f}°C",
      f"Lid={self.lid:.1f}°C",
      f"Ambient={self.ambient:.1f}°C",
      f"Mount_Monitor={self.mount_monitor:.1f}°C",
      f"Lid_Monitor={self.lid_monitor:.1f}°C",
      f"PCB={self.pcb:.1f}°C",
      f"Heatsink={self.heatsink:.1f}°C",
      f"Heatsink_TEC={self.heatsink_tec:.1f}°C",
    ]
    line = "  ".join(parts)
    if self.timestamp:
      return f"ODTCSensorValues({self.timestamp})  {line}"
    return f"ODTCSensorValues  {line}"


# =============================================================================
# ODTCProtocol — Protocol subclass (device-native compiled form)
# =============================================================================


@dataclass
class ODTCProtocol(Protocol):
  """Device-native ODTC runnable unit (method or premethod).

  Extends Protocol with all fields required for ODTC XML upload and
  roundtrip. This is both the compiled output of from_protocol() and
  the parsed representation of device XML.

  Protocol fields inherited: stages, lid_temperature.
  name is overridden below with a non-empty default.

  Validation runs in __post_init__ (previously in ODTCConfig).
  """

  # Required device configuration fields (no defaults — must be explicit)
  variant: ODTCVariant = field(default=96)
  plate_type: int = field(default=0)
  fluid_quantity: FluidQuantity = field(default=FluidQuantity.UL_30_TO_74)
  post_heating: bool = field(default=True)
  start_block_temperature: float = field(default=25.0)
  start_lid_temperature: float = field(default=110.0)
  pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])

  # Identity / metadata
  # Override Protocol.name default ("") so directly-constructed ODTCProtocols always
  # have a valid non-empty name for SetParameters / ExecuteMethod.
  name: str = field(default="plr_currentProtocol")
  kind: Literal["method", "premethod"] = field(default="method")
  is_scratch: bool = field(default=True)
  creator: Optional[str] = field(default=None)
  description: Optional[str] = field(default=None)
  datetime: str = field(default_factory=generate_odtc_timestamp)

  # Premethod targets (only meaningful when kind="premethod")
  target_block_temperature: float = field(default=0.0)
  target_lid_temperature: float = field(default=0.0)

  def __post_init__(self) -> None:
    errors: List[str] = []
    c = get_constraints(self.variant)

    if c.valid_fluid_quantities and self.fluid_quantity not in c.valid_fluid_quantities:
      errors.append(
        f"fluid_quantity={self.fluid_quantity} invalid for variant {self.variant}. "
        f"Valid: {c.valid_fluid_quantities}"
      )
    if self.plate_type not in c.valid_plate_types:
      errors.append(
        f"plate_type={self.plate_type} invalid for variant {self.variant}. "
        f"Valid: {c.valid_plate_types}"
      )
    lid_temp = self.lid_temperature
    if lid_temp is not None and not (c.min_lid_temp <= lid_temp <= c.max_lid_temp):
      errors.append(
        f"lid_temperature={lid_temp}°C outside range "
        f"[{c.min_lid_temp}, {c.max_lid_temp}] for variant {self.variant}"
      )
    if errors:
      raise ValueError("ODTCProtocol validation failed:\n  - " + "\n  - ".join(errors))

  def __str__(self) -> str:
    lines = [f"ODTCProtocol(name={self.name!r}, kind={self.kind!r})"]
    if self.kind == "premethod":
      lines.append(f"  target_block_temperature={self.target_block_temperature:.1f}°C")
      lines.append(f"  target_lid_temperature={self.target_lid_temperature:.1f}°C")
    else:
      step_count = sum(len(s.steps) for s in self.stages)
      lines.append(f"  {len(self.stages)} stage(s), {step_count} step(s)")
      lines.append(f"  start_block_temperature={self.start_block_temperature:.1f}°C")
      lines.append(f"  start_lid_temperature={self.start_lid_temperature:.1f}°C")
    lines.append(f"  variant={self.variant}")
    return "\n".join(lines)

  @classmethod
  def from_protocol(
    cls,
    protocol: "Protocol",
    variant: ODTCVariant = 96,
    params: Optional["ODTCBackendParams"] = None,
    lid_temperature: Optional[float] = None,
    start_lid_temperature: Optional[float] = None,
    description: Optional[str] = None,
    datetime: Optional[str] = None,
  ) -> "ODTCProtocol":
    """Compile a Protocol into a device-ready ODTCProtocol.

    Compilation defaults (fluid_quantity, post_heating, pid_set, etc.) are
    taken from ``params`` (an ODTCBackendParams). This is the same object
    accepted by run_protocol(), so defaults can never drift between the two
    paths.

    When ``params.name`` is provided, ``is_scratch=False`` and the method
    persists on the device across sessions. Without a name it is uploaded as a
    temporary scratch method (deleted on next Reset).

    Args:
      protocol: Source protocol with stages/steps.
      variant: ODTC variant (96 or 384).
      params: Compilation configuration. Defaults to ODTCBackendParams() when
        not provided. See ODTCBackendParams for field descriptions.
      lid_temperature: Default lid temperature (°C). None = hardware max.
        Advanced override; most callers do not need this.
      start_lid_temperature: Lid temperature during preheat. None = lid_temperature.
        Advanced override; most callers do not need this.
      description: Description string stored in device metadata.
      datetime: ISO timestamp stored in device metadata; auto-generated if None.

    Returns:
      ODTCProtocol ready for upload or direct execution.
    """
    from .protocol import _from_protocol  # lazy import — avoids circular dependency
    p = params if params is not None else ODTCBackendParams()
    fq = p.fluid_quantity if p.fluid_quantity is not None else FluidQuantity.UL_30_TO_74
    return _from_protocol(
      protocol,
      variant=variant,
      fluid_quantity=fq,
      plate_type=p.plate_type,
      post_heating=p.post_heating,
      pid_set=list(p.pid_set),
      name=p.name,
      apply_overshoot=p.apply_overshoot,
      default_heating_slope=p.default_heating_slope,
      default_cooling_slope=p.default_cooling_slope,
      creator=p.creator,
      lid_temperature=lid_temperature,
      start_lid_temperature=start_lid_temperature,
      description=description,
      datetime=datetime,
    )


# =============================================================================
# ODTCProgress
# =============================================================================


def _fmt_duration(seconds: float) -> str:
  """Format a duration in seconds as 'Xm YYs'."""
  m = int(seconds) // 60
  s = int(seconds) % 60
  return f"{m}m {s:02d}s"


@dataclass
class ODTCProgress:
  """Progress for a run, built from DataEvent payload and optional protocol."""

  elapsed_s: float
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None
  current_step_index: int = 0
  total_step_count: int = 0
  current_cycle_index: int = 0
  total_cycle_count: int = 0
  remaining_hold_s: float = 0.0
  estimated_duration_s: Optional[float] = None
  remaining_duration_s: Optional[float] = None
  is_premethod: bool = False

  def format_progress_log_message(self) -> str:
    elapsed_str = _fmt_duration(self.elapsed_s)
    if self.estimated_duration_s is not None:
      time_bracket = f"[{elapsed_str} / ~{_fmt_duration(self.estimated_duration_s)}]"
    else:
      time_bracket = f"[{elapsed_str} elapsed]"

    temp_parts = []
    if self.current_temp_c is not None:
      t = f"block {self.current_temp_c:.1f}C"
      if self.target_temp_c is not None:
        t += f" (target {self.target_temp_c:.1f}C)"
      temp_parts.append(t)
    elif self.target_temp_c is not None:
      temp_parts.append(f"target {self.target_temp_c:.1f}C")
    if self.lid_temp_c is not None:
      temp_parts.append(f"lid {self.lid_temp_c:.1f}C")
    temp_str = ", ".join(temp_parts)

    if self.total_step_count and self.total_cycle_count:
      ctx = (
        f"step {self.current_step_index + 1}/{self.total_step_count}, "
        f"cycle {self.current_cycle_index + 1}/{self.total_cycle_count}"
      )
      return f"ODTC {time_bracket} {ctx}" + (f", {temp_str}" if temp_str else "")

    if self.is_premethod:
      return f"ODTC {time_bracket} preheating" + (f", {temp_str}" if temp_str else "")

    return f"ODTC {time_bracket}" + (f" {temp_str}" if temp_str else "")

  def __str__(self) -> str:
    return self.format_progress_log_message()
