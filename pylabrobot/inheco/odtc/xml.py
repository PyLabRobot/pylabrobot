"""ODTC XML serialization and parsing.

Key differences from the original:
- No ODTCStep subclass: steps parse to/from standard Step(Ramp, lid_temperature)
- Stage tree (Stage.inner_stages) is the canonical representation
- Step Number/GotoNumber/LoopNumber are computed from stage position at serialize time
- PIDNumber is always serialized as 1 (or from StepParams.backend_params if set)
- Loop analysis (_analyze_loop_structure, _build_stages_from_parsed_steps) lives here
  and is imported by protocol.py for duration/progress computation
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from pylabrobot.capabilities.thermocycling.standard import Overshoot, Protocol, Ramp, Stage, Step

from .model import (
  ODTCPID,
  ODTCMethodSet,
  ODTCProtocol,
  ODTCSensorValues,
  XMLField,
  XMLFieldType,
  _variant_to_device_code,
  normalize_variant,
)

T = TypeVar("T")


# =============================================================================
# Generic dataclass XML helpers (for ODTCPID, ODTCSensorValues)
# =============================================================================


def _get_xml_meta(f) -> XMLField:
  if "xml" in f.metadata:
    return cast(XMLField, f.metadata["xml"])
  return XMLField(tag=None, field_type=XMLFieldType.ELEMENT)


def _get_tag(f, meta: XMLField) -> str:
  return meta.tag if meta.tag else f.name


def _get_inner_type(type_hint) -> Optional[Type[Any]]:
  origin = get_origin(type_hint)
  args = get_args(type_hint)
  if origin is list and args:
    return cast(Type[Any], args[0])
  if origin is Union and type(None) in args:
    result = next((a for a in args if a is not type(None)), None)
    return cast(Type[Any], result) if result is not None else None
  return None


def _is_dataclass_type(tp: Type) -> bool:
  return hasattr(tp, "__dataclass_fields__")


def _parse_value(text: Optional[str], field_type: Type, scale: float = 1.0) -> Any:
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
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, float):
    scaled = value / scale if scale != 1.0 else value
    if scaled == int(scaled):
      return str(int(scaled))
    return str(scaled)
  if isinstance(value, int):
    return str(int(value / scale) if scale != 1.0 else value)
  return str(value)


def from_xml(elem: ET.Element, cls: Type[T]) -> T:
  """Deserialize an XML element to a dataclass (for ODTCPID, ODTCSensorValues)."""
  if not _is_dataclass_type(cls):
    raise TypeError(f"{cls} is not a dataclass")
  kwargs: Dict[str, Any] = {}
  type_hints = get_type_hints(cls)
  for f in fields(cls):  # type: ignore[arg-type]
    meta = _get_xml_meta(f)
    tag = _get_tag(f, meta)
    field_type = type_hints.get(f.name, f.type)
    inner_type = _get_inner_type(field_type)
    actual_type = inner_type if inner_type and get_origin(field_type) is Union else field_type
    if meta.field_type == XMLFieldType.ATTRIBUTE:
      raw = elem.attrib.get(tag)
      if raw is not None:
        kwargs[f.name] = _parse_value(raw, actual_type, meta.scale)
      elif meta.default is not None:
        kwargs[f.name] = meta.default
    elif meta.field_type == XMLFieldType.ELEMENT:
      child = elem.find(tag)
      if child is not None and child.text:
        kwargs[f.name] = _parse_value(child.text, actual_type, meta.scale)
      elif meta.default is not None:
        kwargs[f.name] = meta.default
    elif meta.field_type == XMLFieldType.CHILD_LIST:
      list_type = _get_inner_type(field_type)
      if list_type and _is_dataclass_type(list_type):
        children = elem.findall(tag)
        kwargs[f.name] = [from_xml(c, list_type) for c in children]
      else:
        kwargs[f.name] = []
  return cls(**kwargs)


def to_xml(obj: Any, tag_name: Optional[str] = None, parent: Optional[ET.Element] = None) -> ET.Element:
  """Serialize a dataclass to XML (for ODTCPID, ODTCSensorValues)."""
  if not _is_dataclass_type(type(obj)):
    raise TypeError(f"{type(obj)} is not a dataclass")
  if tag_name is None:
    tag_name = type(obj).__name__
  elem = ET.SubElement(parent, tag_name) if parent is not None else ET.Element(tag_name)
  for f in fields(type(obj)):
    meta = _get_xml_meta(f)
    tag = _get_tag(f, meta)
    value = getattr(obj, f.name)
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
# Step XML parsing — flat <Step> elements → Step(Ramp, lid_temperature)
# =============================================================================


@dataclass
class _ParsedStep:
  """Intermediate representation of a raw <Step> XML element.

  Carries all XML fields including goto/loop/number used for stage
  reconstruction. Converted to Step after stage structure is resolved.
  """

  number: int
  slope: float
  plateau_temperature: float
  plateau_time: float
  overshoot_slope1: float
  overshoot_temperature: float
  overshoot_time: float
  overshoot_slope2: float
  goto_number: int
  loop_number: int
  lid_temp: float
  pid_number: int = 1

  def to_step(self) -> Step:
    """Convert to a standard Step, encoding overshoot/ramp/lid."""
    overshoot = (
      Overshoot(
        target_temp=self.overshoot_temperature,
        hold_seconds=self.overshoot_time,
        return_rate=self.overshoot_slope2,
      )
      if self.overshoot_temperature > 0
      else None
    )
    ramp = Ramp(rate=self.slope, overshoot=overshoot)
    return Step(
      temperature=self.plateau_temperature,
      hold_seconds=self.plateau_time,
      ramp=ramp,
      lid_temperature=self.lid_temp,
    )


def _read_opt_elem(elem: ET.Element, tag: str, default: Any = None, parse_float: bool = False) -> Any:
  child = elem.find(tag)
  if child is None or child.text is None:
    return default
  text = child.text.strip()
  if not text:
    return default
  if parse_float:
    return float(text)
  return text


def _parse_step_element(elem: ET.Element) -> _ParsedStep:
  """Parse a single <Step> XML element to a _ParsedStep."""
  def f(tag: str, default: float = 0.0) -> float:
    return float(_read_opt_elem(elem, tag, default, parse_float=True))

  def i(tag: str, default: int = 0) -> int:
    return int(float(_read_opt_elem(elem, tag, default) or default))

  return _ParsedStep(
    number=i("Number"),
    slope=f("Slope"),
    plateau_temperature=f("PlateauTemperature"),
    plateau_time=f("PlateauTime"),
    overshoot_slope1=f("OverShootSlope1"),
    overshoot_temperature=f("OverShootTemperature"),
    overshoot_time=f("OverShootTime"),
    overshoot_slope2=f("OverShootSlope2"),
    goto_number=i("GotoNumber"),
    loop_number=i("LoopNumber"),
    lid_temp=f("LidTemp", default=110.0),
    pid_number=i("PIDNumber", default=1),
  )


# =============================================================================
# Loop analysis — rebuild Stage tree from flat parsed steps
# (also imported by odtc_protocol.py for duration/progress computation)
# =============================================================================


def analyze_loop_structure(parsed_steps: List[_ParsedStep]) -> List[Tuple[int, int, int]]:
  """Identify loops from goto/loop numbers.

  Returns list of (start_step_number, end_step_number, total_repeats) sorted by end position.
  total_repeats = LoopNumber + 1 (LoopNumber is "additional" iterations per firmware).
  """
  loops = []
  for s in parsed_steps:
    if s.goto_number > 0:
      loops.append((s.goto_number, s.number, s.loop_number + 1))
  return sorted(loops, key=lambda x: x[1])


def _build_one_stage_for_range(
  steps_by_num: Dict[int, _ParsedStep],
  loops: List[Tuple[int, int, int]],
  start: int,
  end: int,
  repeats: int,
) -> Stage:
  """Recursively build a Stage for the step range [start, end] with given repeats."""
  inner_loops = [
    (s, e, r) for (s, e, r) in loops if start <= s and e <= end and (start, end) != (s, e)
  ]
  inner_loops_sorted = sorted(inner_loops, key=lambda x: x[0])

  if not inner_loops_sorted:
    steps = [steps_by_num[n].to_step() for n in range(start, end + 1) if n in steps_by_num]
    return Stage(steps=steps, repeats=repeats, inner_stages=[])

  # Partition range into step segments and inner loops
  step_nums_in_range = set(range(start, end + 1))
  for is_, ie, _ in inner_loops_sorted:
    for n in range(is_, ie + 1):
      step_nums_in_range.discard(n)

  step_groups: List[List[int]] = []
  pos = start
  for is_, ie, ir in inner_loops_sorted:
    group = [n for n in range(pos, is_) if n in steps_by_num]
    if group:
      step_groups.append(group)
    pos = ie + 1
  if pos <= end:
    group = [n for n in range(pos, end + 1) if n in steps_by_num]
    if group:
      step_groups.append(group)

  steps_list: List[Step] = []
  inner_stages_list: List[Stage] = []
  for gi, (is_, ie, ir) in enumerate(inner_loops_sorted):
    if gi < len(step_groups):
      steps_list.extend(steps_by_num[n].to_step() for n in step_groups[gi])
    inner_stages_list.append(_build_one_stage_for_range(steps_by_num, loops, is_, ie, ir))
  if len(step_groups) > len(inner_loops_sorted):
    steps_list.extend(steps_by_num[n].to_step() for n in step_groups[len(inner_loops_sorted)])

  return Stage(steps=steps_list, repeats=repeats, inner_stages=inner_stages_list)


def build_stages_from_parsed_steps(parsed_steps: List[_ParsedStep]) -> List[Stage]:
  """Build a Stage tree from flat parsed steps using goto/loop structure."""
  if not parsed_steps:
    return []
  steps_by_num = {s.number: s for s in parsed_steps}
  loops = analyze_loop_structure(parsed_steps)
  max_step = max(s.number for s in parsed_steps)

  if not loops:
    flat = [steps_by_num[n].to_step() for n in range(1, max_step + 1) if n in steps_by_num]
    return [Stage(steps=flat, repeats=1, inner_stages=[])]

  def contains(outer: Tuple[int, int, int], inner: Tuple[int, int, int]) -> bool:
    (s, e, _), (s2, e2, _) = outer, inner
    return s <= s2 and e2 <= e and (s, e) != (s2, e2)

  top_level = [L for L in loops if not any(contains(M, L) for M in loops if M != L)]
  top_level.sort(key=lambda x: (x[0], x[1]))
  step_nums_in_top_level: set = set()
  for s, e, _ in top_level:
    for n in range(s, e + 1):
      step_nums_in_top_level.add(n)

  stages: List[Stage] = []
  i = 1
  while i <= max_step:
    if i not in steps_by_num:
      i += 1
      continue
    if i not in step_nums_in_top_level:
      flat_steps: List[Step] = []
      while i <= max_step and i in steps_by_num and i not in step_nums_in_top_level:
        flat_steps.append(steps_by_num[i].to_step())
        i += 1
      if flat_steps:
        stages.append(Stage(steps=flat_steps, repeats=1, inner_stages=[]))
      continue
    for start, end, repeats in top_level:
      if start <= i <= end:
        stages.append(_build_one_stage_for_range(steps_by_num, loops, start, end, repeats))
        i = end + 1
        break
    else:
      i += 1

  return stages


# =============================================================================
# Stage tree → flat XML steps (serialization)
# =============================================================================


def _flatten_stages_for_xml(
  stages: List[Stage],
) -> List[Tuple[Step, int, int, int]]:
  """Walk the stage tree and produce (step, number, goto_number, loop_number) tuples.

  Step numbers are assigned sequentially starting from 1.
  GotoNumber/LoopNumber are derived from Stage.repeats and Stage.inner_stages.
  """
  result: List[Tuple[Step, int, int, int]] = []
  _flatten_stage_list(stages, result, [1])
  return result


def _flatten_stage_list(
  stages: List[Stage],
  result: List[Tuple[Step, int, int, int]],
  counter: List[int],
) -> None:
  for stage in stages:
    _flatten_one_stage(stage, result, counter)


def _flatten_one_stage(
  stage: Stage,
  result: List[Tuple[Step, int, int, int]],
  counter: List[int],
) -> None:
  """Recursively flatten one Stage into (step, number, goto, loop) tuples."""
  first_num = counter[0]
  inner_stages = stage.inner_stages or []
  steps = stage.steps

  # Interleave steps and inner_stages (steps[0], inner_stages[0], steps[1], ...)
  for gi, inner in enumerate(inner_stages):
    if gi < len(steps):
      step = steps[gi]
      result.append((step, counter[0], 0, 0))
      counter[0] += 1
    _flatten_one_stage(inner, result, counter)
  if len(steps) > len(inner_stages):
    for step in steps[len(inner_stages):]:
      result.append((step, counter[0], 0, 0))
      counter[0] += 1
  elif not inner_stages:
    for step in steps:
      result.append((step, counter[0], 0, 0))
      counter[0] += 1

  # Set goto/loop on last produced item for this stage if repeats > 1
  if stage.repeats > 1 and result:
    last_idx = _find_last_in_range(result, first_num, counter[0] - 1)
    if last_idx >= 0:
      step, num, _, _ = result[last_idx]
      result[last_idx] = (step, num, first_num, stage.repeats - 1)


def _find_last_in_range(
  result: List[Tuple[Step, int, int, int]],
  first_num: int,
  last_num: int,
) -> int:
  """Find the index of the last entry with step number <= last_num and >= first_num."""
  for i in range(len(result) - 1, -1, -1):
    _, num, _, _ = result[i]
    if first_num <= num <= last_num:
      return i
  return -1


def _step_to_xml_element(
  step: Step,
  number: int,
  goto_number: int,
  loop_number: int,
  parent: ET.Element,
  pid_number: int = 1,
) -> None:
  """Write a single <Step> element from a Step and its positional metadata."""
  elem = ET.SubElement(parent, "Step")
  ramp = step.ramp
  slope = ramp.rate if ramp.rate != float("inf") else 4.4
  os_temp = ramp.overshoot.target_temp if ramp.overshoot else 0.0
  os_time = ramp.overshoot.hold_seconds if ramp.overshoot else 0.0
  os2 = ramp.overshoot.return_rate if ramp.overshoot else 2.2
  os1 = slope  # OverShootSlope1 == Slope (approach rate equals ramp rate)
  lid_temp = step.lid_temperature if step.lid_temperature is not None else 110.0

  # Check for per-step backend_params PIDNumber override
  from pylabrobot.capabilities.capability import BackendParams  # noqa: F401 (lazy import)
  try:
    # Avoid circular import; just try attribute access
    bp = step.backend_params
    if bp is not None and hasattr(bp, "pid_number"):
      pid_number = bp.pid_number  # type: ignore[union-attr]
  except Exception:
    pass

  ET.SubElement(elem, "Number").text = str(number)
  ET.SubElement(elem, "Slope").text = _format_value(slope)
  ET.SubElement(elem, "PlateauTemperature").text = _format_value(step.temperature)
  ET.SubElement(elem, "PlateauTime").text = _format_value(step.hold_seconds)
  ET.SubElement(elem, "OverShootSlope1").text = _format_value(os1)
  ET.SubElement(elem, "OverShootTemperature").text = _format_value(os_temp)
  ET.SubElement(elem, "OverShootTime").text = _format_value(os_time)
  ET.SubElement(elem, "OverShootSlope2").text = _format_value(os2)
  ET.SubElement(elem, "GotoNumber").text = str(goto_number)
  ET.SubElement(elem, "LoopNumber").text = str(loop_number)
  ET.SubElement(elem, "PIDNumber").text = str(pid_number)
  ET.SubElement(elem, "LidTemp").text = _format_value(lid_temp)


# =============================================================================
# ODTCProtocol ↔ XML
# =============================================================================


def _parse_method_element_to_odtc_protocol(elem: ET.Element) -> ODTCProtocol:
  """Parse a <Method> element into ODTCProtocol with Stage tree."""
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

  parsed_steps = [_parse_step_element(step_elem) for step_elem in elem.findall("Step")]
  stages = build_stages_from_parsed_steps(parsed_steps)

  pid_set: List[ODTCPID] = []
  pid_set_elem = elem.find("PIDSet")
  if pid_set_elem is not None:
    pid_set = [from_xml(pid_elem, ODTCPID) for pid_elem in pid_set_elem.findall("PID")]
  if not pid_set:
    pid_set = [ODTCPID(number=1)]

  return ODTCProtocol(
    kind="method",
    stages=stages,
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
    stages=[],
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


def _odtc_protocol_to_method_xml(odtc_protocol: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='method') to <Method> XML element."""
  if odtc_protocol.kind != "method":
    raise ValueError("ODTCProtocol must have kind='method' to serialize as Method")

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
  ET.SubElement(elem, "FluidQuantity").text = str(int(odtc_protocol.fluid_quantity))
  ET.SubElement(elem, "PostHeating").text = "true" if odtc_protocol.post_heating else "false"
  ET.SubElement(elem, "StartBlockTemperature").text = _format_value(odtc_protocol.start_block_temperature)
  ET.SubElement(elem, "StartLidTemperature").text = _format_value(odtc_protocol.start_lid_temperature)

  flat = _flatten_stages_for_xml(odtc_protocol.stages)
  for step, number, goto, loop in flat:
    _step_to_xml_element(step, number, goto, loop, elem)

  if odtc_protocol.pid_set:
    pid_set_elem = ET.SubElement(elem, "PIDSet")
    for pid in odtc_protocol.pid_set:
      to_xml(pid, "PID", pid_set_elem)

  return elem


