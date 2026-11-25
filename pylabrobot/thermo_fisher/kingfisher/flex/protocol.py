"""Lossless Flex BindIt inspection and script compatibility validation."""

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Optional, Union

from pylabrobot.resources import Plate
from pylabrobot.thermo_fisher.kingfisher.bdz_container import parse_xml, read_container

FLEX_TYPE_ID = "844c2ce4-e8cf-4c19-b8e6-c43037658b8c"
ProtocolSource = Union[str, Path, bytes]


class FlexProtocolError(ValueError):
  """Invalid container or unsupported script behavior."""


@dataclass(frozen=True)
class FlexProtocolIssue:
  """An execution blocker found offline, with a stable category and source location.

  Absence of issues means the driver accepts the export, not that this particular
  protocol has passed a hardware test. Line numbers refer to the original script,
  including metadata lines, rather than the filtered streaming command sequence.
  """

  code: str
  location: str
  message: str
  line_number: Optional[int] = None


@dataclass(frozen=True)
class FlexCommand:
  """An original script line and its one-based source line number."""

  line_number: int
  text: str

  @property
  def opcode(self) -> str:
    """Return the command's wire opcode."""
    return self.text.split(" ", 1)[0]


@dataclass(frozen=True)
class FlexPlate:
  """A layout plate; its name is not a physical carousel slot."""

  id: str
  name: str
  type_id: str


@dataclass(frozen=True)
class FlexTip:
  """Tip configuration with all ordered pickup/return plate references."""

  name: str
  type_id: str
  plate_ids: tuple[str, ...]


@dataclass(frozen=True)
class FlexDispense:
  """Required reagent addition to every well of a plate; volume is per well in uL."""

  plate_id: str
  plate_name: str
  reagent_name: str
  volume: float


@dataclass(frozen=True)
class FlexPrompt:
  """A plate request or protocol pause, with orientation from preceding MOR.

  For a pause, ``loading_slot`` is the accessible tray, not an unload instruction.
  """

  line_number: int
  action: Literal["load", "unload", "pause"]
  label: str
  processing_slot: int
  loading_slot: int
  additions: tuple[FlexDispense, ...] = ()
  plate_id: Optional[str] = None


def loading_slot(processing_slot: int) -> int:
  """Convert a completed MOR target to the printed tray at the loading station."""
  if not 1 <= processing_slot <= 8:
    raise ValueError("Flex processing slot must be in 1..8")
  return (processing_slot - 1 + 3) % 8 + 1


def _xml(payload: bytes, root_name: str) -> ET.Element:
  """Report shared XML validation failures as Flex protocol errors."""
  try:
    return parse_xml(payload, root_name)
  except ValueError as error:
    raise FlexProtocolError(str(error)) from error


# Supported command syntax. TEM bounds: Flex service manual, section 7.1.11.16.
_FORMS = {
  "DTL": r'DTL [0-4] "[^"\r\n]*"',
  "TIM": r"TIM [0-9]+",
  "MOR": r"MOR [1-8]",
  "MOS": r"MOS [01]",
  "MOM": r"MOM -?[0-9]+",
  "MOH": r"MOH [0-9]+ [0-9]+(?: 0 [0-9]+ 0)?",
  "WAI": r"WAI 1(?: [0-9]+)?",
  "PAU": r'PAU [012] "[^"\r\n]*"',
  "TXT": r'TXT (?:(?:[24678]|10|11) )?"[^"\r\n]*"',
  "TEM": r"TEM [0-9]+ [0-9]+",
  "MOI": r"MOI (?:0|310)",
}


def _temperature_supported(value: str) -> bool:
  """Accept documented integer Celsius setpoints, including zero for RT/off mode."""
  return re.fullmatch(r"(?:0|[1-8][0-9]|9[0-6])", value) is not None


