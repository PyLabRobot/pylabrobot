"""
Schema-driven XML serialization for ODTC MethodSet.

Uses dataclass field metadata to define XML mapping, enabling automatic
bidirectional conversion between Python objects and XML.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union, get_args, get_origin, get_type_hints

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Hardware Constraints
# =============================================================================


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
class ODTCStep:
  """A single step in an ODTC method."""

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
class ODTCPreMethod:
  """ODTC PreMethod for initial temperature conditioning."""

  name: str = xml_attr(tag="methodName", default="")
  target_block_temperature: float = xml_field(tag="TargetBlockTemperature", default=0.0)
  target_lid_temperature: float = xml_field(tag="TargetLidTemp", default=0.0)
  creator: Optional[str] = xml_attr(tag="creator", default=None)
  description: Optional[str] = xml_attr(tag="description", default=None)
  datetime: Optional[str] = xml_attr(tag="dateTime", default=None)


@dataclass
class ODTCMethod:
  """Full ODTC Method with thermal cycling parameters."""

  name: str = xml_attr(tag="methodName", default="")
  variant: int = xml_field(tag="Variant", default=960000)
  plate_type: int = xml_field(tag="PlateType", default=0)
  fluid_quantity: int = xml_field(tag="FluidQuantity", default=0)
  post_heating: bool = xml_field(tag="PostHeating", default=False)
  start_block_temperature: float = xml_field(tag="StartBlockTemperature", default=0.0)
  start_lid_temperature: float = xml_field(tag="StartLidTemperature", default=0.0)
  steps: List[ODTCStep] = xml_child_list(tag="Step")
  pid_set: List[ODTCPID] = xml_child_list(tag="PID")
  creator: Optional[str] = xml_attr(tag="creator", default=None)
  description: Optional[str] = xml_attr(tag="description", default=None)
  datetime: Optional[str] = xml_attr(tag="dateTime", default=None)

  def get_loop_structure(self) -> List[tuple]:
    """
    Analyze loop structure and return list of (loop_start_step, loop_end_step, repeat_count).
    Step numbers are 1-indexed as in the XML.
    """
    loops = []
    for step in self.steps:
      if step.goto_number > 0 and step.loop_number > 0:
        loops.append((step.goto_number, step.number, step.loop_number + 1))
    return loops


@dataclass
class ODTCMethodSet:
  """Container for all methods and premethods."""

  delete_all_methods: bool = xml_field(tag="DeleteAllMethods", default=False)
  premethods: List[ODTCPreMethod] = xml_child_list(tag="PreMethod")
  methods: List[ODTCMethod] = xml_child_list(tag="Method")


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


# =============================================================================
# Protocol Conversion Config Classes
# =============================================================================


@dataclass
class ODTCStepSettings:
  """Per-step ODTC parameters for Protocol to ODTCMethod conversion.

  When converting ODTCMethod to Protocol, these capture the original values.
  When converting Protocol to ODTCMethod, these override defaults.
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
    2. When extracting from ODTCMethod: Captures all params for lossless round-trip

  Validation is performed on construction by default. Set _validate=False to skip
  validation (useful when reading data from a trusted source like the device).
  """

  # Method identification/metadata
  name: str = "converted_protocol"
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
      raise ValueError(f"ODTCConfig validation failed:\n  - " + "\n  - ".join(errors))

    return errors


# =============================================================================
# Generic XML Serialization/Deserialization
# =============================================================================


def _get_xml_meta(f) -> XMLField:
  """Get XMLField metadata from a dataclass field, or create default."""
  if "xml" in f.metadata:
    return f.metadata["xml"]
  # Default: element with field name as tag
  return XMLField(tag=None, field_type=XMLFieldType.ELEMENT)


def _get_tag(f, meta: XMLField) -> str:
  """Get the XML tag name for a field."""
  return meta.tag if meta.tag else f.name


def _get_inner_type(type_hint) -> Optional[Type]:
  """Extract the inner type from List[T] or Optional[T]."""
  origin = get_origin(type_hint)
  args = get_args(type_hint)
  if origin is list and args:
    return args[0]
  if origin is Union and type(None) in args:
    # Optional[T] is Union[T, None]
    return next((a for a in args if a is not type(None)), None)
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

  for f in fields(cls):
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

  for f in fields(type(obj)):
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
# MethodSet-specific parsing (handles PIDSet wrapper)
# =============================================================================


def _parse_method(elem: ET.Element) -> ODTCMethod:
  """Parse a Method element, handling the PIDSet wrapper for PID elements."""
  # First parse the basic fields
  method = from_xml(elem, ODTCMethod)

  # Handle PIDSet wrapper specially
  pid_set_elem = elem.find("PIDSet")
  if pid_set_elem is not None:
    pids = []
    for pid_elem in pid_set_elem.findall("PID"):
      pids.append(from_xml(pid_elem, ODTCPID))
    method.pid_set = pids

  return method


def _method_to_xml(method: ODTCMethod, parent: ET.Element) -> ET.Element:
  """Serialize a Method to XML, handling the PIDSet wrapper."""
  elem = ET.SubElement(parent, "Method")

  # Set attributes
  elem.set("methodName", method.name)
  if method.creator:
    elem.set("creator", method.creator)
  if method.description:
    elem.set("description", method.description)
  if method.datetime:
    elem.set("dateTime", method.datetime)

  # Add child elements
  ET.SubElement(elem, "Variant").text = str(method.variant)
  ET.SubElement(elem, "PlateType").text = str(method.plate_type)
  ET.SubElement(elem, "FluidQuantity").text = str(method.fluid_quantity)
  ET.SubElement(elem, "PostHeating").text = "true" if method.post_heating else "false"
  ET.SubElement(elem, "StartBlockTemperature").text = str(method.start_block_temperature)
  ET.SubElement(elem, "StartLidTemperature").text = str(method.start_lid_temperature)

  # Add steps
  for step in method.steps:
    to_xml(step, "Step", elem)

  # Add PIDSet wrapper if there are PIDs
  if method.pid_set:
    pid_set_elem = ET.SubElement(elem, "PIDSet")
    for pid in method.pid_set:
      to_xml(pid, "PID", pid_set_elem)

  return elem


# =============================================================================
# Convenience Functions
# =============================================================================


def parse_method_set(xml_str: str) -> ODTCMethodSet:
  """Parse a MethodSet XML string."""
  root = ET.fromstring(xml_str)

  # Parse DeleteAllMethods
  delete_elem = root.find("DeleteAllMethods")
  delete_all = False
  if delete_elem is not None and delete_elem.text:
    delete_all = delete_elem.text.lower() == "true"

  # Parse PreMethods
  premethods = [from_xml(pm, ODTCPreMethod) for pm in root.findall("PreMethod")]

  # Parse Methods (with special PIDSet handling)
  methods = [_parse_method(m) for m in root.findall("Method")]

  return ODTCMethodSet(
    delete_all_methods=delete_all,
    premethods=premethods,
    methods=methods,
  )


def parse_method_set_file(filepath: str) -> ODTCMethodSet:
  """Parse a MethodSet XML file."""
  tree = ET.parse(filepath)
  root = tree.getroot()

  # Parse DeleteAllMethods
  delete_elem = root.find("DeleteAllMethods")
  delete_all = False
  if delete_elem is not None and delete_elem.text:
    delete_all = delete_elem.text.lower() == "true"

  # Parse PreMethods
  premethods = [from_xml(pm, ODTCPreMethod) for pm in root.findall("PreMethod")]

  # Parse Methods (with special PIDSet handling)
  methods = [_parse_method(m) for m in root.findall("Method")]

  return ODTCMethodSet(
    delete_all_methods=delete_all,
    premethods=premethods,
    methods=methods,
  )


def method_set_to_xml(method_set: ODTCMethodSet) -> str:
  """Serialize a MethodSet to XML string."""
  root = ET.Element("MethodSet")

  # Add DeleteAllMethods
  ET.SubElement(root, "DeleteAllMethods").text = "true" if method_set.delete_all_methods else "false"

  # Add PreMethods
  for pm in method_set.premethods:
    to_xml(pm, "PreMethod", root)

  # Add Methods (with special PIDSet handling)
  for m in method_set.methods:
    _method_to_xml(m, root)

  return ET.tostring(root, encoding="unicode", xml_declaration=True)


def parse_sensor_values(xml_str: str) -> ODTCSensorValues:
  """Parse SensorValues XML string."""
  root = ET.fromstring(xml_str)
  return from_xml(root, ODTCSensorValues)


# =============================================================================
# Method Lookup Helpers
# =============================================================================


def get_method_by_name(method_set: ODTCMethodSet, name: str) -> Optional[ODTCMethod]:
  """Find a method by name."""
  return next((m for m in method_set.methods if m.name == name), None)


def get_premethod_by_name(method_set: ODTCMethodSet, name: str) -> Optional[ODTCPreMethod]:
  """Find a premethod by name."""
  return next((pm for pm in method_set.premethods if pm.name == name), None)


def list_method_names(method_set: ODTCMethodSet) -> List[str]:
  """Get all method names."""
  return [m.name for m in method_set.methods]


def list_premethod_names(method_set: ODTCMethodSet) -> List[str]:
  """Get all premethod names."""
  return [pm.name for pm in method_set.premethods]


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


def protocol_to_odtc_method(
  protocol: "Protocol",
  config: Optional[ODTCConfig] = None,
) -> ODTCMethod:
  """Convert a standard Protocol to an ODTCMethod.

  Args:
    protocol: Standard Protocol object with stages and steps.
    config: Optional ODTC config for device-specific parameters.
      If None, defaults are used.

  Returns:
    ODTCMethod ready for upload to ODTC device.

  Note:
    This function handles sequential stages with repeats. Each stage with
    repeats > 1 is converted to an ODTC loop using GotoNumber/LoopNumber.
  """
  # Import here to avoid circular imports
  from pylabrobot.thermocycling.standard import Protocol

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
      last_step.loop_number = stage.repeats - 1  # ODTC uses 0-based loop count

  # Determine start temperatures
  start_block_temp = protocol.stages[0].steps[0].temperature[0] if protocol.stages else 25.0
  start_lid_temp = (
    config.start_lid_temperature
    if config.start_lid_temperature is not None
    else config.lid_temperature
  )

  return ODTCMethod(
    name=config.name,
    variant=config.variant,
    plate_type=config.plate_type,
    fluid_quantity=config.fluid_quantity,
    post_heating=config.post_heating,
    start_block_temperature=start_block_temp,
    start_lid_temperature=start_lid_temp,
    steps=odtc_steps,
    pid_set=list(config.pid_set),  # Copy the list
    creator=config.creator,
    description=config.description,
    datetime=config.datetime,
  )


def _validate_no_nested_loops(method: ODTCMethod) -> None:
  """Validate that an ODTCMethod has no nested loops.

  Args:
    method: The ODTCMethod to validate.

  Raises:
    ValueError: If the method contains nested/overlapping loops.
  """
  loops = []
  for step in method.steps:
    if step.goto_number > 0:
      # (start_step, end_step, repeat_count) - using 1-based step numbers
      loops.append((step.goto_number, step.number, step.loop_number + 1))

  # Check all pairs for nesting/overlap
  for i, (start1, end1, _) in enumerate(loops):
    for j, (start2, end2, _) in enumerate(loops):
      if i >= j:
        continue  # Only check each pair once

      # Check for any kind of nesting or overlap:
      # 1. Loop 2 fully contained in loop 1: start1 <= start2 AND end2 <= end1
      #    (and they're not identical)
      # 2. Loop 1 fully contained in loop 2: start2 <= start1 AND end1 <= end2
      # 3. Partial overlap: start1 < start2 < end1 < end2
      # 4. Partial overlap: start2 < start1 < end2 < end1

      # For sequential loops, ranges don't overlap: end1 < start2 OR end2 < start1
      ranges_overlap = not (end1 < start2 or end2 < start1)

      # If ranges overlap and they're not identical, that's a problem
      if ranges_overlap and not (start1 == start2 and end1 == end2):
        raise ValueError(
          f"ODTCMethod '{method.name}' contains nested loops "
          f"(steps {start1}-{end1} and {start2}-{end2}) which cannot be "
          "converted to Protocol. Use ODTCMethod directly."
        )


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
      loops.append((step.goto_number, step.number, step.loop_number + 1))
  return sorted(loops, key=lambda x: x[1])  # Sort by end position


def odtc_method_to_protocol(
  method: ODTCMethod,
) -> Tuple["Protocol", ODTCConfig]:
  """Convert an ODTCMethod to a Protocol with companion config for lossless round-trip.

  Args:
    method: The ODTCMethod to convert.

  Returns:
    Tuple of (Protocol, ODTCConfig) where the config captures
    all ODTC-specific parameters needed to reconstruct the original method.

  Raises:
    ValueError: If method contains nested loops that can't be expressed in Protocol.

  Note:
    For methods without nested loops, the conversion is lossless. The returned
    config captures all ODTC-specific parameters (slopes, overshoot, PID, etc.)
    so that protocol_to_odtc_method(protocol, config) produces an equivalent method.
  """
  # Import here to avoid circular imports
  from pylabrobot.thermocycling.standard import Protocol, Stage, Step

  # Validate no nested loops
  _validate_no_nested_loops(method)

  # Analyze loop structure
  loops = _analyze_loop_structure(method.steps)

  # Build step settings for all ODTC-specific parameters
  step_settings: Dict[int, ODTCStepSettings] = {}
  for i, step in enumerate(method.steps):
    step_settings[i] = ODTCStepSettings(
      slope=step.slope,
      overshoot_slope1=step.overshoot_slope1,
      overshoot_temperature=step.overshoot_temperature,
      overshoot_time=step.overshoot_time,
      overshoot_slope2=step.overshoot_slope2,
      lid_temp=step.lid_temp,
      pid_number=step.pid_number,
    )

  # Build the config capturing all method-level parameters
  config = ODTCConfig(
    name=method.name,
    creator=method.creator,
    description=method.description,
    datetime=method.datetime,
    fluid_quantity=method.fluid_quantity,
    variant=method.variant,
    plate_type=method.plate_type,
    lid_temperature=method.start_lid_temperature,
    start_lid_temperature=method.start_lid_temperature,
    post_heating=method.post_heating,
    pid_set=list(method.pid_set) if method.pid_set else [ODTCPID(number=1)],
    step_settings=step_settings,
    _validate=False,  # Skip validation for data read from device
  )

  # Group steps into stages based on loop boundaries
  # Create a map of which step ends a loop and what its repeat count is
  loop_ends: Dict[int, int] = {}  # step_number -> repeat_count
  loop_starts: Dict[int, int] = {}  # end_step_number -> start_step_number

  for start, end, repeats in loops:
    loop_ends[end] = repeats
    loop_starts[end] = start

  # Build stages
  stages: List[Stage] = []
  current_stage_steps: List[Step] = []
  current_stage_start = 1  # 1-based step number where current stage starts

  for i, odtc_step in enumerate(method.steps):
    step_number = odtc_step.number  # 1-based

    # Create Protocol Step
    protocol_step = Step(
      temperature=[odtc_step.plateau_temperature],
      hold_seconds=odtc_step.plateau_time,
      rate=odtc_step.slope,
    )
    current_stage_steps.append(protocol_step)

    # Check if this step ends a loop
    if step_number in loop_ends:
      loop_start = loop_starts[step_number]
      repeats = loop_ends[step_number]

      # If the loop starts at the beginning of current stage, this is a repeating stage
      if loop_start == current_stage_start:
        # This entire stage repeats
        stages.append(Stage(steps=current_stage_steps, repeats=repeats))
        current_stage_steps = []
        current_stage_start = step_number + 1
      else:
        # Loop doesn't start at stage beginning - need to split
        # Steps before loop_start form a non-repeating stage
        # Steps from loop_start to here form a repeating stage
        pre_loop_count = loop_start - current_stage_start
        if pre_loop_count > 0:
          pre_loop_steps = current_stage_steps[:pre_loop_count]
          stages.append(Stage(steps=pre_loop_steps, repeats=1))

        loop_steps = current_stage_steps[pre_loop_count:]
        stages.append(Stage(steps=loop_steps, repeats=repeats))

        current_stage_steps = []
        current_stage_start = step_number + 1

  # Add any remaining steps as a final stage
  if current_stage_steps:
    stages.append(Stage(steps=current_stage_steps, repeats=1))

  protocol = Protocol(stages=stages)
  return protocol, config