def _odtc_protocol_to_premethod_xml(odtc_protocol: ODTCProtocol, parent: ET.Element) -> ET.Element:
  """Serialize ODTCProtocol (kind='premethod') to <PreMethod> XML element."""
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
  ET.SubElement(elem, "TargetBlockTemperature").text = _format_value(odtc_protocol.target_block_temperature)
  ET.SubElement(elem, "TargetLidTemp").text = _format_value(odtc_protocol.target_lid_temperature)
  return elem


# =============================================================================
# Convenience functions
# =============================================================================


def parse_method_set_from_root(root: ET.Element) -> ODTCMethodSet:
  """Parse a MethodSet XML root element into ODTCMethodSet."""
  delete_elem = root.find("DeleteAllMethods")
  delete_all = delete_elem is not None and (delete_elem.text or "").lower() == "true"
  premethods = [_parse_premethod_element_to_odtc_protocol(pm) for pm in root.findall("PreMethod")]
  methods = [_parse_method_element_to_odtc_protocol(m) for m in root.findall("Method")]
  return ODTCMethodSet(delete_all_methods=delete_all, premethods=premethods, methods=methods)


def parse_method_set(xml_str: str) -> ODTCMethodSet:
  """Parse a MethodSet XML string."""
  root = ET.fromstring(xml_str)
  return parse_method_set_from_root(root)


def parse_method_set_file(filepath: str) -> ODTCMethodSet:
  """Parse a MethodSet XML file."""
  tree = ET.parse(filepath)
  return parse_method_set_from_root(tree.getroot())


def method_set_to_xml(method_set: ODTCMethodSet) -> str:
  """Serialize a MethodSet to XML string."""
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