def _inspect_script(script: str) -> tuple[tuple[FlexCommand, ...], tuple[FlexProtocolIssue, ...]]:
  """Collect syntax blockers and valid lines without authorizing partial execution."""
  issues = []
  if any((ord(char) < 32 and char not in "\r\n") or ord(char) > 126 for char in script):
    issues.append(
      FlexProtocolIssue(
        "script_encoding", "CommandScript", "CommandScript contains non-ASCII or control characters"
      )
    )
  commands = []
  for number, line in enumerate(script.splitlines(), 1):
    location = f"CommandScript/line[{number}]"
    if not line:
      issues.append(
        FlexProtocolIssue("empty_line", location, f"Empty script line {number}", number)
      )
      continue
    if any(ord(char) < 32 or ord(char) > 126 for char in line):
      issues.append(
        FlexProtocolIssue(
          "script_encoding",
          location,
          f"Non-ASCII or control character on script line {number}",
          number,
        )
      )
      continue
    opcode = line.split(" ", 1)[0]
    form = _FORMS.get(opcode)
    if form is None or re.fullmatch(form, line) is None:
      issues.append(
        FlexProtocolIssue(
          "unsupported_command", location, f"Unsupported script line {number}: {line!r}", number
        )
      )
      continue
    if any(
      len(value) > 11 or not -(2**31) <= int(value) < 2**31
      for value in re.findall(r"-?[0-9]+", line.split('"', 1)[0])
    ):
      issues.append(
        FlexProtocolIssue(
          "integer_range",
          location,
          f"Integer exceeds the supported representation on line {number}",
          number,
        )
      )
      continue
    if opcode == "TEM":
      _, target, interval = line.split()
      if not _temperature_supported(target) or (int(interval) != 0 and int(interval) < 100):
        issues.append(
          FlexProtocolIssue(
            "temperature_range",
            location,
            "TEM requires an integer target of 0 or 10..96 C and a reporting interval "
            "of 0 (disabled) or at least 100 centiseconds",
            number,
          )
        )
        continue
    commands.append(FlexCommand(number, line))
  if not script.splitlines():
    issues.append(FlexProtocolIssue("empty_script", "CommandScript", "Empty CommandScript"))
  return tuple(commands), tuple(issues)


def _preflight(script: str) -> tuple[FlexCommand, ...]:
  """Reject the entire script if any line is unsupported; never stream a valid subset."""
  commands, issues = _inspect_script(script)
  if issues:
    raise FlexProtocolError("; ".join(issue.message for issue in issues))
  return commands


def _command_prompt(command: FlexCommand, orientation: int) -> FlexPrompt:
  """Describe one validated PAU without conflating pauses and plate actions."""
  actions: dict[str, Literal["load", "unload", "pause"]] = {
    "0": "pause",
    "1": "load",
    "2": "unload",
  }
  return FlexPrompt(
    command.line_number,
    actions[command.text.split()[1]],
    command.text.split('"')[1],
    orientation,
    loading_slot(orientation),
  )


def _script_prompts(commands: tuple[FlexCommand, ...]) -> tuple[FlexPrompt, ...]:
  """Describe plate requests and protocol pauses using preceding orientations."""
  prompts = []
  orientation = 0
  for command in commands:
    if command.opcode == "MOR":
      orientation = int(command.text.split()[1])
    elif command.opcode == "PAU":
      prompts.append(_command_prompt(command, orientation))
  return tuple(prompts)


