"""
ODTC model: domain types, XML serialization, and Protocol conversion.

Defines ODTC dataclasses (ODTCProtocol, ODTCConfig, etc.), schema-driven
XML serialization for MethodSet, and conversion between PyLabRobot Protocol
and ODTC representation. Methods and premethods are consolidated as ODTCProtocol
(kind='method' | 'premethod').
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Literal, Optional, Tuple, Type, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from pylabrobot.thermocycling.standard import Protocol, Stage, Step

if TYPE_CHECKING:
  pass  # Protocol used at runtime for ODTCProtocol base

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Scratch Method Names
# =============================================================================

SCRATCH_PROTOCOL_NAME = "plr_currentProtocol"


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


def resolve_protocol_name(name: Optional[str]) -> str:
  """Resolve protocol name, using scratch name if not provided.

  Args:
    name: Protocol name (may be None or empty string).

  Returns:
    Resolved name. If name is None or empty, returns scratch name.
  """
  if not name:  # None or empty string
    return SCRATCH_PROTOCOL_NAME
  return name


# =============================================================================
# Hardware Constraints
# =============================================================================


@dataclass(frozen=True)
class ODTCDimensions:
  """ODTC footprint dimensions (mm). Single source of truth for resource sizing."""

  x: float
  y: float
  z: float


ODTC_DIMENSIONS = ODTCDimensions(x=147.0, y=298.0, z=130.0)

# PreMethod estimated duration (10 min) when DynamicPreMethodDuration is off (ODTC Firmware doc).
PREMETHOD_ESTIMATED_DURATION_SECONDS: float = 600.0


@dataclass(frozen=True)
class ODTCHardwareConstraints:
  """Hardware limits for ODTC variants - immutable reference data.

  These values are derived from Inheco documentation and Script Editor defaults.
  Note: Actual achievable rates may vary based on fluid quantity and target temperature.
  """

  variant: int
  variant_name: str
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


ODTC_96_CONSTRAINTS = ODTCHardwareConstraints(
  variant=960000,
  variant_name="ODTC 96",
  max_heating_slope=4.4,
  max_lid_temp=110.0,
  valid_fluid_quantities=(-1, 0, 1, 2),
)

ODTC_384_CONSTRAINTS = ODTCHardwareConstraints(
  variant=384000,
  variant_name="ODTC 384",
  max_heating_slope=5.0,
  max_lid_temp=115.0,
  valid_fluid_quantities=(-1, 0, 1, 2),  # Same as 96-well per XML samples
  valid_plate_types=(0, 2),  # Only 0 observed in XML samples
)

_CONSTRAINTS_MAP: Dict[int, ODTCHardwareConstraints] = {
  960000: ODTC_96_CONSTRAINTS,
  384000: ODTC_384_CONSTRAINTS,
  3840000: ODTC_384_CONSTRAINTS,  # Alias
}


def get_constraints(variant: int) -> ODTCHardwareConstraints:
  """Get hardware constraints for a variant.

  Args:
    variant: ODTC variant code (960000 for 96-well, 384000 for 384-well).

  Returns:
    ODTCHardwareConstraints for the specified variant.

  Raises:
    ValueError: If variant is unknown.
  """
  if variant not in _CONSTRAINTS_MAP:
    raise ValueError(f"Unknown variant {variant}. Valid: {list(_CONSTRAINTS_MAP.keys())}")
  return _CONSTRAINTS_MAP[variant]


_VALID_VARIANTS = (96, 384, 960000, 384000, 3840000)


def normalize_variant(variant: int) -> int:
  """Normalize variant to ODTC device code.

  Accepts well count (96, 384) or device codes (960000, 384000, 3840000).
  Maps 96 -> 960000, 384 -> 384000; passes through 960000, 384000, 3840000 unchanged.

  Args:
    variant: Well count (96, 384) or ODTC variant code (960000, 384000, 3840000).

  Returns:
    ODTC variant code: 960000 or 384000.

  Raises:
    ValueError: If variant is not one of 96, 384, 960000, 384000, 3840000.
  """
  if variant == 96:
    return 960000
  if variant in (384, 3840000):
    return 384000
  if variant in (960000, 384000):
    return variant
  raise ValueError(
    f"Unknown variant {variant}. Valid: {list(_VALID_VARIANTS)}"
  )


# =============================================================================
# XML Field Metadata
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
  scale: float = 1.0  # For unit conversion (e.g., 1/100°C -> °C)


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
# DataEvent Snapshots (SiLA DataEvent payload parsing)
# =============================================================================


@dataclass
class ODTCDataEventSnapshot:
  """Parsed snapshot from one DataEvent (elapsed time and temperatures)."""

  elapsed_s: float
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None


def _parse_data_event_series_value(series_elem: Any) -> Optional[float]:
  """Extract last integerValue from a dataSeries element as float."""
  values = series_elem.findall(".//integerValue")
  if not values:
    return None
  text = values[-1].text
  if text is None:
    return None
  try:
    return float(text)
  except ValueError:
    return None


def parse_data_event_payload(payload: Dict[str, Any]) -> Optional[ODTCDataEventSnapshot]:
  """Parse a single DataEvent payload into an ODTCDataEventSnapshot.

  Input: dict with 'requestId' and 'dataValue' (string of XML, possibly
  double-escaped). Extracts Elapsed time (ms), Target temperature, Current
  temperature, LID temperature (1/100°C -> °C). Returns None on parse error.
  """
  if not isinstance(payload, dict):
    return None
  data_value = payload.get("dataValue")
  if not data_value or not isinstance(data_value, str):
    return None
  try:
    outer = ET.fromstring(data_value)
  except ET.ParseError:
    return None
  any_data = outer.find(".//{*}AnyData") or outer.find(".//AnyData")
  if any_data is None or any_data.text is None:
    return None
  inner_xml = any_data.text.strip()
  if not inner_xml:
    return None
  if "&lt;" in inner_xml or "&gt;" in inner_xml:
    inner_xml = html.unescape(inner_xml)
  try:
    inner = ET.fromstring(inner_xml)
  except ET.ParseError:
    return None
  elapsed_s = 0.0
  target_temp_c: Optional[float] = None
  current_temp_c: Optional[float] = None
  lid_temp_c: Optional[float] = None
  for elem in inner.iter():
    if not elem.tag.endswith("dataSeries"):
      continue
    name_id = elem.get("nameId")
    unit = elem.get("unit") or ""
    raw = _parse_data_event_series_value(elem)
    if raw is None:
      continue
    if name_id == "Elapsed time" and unit == "ms":
      elapsed_s = raw / 1000.0
    elif name_id == "Target temperature" and unit == "1/100°C":
      target_temp_c = raw / 100.0
    elif name_id == "Current temperature" and unit == "1/100°C":
      current_temp_c = raw / 100.0
    elif name_id == "LID temperature" and unit == "1/100°C":
      lid_temp_c = raw / 100.0
  return ODTCDataEventSnapshot(
    elapsed_s=elapsed_s,
    target_temp_c=target_temp_c,
    current_temp_c=current_temp_c,
    lid_temp_c=lid_temp_c,
  )


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


@dataclass
class ODTCConfig:
  """ODTC-specific configuration for running a Protocol.

  This class serves two purposes:
    1. When creating new protocols: Specify ODTC-specific parameters
    2. When extracting from ODTCProtocol: Captures all params for lossless round-trip

  Validation is performed on construction by default. Set _validate=False to skip
  validation (useful when reading data from a trusted source like the device).
  """

  # Method identification/metadata
  name: Optional[str] = None
  creator: Optional[str] = None
  description: Optional[str] = None
  datetime: Optional[str] = None

  # Device calibration
  fluid_quantity: int = 1  # -1=verification, 0=10-29ul, 1=30-74ul, 2=75-100ul
  variant: int = 960000  # 96-well ODTC
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

  # Validation control - set to False to skip validation on construction
  _validate: bool = field(default=True, repr=False)

  def __post_init__(self):
    if self._validate:
      self.validate()

  @property
  def constraints(self) -> ODTCHardwareConstraints:
    """Get hardware constraints for this config's variant."""
    return get_constraints(self.variant)

  def validate(self) -> List[str]:
    """Validate config against hardware constraints.

    Returns:
      List of validation error messages (empty if valid).

    Raises:
      ValueError: If any validation fails.
    """
    errors: List[str] = []
    c = self.constraints

    # Validate fluid_quantity
    if c.valid_fluid_quantities and self.fluid_quantity not in c.valid_fluid_quantities:
      errors.append(
        f"fluid_quantity={self.fluid_quantity} invalid for {c.variant_name}. "
        f"Valid: {c.valid_fluid_quantities}"
      )

    # Validate plate_type
    if self.plate_type not in c.valid_plate_types:
      errors.append(
        f"plate_type={self.plate_type} invalid for {c.variant_name}. "
        f"Valid: {c.valid_plate_types}"
      )

    # Validate lid_temperature
    if not c.min_lid_temp <= self.lid_temperature <= c.max_lid_temp:
      errors.append(
        f"lid_temperature={self.lid_temperature}°C outside range "
        f"[{c.min_lid_temp}, {c.max_lid_temp}] for {c.variant_name}"
      )

    # Validate default slopes
    if self.default_heating_slope > c.max_heating_slope:
      errors.append(
        f"default_heating_slope={self.default_heating_slope}°C/s exceeds max "
        f"{c.max_heating_slope}°C/s for {c.variant_name}"
      )
    if self.default_cooling_slope > c.max_cooling_slope:
      errors.append(
        f"default_cooling_slope={self.default_cooling_slope}°C/s exceeds max "
        f"{c.max_cooling_slope}°C/s for {c.variant_name}"
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

    return errors


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
# ODTCProtocol (protocol + config; subclasses Protocol for resource API)
# =============================================================================


@dataclass
class ODTCProtocol(Protocol):
  """ODTC runnable unit: protocol + config (method or premethod).

  Subclasses Protocol so Thermocycler.run_protocol(protocol, ...) accepts
  ODTCProtocol. For kind='method', stages is the cycle; for kind='premethod',
  pass stages=[] (premethods run by name only).
  """

  kind: Literal["method", "premethod"] = "method"
  name: str = ""
  creator: Optional[str] = None
  description: Optional[str] = None
  datetime: Optional[str] = None
  target_block_temperature: float = 0.0
  target_lid_temperature: float = 0.0
  variant: int = 960000
  plate_type: int = 0
  fluid_quantity: int = 0
  post_heating: bool = False
  start_block_temperature: float = 0.0
  start_lid_temperature: float = 0.0
  steps: List[ODTCStep] = field(default_factory=list)
  pid_set: List[ODTCPID] = field(default_factory=lambda: [ODTCPID(number=1)])
  step_settings: Dict[int, ODTCStepSettings] = field(default_factory=dict)
  default_heating_slope: float = 4.4
  default_cooling_slope: float = 2.2


def protocol_to_odtc_protocol(
  protocol: "Protocol",
  config: Optional[ODTCConfig] = None,
) -> ODTCProtocol:
  """Convert a standard Protocol to ODTCProtocol (kind='method').

  Args:
    protocol: Standard Protocol with stages and steps.
    config: Optional ODTC config; if None, defaults are used.

  Returns:
    ODTCProtocol (kind='method') ready for upload or run. Steps are authoritative;
    stages=[] so the stage view is derived via odtc_method_to_protocol(odtc) when needed.
  """
  if config is None:
    config = ODTCConfig()

  odtc_steps: List[ODTCStep] = []
  step_number = 1

  # Track previous temperature for slope calculation
  # Start from room temperature - first step needs to ramp from ambient
  prev_temp = 25.0

  for stage_idx, stage in enumerate(protocol.stages):
    stage_start_step = step_number

    for step_idx, step in enumerate(stage.steps):
      # Get the target temperature (use first zone for ODTC single-zone)
      target_temp = step.temperature[0] if step.temperature else 25.0

      # Calculate slope
      slope = _calculate_slope(prev_temp, target_temp, step.rate, config)

      # Get step settings overrides if any
      # Use global step index (across all stages)
      global_step_idx = step_number - 1
      step_setting = config.step_settings.get(global_step_idx, ODTCStepSettings())

      # Create ODTC step with defaults or overrides
      odtc_step = ODTCStep(
        number=step_number,
        slope=step_setting.slope if step_setting.slope is not None else slope,
        plateau_temperature=target_temp,
        plateau_time=step.hold_seconds,
        overshoot_slope1=(
          step_setting.overshoot_slope1 if step_setting.overshoot_slope1 is not None else 0.1
        ),
        overshoot_temperature=(
          step_setting.overshoot_temperature if step_setting.overshoot_temperature is not None else 0.0
        ),
        overshoot_time=(
          step_setting.overshoot_time if step_setting.overshoot_time is not None else 0.0
        ),
        overshoot_slope2=(
          step_setting.overshoot_slope2 if step_setting.overshoot_slope2 is not None else 0.1
        ),
        goto_number=0,  # Will be set below for loops
        loop_number=0,  # Will be set below for loops
        pid_number=step_setting.pid_number if step_setting.pid_number is not None else 1,
        lid_temp=(
          step_setting.lid_temp if step_setting.lid_temp is not None else config.lid_temperature
        ),
      )

      odtc_steps.append(odtc_step)
      prev_temp = target_temp
      step_number += 1

    # If stage has repeats > 1, add loop on the last step of the stage
    if stage.repeats > 1 and odtc_steps:
      last_step = odtc_steps[-1]
      last_step.goto_number = stage_start_step
      last_step.loop_number = stage.repeats  # LoopNumber = actual repeat count (per loaded_set.xml)

  # Determine start temperatures
  start_block_temp = protocol.stages[0].steps[0].temperature[0] if protocol.stages else 25.0
  start_lid_temp = (
    config.start_lid_temperature
    if config.start_lid_temperature is not None
    else config.lid_temperature
  )

  # Resolve method name (use scratch name if not provided)
  resolved_name = resolve_protocol_name(config.name)

  # Generate timestamp if not already set
  resolved_datetime = config.datetime if config.datetime else generate_odtc_timestamp()

  return ODTCProtocol(
    kind="method",
    name=resolved_name,
    variant=config.variant,
    plate_type=config.plate_type,
    fluid_quantity=config.fluid_quantity,
    post_heating=config.post_heating,
    start_block_temperature=start_block_temp,
    start_lid_temperature=start_lid_temp,
    steps=odtc_steps,
    pid_set=list(config.pid_set),
    creator=config.creator,
    description=config.description,
    datetime=resolved_datetime,
    stages=[],  # Steps are authoritative; stage view via odtc_method_to_protocol(odtc)
  )


def odtc_protocol_to_protocol(odtc: ODTCProtocol) -> Tuple["Protocol", ODTCProtocol]:
  """Convert ODTCProtocol to Protocol view and return (protocol, odtc).

  For kind='method', builds Protocol from steps; for kind='premethod',
  returns Protocol(stages=[]) since premethods have no cycle.

  Returns:
    Tuple of (Protocol, ODTCProtocol). The ODTCProtocol is the same object
    for convenience (e.g. config fields).
  """
  if odtc.kind == "method":
    protocol, _ = odtc_method_to_protocol(odtc)
    return (protocol, odtc)
  return (Protocol(stages=[]), odtc)


def estimate_odtc_protocol_duration_seconds(odtc: ODTCProtocol) -> float:
  """Estimate total run duration for an ODTCProtocol.

  Premethods use PREMETHOD_ESTIMATED_DURATION_SECONDS; methods use
  step/loop-based estimation.

  Returns:
    Estimated duration in seconds.
  """
  if odtc.kind == "premethod":
    return PREMETHOD_ESTIMATED_DURATION_SECONDS
  return estimate_method_duration_seconds(odtc)


@dataclass
class StoredProtocol:
  """A protocol stored on the device, with instrument config for running it.

  Returned by backend get_protocol(name). Use stored.protocol and stored.config
  to inspect or run via run_protocol(stored.protocol, block_max_volume, config=stored.config).
  """

  name: str
  protocol: "Protocol"
  config: ODTCConfig

  def __str__(self) -> str:
    """Human-readable summary: name, stage/step counts, steps, optional config (variant, lid temp)."""
    lines: List[str] = [f"StoredProtocol(name={self.name!r})"]
    stages = self.protocol.stages
    if not stages:
      lines.append("  protocol: 0 stages")
    else:
      lines.append(f"  protocol: {len(stages)} stage(s)")
      for i, stage in enumerate(stages):
        step_count = len(stage.steps)
        first_temp = ""
        if stage.steps:
          temps = stage.steps[0].temperature
          first_temp = f", first step temp={temps[0]:.1f}°C" if temps else ""
        lines.append(
          f"    stage {i + 1}: {stage.repeats} repeat(s), {step_count} step(s){first_temp}"
        )
        # Step-by-step instruction set
        for j, step in enumerate(stage.steps):
          temps = step.temperature
          t_str = f"{temps[0]:.1f}°C" if temps else "—"
          hold = step.hold_seconds
          hold_str = f"{hold:.1f}s" if hold != float("inf") else "∞"
          rate_str = f" @ {step.rate:.1f}°C/s" if step.rate is not None else ""
          lines.append(f"      step {j + 1}: {t_str} hold {hold_str}{rate_str}")
    c = self.config
    if c.variant is not None or c.lid_temperature is not None:
      variant_str = f"variant={c.variant}" if c.variant is not None else ""
      lid_str = f"lid_temperature={c.lid_temperature}°C" if c.lid_temperature is not None else ""
      config_parts = [x for x in (variant_str, lid_str) if x]
      if config_parts:
        lines.append("  config: " + ", ".join(config_parts))
    return "\n".join(lines)


# =============================================================================
# Generic XML Serialization/Deserialization
# =============================================================================


def _get_xml_meta(f) -> XMLField:
  """Get XMLField metadata from a dataclass field, or create default."""
  if "xml" in f.metadata:
    return cast(XMLField, f.metadata["xml"])
  # Default: element with field name as tag
  return XMLField(tag=None, field_type=XMLFieldType.ELEMENT)


def _get_tag(f, meta: XMLField) -> str:
  """Get the XML tag name for a field."""
  return meta.tag if meta.tag else f.name


def _get_inner_type(type_hint) -> Optional[Type[Any]]:
  """Extract the inner type from List[T] or Optional[T]."""
  origin = get_origin(type_hint)
  args = get_args(type_hint)
  if origin is list and args:
    return cast(Type[Any], args[0])
  if origin is Union and type(None) in args:
    # Optional[T] is Union[T, None]
    result = next((a for a in args if a is not type(None)), None)
    return cast(Type[Any], result) if result is not None else None
  return None


def _is_dataclass_type(tp: Type) -> bool:
  """Check if a type is a dataclass."""
  return hasattr(tp, "__dataclass_fields__")


def _parse_value(text: Optional[str], field_type: Type, scale: float = 1.0) -> Any:
  """Parse a string value to the appropriate Python type."""
  if text is None:
    return None

  text = text.strip()

  if field_type is bool:
    return text.lower() == "true"
  if field_type is int:
    return int(float(text) * scale)
  if field_type is float:
    return float(text) * scale
  return text


def _format_value(value: Any, scale: float = 1.0) -> str:
  """Format a Python value to string for XML."""
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, float):
    scaled = value / scale if scale != 1.0 else value
    # Avoid unnecessary decimals for whole numbers
    if scaled == int(scaled):
      return str(int(scaled))
    return str(scaled)
  if isinstance(value, int):
    return str(int(value / scale) if scale != 1.0 else value)
  return str(value)


