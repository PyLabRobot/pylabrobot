"""
ODTC model: domain types and constants.

Defines ODTC dataclasses (ODTCProtocol, ODTCConfig, etc.) and hardware
constants. XML serialization lives in odtc_xml.py; protocol conversion
lives in odtc_protocol.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (
  TYPE_CHECKING,
  Any,
  Dict,
  List,
  Literal,
  Optional,
  Tuple,
)

from pylabrobot.thermocycling.standard import Stage, Step

if TYPE_CHECKING:
  pass  # Protocol used at runtime for ODTCProtocol base

logger = logging.getLogger(__name__)

ODTCVariant = Literal[96, 384]


# =============================================================================
# Timestamp Generation
# =============================================================================


def generate_odtc_timestamp() -> str:
  """Generate ISO 8601 timestamp in ODTC format.

  Returns:
    Timestamp string in ISO 8601 format: YYYY-MM-DDTHH:MM:SS.ffffff (6 decimal places).
    Example: "2026-01-26T14:30:45.123456"

  Note:
    Uses Python's standard ISO 8601 formatting with microseconds (6 decimal places).
    ODTC accepts timestamps with 0-7 decimal places, so this standard format is compatible.
  """
  return datetime.now().isoformat(timespec="microseconds")


# =============================================================================
# Hardware Constraints
# =============================================================================


@dataclass(frozen=True)
class ODTCDimensions:
  """ODTC footprint dimensions (mm). Single source of truth for resource sizing."""

  x: float
  y: float
  z: float


ODTC_DIMENSIONS = ODTCDimensions(x=156.5, y=248.0, z=124.3)

# PreMethod estimated duration (10 min) when DynamicPreMethodDuration is off (ODTC Firmware doc).
PREMETHOD_ESTIMATED_DURATION_SECONDS: float = 600.0


@dataclass(frozen=True)
class ODTCHardwareConstraints:
  """Hardware limits for ODTC variants - immutable reference data.

  These values are derived from Inheco documentation and Script Editor defaults.
  Note: Actual achievable rates may vary based on fluid quantity and target temperature.
  """

  min_block_temp: float = 4.0
  max_block_temp: float = 99.0
  min_lid_temp: float = 30.0
  max_lid_temp: float = 115.0
  min_slope: float = 0.1
  max_heating_slope: float = 4.4
  max_cooling_slope: float = 2.2
  valid_fluid_quantities: Tuple[int, ...] = (-1, 0, 1, 2)  # -1 = verification tool
  valid_plate_types: Tuple[int, ...] = (0,)
  max_steps_per_method: int = 100


def get_constraints(variant: ODTCVariant) -> ODTCHardwareConstraints:
  if variant == 96:
    return ODTCHardwareConstraints(max_heating_slope=4.4, max_lid_temp=110.0)
  if variant == 384:
    return ODTCHardwareConstraints(
      max_heating_slope=5.0, max_lid_temp=115.0, valid_plate_types=(0, 2)
    )
  raise ValueError(f"Unknown variant {variant}. Valid: [96, 384]")


def normalize_variant(variant: int) -> ODTCVariant:
  """Normalize variant to 96 or 384.

  Accepts well count (96, 384) or device codes (960000, 384000, 3840000).

  Args:
    variant: Well count or ODTC device code.

  Returns:
    96 or 384.

  Raises:
    ValueError: If variant is not recognized.
  """
  if variant in (96, 960000):
    return 96
  if variant in (384, 384000, 3840000):
    return 384
  raise ValueError(f"Unknown variant {variant}. Expected 96, 384, 960000, 384000, or 3840000.")


def _variant_to_device_code(variant: ODTCVariant) -> int:
  """Convert variant (96/384) to ODTC device code for XML serialization."""
  return {96: 960000, 384: 384000}[variant]


# =============================================================================
# Volume / fluid quantity (ODTC domain rule: uL -> fluid_quantity code)
# =============================================================================


def volume_to_fluid_quantity(volume_ul: float) -> int:
  """Map volume in uL to ODTC fluid_quantity code.

  Args:
    volume_ul: Volume in microliters.

  Returns:
    fluid_quantity code: 0 (10-29ul), 1 (30-74ul), or 2 (75-100ul).

  Raises:
    ValueError: If volume > 100 uL.
  """
  if volume_ul > 100:
    raise ValueError(
      f"Volume {volume_ul} µL exceeds ODTC maximum of 100 µL. Please use a volume between 0-100 µL."
    )
  if volume_ul <= 29:
    return 0  # 10-29ul
  if volume_ul <= 74:
    return 1  # 30-74ul
  return 2  # 75-100ul


# =============================================================================
# XML Field Metadata (defined here so dataclasses can use them; full XML
# serialization engine lives in odtc_xml.py)
# =============================================================================


class XMLFieldType(Enum):
  """How a field maps to XML."""

  ELEMENT = "element"  # <FieldName>value</FieldName>
  ATTRIBUTE = "attribute"  # <Parent fieldName="value">
  CHILD_LIST = "child_list"  # List of child elements


@dataclass(frozen=True)
class XMLField:
  """Metadata for XML field mapping."""

  tag: Optional[str] = None  # XML tag name (defaults to field name)
  field_type: XMLFieldType = XMLFieldType.ELEMENT
  default: Any = None  # Default value if missing
  scale: float = 1.0  # For unit conversion (e.g., 1/100C -> C)


def xml_field(
  tag: Optional[str] = None,
  field_type: XMLFieldType = XMLFieldType.ELEMENT,
  default: Any = None,
  scale: float = 1.0,
) -> Any:
  """Create a dataclass field with XML metadata."""
  metadata = {"xml": XMLField(tag=tag, field_type=field_type, default=default, scale=scale)}
  if default is None:
    return field(default=None, metadata=metadata)
  return field(default=default, metadata=metadata)


def xml_attr(tag: Optional[str] = None, default: Any = None) -> Any:
  """Shorthand for an XML attribute field."""
  return xml_field(tag=tag, field_type=XMLFieldType.ATTRIBUTE, default=default)


def xml_child_list(tag: Optional[str] = None) -> Any:
  """Shorthand for a list of child elements."""
  metadata = {"xml": XMLField(tag=tag, field_type=XMLFieldType.CHILD_LIST, default=None)}
  return field(default_factory=list, metadata=metadata)


# =============================================================================
# ODTC Data Classes with XML Schema
# =============================================================================


@dataclass
class ODTCStep(Step):
  """A single step in an ODTC method. Subclasses Step; ODTC params are canonical."""

  # Step requires temperature, hold_seconds, rate; we give defaults and sync from ODTC in __post_init__
  temperature: List[float] = field(default_factory=lambda: [0.0])
  hold_seconds: float = 0.0
  rate: Optional[float] = None
  number: int = xml_field(tag="Number", default=0)
  slope: float = xml_field(tag="Slope", default=0.0)
  plateau_temperature: float = xml_field(tag="PlateauTemperature", default=0.0)
  plateau_time: float = xml_field(tag="PlateauTime", default=0.0)
  overshoot_slope1: float = xml_field(tag="OverShootSlope1", default=0.1)
  overshoot_temperature: float = xml_field(tag="OverShootTemperature", default=0.0)
  overshoot_time: float = xml_field(tag="OverShootTime", default=0.0)
  overshoot_slope2: float = xml_field(tag="OverShootSlope2", default=0.1)
  goto_number: int = xml_field(tag="GotoNumber", default=0)
  loop_number: int = xml_field(tag="LoopNumber", default=0)
  pid_number: int = xml_field(tag="PIDNumber", default=1)
  lid_temp: float = xml_field(tag="LidTemp", default=110.0)

  def __post_init__(self) -> None:
    # Keep Step interface in sync with ODTC canonical params
    self.temperature = [self.plateau_temperature]
    self.hold_seconds = self.plateau_time
    self.rate = self.slope

  @classmethod
  def from_step(
    cls,
    step: Step,
    number: int = 0,
    goto_number: int = 0,
    loop_number: int = 0,
  ) -> "ODTCStep":
    """Build ODTCStep from a generic Step (e.g. when serializing plain Stage); uses ODTC defaults for overshoot/lid/pid."""
    temp = step.temperature[0] if step.temperature else 25.0
    return cls(
      number=number,
      slope=step.rate if step.rate is not None else 0.1,
      plateau_temperature=temp,
      plateau_time=step.hold_seconds,
      overshoot_slope1=0.1,
      overshoot_temperature=0.0,
      overshoot_time=0.0,
      overshoot_slope2=0.1,
      goto_number=goto_number,
      loop_number=loop_number,
      pid_number=1,
      lid_temp=110.0,
    )


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
class ODTCMethodSet:
  """Container for all methods and premethods as ODTCProtocol (kind='method' | 'premethod')."""

  delete_all_methods: bool = False
  premethods: List[ODTCProtocol] = field(default_factory=list)
  methods: List[ODTCProtocol] = field(default_factory=list)

  def get(self, name: str) -> Optional[ODTCProtocol]:
    """Find a method or premethod by name. Returns ODTCProtocol or None."""
    return next((p for p in self.methods + self.premethods if p.name == name), None)

  def __str__(self) -> str:
    lines: List[str] = ["Methods (runnable protocols):"]
    if self.methods:
      for m in self.methods:
        lines.append(f"  - {m.name} ({len(m.steps)} steps)")
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
  """Temperature sensor readings from ODTC.

  Note: Raw values from device are in 1/100°C, but are automatically
  converted to °C by the scale parameter.
  """

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
    """Human-readable labeled temperatures in °C (multi-line for display/notebooks)."""
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
    """Single-line format for logs and parsing (one reading per log line)."""
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
# Protocol Conversion Config Classes
# =============================================================================


@dataclass
class ODTCStepSettings:
  """Per-step ODTC parameters for Protocol to ODTCProtocol conversion.

  When converting ODTCProtocol to Protocol, these capture the original values.
  When converting Protocol to ODTCProtocol, these override defaults.
  """

  slope: Optional[float] = None
  overshoot_slope1: Optional[float] = None
  overshoot_temperature: Optional[float] = None
  overshoot_time: Optional[float] = None
  overshoot_slope2: Optional[float] = None
  lid_temp: Optional[float] = None
  pid_number: Optional[int] = None


@dataclass(frozen=True)
class ODTCConfig:
  """ODTC-specific configuration for running a Protocol.

  This class serves two purposes:
    1. When creating new protocols: Specify ODTC-specific parameters
    2. When extracting from ODTCProtocol: Captures all params for lossless round-trip

  """

  # Method identification/metadata
  name: Optional[str] = None
  creator: Optional[str] = None
  description: Optional[str] = None
  datetime: Optional[str] = None

  # Device calibration
  fluid_quantity: int = 1  # -1=verification, 0=10-29ul, 1=30-74ul, 2=75-100ul
  variant: ODTCVariant = 96
  plate_type: int = 0

  # Temperature settings
  lid_temperature: float = 110.0
  start_lid_temperature: Optional[float] = None  # If different from lid_temperature
  post_heating: bool = True

  # Default ramp rates (°C/s) - defaults to hardware max for fastest transitions
  # Used when Step.rate is None and no step_settings override
  default_heating_slope: float = 4.4  # Will be validated against variant constraints
  default_cooling_slope: float = 2.2  # Will be validated against variant constraints

  # PID configuration (full set for round-trip preservation)
  pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])

  # Per-step overrides/captures (keyed by step index, 0-based)
  step_settings: Dict[int, ODTCStepSettings] = field(default_factory=dict)

  def __post_init__(self):
    errors: List[str] = []
    c = get_constraints(self.variant)

    # Validate fluid_quantity
    if c.valid_fluid_quantities and self.fluid_quantity not in c.valid_fluid_quantities:
      errors.append(
        f"fluid_quantity={self.fluid_quantity} invalid for {self.variant}. "
        f"Valid: {c.valid_fluid_quantities}"
      )

    # Validate plate_type
    if self.plate_type not in c.valid_plate_types:
      errors.append(
        f"plate_type={self.plate_type} invalid for {self.variant}. Valid: {c.valid_plate_types}"
      )

    # Validate lid_temperature
    if not c.min_lid_temp <= self.lid_temperature <= c.max_lid_temp:
      errors.append(
        f"lid_temperature={self.lid_temperature}°C outside range "
        f"[{c.min_lid_temp}, {c.max_lid_temp}] for {self.variant}"
      )

    # Validate default slopes
    if self.default_heating_slope > c.max_heating_slope:
      errors.append(
        f"default_heating_slope={self.default_heating_slope}°C/s exceeds max "
        f"{c.max_heating_slope}°C/s for {self.variant}"
      )
    if self.default_cooling_slope > c.max_cooling_slope:
      errors.append(
        f"default_cooling_slope={self.default_cooling_slope}°C/s exceeds max "
        f"{c.max_cooling_slope}°C/s for {self.variant}"
      )

    # Validate step_settings
    for idx, settings in self.step_settings.items():
      if settings.lid_temp is not None:
        if not c.min_lid_temp <= settings.lid_temp <= c.max_lid_temp:
          errors.append(
            f"step_settings[{idx}].lid_temp={settings.lid_temp}°C outside range "
            f"[{c.min_lid_temp}, {c.max_lid_temp}]"
          )
      if settings.slope is not None:
        # Can't easily check heating vs cooling without knowing step sequence,
        # so just check against the higher max
        max_slope = max(c.max_heating_slope, c.max_cooling_slope)
        if settings.slope > max_slope:
          errors.append(
            f"step_settings[{idx}].slope={settings.slope}°C/s exceeds max {max_slope}°C/s"
          )

    if errors:
      raise ValueError("ODTCConfig validation failed:\n  - " + "\n  - ".join(errors))


# =============================================================================
# ODTCStage (Stage with optional nested inner_stages for loop tree)
# =============================================================================


@dataclass
class ODTCStage(Stage):
  """Stage with optional inner_stages for nested loops.

  Execution: steps and inner_stages are interleaved (steps[0], inner_stages[0],
  steps[1], inner_stages[1], ...); then the whole block repeats `repeats` times.
  So for outer 1-5 with inner 2-4: steps=[step1, step5], inner_stages=[ODTCStage(2-4, 5)].
  At runtime steps are ODTCStep; we cast to List[Step] at construction so Stage.steps stays List[Step].
  """

  inner_stages: Optional[List["ODTCStage"]] = None


# =============================================================================
# ODTCProtocol (ODTC internal representation)
# =============================================================================


@dataclass
class ODTCProtocol:
  """ODTC runnable unit (method or premethod).

  Internal representation for the ODTC device. Config-driven fields have no
  defaults — they must be set explicitly (from ODTCConfig during conversion,
  or from parsed XML).
  """

  # Config-driven fields
  variant: ODTCVariant
  plate_type: int
  fluid_quantity: int
  post_heating: bool
  start_block_temperature: float
  start_lid_temperature: float
  steps: List[ODTCStep]
  pid_set: List[ODTCPID]

  # Identity / metadata
  kind: Literal["method", "premethod"] = "method"
  name: str = "plr_currentProtocol"
  is_scratch: bool = True
  creator: Optional[str] = None
  description: Optional[str] = None
  datetime: str = field(default_factory=generate_odtc_timestamp)
  target_block_temperature: float = 0.0
  target_lid_temperature: float = 0.0

  def __str__(self) -> str:
    """Human-readable summary: name, kind, steps or target temps, key config."""
    lines: List[str] = [f"ODTCProtocol(name={self.name!r}, kind={self.kind!r})"]
    if self.kind == "premethod":
      lines.append(f"  target_block_temperature={self.target_block_temperature:.1f}°C")
      lines.append(f"  target_lid_temperature={self.target_lid_temperature:.1f}°C")
    else:
      steps = self.steps
      if not steps:
        lines.append("  0 steps")
      else:
        lines.append(f"  {len(steps)} step(s)")
        for s in steps:
          hold_str = f"{s.plateau_time:.1f}s" if s.plateau_time != float("inf") else "∞"
          loop_str = (
            f" goto={s.goto_number} loop={s.loop_number}" if s.goto_number or s.loop_number else ""
          )
          lines.append(
            f"    step {s.number}: {s.plateau_temperature:.1f}°C hold {hold_str}{loop_str}"
          )
      lines.append(f"  start_block_temperature={self.start_block_temperature:.1f}°C")
      lines.append(f"  start_lid_temperature={self.start_lid_temperature:.1f}°C")
    if self.variant is not None:
      lines.append(f"  variant={self.variant}")
    return "\n".join(lines)


# =============================================================================
# ODTCProgress (raw DataEvent payload + optional protocol -> progress for interface)
# =============================================================================


@dataclass
class ODTCProgress:
  """Progress for a run: built from raw DataEvent payload and optional ODTCProtocol.

  Single type for all progress/duration. Event-derived: elapsed_s, temps (from
  parsing payload). Protocol-derived: step/cycle/setpoint/hold (from timeline lookup).
  estimated_duration_s is our protocol-based total; remaining_duration_s is always
  max(0, estimated_duration_s - elapsed_s). Device never sends estimated or remaining duration.
  Returned from get_progress_snapshot and passed to the progress callback.
  str(progress) or format_progress_log_message() gives the standard progress line (same as logged every progress_log_interval).
  """

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

  def format_progress_log_message(self) -> str:
    """Return the progress log message (elapsed, step/cycle/setpoint when present, temps)."""
    step_total = self.total_step_count
    cycle_total = self.total_cycle_count
    step_idx = self.current_step_index
    cycle_idx = self.current_cycle_index
    setpoint = self.target_temp_c if self.target_temp_c is not None else 0.0
    block = self.current_temp_c or 0.0
    lid = self.lid_temp_c or 0.0
    if step_total and cycle_total:
      return (
        f"ODTC progress: elapsed {self.elapsed_s:.0f}s, step {step_idx + 1}/{step_total}, "
        f"cycle {cycle_idx + 1}/{cycle_total}, setpoint {setpoint:.1f}°C, "
        f"block {block:.1f}°C, lid {lid:.1f}°C"
      )
    return (
      f"ODTC progress: elapsed {self.elapsed_s:.0f}s, block {block:.1f}°C "
      f"(target {setpoint:.1f}°C), lid {lid:.1f}°C"
    )

  def __str__(self) -> str:
    """Same as format_progress_log_message(); use for consistent printing and progress reporting."""
    return self.format_progress_log_message()
