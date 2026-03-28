"""ODTC XML serialization and parsing.

Schema-driven XML serialization for MethodSet, SensorValues, and related
ODTC dataclasses. Handles Method and PreMethod XML elements and round-trip
serialization of ODTCProtocol.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import fields
from typing import (
  Any,
  Dict,
  List,
  Optional,
  Type,
  TypeVar,
  Union,
  cast,
  get_args,
  get_origin,
  get_type_hints,
)


from .odtc_model import (
  ODTCPID,
  ODTCMethodSet,
  ODTCProtocol,
  ODTCSensorValues,
  ODTCStep,
  XMLField,
  XMLFieldType,
  _variant_to_device_code,
  normalize_variant,
)

T = TypeVar("T")


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


def to_xml(
  obj: Any, tag_name: Optional[str] = None, parent: Optional[ET.Element] = None
) -> ET.Element:
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
  name = elem.attrib["methodName"]
  creator = elem.attrib.get("creator")
  description = elem.attrib.get("description")
  datetime_ = elem.attrib["dateTime"]
  variant = normalize_variant(int(float(_read_opt_elem(elem, "Variant") or 960000)))
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
    is_scratch=False,
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
  )


def _parse_premethod_element_to_odtc_protocol(elem: ET.Element) -> ODTCProtocol:
  """Parse a <PreMethod> element into ODTCProtocol (kind='premethod')."""
  name = elem.attrib.get("methodName") or ""
  creator = elem.attrib.get("creator")
  description = elem.attrib.get("description")
  datetime_ = elem.attrib.get("dateTime")
  target_block_temperature = float(_read_opt_elem(elem, "TargetBlockTemperature") or 0.0)
  target_lid_temperature = float(_read_opt_elem(elem, "TargetLidTemp") or 0.0)
  return ODTCProtocol(
    variant=96,
    plate_type=0,
    fluid_quantity=0,
    post_heating=False,
    start_block_temperature=0.0,
    start_lid_temperature=0.0,
    steps=[],
    pid_set=[ODTCPID(number=1)],
    kind="premethod",
    name=name,
    is_scratch=False,
    creator=creator,
    description=description,
    datetime=datetime_,
    target_block_temperature=target_block_temperature,
    target_lid_temperature=target_lid_temperature,
  )


def _get_steps_for_serialization(odtc_protocol: ODTCProtocol) -> List[ODTCStep]:
  """Return canonical ODTCStep list for serializing an ODTCProtocol (kind='method')."""
  return odtc_protocol.steps


def _odtc_protocol_to_method_xml(odtc_protocol: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='method') to <Method> XML."""
  if odtc_protocol.kind != "method":
    raise ValueError("ODTCProtocol must have kind='method' to serialize as Method")
  steps_to_serialize = _get_steps_for_serialization(odtc_protocol)
  elem = ET.SubElement(parent, "Method")
  elem.set("methodName", odtc_protocol.name)
  if odtc_protocol.creator:
    elem.set("creator", odtc_protocol.creator)
  if odtc_protocol.description:
    elem.set("description", odtc_protocol.description)
  if odtc_protocol.datetime:
    elem.set("dateTime", odtc_protocol.datetime)
  ET.SubElement(elem, "Variant").text = str(_variant_to_device_code(odtc_protocol.variant))
  ET.SubElement(elem, "PlateType").text = str(odtc_protocol.plate_type)
  ET.SubElement(elem, "FluidQuantity").text = str(odtc_protocol.fluid_quantity)
  ET.SubElement(elem, "PostHeating").text = "true" if odtc_protocol.post_heating else "false"
  ET.SubElement(elem, "StartBlockTemperature").text = _format_value(
    odtc_protocol.start_block_temperature
  )
  ET.SubElement(elem, "StartLidTemperature").text = _format_value(
    odtc_protocol.start_lid_temperature
  )
  for step in steps_to_serialize:
    to_xml(step, "Step", elem)
  if odtc_protocol.pid_set:
    pid_set_elem = ET.SubElement(elem, "PIDSet")
    for pid in odtc_protocol.pid_set:
      to_xml(pid, "PID", pid_set_elem)
  return elem


def _odtc_protocol_to_premethod_xml(odtc_protocol: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='premethod') to <PreMethod> XML."""
  if odtc_protocol.kind != "premethod":
    raise ValueError("ODTCProtocol must have kind='premethod' to serialize as PreMethod")
  elem = ET.SubElement(parent, "PreMethod")
  elem.set("methodName", odtc_protocol.name)
  if odtc_protocol.creator:
    elem.set("creator", odtc_protocol.creator)
  if odtc_protocol.description:
    elem.set("description", odtc_protocol.description)
  if odtc_protocol.datetime:
    elem.set("dateTime", odtc_protocol.datetime)
  ET.SubElement(elem, "TargetBlockTemperature").text = _format_value(
    odtc_protocol.target_block_temperature
  )
  ET.SubElement(elem, "TargetLidTemp").text = _format_value(odtc_protocol.target_lid_temperature)
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
  ET.SubElement(root, "DeleteAllMethods").text = (
    "true" if method_set.delete_all_methods else "false"
  )
  for pm in method_set.premethods:
    _odtc_protocol_to_premethod_xml(pm, root)
  for m in method_set.methods:
    _odtc_protocol_to_method_xml(m, root)
  return ET.tostring(root, encoding="unicode", xml_declaration=True)


def parse_sensor_values(xml_str: str) -> ODTCSensorValues:
  """Parse SensorValues XML string."""
  root = ET.fromstring(xml_str)
  return from_xml(root, ODTCSensorValues)