def from_xml(elem: ET.Element, cls: Type[T]) -> T:
  """
  Deserialize an XML element to a dataclass instance.

  Uses field metadata to map XML tags/attributes to fields.
  """
  if not _is_dataclass_type(cls):
    raise TypeError(f"{cls} is not a dataclass")

  kwargs: Dict[str, Any] = {}

  # Use get_type_hints to resolve string annotations to actual types
  type_hints = get_type_hints(cls)

  # For ODTCStep, only read ODTC XML tags (not Step's temperature/hold_seconds/rate)
  step_field_names = {"temperature", "hold_seconds", "rate"} if cls is ODTCStep else set()

  # Type narrowing: we've verified cls is a dataclass, so fields() is safe
  for f in fields(cls):  # type: ignore[arg-type]
    if f.name in step_field_names:
      continue
    meta = _get_xml_meta(f)
    tag = _get_tag(f, meta)
    field_type = type_hints.get(f.name, f.type)

    # Handle Optional types
    inner_type = _get_inner_type(field_type)
    actual_type = inner_type if inner_type and get_origin(field_type) is Union else field_type

    if meta.field_type == XMLFieldType.ATTRIBUTE:
      # Read from element attribute
      raw = elem.attrib.get(tag)
      if raw is not None:
        kwargs[f.name] = _parse_value(raw, actual_type, meta.scale)
      elif meta.default is not None:
        kwargs[f.name] = meta.default

    elif meta.field_type == XMLFieldType.ELEMENT:
      # Read from child element text
      child = elem.find(tag)
      if child is not None and child.text:
        kwargs[f.name] = _parse_value(child.text, actual_type, meta.scale)
      elif meta.default is not None:
        kwargs[f.name] = meta.default

    elif meta.field_type == XMLFieldType.CHILD_LIST:
      # Read list of child elements
      list_type = _get_inner_type(field_type)
      if list_type and _is_dataclass_type(list_type):
        children = elem.findall(tag)
        kwargs[f.name] = [from_xml(c, list_type) for c in children]
      else:
        kwargs[f.name] = []

  if cls is ODTCStep:
    kwargs["temperature"] = [kwargs.get("plateau_temperature", 0.0)]
    kwargs["hold_seconds"] = kwargs.get("plateau_time", 0.0)
    kwargs["rate"] = kwargs.get("slope", 0.0)

  return cls(**kwargs)


