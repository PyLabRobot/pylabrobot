"""Lossless Flex BindIt inspection and script compatibility validation."""

import hashlib
import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Optional, Union

FLEX_TYPE_ID = "844c2ce4-e8cf-4c19-b8e6-c43037658b8c"
MAX_XML_SIZE = 16 * 1024 * 1024
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


def loading_slot(processing_slot: int) -> int:
  """Convert a completed MOR target to the printed tray at the loading station."""
  if not 1 <= processing_slot <= 8:
    raise ValueError("Flex processing slot must be in 1..8")
  return (processing_slot - 1 + 3) % 8 + 1


def _xml(payload: bytes, root_name: str) -> ET.Element:
  """Parse bounded XML without declarations that can expand external data."""
  # BindIt exports are UTF-8; reject alternate encodings rather than bypass this check.
  try:
    text = payload.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper() or "\x00" in text:
      raise FlexProtocolError("Unsupported XML declaration or encoding")
    root = ET.fromstring(text)
  except (UnicodeError, ET.ParseError) as error:
    raise FlexProtocolError("Invalid BindIt XML") from error
  if root.tag != root_name:
    raise FlexProtocolError(f"Expected {root_name}, got {root.tag}")
  return root


def _string(data: bytes, offset: int) -> tuple[str, int]:
  """Read a length-prefixed UTF-8 container string with bounds checking."""
  if offset + 2 > len(data):
    raise FlexProtocolError("Truncated BindIt string length")
  size = struct.unpack_from("<H", data, offset)[0]
  end = offset + 2 + size
  if end > len(data):
    raise FlexProtocolError("Truncated BindIt string")
  try:
    return data[offset + 2 : end].decode("utf-8"), end
  except UnicodeError as error:
    raise FlexProtocolError("Invalid BindIt string") from error


def _gzip(data: bytes, offset: int, size: int) -> tuple[bytes, int]:
  """Validate a complete size-bounded gzip member, including CRC and EOF."""
  end = offset + size
  if size <= 0 or end > len(data):
    raise FlexProtocolError("Invalid compressed stream size")
  decoder = zlib.decompressobj(31)
  try:
    payload = decoder.decompress(data[offset:end], MAX_XML_SIZE + 1)
  except zlib.error as error:
    raise FlexProtocolError("Corrupt BindIt gzip member") from error
  if len(payload) > MAX_XML_SIZE:
    raise FlexProtocolError("BindIt XML exceeds the inspection size limit")
  if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
    raise FlexProtocolError("BindIt gzip member does not match its declared size")
  return payload, end


def _parts(data: bytes) -> tuple[str, str, bytes, bytes]:
  """Parse variable-length header fields and validate both members and trailer."""
  if len(data) < 16 or data[:12] != bytes.fromhex("b6751cf201000a0001000000"):
    raise FlexProtocolError("Unsupported BindIt header")
  strings_size = struct.unpack_from("<I", data, 12)[0]
  vendor, offset = _string(data, 16)
  version, offset = _string(data, offset)
  if vendor != "BindIt Software" or offset - 16 != strings_size:
    raise FlexProtocolError("Invalid BindIt vendor/version header")
  if offset + 18 > len(data):
    raise FlexProtocolError("Truncated BindIt header")
  if data[offset : offset + 6] != bytes.fromhex("0b0001000000"):
    raise FlexProtocolError("Unsupported BindIt object header")
  content_size = struct.unpack_from("<I", data, offset + 6)[0]
  if content_size != len(data) - (offset + 20):
    raise FlexProtocolError("BindIt file size does not match its header")
  if data[offset + 10 : offset + 14] != bytes.fromhex("00000001"):
    raise FlexProtocolError("Invalid first stream marker")
  size = struct.unpack_from("<I", data, offset + 14)[0]
  props, offset = _gzip(data, offset + 18, size)
  if data[offset : offset + 4] != bytes.fromhex("01000001") or offset + 8 > len(data):
    raise FlexProtocolError("Invalid second stream marker")
  size = struct.unpack_from("<I", data, offset + 4)[0]
  exported, offset = _gzip(data, offset + 8, size)
  trailer = data[offset:]
  if len(trailer) < 42 or struct.unpack_from("<I", trailer)[0] != 2:
    raise FlexProtocolError("Invalid BindIt trailer")
  if struct.unpack_from("<I", trailer, 8)[0] != 711:
    raise FlexProtocolError("Not a Flex instrument parameters trailer")
  if trailer[12:30] != b"\x00" * 14 + b"\x01\x00\x00\x00":
    raise FlexProtocolError("Unsupported BindIt trailer fields")
  name, end = _string(trailer, 30)
  if struct.unpack_from("<I", trailer, 4)[0] != end - 32 + 24:
    raise FlexProtocolError("Invalid BindIt trailer name size")
  if trailer[end:] != bytes.fromhex("01000100000000000000"):
    raise FlexProtocolError("Invalid BindIt trailing bytes")
  return version, name, props, exported


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
    loads = [
      p
      for p in prompts
      if p.action == "load" and p.label == plate.name and p.line_number < block[0].line_number
    ]
    if (
      len(loads) != 1
      or loads[0].loading_slot != loading_slot(int(block[1].text.split()[1]))
      or int(block[4].text.split()[1]) != loads[0].loading_slot
    ):
      raise FlexProtocolError("Dispense orientation does not match the target plate's loaded tray")
    if block[0].line_number in used_markers:
      raise FlexProtocolError("Ambiguous duplicate dispensing step")
    used_markers.add(block[0].line_number)
    result[block[2].line_number] = tuple(additions)
  if used_markers != markers:
    raise FlexProtocolError("TXT 11 requires matching standalone Dispense metadata")
  return result


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
  if len(data) > 2 * MAX_XML_SIZE:
    raise FlexProtocolError("BindIt file exceeds the inspection size limit")
  version, trailer_name, props, exported = _parts(data)
  properties = _xml(props, "Properties")
  objects = properties.findall("ExportedObject")
  parameters = properties.find("InstrumentParameters")
  if len(objects) != 1 or objects[0].findtext("InstrumentTypeId") != FLEX_TYPE_ID:
    raise FlexProtocolError("Expected exactly one Flex exported object")
  if parameters is None or parameters.get("type") != "711":
    raise FlexProtocolError("Expected Flex InstrumentParameters type 711")
  root = _xml(exported, "ExportedData")
  protocols = root.findall("./Protocol/Run/Protocol")
  if len(protocols) != 1:
    raise FlexProtocolError("Expected exactly one protocol")
  name = objects[0].get("name", "")
  if not name or name != trailer_name or name != protocols[0].get("name"):
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
  return FlexProtocol(name, version, data, props, exported, scripts[0].text, plates, tips)
