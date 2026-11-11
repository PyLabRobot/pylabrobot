"""Explicit ODTC MethodSet, SensorValues, and DataEvent XML codecs."""

import math
import xml.etree.ElementTree as ET

from pylabrobot.inheco.odtc.protocol import (
  ODTCPID,
  ODTCMethod,
  ODTCMethodSet,
  ODTCMethodStep,
  ODTCOvershoot,
  ODTCPreMethod,
  ODTCProgress,
  ODTCTemperatures,
  validate_method,
)
from pylabrobot.inheco.soap import required_text


def _number(root: ET.Element, name: str) -> float:
  """Read a required finite numeric element."""
  number = float(required_text(root, name))
  if not math.isfinite(number):
    raise ValueError(f"{name} must be finite")
  return number


def _boolean(root: ET.Element, name: str) -> bool:
  """Read the XML boolean forms used by the device."""
  value = required_text(root, name)
  if value not in ("true", "false", "1", "0"):
    raise ValueError(f"Invalid {name}: {value!r}")
  return value in ("true", "1")


def _fields(root: ET.Element, values: dict[str, str | float | int | bool]) -> None:
  """Write an explicit ordered mapping of device fields."""
  for name, value in values.items():
    text = str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, float):
      text = format(value, ".15g")
    ET.SubElement(root, name).text = text


def _metadata(root: ET.Element, method: ODTCMethod | ODTCPreMethod) -> None:
  """Write only metadata that was supplied by the user or instrument."""
  root.set("methodName", method.name)
  if method.creator is not None:
    root.set("creator", method.creator)
  if method.description is not None:
    root.set("description", method.description)
  if method.date_time is not None:
    root.set("dateTime", method.date_time)


def _write_pid(parent: ET.Element, pid: ODTCPID) -> None:
  """Encode one native PID set without reflection."""
  element = ET.SubElement(parent, "PID", number=str(pid.number))
  _fields(
    element,
    {
      "PHeating": pid.p_heating,
      "PCooling": pid.p_cooling,
      "IHeating": pid.i_heating,
      "ICooling": pid.i_cooling,
      "DHeating": pid.d_heating,
      "DCooling": pid.d_cooling,
      "PLid": pid.p_lid,
      "ILid": pid.i_lid,
    },
  )


def method_set_to_xml(method_set: ODTCMethodSet) -> str:
  """Serialize methods for an additive SetParameters upload; never delete stored methods."""
  root = ET.Element("MethodSet")
  names = [method.name for method in method_set.methods] + [p.name for p in method_set.premethods]
  if len(set(names)) != len(names):
    raise ValueError("Method and premethod names must be unique")
  for premethod in method_set.premethods:
    if not premethod.name.strip():
      raise ValueError("Premethod name must not be empty")
    element = ET.SubElement(root, "PreMethod")
    _metadata(element, premethod)
    _fields(
      element,
      {"TargetBlockTemperature": premethod.temperature, "TargetLidTemp": premethod.lid_temperature},
    )
  for method in method_set.methods:
    validate_method(method)
    element = ET.SubElement(root, "Method")
    _metadata(element, method)
    _fields(
      element,
      {
        "Variant": {96: 960000, 384: 384000}[method.variant],
        "PlateType": method.plate_type,
        "FluidQuantity": method.fluid_quantity,
        "PostHeating": method.post_heating,
        "StartBlockTemperature": method.start_temperature,
        "StartLidTemperature": method.start_lid_temperature,
      },
    )
    for step in method.steps:
      overshoot = step.overshoot
      _fields(
        ET.SubElement(element, "Step"),
        {
          "Number": step.number,
          "Slope": step.ramp_rate,
          "PlateauTemperature": step.temperature,
          "PlateauTime": step.duration,
          "OverShootSlope1": step.ramp_rate
          if overshoot.approach_rate is None
          else overshoot.approach_rate,
          "OverShootTemperature": overshoot.temperature,
          "OverShootTime": overshoot.duration,
          "OverShootSlope2": overshoot.return_rate,
          "GotoNumber": step.goto,
          "LoopNumber": step.loops,
          "PIDNumber": step.pid_number,
          "LidTemp": step.lid_temperature,
        },
      )
    pids = ET.SubElement(element, "PIDSet")
    for pid in method.pid_set:
      _write_pid(pids, pid)
  return ET.tostring(root, encoding="unicode")


def _read_pid(element: ET.Element) -> ODTCPID:
  """Decode every PID coefficient explicitly."""
  return ODTCPID(
    number=int(element.attrib["number"]),
    p_heating=_number(element, "PHeating"),
    p_cooling=_number(element, "PCooling"),
    i_heating=_number(element, "IHeating"),
    i_cooling=_number(element, "ICooling"),
    d_heating=_number(element, "DHeating"),
    d_cooling=_number(element, "DCooling"),
    p_lid=_number(element, "PLid"),
    i_lid=_number(element, "ILid"),
  )