def to_xml(obj: Any, tag_name: Optional[str] = None, parent: Optional[ET.Element] = None) -> ET.Element:
  """
  Serialize a dataclass instance to an XML element.

  Uses field metadata to map fields to XML tags/attributes.
  """
  if not _is_dataclass_type(type(obj)):
    raise TypeError(f"{type(obj)} is not a dataclass")

  # Determine element tag name
  if tag_name is None:
    tag_name = type(obj).__name__

  # Create element
  if parent is not None:
    elem = ET.SubElement(parent, tag_name)
  else:
    elem = ET.Element(tag_name)

  # For ODTCStep, only serialize ODTC fields (not Step's temperature/hold_seconds/rate)
  skip_fields = {"temperature", "hold_seconds", "rate"} if type(obj) is ODTCStep else set()

  for f in fields(type(obj)):
    if f.name in skip_fields:
      continue
    meta = _get_xml_meta(f)
    tag = _get_tag(f, meta)
    value = getattr(obj, f.name)

    # Skip None values
    if value is None:
      continue

    if meta.field_type == XMLFieldType.ATTRIBUTE:
      elem.set(tag, _format_value(value, meta.scale))

    elif meta.field_type == XMLFieldType.ELEMENT:
      child = ET.SubElement(elem, tag)
      child.text = _format_value(value, meta.scale)

    elif meta.field_type == XMLFieldType.CHILD_LIST:
      for item in value:
        if _is_dataclass_type(type(item)):
          to_xml(item, tag, elem)

  return elem