def _dispensing_prompts(
  root: ET.Element, commands: tuple[FlexCommand, ...], plates: tuple[FlexPlate, ...]
) -> dict[int, tuple[FlexDispense, ...]]:
  """Map standalone whole-plate additions to unambiguous exported PAU commands.

  Reject unmatched metadata, partial well groups and ambiguous plate/step names.
  The observed TXT 11/MOR/PAU/TXT/MOR sequence presents one plate for addition.
  """
  result: dict[int, tuple[FlexDispense, ...]] = {}
  plate_map = {p.id: p for p in plates}
  prompts = _script_prompts(commands)
  markers = {c.line_number for c in commands if c.text.startswith("TXT 11 ")}
  used_markers: set[int] = set()
  for pause in root.findall("./Protocol/Run/Protocol/.//Steps/Pause"):
    dispense = pause.find("Dispense")
    if dispense is None or dispense.get("enabled") != "true":
      continue
    name = pause.get("name", "")
    refs = pause.findall("Plates/Plate")
    if len(refs) != 1 or refs[0].get("wellGroup") != "Plate":
      raise FlexProtocolError("Dispense requires one whole-plate target")
    plate = plate_map.get(refs[0].get("id", ""))
    if plate is None or sum(p.name == plate.name for p in plates) != 1:
      raise FlexProtocolError("Dispense target must identify a unique layout plate")
    layout = root.findall("./Protocol/Run/PlateLayout/Plates/Plate")
    groups = [
      g
      for p in layout
      if p.get("id") == plate.id
      for g in p.findall("Wells/Group")
      if g.get("name") == "Plate"
    ]
    if (
      len(groups) != 1
      or len(groups[0]) != 1
      or groups[0][0].tag != "Region"
      or any(
        groups[0][0].get(k) != v
        for k, v in {"x": "0", "y": "0", "columns": "12", "rows": "8"}.items()
      )
    ):
      raise FlexProtocolError("Dispense requires the complete 96-well Plate group")
    additions = []
    for reagent in dispense.findall("Reagents/Reagent"):
      try:
        volume = float(reagent.get("volume", ""))
      except ValueError as error:
        raise FlexProtocolError("Dispense volume must be a positive finite number") from error
      reagent_name = reagent.get("name", "").strip()
      if not reagent_name or not math.isfinite(volume) or volume <= 0:
        raise FlexProtocolError("Dispense requires a reagent name and positive finite volume")
      additions.append(FlexDispense(plate.id, plate.name, reagent_name, volume))
    if not additions:
      raise FlexProtocolError("Dispense requires at least one reagent")
    matches = [i for i, c in enumerate(commands) if c.text == f'TXT 11 "{name}"']
    if len(matches) != 1:
      raise FlexProtocolError("Dispense requires one matching TXT 11 step marker")
    i = matches[0]
    block = commands[i : i + 5]
    if (
      len(block) != 5
      or block[1].opcode != "MOR"
      or block[2].text != f'PAU 0 "{pause.findtext("Message", "")}"'
      or block[3].text != f'TXT "{name}"'
      or block[4].opcode != "MOR"
    ):
      raise FlexProtocolError("Dispense requires the supported plate-presentation pause sequence")
    transfers = [
      p
      for p in prompts
      if p.action in ("load", "unload")
      and p.label == plate.name
      and p.line_number < block[0].line_number
    ]
    if (
      not transfers
      or transfers[-1].action != "load"
      or transfers[-1].loading_slot != loading_slot(int(block[1].text.split()[1]))
      or int(block[4].text.split()[1]) != transfers[-1].loading_slot
    ):
      raise FlexProtocolError("Dispense orientation does not match the target plate's loaded tray")
    if block[0].line_number in used_markers:
      raise FlexProtocolError("Ambiguous duplicate dispensing step")
    used_markers.add(block[0].line_number)
    result[block[2].line_number] = tuple(additions)
  if used_markers != markers:
    raise FlexProtocolError("TXT 11 requires matching standalone Dispense metadata")
  return result