def _read_step(element: ET.Element) -> ODTCMethodStep:
  """Preserve all native ramp, PID, and loop values on a downloaded step."""
  return ODTCMethodStep(
    number=int(required_text(element, "Number")),
    temperature=_number(element, "PlateauTemperature"),
    duration=_number(element, "PlateauTime"),
    ramp_rate=_number(element, "Slope"),
    lid_temperature=_number(element, "LidTemp"),
    overshoot=ODTCOvershoot(
      temperature=_number(element, "OverShootTemperature"),
      duration=_number(element, "OverShootTime"),
      return_rate=_number(element, "OverShootSlope2"),
      approach_rate=_number(element, "OverShootSlope1"),
    ),
    goto=int(required_text(element, "GotoNumber")),
    loops=int(required_text(element, "LoopNumber")),
    pid_number=int(required_text(element, "PIDNumber")),
  )


def parse_method_set(xml: str) -> ODTCMethodSet:
  """Read native methods and preparation methods without reconstructing guessed stages."""
  root = ET.fromstring(xml)
  if root.tag.rsplit("}", 1)[-1] != "MethodSet":
    raise ValueError("Expected MethodSet XML")
  methods = []
  for element in root.findall("{*}Method"):
    code = int(required_text(element, "Variant"))
    if code not in (960000, 384000):
      raise ValueError(f"Unsupported ODTC variant code {code}")
    method = ODTCMethod(
      name=element.attrib["methodName"],
      variant=96 if code == 960000 else 384,
      plate_type=int(required_text(element, "PlateType")),
      fluid_quantity=int(required_text(element, "FluidQuantity")),
      post_heating=_boolean(element, "PostHeating"),
      start_temperature=_number(element, "StartBlockTemperature"),
      start_lid_temperature=_number(element, "StartLidTemperature"),
      steps=[_read_step(step) for step in element.findall("{*}Step")],
      pid_set=[_read_pid(pid) for pid in element.findall("{*}PIDSet/{*}PID")],
      creator=element.get("creator"),
      description=element.get("description"),
      date_time=element.get("dateTime"),
    )
    validate_method(method)
    methods.append(method)
  premethods = [
    ODTCPreMethod(
      name=element.attrib["methodName"],
      temperature=_number(element, "TargetBlockTemperature"),
      lid_temperature=_number(element, "TargetLidTemp"),
      creator=element.get("creator"),
      description=element.get("description"),
      date_time=element.get("dateTime"),
    )
    for element in root.findall("{*}PreMethod")
  ]
  return ODTCMethodSet(methods, premethods)


def parse_sensor_values(xml: str) -> ODTCTemperatures:
  """Convert all eight integer sensor readings from hundredths of a degree."""
  root = ET.fromstring(xml)
  if root.tag.rsplit("}", 1)[-1] != "SensorValues":
    raise ValueError("Expected SensorValues XML")
  return ODTCTemperatures(
    mount=_number(root, "Mount") / 100,
    mount_monitor=_number(root, "Mount_Monitor") / 100,
    lid=_number(root, "Lid") / 100,
    lid_monitor=_number(root, "Lid_Monitor") / 100,
    ambient=_number(root, "Ambient") / 100,
    pcb=_number(root, "PCB") / 100,
    heatsink=_number(root, "Heatsink") / 100,
    heatsink_tec=_number(root, "Heatsink_TEC") / 100,
    timestamp=root.get("timestamp"),
  )


def parse_progress(xml: str) -> ODTCProgress:
  """Read the four documented DataCapture series, rejecting missing or mismatched samples."""
  outer = ET.fromstring(xml)
  data = required_text(outer, "AnyData")
  root = ET.fromstring(data)
  expected = {
    "Elapsed time": "ms",
    "Target temperature": "1/100°C",
    "Current temperature": "1/100°C",
    "LID temperature": "1/100°C",
  }
  samples = {}
  sample_count = None
  for series in root.findall(".//{*}dataSeries"):
    name = series.attrib["nameId"]
    if name not in expected:
      continue
    if name in samples or series.attrib["unit"] != expected[name]:
      raise ValueError(f"Duplicate series or unexpected units for {name}")
    values = series.findall("{*}seriesValueSet/{*}integerValue")
    if not values or (sample_count is not None and len(values) != sample_count):
      raise ValueError("DataEvent series must contain matching nonempty samples")
    sample_count = len(values)
    text = values[-1].text
    if text is None:
      raise ValueError(f"Missing value for {name}")
    samples[name] = int(text)
  if samples.keys() != expected.keys():
    raise ValueError(f"DataEvent is missing series: {expected.keys() - samples.keys()}")
  return ODTCProgress(
    samples["Elapsed time"] / 1000,
    samples["Target temperature"] / 100,
    samples["Current temperature"] / 100,
    samples["LID temperature"] / 100,
  )