# =============================================================================
# MethodSet-specific parsing: XML <-> ODTCProtocol (no ODTCMethod/ODTCPreMethod)
# =============================================================================


def _read_opt_attr(elem: ET.Element, key: str, default: Optional[str] = None) -> Optional[str]:
  """Read optional attribute from element."""
  return elem.attrib.get(key, default)


def _read_opt_elem(
  elem: ET.Element, tag: str, default: Any = None, parse_float: bool = False
) -> Any:
  """Read optional child element text. If parse_float, return float; else str or default."""
  child = elem.find(tag)
  if child is None or child.text is None:
    return default
  text = child.text.strip()
  if not text:
    return default
  if parse_float:
    return float(text)
  return text


def _parse_method_element_to_odtc_protocol(elem: ET.Element) -> ODTCProtocol:
  """Parse a <Method> element into ODTCProtocol (kind='method', stages=[]). No nested-loop validation."""
  name = _read_opt_attr(elem, "methodName") or ""
  creator = _read_opt_attr(elem, "creator")
  description = _read_opt_attr(elem, "description")
  datetime_ = _read_opt_attr(elem, "dateTime")
  variant = int(float(_read_opt_elem(elem, "Variant") or 960000))
  plate_type = int(float(_read_opt_elem(elem, "PlateType") or 0))
  fluid_quantity = int(float(_read_opt_elem(elem, "FluidQuantity") or 0))
  post_heating = (_read_opt_elem(elem, "PostHeating") or "false").lower() == "true"
  start_block_temperature = float(_read_opt_elem(elem, "StartBlockTemperature") or 0.0)
  start_lid_temperature = float(_read_opt_elem(elem, "StartLidTemperature") or 0.0)
  steps = [from_xml(step_elem, ODTCStep) for step_elem in elem.findall("Step")]
  pid_set: List[ODTCPID] = []
  pid_set_elem = elem.find("PIDSet")
  if pid_set_elem is not None:
    pid_set = [from_xml(pid_elem, ODTCPID) for pid_elem in pid_set_elem.findall("PID")]
  if not pid_set:
    pid_set = [ODTCPID(number=1)]
  return ODTCProtocol(
    kind="method",
    name=name,
    creator=creator,
    description=description,
    datetime=datetime_,
    variant=variant,
    plate_type=plate_type,
    fluid_quantity=fluid_quantity,
    post_heating=post_heating,
    start_block_temperature=start_block_temperature,
    start_lid_temperature=start_lid_temperature,
    steps=steps,
    pid_set=pid_set,
    stages=[],  # Not built on parse; built on demand in odtc_protocol_to_protocol
  )


