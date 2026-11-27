"""Reading and writing protocol files.

A protocol file is an encrypted XML document whose element order, indentation and line endings are
what the vendor's software expects to read back, so the writer reproduces them exactly.

What a written file does not carry: the audit trail and revision counter, which record who saved a
protocol and when and would be fabricated by writing them; and four newer elements covering
stacker lids and a plate height override, whose meaning is not established. A file read and
written again therefore loses those, and is not byte-identical. Everything else survives,
including entries that sequence the run rather than operating the instrument.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from pylabrobot.agilent.biotek.lhc.enums.steps.step_action import StepAction
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol, ProtocolEntry
from pylabrobot.agilent.biotek.lhc.protocols.read_write_utilities.encryption import decrypt, encrypt
from pylabrobot.agilent.biotek.lhc.protocols.steps import Step

_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'
_NAMESPACES = (
  ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
  ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
)
_ROOT = "ProgramProtocol"
_NEWLINE = "\r\n"
_INDENT = "  "
_NO_PLATE_TYPE = -1
_NO_STEP_TYPE = -1
_UNDEFINED_ACTION = "eStepActionUndefined"


def read(path: str | Path) -> Protocol:
  """Read a protocol file.

  The steps themselves are not read yet: call :meth:`Protocol.build_steps` for that. A file whose
  steps this package cannot read is still readable this far.

  Args:
    path: The file to read.

  Returns:
    The protocol.

  Raises:
    RuntimeError: If pycryptodome is not installed.
    ValueError: If the file is not a protocol file, or holds no protocol document.
  """
  return from_xml(decrypt(Path(path).read_bytes()))


def write(path: str | Path, protocol: Protocol) -> None:
  """Write a protocol file.

  Args:
    path: The file to write.
    protocol: The protocol to write.

  Raises:
    RuntimeError: If pycryptodome is not installed.
  """
  Path(path).write_bytes(encrypt(to_xml(protocol)))


def from_xml(document: str) -> Protocol:
  """Read a protocol out of the document a protocol file holds.

  The instrument settings are handed over as the nested document they are stored as; making sense
  of them belongs to the device, which can also read them off the instrument itself.

  Args:
    document: The XML document.

  Returns:
    The protocol.

  Raises:
    ValueError: If the document does not parse, or an entry names an action or step type that
      does not exist.
  """
  try:
    root = ElementTree.fromstring(document.strip())
  except ElementTree.ParseError as error:
    raise ValueError(f"not a protocol document: {error}") from error

  def text(tag: str, default: str = "") -> str:
    """One element's text, with the line endings a protocol file stores.

    Args:
      tag: Which element to read.
      default: What an absent or empty element means.

    Returns:
      The text.
    """
    element = root.find(tag)
    if element is None or element.text is None:
      return default
    return _with_carriage_returns(element.text)

  protocol = Protocol(
    instrument_settings_xml=text("InstrumentSettingsXML"),
    instrument_settings=text("InstrumentSettings"),
    lhc_version=text("LHCVersion"),
    instrument_name=text("InstrumentName"),
    com_port=text("ComPort"),
    protocol_name=text("ProtocolName"),
    protocol_version=text("ProtocolVersion"),
    plate_type=text("PlateType"),
    plate_type_number=int(text("PlateTypeEnum", str(_NO_PLATE_TYPE))),
    comments=text("Comments"),
    read_only=text("ReadOnly") == "true",
    stacker_enabled=text("BioStackEnabled") == "true",
    use_stacker=text("UseBioStack") == "true",
    entire_stack=text("EntireStack") == "true",
    stacker_plate_count=int(text("BioStackPlateCount", "10")),
  )
  for element in root.findall("Step"):
    action = element.findtext("Action") or _UNDEFINED_ACTION
    step_type = int(element.findtext("Type") or _NO_STEP_TYPE)
    if action not in _ACTIONS:
      raise ValueError(f"unknown step action: {action!r}")
    protocol.entries.append(
      ProtocolEntry(
        action=_ACTIONS[action],
        step_type=StepType(step_type) if step_type >= 0 else StepType.UNDEFINED,
        definition=element.findtext("Definition") or "",
      )
    )
  return protocol


def _with_carriage_returns(document: str) -> str:
  """Put back the line endings reading an element's text takes out.

  A protocol file ends every line with a carriage return, including inside the elements whose text
  runs to several lines -- the fitted-options document it carries as escaped text, the comments, and
  the prose an older release stores its options as. Reading an element's text normalises those away,
  so a file read and written again would differ from the one it came from. Writing is not the place
  to fix it: by then it is not known whether the line endings were ever there.

  Args:
    document: The text as reading it produced.

  Returns:
    The text with carriage returns restored.
  """
  return document.replace("\r\n", "\n").replace("\n", "\r\n")


def to_xml(protocol: Protocol) -> str:
  """Write a protocol as the document a protocol file holds.

  The entries are written when the protocol has them, so a file that was read keeps its own text
  and the entries that sequence the run survive being written again. Otherwise they are built from
  the protocol's steps, which are all instrument operations.

  Args:
    protocol: The protocol to write.

  Returns:
    The document.
  """
  entries = protocol.entries or entries_for(protocol.steps)
  lines = [_DECLARATION, f"<{_ROOT}{_NAMESPACES}>"]
  for tag, value in (
    ("LHCVersion", protocol.lhc_version),
    ("InstrumentName", protocol.instrument_name),
    ("ComPort", protocol.com_port),
    ("InstrumentSettings", protocol.instrument_settings),
    ("InstrumentSettingsXML", protocol.instrument_settings_xml),
    ("ProtocolName", protocol.protocol_name),
    ("ProtocolVersion", protocol.protocol_version),
    ("PlateType", protocol.plate_type),
    ("PlateTypeEnum", protocol.plate_type_number),
    ("Comments", protocol.comments),
    ("ReadOnly", protocol.read_only),
    ("BioStackEnabled", protocol.stacker_enabled),
    ("UseBioStack", protocol.use_stacker),
    ("EntireStack", protocol.entire_stack),
    ("BioStackPlateCount", protocol.stacker_plate_count),
  ):
    lines.append(_INDENT + _element(tag, value))
  for entry in entries:
    lines.append(_INDENT + "<Step>")
    lines.append(_INDENT * 2 + _element("Action", _ACTION_NAMES[entry.action]))
    lines.append(
      _INDENT * 2
      + _element("Type", entry.step_type.value if entry.is_device_step else _NO_STEP_TYPE)
    )
    lines.append(_INDENT * 2 + _element("Definition", entry.definition))
    lines.append(_INDENT + "</Step>")
  lines.append(f"</{_ROOT}>")
  return _NEWLINE.join(lines)


def entries_for(steps: list[Step]) -> list[ProtocolEntry]:
  """Build file entries for steps that have none yet.

  Args:
    steps: The steps to write.

  Returns:
    One entry per step, each an instrument operation, in order.
  """
  return [
    ProtocolEntry(
      action=StepAction.CUSTOM, step_type=step.step_type, definition=step.to_definition()
    )
    for step in steps
  ]


_ACTIONS: dict[str, StepAction] = {
  "eStepActionUndefined": StepAction.UNDEFINED,
  "eStepActionEndOfList": StepAction.END_OF_LIST,
  "eStepActionCustom": StepAction.CUSTOM,
  "eStepActionDelay": StepAction.DELAY,
  "eStepActionRemark": StepAction.REMARK,
  "eStepActionLoopStart": StepAction.LOOP_START,
  "eStepActionLoopEnd": StepAction.LOOP_END,
  "eStepActionDeliverPlate": StepAction.DELIVER_PLATE,
  "eStepActionNthPlate": StepAction.NTH_PLATE,
  "eStepActionNthPlateEnd": StepAction.NTH_PLATE_END,
  "eStepActionRetrievePlate": StepAction.RETRIEVE_PLATE,
  "eStepActionRestack": StepAction.RESTACK,
  "eStepActionDelayStartTimer": StepAction.DELAY_START_TIMER,
}
"""How a protocol file spells each action."""

_ACTION_NAMES: dict[StepAction, str] = {value: key for key, value in _ACTIONS.items()}
"""How to spell each action in a protocol file."""


def _element(tag: str, value: object) -> str:
  """One element of the document.

  Args:
    tag: The element name.
    value: Its value. Booleans are written as ``true`` and ``false``, and an empty value as an
      empty element.

  Returns:
    The element.
  """
  if isinstance(value, bool):
    value = "true" if value else "false"
  text = escape(str(value))
  return f"<{tag}>{text}</{tag}>" if text else f"<{tag} />"