class FlexBoundProtocol:
  """One export with a complete, read-only mapping of BDZ plate IDs to PLR plates.

  Construct through ``protocol.bind_plates({bdz_name: plr_plate, ...})`` before
  running. This associates identities; it does not rename, move or assign the
  plates, verify their physical contents, or validate their physical geometry.
  """

  def __init__(self, protocol: "FlexProtocol", plates_by_name: Mapping[str, Plate]) -> None:
    """Resolve exact BDZ names once and retain the original PLR object references."""
    if not isinstance(plates_by_name, Mapping):
      raise FlexProtocolError("Plate bindings must map BDZ plate names to PLR Plate objects")
    bindings = dict(plates_by_name)
    if any(not isinstance(name, str) for name in bindings):
      raise FlexProtocolError("Plate binding keys must be BDZ plate names (strings)")
    names = [plate.name for plate in protocol.plates]
    if not names or any(not name.strip() for name in names):
      raise FlexProtocolError("Binding requires nonempty BDZ plate names")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
      raise FlexProtocolError(f"Ambiguous duplicate BDZ plate names: {duplicates!r}")
    ids = [plate.id for plate in protocol.plates]
    if any(not plate_id for plate_id in ids) or len(set(ids)) != len(ids):
      raise FlexProtocolError("Binding requires unique, nonempty BDZ plate IDs")
    missing = sorted(set(names) - bindings.keys())
    extra = sorted(bindings.keys() - set(names))
    if missing or extra:
      details = []
      if missing:
        details.append(f"Missing PLR plate bindings for BDZ plates: {missing!r}")
      if extra:
        details.append(f"Unknown BDZ plate names in bindings: {extra!r}")
      raise FlexProtocolError("; ".join(details))
    seen: dict[int, str] = {}
    by_id: dict[str, Plate] = {}
    for plate in protocol.plates:
      resource = bindings[plate.name]
      if not isinstance(resource, Plate):
        raise FlexProtocolError(f"BDZ plate {plate.name!r} must bind to a PLR Plate object")
      if id(resource) in seen:
        raise FlexProtocolError(
          f"BDZ plates {seen[id(resource)]!r} and {plate.name!r} bind to the same PLR Plate"
        )
      seen[id(resource)] = plate.name
      by_id[plate.id] = resource
    self._protocol = protocol
    self._plates_by_id = MappingProxyType(by_id)

  @property
  def protocol(self) -> "FlexProtocol":
    """The exact parsed export associated with these bindings."""
    return self._protocol

  @property
  def plates_by_id(self) -> Mapping[str, Plate]:
    """Read-only BDZ plate ID to PLR Plate associations; objects retain their names."""
    return self._plates_by_id

  def prompts(self) -> tuple[FlexPrompt, ...]:
    """Resolve transfer labels and explicit intervention metadata to stable plate IDs.

    Free-text pauses without plate-presentation metadata have no inferred target.
    Load/unload labels must exactly match a unique plate in the selected export.
    """
    by_name = {plate.name: plate.id for plate in self.protocol.plates}
    commands = self.protocol.preflight()
    positions = {command.line_number: i for i, command in enumerate(commands)}
    root = _xml(self.protocol.exported_xml, "ExportedData")
    result = []
    for prompt in self.protocol.prompts():
      plate_id = None
      if prompt.action in ("load", "unload"):
        plate_id = by_name.get(prompt.label)
        if plate_id is None:
          raise FlexProtocolError(
            f"Line {prompt.line_number}: unknown BDZ plate in {prompt.action}: {prompt.label!r}"
          )
      elif prompt.additions:
        ids = {addition.plate_id for addition in prompt.additions}
        if len(ids) != 1:
          raise FlexProtocolError("A presented intervention must target exactly one plate")
        plate_id = next(iter(ids))
      else:
        i = positions[prompt.line_number]
        if i >= 2 and commands[i - 2].text.startswith('TXT 10 "'):
          marker = commands[i - 2].text.split('"')[1]
          matches = [
            pause
            for pause in root.findall("./Protocol/Run/Protocol/.//Steps/Pause")
            if pause.get("name") == marker and pause.findtext("Message", "") == prompt.label
          ]
          if len(matches) != 1 or commands[i - 1].opcode != "MOR":
            raise FlexProtocolError(f"Line {prompt.line_number}: ambiguous intervention metadata")
          refs = matches[0].findall("Plates/Plate")
          if len(refs) != 1:
            raise FlexProtocolError("A presented intervention must target exactly one plate")
          plate_id = refs[0].get("id")
          if plate_id not in self.plates_by_id:
            raise FlexProtocolError("Intervention references an unbound plate")
      result.append(replace(prompt, plate_id=plate_id))
    return tuple(result)