def _parse_premethod_element_to_odtc_protocol(elem: ET.Element) -> ODTCProtocol:
  """Parse a <PreMethod> element into ODTCProtocol (kind='premethod')."""
  name = _read_opt_attr(elem, "methodName") or ""
  creator = _read_opt_attr(elem, "creator")
  description = _read_opt_attr(elem, "description")
  datetime_ = _read_opt_attr(elem, "dateTime")
  target_block_temperature = float(_read_opt_elem(elem, "TargetBlockTemperature") or 0.0)
  target_lid_temperature = float(_read_opt_elem(elem, "TargetLidTemp") or 0.0)
  return ODTCProtocol(
    kind="premethod",
    name=name,
    creator=creator,
    description=description,
    datetime=datetime_,
    target_block_temperature=target_block_temperature,
    target_lid_temperature=target_lid_temperature,
    stages=[],
  )


def _get_steps_for_serialization(odtc: ODTCProtocol) -> List[ODTCStep]:
  """Return canonical ODTCStep list for serializing an ODTCProtocol (kind='method').

  Uses odtc.steps when present; otherwise builds from odtc.stages via _odtc_stages_to_steps.
  """
  if odtc.steps:
    return odtc.steps
  if odtc.stages:
    stages_as_odtc = []
    for s in odtc.stages:
      if isinstance(s, ODTCStage):
        stages_as_odtc.append(s)
      else:
        steps_odtc = [
          st if isinstance(st, ODTCStep) else ODTCStep.from_step(st)
          for st in s.steps
        ]
        stages_as_odtc.append(ODTCStage(steps=cast(List[Step], steps_odtc), repeats=s.repeats, inner_stages=None))
    return _odtc_stages_to_steps(stages_as_odtc)
  return []


def _odtc_protocol_to_method_xml(odtc: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='method') to <Method> XML."""
  if odtc.kind != "method":
    raise ValueError("ODTCProtocol must have kind='method' to serialize as Method")
  steps_to_serialize = _get_steps_for_serialization(odtc)
  elem = ET.SubElement(parent, "Method")
  elem.set("methodName", odtc.name)
  if odtc.creator:
    elem.set("creator", odtc.creator)
  if odtc.description:
    elem.set("description", odtc.description)
  if odtc.datetime:
    elem.set("dateTime", odtc.datetime)
  ET.SubElement(elem, "Variant").text = str(odtc.variant)
  ET.SubElement(elem, "PlateType").text = str(odtc.plate_type)
  ET.SubElement(elem, "FluidQuantity").text = str(odtc.fluid_quantity)
  ET.SubElement(elem, "PostHeating").text = "true" if odtc.post_heating else "false"
  ET.SubElement(elem, "StartBlockTemperature").text = _format_value(odtc.start_block_temperature)
  ET.SubElement(elem, "StartLidTemperature").text = _format_value(odtc.start_lid_temperature)
  for step in steps_to_serialize:
    to_xml(step, "Step", elem)
  if odtc.pid_set:
    pid_set_elem = ET.SubElement(elem, "PIDSet")
    for pid in odtc.pid_set:
      to_xml(pid, "PID", pid_set_elem)
  return elem


def _odtc_protocol_to_premethod_xml(odtc: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='premethod') to <PreMethod> XML."""
  if odtc.kind != "premethod":
    raise ValueError("ODTCProtocol must have kind='premethod' to serialize as PreMethod")
  elem = ET.SubElement(parent, "PreMethod")
  elem.set("methodName", odtc.name)
  if odtc.creator:
    elem.set("creator", odtc.creator)
  if odtc.description:
    elem.set("description", odtc.description)
  if odtc.datetime:
    elem.set("dateTime", odtc.datetime)
  ET.SubElement(elem, "TargetBlockTemperature").text = _format_value(odtc.target_block_temperature)
  ET.SubElement(elem, "TargetLidTemp").text = _format_value(odtc.target_lid_temperature)
  return elem


# =============================================================================
# Convenience Functions
# =============================================================================


def parse_method_set_from_root(root: ET.Element) -> ODTCMethodSet:
  """Parse a MethodSet from an XML root element into ODTCProtocol only.

  Methods and premethods are parsed directly to ODTCProtocol (stages=[] for
  methods so list_protocols does not trigger nested-loop validation).
  """
  delete_elem = root.find("DeleteAllMethods")
  delete_all = False
  if delete_elem is not None and delete_elem.text:
    delete_all = delete_elem.text.lower() == "true"
  premethods = [_parse_premethod_element_to_odtc_protocol(pm) for pm in root.findall("PreMethod")]
  methods = [_parse_method_element_to_odtc_protocol(m) for m in root.findall("Method")]
  return ODTCMethodSet(
    delete_all_methods=delete_all,
    premethods=premethods,
    methods=methods,
  )


def parse_method_set(xml_str: str) -> ODTCMethodSet:
  """Parse a MethodSet XML string."""
  root = ET.fromstring(xml_str)
  return parse_method_set_from_root(root)


def parse_method_set_file(filepath: str) -> ODTCMethodSet:
  """Parse a MethodSet XML file."""
  tree = ET.parse(filepath)
  return parse_method_set_from_root(tree.getroot())


def method_set_to_xml(method_set: ODTCMethodSet) -> str:
  """Serialize a MethodSet to XML string (ODTCProtocol -> Method/PreMethod elements)."""
  root = ET.Element("MethodSet")
  ET.SubElement(root, "DeleteAllMethods").text = "true" if method_set.delete_all_methods else "false"
  for pm in method_set.premethods:
    _odtc_protocol_to_premethod_xml(pm, root)
  for m in method_set.methods:
    _odtc_protocol_to_method_xml(m, root)
  return ET.tostring(root, encoding="unicode", xml_declaration=True)


def parse_sensor_values(xml_str: str) -> ODTCSensorValues:
  """Parse SensorValues XML string."""
  root = ET.fromstring(xml_str)
  return from_xml(root, ODTCSensorValues)


# =============================================================================
# Method Lookup Helpers
# =============================================================================




def get_premethod_by_name(method_set: ODTCMethodSet, name: str) -> Optional[ODTCProtocol]:
  """Find a premethod by name."""
  return next((pm for pm in method_set.premethods if pm.name == name), None)


def _get_method_only_by_name(method_set: ODTCMethodSet, name: str) -> Optional[ODTCProtocol]:
  """Find a method by name (methods only, not premethods)."""
  return next((m for m in method_set.methods if m.name == name), None)


def get_method_by_name(method_set: ODTCMethodSet, name: str) -> Optional[ODTCProtocol]:
  """Find a method or premethod by name. Returns ODTCProtocol or None."""
  m = _get_method_only_by_name(method_set, name)
  if m is not None:
    return m
  return get_premethod_by_name(method_set, name)


def list_method_names_only(method_set: ODTCMethodSet) -> List[str]:
  """Get all method names (methods only, not premethods)."""
  return [m.name for m in method_set.methods]


def list_premethod_names(method_set: ODTCMethodSet) -> List[str]:
  """Get all premethod names."""
  return [pm.name for pm in method_set.premethods]


def list_method_names(method_set: ODTCMethodSet) -> List[str]:
  """Get all method names (both methods and premethods)."""
  method_names = [m.name for m in method_set.methods]
  premethod_names = [pm.name for pm in method_set.premethods]
  return method_names + premethod_names


class ProtocolList:
  """Result of list_protocols(): methods and premethods with nice __str__ and backward-compat .all / iteration."""

  def __init__(self, methods: List[str], premethods: List[str]) -> None:
    self.methods = list(methods)
    self.premethods = list(premethods)

  @property
  def all(self) -> List[str]:
    """Flat list of all protocol names (methods then premethods), for backward compatibility."""
    return self.methods + self.premethods

  def __iter__(self) -> Iterator[str]:
    yield from self.all

  def __str__(self) -> str:
    lines: List[str] = ["Methods (runnable protocols):"]
    if self.methods:
      for name in self.methods:
        lines.append(f"  - {name}")
    else:
      lines.append("  (none)")
    lines.append("PreMethods (setup-only, e.g. set temperature):")
    if self.premethods:
      for name in self.premethods:
        lines.append(f"  - {name}")
    else:
      lines.append("  (none)")
    return "\n".join(lines)

  def __eq__(self, other: object) -> bool:
    if isinstance(other, list):
      return self.all == other
    if isinstance(other, ProtocolList):
      return self.methods == other.methods and self.premethods == other.premethods
    return NotImplemented


# =============================================================================
# Protocol Conversion Functions
# =============================================================================