@dataclass(frozen=True)
class FlexProtocol:
  """An immutable BindIt export, inspected without reconstructing its XML.

  Reading supports inspection of unknown script features. Call ``preflight``
  to check executable coverage. Preserved XML includes reagents, empty shake
  lists, disabled settings and any structures not interpreted by this reader.
  """

  name: str
  bindit_version: str
  original_bytes: bytes
  properties_xml: bytes
  exported_xml: bytes
  script: str
  plates: tuple[FlexPlate, ...]
  tips: tuple[FlexTip, ...]

  @property
  def sha256(self) -> str:
    """Return the digest of the immutable original export."""
    return hashlib.sha256(self.original_bytes).hexdigest()

  def bind_plates(self, plates_by_name: Mapping[str, Plate]) -> FlexBoundProtocol:
    """Bind every BDZ plate by its exact, case-sensitive name before execution.

    Missing/unknown names, ambiguous duplicate BDZ names, non-Plate values and
    reuse of one PLR Plate for distinct BDZ plates raise ``FlexProtocolError``.
    The mapping is copied; later caller edits cannot change a prepared run.
    Protocol inspection remains available without bindings or hardware access.
    """
    return FlexBoundProtocol(self, plates_by_name)

  def compatibility_issues(self) -> tuple[FlexProtocolIssue, ...]:
    """Report all detected execution blockers without device access or script changes.

    This uses the same checks as ``preflight``. Unsupported features remain blocked
    even if their generated command syntax is otherwise recognized. Malformed BDZ
    containers are rejected earlier by ``read_flex_bdz``. No geometry calculation,
    volume-profile lookup or hardware validation is performed here.
    """
    issues: list[FlexProtocolIssue] = []
    try:
      root = _xml(self.exported_xml, "ExportedData")
    except FlexProtocolError as error:
      issues.append(FlexProtocolIssue("invalid_xml", "ExportedData", str(error)))
    else:
      protocols = root.findall("./Protocol/Run/Protocol")
      if len(protocols) != 1:
        issues.append(
          FlexProtocolIssue("not_executable", "Protocol", "Expected one executable protocol")
        )
      else:
        protocol = protocols[0]
        if protocol.get("IsExecutable") != "true":
          issues.append(
            FlexProtocolIssue("not_executable", "Protocol", "Expected one executable protocol")
          )
        if protocol.get("enabled", "true") != "true":
          issues.append(FlexProtocolIssue("disabled_protocol", "Protocol", "Protocol is disabled"))
        for index, step in enumerate(protocol.findall(".//Steps/*"), 1):
          location = f"Steps/{step.tag}[{index}] {step.get('name', '')!r}"
          if step.tag not in ("Tip", "Mix", "Dry", "ReleaseBeads", "CollectBeads", "Pause"):
            issues.append(
              FlexProtocolIssue("unsupported_step", location, f"Unsupported step type: {step.tag}")
            )
          if step.tag != "Pause" and any(
            pause.get("enabled") == "true" for pause in step.findall("Pause")
          ):
            issues.append(
              FlexProtocolIssue(
                "nested_pause",
                location + "/Pause",
                "Enabled nested Pause metadata is unsupported",
              )
            )
        parents = {child: parent for parent in protocol.iter() for child in parent}
        for feature in ("Heating", "PostTemperature", "Precollect", "Postmix", "Dispense"):
          for index, element in enumerate(protocol.iter(feature), 1):
            if element.get("enabled") == "true":
              parent = parents[element]
              # Mix precollection/postmix are sequences of existing supported motions
              # and timers. Preserve the exported commands; do not regenerate them.
              if feature in ("Precollect", "Postmix") and parent.tag == "Mix":
                continue
              if (
                feature == "Heating"
                and parent.tag == "Mix"
                and _temperature_supported(element.findtext("Temperature", ""))
                and element.findtext("Preheat") in ("true", "false")
                and f'TXT 7 "{parent.get("name", "")}"' in self.script.splitlines()
                and any(
                  line.startswith(f"TEM {element.findtext('Temperature')} ")
                  for line in self.script.splitlines()
                )
                and "MOI 310" in self.script.splitlines()
              ):
                continue
              if feature == "Dispense" and parent.tag == "Pause":
                continue
              location = f"{parent.tag} {parent.get('name', '')!r}/{feature}[{index}]"
              issues.append(
                FlexProtocolIssue(
                  "unsupported_feature",
                  location,
                  f"Enabled {feature} metadata is unsupported",
                )
              )
    if not self.plates:
      issues.append(FlexProtocolIssue("missing_plates", "PlateLayout", "No layout plates"))
    for plate in self.plates:
      if plate.type_id != "8b7d7c98-275f-4285-8129-5f8ed46fb01e":
        issues.append(
          FlexProtocolIssue(
            "unsupported_plate",
            f"PlateLayout/{plate.id}",
            f"Execution supports only 96 deep-well plates; "
            f"plate {plate.name!r} uses {plate.type_id}",
          )
        )
    if not self.tips:
      issues.append(FlexProtocolIssue("missing_tips", "Steps/Tip", "No tip configuration"))
    for tip in self.tips:
      if tip.type_id != "2ab24e9a-b88e-4d3b-8fc3-babbc5a7c742":
        issues.append(
          FlexProtocolIssue(
            "unsupported_tip",
            f"Tip {tip.name!r}",
            f"Execution supports only the 96DW tip configuration; "
            f"tip {tip.name!r} uses {tip.type_id}",
          )
        )
    commands, script_issues = _inspect_script(self.script)
    issues.extend(script_issues)
    orientation = None
    timer_set = False
    for command in commands:
      location = f"CommandScript/line[{command.line_number}]"
      if command.opcode == "MOR":
        orientation = int(command.text.split()[1])
      if command.opcode == "PAU" and orientation is None:
        issues.append(
          FlexProtocolIssue(
            "prompt_without_orientation",
            location,
            "PAU requires a preceding MOR orientation",
            command.line_number,
          )
        )
      if command.opcode == "WAI":
        if len(command.text.split()) == 3:
          timer_set = True
        elif not timer_set:
          issues.append(
            FlexProtocolIssue(
              "wait_without_timer",
              location,
              "WAI wait requires a preceding timer-setting command",
              command.line_number,
            )
          )
    if not issues:
      try:
        _dispensing_prompts(root, commands, self.plates)
      except FlexProtocolError as error:
        issues.append(FlexProtocolIssue("unsupported_dispense", "Dispense", str(error)))
    return tuple(issues)

  def preflight(self, mode: Literal["stream", "record"] = "stream") -> tuple[FlexCommand, ...]:
    """Require a compatible whole protocol before selecting stream or record commands.

    Recording retains DTL/TIM; streaming omits only those recognized metadata forms.
    All compatibility blockers are included in the exception. Accepted syntax does
    not establish physical validation, and no supported subset is returned on failure.
    """
    if mode not in ("stream", "record"):
      raise ValueError("mode must be 'stream' or 'record'")
    issues = self.compatibility_issues()
    if issues:
      raise FlexProtocolError("; ".join(f"{issue.location}: {issue.message}" for issue in issues))
    commands = _preflight(self.script)
    return tuple(c for c in commands if mode == "record" or c.opcode not in ("DTL", "TIM"))

  def prompts(self) -> tuple[FlexPrompt, ...]:
    """Describe validated plate requests and protocol pauses in script order."""
    commands = self.preflight()
    additions = _dispensing_prompts(_xml(self.exported_xml, "ExportedData"), commands, self.plates)
    prompts = []
    for prompt in _script_prompts(commands):
      required = additions.get(prompt.line_number, ())
      if required:
        instructions = "; ".join(
          f"Add {a.volume:g} uL of {a.reagent_name} per well to {a.plate_name}" for a in required
        )
        prompt = replace(prompt, label=f"{prompt.label} — {instructions}", additions=required)
      prompts.append(prompt)
    return tuple(prompts)