def _calculate_slope(
  from_temp: float,
  to_temp: float,
  rate: Optional[float],
  config: ODTCConfig,
) -> float:
  """Calculate and validate slope (ramp rate) for temperature transition.

  Both Protocol.Step.rate and ODTC slope represent the same thing: ramp rate in °C/s.
  This function validates against hardware limits and clamps if necessary.

  Args:
    from_temp: Starting temperature in °C.
    to_temp: Target temperature in °C.
    rate: Optional rate from Protocol Step (°C/s). Same units as ODTC slope.
    config: ODTC config with default slopes and variant.

  Returns:
    Slope value in °C/s, clamped to hardware limits if necessary.
  """
  constraints = get_constraints(config.variant)
  is_heating = to_temp > from_temp
  max_slope = constraints.max_heating_slope if is_heating else constraints.max_cooling_slope
  direction = "heating" if is_heating else "cooling"

  if rate is not None:
    # User provided an explicit rate - validate and clamp if needed
    if rate > max_slope:
      logger.warning(
        "Requested %s rate %.2f °C/s exceeds hardware maximum %.2f °C/s. "
        "Clamping to maximum. Temperature transition: %.1f°C → %.1f°C",
        direction,
        rate,
        max_slope,
        from_temp,
        to_temp,
      )
      return max_slope
    return rate

  # No rate specified - use config defaults (which should already be within limits)
  default_slope = config.default_heating_slope if is_heating else config.default_cooling_slope

  # Validate config defaults too (in case user configured invalid defaults)
  if default_slope > max_slope:
    logger.warning(
      "Config default_%s_slope %.2f °C/s exceeds hardware maximum %.2f °C/s. "
      "Clamping to maximum.",
      direction,
      default_slope,
      max_slope,
    )
    return max_slope

  return default_slope


def _analyze_loop_structure(
  steps: List[ODTCStep],
) -> List[Tuple[int, int, int]]:
  """Analyze loop structure in ODTC steps.

  Args:
    steps: List of ODTCStep objects.

  Returns:
    List of (start_step, end_step, repeat_count) tuples, sorted by end position.
    Step numbers are 1-based as in the XML.
  """
  loops = []
  for step in steps:
    if step.goto_number > 0:
      # LoopNumber in XML is actual repeat count (per loaded_set.xml / firmware doc)
      loops.append((step.goto_number, step.number, step.loop_number))
  return sorted(loops, key=lambda x: x[1])  # Sort by end position


def _build_one_odtc_stage_for_range(
  steps_by_num: Dict[int, ODTCStep],
  loops: List[Tuple[int, int, int]],
  start: int,
  end: int,
  repeats: int,
) -> ODTCStage:
  """Build one ODTCStage for step range [start, end] with repeats; recurse for inner loops."""
  # Loops strictly inside (start, end): contained if start <= s and e <= end and (start,end) != (s,e)
  inner_loops = [
    (s, e, r) for (s, e, r) in loops
    if start <= s and e <= end and (start, end) != (s, e)
  ]
  inner_loops_sorted = sorted(inner_loops, key=lambda x: x[0])

  if not inner_loops_sorted:
    # Flat: all steps in range are one stage (use ODTCStep directly)
    stage_steps = [steps_by_num[n] for n in range(start, end + 1) if n in steps_by_num]
    return ODTCStage(steps=cast(List[Step], stage_steps), repeats=repeats, inner_stages=None)

  # Nested: partition range into step-only segments and inner loops; interleave steps and inner_stages
  step_nums_in_range = set(range(start, end + 1))
  for (is_, ie, _) in inner_loops_sorted:
    for n in range(is_, ie + 1):
      step_nums_in_range.discard(n)
  sorted(step_nums_in_range)

  # Groups: steps before first inner, between inners, after last inner
  step_groups: List[List[int]] = []
  pos = start
  for (is_, ie, ir) in inner_loops_sorted:
    group = [n for n in range(pos, is_) if n in steps_by_num]
    if group:
      step_groups.append(group)
    pos = ie + 1
  if pos <= end:
    group = [n for n in range(pos, end + 1) if n in steps_by_num]
    if group:
      step_groups.append(group)

  steps_list: List[ODTCStep] = []
  inner_stages_list: List[ODTCStage] = []
  for gi, (is_, ie, ir) in enumerate(inner_loops_sorted):
    if gi < len(step_groups):
      steps_list.extend(steps_by_num[n] for n in step_groups[gi])
    inner_stages_list.append(_build_one_odtc_stage_for_range(steps_by_num, loops, is_, ie, ir))
  if len(step_groups) > len(inner_loops_sorted):
    steps_list.extend(steps_by_num[n] for n in step_groups[len(inner_loops_sorted)])
  return ODTCStage(steps=cast(List[Step], steps_list), repeats=repeats, inner_stages=inner_stages_list)


def _odtc_stage_to_steps_impl(
  stage: "ODTCStage",
  start_number: int,
) -> Tuple[List[ODTCStep], int]:
  """Convert one ODTCStage to ODTCSteps with step numbers; return (steps, next_number)."""
  inner_stages = stage.inner_stages or []
  out: List[ODTCStep] = []
  num = start_number
  first_step_num = start_number

  for i, step in enumerate(stage.steps):
    # stage.steps are ODTCStep (or Step when from plain Stage); copy and assign number
    if isinstance(step, ODTCStep):
      step_copy = replace(step, number=num)
    else:
      step_copy = ODTCStep.from_step(step, number=num)
    out.append(step_copy)
    num += 1
    if i < len(inner_stages):
      inner_steps, num = _odtc_stage_to_steps_impl(inner_stages[i], num)
      out.extend(inner_steps)

  if stage.repeats > 1 and out:
    out[-1].goto_number = first_step_num
    out[-1].loop_number = stage.repeats
  return (out, num)


def _odtc_stages_to_steps(stages: List["ODTCStage"]) -> List[ODTCStep]:
  """Convert ODTCStage tree to flat List[ODTCStep] with correct step numbers and goto/loop."""
  result: List[ODTCStep] = []
  num = 1
  for stage in stages:
    steps, num = _odtc_stage_to_steps_impl(stage, num)
    result.extend(steps)
  return result