def read_flex_bdz(source: ProtocolSource) -> FlexProtocol:
  """Read a path or bytes, validating Flex identity without rewriting the export."""
  data = source if isinstance(source, bytes) else Path(source).read_bytes()
  try:
    container = read_container(data)
    properties = parse_xml(container.properties, "Properties")
    root = parse_xml(container.exported, "ExportedData")
  except ValueError as error:
    raise FlexProtocolError(str(error)) from error
  if container.parameters_type != 711:
    raise FlexProtocolError("Not a Flex instrument parameters trailer")
  objects = properties.findall("ExportedObject")
  parameters = properties.find("InstrumentParameters")
  if len(objects) != 1 or objects[0].findtext("InstrumentTypeId") != FLEX_TYPE_ID:
    raise FlexProtocolError("Expected exactly one Flex exported object")
  if parameters is None or parameters.get("type") != "711":
    raise FlexProtocolError("Expected Flex InstrumentParameters type 711")
  protocols = root.findall("./Protocol/Run/Protocol")
  if len(protocols) != 1:
    raise FlexProtocolError("Expected exactly one protocol")
  name = objects[0].get("name", "")
  if not name or name != container.name or name != protocols[0].get("name"):
    raise FlexProtocolError("Protocol names disagree across container sections")
  scripts = root.findall("CommandScript")
  if len(scripts) != 1 or not scripts[0].text:
    if protocols[0].get("IsExecutable") == "false":
      plate_ids = {p.get("id") for p in root.findall("./Protocol/Run/PlateLayout/Plates/Plate")}
      unresolved = [
        step.get("name", step.tag)
        for step in protocols[0].findall(".//Steps/*")
        if any(p.get("id") not in plate_ids for p in step.findall("Plates/Plate"))
      ]
      detail = f" Unassigned or unknown plate in: {', '.join(unresolved)}." if unresolved else ""
      raise FlexProtocolError(
        "BindIt marked the export non-executable and supplied no usable CommandScript."
        + detail
        + " Correct the protocol in BindIt and re-export it."
      )
    raise FlexProtocolError("Expected one nonempty generated CommandScript")
  plates = tuple(
    FlexPlate(p.get("id", ""), p.get("name", ""), p.get("plateTypeID", ""))
    for p in root.findall("./Protocol/Run/PlateLayout/Plates/Plate")
  )
  if any(not p.id or not p.type_id for p in plates) or len({p.id for p in plates}) != len(plates):
    raise FlexProtocolError("Invalid or duplicate plate identities")
  tips = tuple(
    FlexTip(
      tip.get("name", ""),
      tip.get("id", ""),
      tuple(p.get("id", "") for p in tip.findall("Plates/Plate")),
    )
    for tip in protocols[0].findall("Steps/Tip")
  )
  plate_ids = {p.id for p in plates}
  for reference in protocols[0].findall(".//Plates/Plate"):
    if reference.get("id") not in plate_ids:
      raise FlexProtocolError("Protocol references a plate absent from its layout")
  return FlexProtocol(
    name,
    container.version,
    data,
    container.properties,
    container.exported,
    scripts[0].text,
    plates,
    tips,
  )