def _build_odtc_stages_from_steps(steps: List[ODTCStep]) -> List[ODTCStage]:
  """Build ODTCStage tree from ODTC steps (handles flat and nested loops).

  Uses _analyze_loop_structure for (start, end, repeat_count). No loops -> one stage
  with all steps, repeats=1. We only emit for top-level loops (loops not contained in
  any other), so outer 1-5 x 30 with inner 2-4 x 5 produces one ODTCStage with inner_stages.
  """
  if not steps:
    return []
  steps_by_num = {s.number: s for s in steps}
  loops = _analyze_loop_structure(steps)
  max_step = max(s.number for s in steps)

  if not loops:
    flat = [steps_by_num[n] for n in range(1, max_step + 1) if n in steps_by_num]
    return [
      ODTCStage(steps=cast(List[Step], flat), repeats=1, inner_stages=None)
    ]

  def contains(outer: Tuple[int, int, int], inner: Tuple[int, int, int]) -> bool:
    (s, e, _), (s2, e2, _) = outer, inner
    return s <= s2 and e2 <= e and (s, e) != (s2, e2)

  top_level = [L for L in loops if not any(contains(M, L) for M in loops if M != L)]
  top_level.sort(key=lambda x: (x[0], x[1]))
  step_nums_in_top_level = set()
  for (s, e, _) in top_level:
    for n in range(s, e + 1):
      step_nums_in_top_level.add(n)

  stages: List[ODTCStage] = []
  i = 1
  while i <= max_step:
    if i not in steps_by_num:
      i += 1
      continue
    if i not in step_nums_in_top_level:
      # Flat run of steps not in any top-level loop (use ODTCStep directly)
      flat_steps: List[ODTCStep] = []
      while i <= max_step and i in steps_by_num and i not in step_nums_in_top_level:
        flat_steps.append(steps_by_num[i])
        i += 1
      if flat_steps:
        stages.append(ODTCStage(steps=cast(List[Step], flat_steps), repeats=1, inner_stages=None))
      continue
    # i is inside some top-level loop; find the loop that ends at the smallest end >= i
    for (start, end, repeats) in top_level:
      if start <= i <= end:
        stages.append(_build_one_odtc_stage_for_range(steps_by_num, loops, start, end, repeats))
        i = end + 1
        break
    else:
      i += 1

  return stages


def _expand_step_sequence(
  steps: List[ODTCStep],
  loops: List[Tuple[int, int, int]],
) -> List[int]:
  """Return step numbers (1-based) in execution order with loops expanded."""
  if not steps:
    return []
  steps_by_num = {s.number: s for s in steps}
  max_step = max(s.number for s in steps)
  loop_by_end = {end: (start, count) for start, end, count in loops}

  expanded: List[int] = []
  i = 1
  while i <= max_step:
    if i not in steps_by_num:
      i += 1
      continue
    expanded.append(i)
    if i in loop_by_end:
      start, count = loop_by_end[i]
      for _ in range(count - 1):
        for j in range(start, i + 1):
          if j in steps_by_num:
            expanded.append(j)
    i += 1
  return expanded


def estimate_method_duration_seconds(odtc: ODTCProtocol) -> float:
  """Estimate total method duration from steps (ramp + plateau + overshoot, with loops).

  Per ODTC Firmware Command Set: duration is slope time + overshoot time + plateau
  time per step in consideration of the loops.

  Args:
    odtc: ODTCProtocol (kind='method') with steps and start_block_temperature.

  Returns:
    Estimated duration in seconds.
  """
  if not odtc.steps:
    return 0.0
  loops = _analyze_loop_structure(odtc.steps)
  step_nums = _expand_step_sequence(odtc.steps, loops)
  steps_by_num = {s.number: s for s in odtc.steps}

  total = 0.0
  prev_temp = odtc.start_block_temperature
  min_slope = 0.1

  for step_num in step_nums:
    step = steps_by_num[step_num]
    slope = max(abs(step.slope), min_slope)
    ramp_time = abs(step.plateau_temperature - prev_temp) / slope
    total += ramp_time + step.plateau_time + step.overshoot_time
    prev_temp = step.plateau_temperature

  return total


def odtc_method_to_protocol(odtc: ODTCProtocol) -> Tuple["Protocol", ODTCConfig]:
  """Convert an ODTCProtocol (kind='method') to a Protocol with companion config.

  Args:
    odtc: The ODTCProtocol to convert.

  Returns:
    Tuple of (Protocol, ODTCConfig) where the config captures
    all ODTC-specific parameters needed to reconstruct the original method.
  """
  from pylabrobot.thermocycling.standard import Protocol

  step_settings: Dict[int, ODTCStepSettings] = {}
  for i, step in enumerate(odtc.steps):
    step_settings[i] = ODTCStepSettings(
      slope=step.slope,
      overshoot_slope1=step.overshoot_slope1,
      overshoot_temperature=step.overshoot_temperature,
      overshoot_time=step.overshoot_time,
      overshoot_slope2=step.overshoot_slope2,
      lid_temp=step.lid_temp,
      pid_number=step.pid_number,
    )

  config = ODTCConfig(
    name=odtc.name,
    creator=odtc.creator,
    description=odtc.description,
    datetime=odtc.datetime,
    fluid_quantity=odtc.fluid_quantity,
    variant=odtc.variant,
    plate_type=odtc.plate_type,
    lid_temperature=odtc.start_lid_temperature,
    start_lid_temperature=odtc.start_lid_temperature,
    post_heating=odtc.post_heating,
    pid_set=list(odtc.pid_set) if odtc.pid_set else [ODTCPID(number=1)],
    step_settings=step_settings,
    _validate=False,
  )

  stages = _build_odtc_stages_from_steps(odtc.steps)
  return Protocol(stages=cast(List[Stage], stages)), config
