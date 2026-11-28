"""The driver's exceptions, and the MPE2's firmware errors named from Hamilton's tables
(``error_tables.py``).

A firmware error arrives as an HcResult entry from the object that raised it. An interface's own
error ``code`` is taken to come as ``0x0F00 | code``, as on the Nimbus, and a warning with the high
bit set; neither
is seen on an MPE2 yet, so an unknown result keeps its raw value in the message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pylabrobot.hamilton.mpe2.error_tables import (
  ERRORS,
  INTERFACE_ERROR_BASE,
  INTERFACE_IDS,
  OBJECT_INTERFACES,
  WARNINGS,
)
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry


@dataclass
class RecoveryReport:
  """What ``MPE2.recover()`` did, where the MPE2 ended up, and what to check by hand."""

  steps: List[str] = field(default_factory=list)
  failed_steps: List[str] = field(default_factory=list)
  final_state: Optional[str] = None
  input_pressure_psi: Optional[float] = None
  check_by_hand: List[str] = field(default_factory=list)

  def __str__(self) -> str:
    """The report as one line."""
    parts = [f"ended in state {self.final_state}"]
    if self.input_pressure_psi is not None:
      parts.append(f"input gas {self.input_pressure_psi:.1f} psi")
    parts += [f"done: {s}" for s in self.steps]
    parts += [f"failed: {s}" for s in self.failed_steps]
    parts += [f"check: {s}" for s in self.check_by_hand]
    return "; ".join(parts)


class MPE2Error(Exception):
  """An MPE2 error: refused by the driver before sending, or reported by the firmware.

  Attributes:
    command: the command it came from.
    interface: the firmware interface that reported it ("ISPEControl"), if the firmware did.
    code: the interface's error code, if known.
    name: Hamilton's name for it ("MinimumInputPressureNotProvided").
    help_text: Hamilton's help text.
    entries: the raw HcResult entries.
    traversed_states: the SPEControl states the command went through.
    recovery: what ``recover()`` did after it, when it ran.
  """

  def __init__(
    self,
    message: str,
    *,
    command: Optional[str] = None,
    interface: Optional[str] = None,
    code: Optional[int] = None,
    name: Optional[str] = None,
    help_text: Optional[str] = None,
    entries: Optional[List[HcResultEntry]] = None,
    traversed_states: Optional[List[int]] = None,
  ):
    super().__init__(message)
    self.command = command
    self.interface = interface
    self.code = code
    self.name = name
    self.help_text = help_text
    self.entries = entries or []
    self.traversed_states = traversed_states or []
    self.recovery: Optional[RecoveryReport] = None

  def __str__(self) -> str:
    """The message, with the recovery report when there is one."""
    text = super().__str__()
    if self.recovery is not None:
      text += f" [recovery: {self.recovery}]"
    return text


class MPE2PressureError(MPE2Error):
  """The gas side failed: the supply, a pressure valve's control, or the manifold's pressure.
  The MPE2 recovers from these by itself."""


class MPE2MotorError(MPE2Error):
  """A motor failed: the shuttle, a lifter, a stepper or the motor controller. The MPE2 stops and
  holds until someone has looked and calls ``recover()``."""


class MPE2InputPressureError(MPE2PressureError):
  """The gas supply is outside 80 to 120 psi (ISPEControl 0x0E, 0x10; IPressureControl 0x06,
  0x07)."""


class MPE2IntrusionError(MPE2Error):
  """Something got in the way of a moving part (ISPEControl 0x0B; the upper lifter's 0x05 to
  0x08)."""


class MPE2StateError(MPE2Error):
  """The command doesn't fit the state the MPE2 is in (ISPEControl 0x01)."""


class MPE2AbortedError(MPE2Error):
  """The process was aborted (ISPEControl 0x05, 0x06)."""


class MPE2WasteError(MPE2Error):
  """A waste container is missing, full or was removed (IWasteSystem)."""


class MPE2VacuumError(MPE2Error):
  """The vacuum was not built or held, or was too strong (IVacuum)."""


class MPE2AccessoryError(MPE2Error):
  """The reagent fill or the evaporator reported an error."""


class MPE2TimeoutError(MPE2Error):
  """The MPE2 did not answer, or did not finish, in time."""


class MPE2ConnectionError(MPE2Error):
  """The link to the MPE2 is down or can't be trusted."""


_BY_CODE = {
  ("ISPEControl", 0x03): MPE2MotorError,
  ("ISPEControl", 0x04): MPE2MotorError,
  ("ISPEControl", 0x07): MPE2PressureError,
  ("ISPEControl", 0x09): MPE2PressureError,
  ("ISPEControl", 0x0C): MPE2PressureError,
  ("ISPEControl", 0x0E): MPE2InputPressureError,
  ("ISPEControl", 0x10): MPE2InputPressureError,
  ("IPressureControl", 0x06): MPE2InputPressureError,
  ("IPressureControl", 0x07): MPE2InputPressureError,
  ("ISPEControl", 0x0B): MPE2IntrusionError,
  ("IUpperLifterAxis", 0x05): MPE2IntrusionError,
  ("IUpperLifterAxis", 0x06): MPE2IntrusionError,
  ("IUpperLifterAxis", 0x08): MPE2IntrusionError,
  ("ISPEControl", 0x01): MPE2StateError,
  ("ISPEControl", 0x05): MPE2AbortedError,
  ("ISPEControl", 0x06): MPE2WasteError,  # WasteSystemErrorCausedAbort
}
_BY_INTERFACE = {
  "IPressureControl": MPE2PressureError,
  "ILowPressureValvePressureControl": MPE2PressureError,
  "IValvesAndSensors": MPE2PressureError,
  "ISPEMotorControl": MPE2MotorError,
  "IPlateShuttleAxis": MPE2MotorError,
  "ILifterAxis": MPE2MotorError,
  "IUpperLifterAxis": MPE2MotorError,
  "ILowerLifterAxis": MPE2MotorError,
  "IDispenseAxis": MPE2MotorError,
  "IL6470Axis": MPE2MotorError,
  "IL6470Stepper": MPE2MotorError,
  "IL6470StepperEx1": MPE2MotorError,
  "IWasteSystem": MPE2WasteError,
  "IVacuum": MPE2VacuumError,
  "IDispenser": MPE2AccessoryError,
  "IEvaporator": MPE2AccessoryError,
  "IHeater": MPE2AccessoryError,
  "IReagentPump": MPE2AccessoryError,
  "ISPEAcmeService": MPE2AccessoryError,
  "ISourceContainer": MPE2AccessoryError,
  "ISourceContainerCalibration": MPE2AccessoryError,
  "ISourceContainerCalibrationEx05": MPE2AccessoryError,
}

Address = Tuple[int, int, int]

# Errors that report an abort rather than its cause: ISPEControl's SPEProcessAborted. By interface
# and code: another interface's 0x05 (IWasteSystem's WasteContainerIsFull) is a cause.
_CONSEQUENCES = {("ISPEControl", 0x05)}


def interface_code(
  object_name: Optional[str], result: int, interface_id: Optional[int] = None
) -> Tuple[Optional[str], Optional[int]]:
  """The interface and its own error code for an HcResult from an object, if the table knows it.

  Args:
    object_name: the last part of the object's path ("SPEControl"), if known.
    result: the entry's HcResult.
    interface_id: the entry's HOI interface id, which tells apart interfaces sharing codes.
  """
  if object_name is None or (result & 0x7F00) != INTERFACE_ERROR_BASE:
    return None, None
  code = result & 0x00FF
  table = WARNINGS if result & 0x8000 else ERRORS
  by_id = INTERFACE_IDS.get(object_name, {})
  candidates = (
    (by_id[interface_id],) if interface_id in by_id else OBJECT_INTERFACES.get(object_name, ())
  )
  for interface in candidates:
    if (interface, code) in table:
      return interface, code
  return None, None


def describe(object_name: Optional[str], result: int) -> str:
  """An HcResult as Hamilton's name and help text, or its raw value."""
  interface, code = interface_code(object_name, result)
  if interface is None or code is None:
    return f"0x{result:04X}"
  table = WARNINGS if result & 0x8000 else ERRORS
  name, help_text = table[(interface, code)]
  return f"{name} ({interface} 0x{code:02X}): {help_text}"


def error_from_entries(
  command: str,
  entries: List[HcResultEntry],
  object_names: Dict[Address, str],
  traversed_states: Optional[List[int]] = None,
) -> MPE2Error:
  """The exception for a command's failed HcResult entries; the first error entry names it.

  Args:
    command: the command's name.
    entries: the HcResult entries (from a HoiError or GetLastCommandResults).
    object_names: the last path part of each resolved address ("SPEControl").
    traversed_states: the SPEControl states the command went through.
  """
  failed = [e for e in entries if not e.is_success] or list(entries)
  interface = code = name = help_text = None
  first: Optional[HcResultEntry] = None

  # The entry that names the error is the cause, not the abort it led to: the MPE2 reports
  # SPEProcessAborted first and MaximumInputPressureExceeded second.
  def found_for(e):
    """The interface, code and name of one result entry."""
    return interface_code(object_names.get(e.address), e.result, e.interface_id)

  for entry in sorted(failed, key=lambda e: found_for(e) in _CONSEQUENCES):
    found = found_for(entry)
    if found[0] is not None:
      first, (interface, code) = entry, found
      break
  if first is None and failed:
    first = failed[0]
  if interface is not None and code is not None and first is not None:
    table = WARNINGS if first.is_warning else ERRORS
    name, help_text = table[(interface, code)]
    cls = _BY_CODE.get((interface, code)) or _BY_INTERFACE.get(interface, MPE2Error)
    message = f"{command} failed on the MPE2: {name} ({interface} 0x{code:02X}): {help_text}"
  else:
    cls = MPE2Error
    raw = ", ".join(f"0x{e.result:04X} at {e.address}" for e in failed) or "no entries"
    message = f"{command} failed on the MPE2: {raw}"
  return cls(
    message,
    command=command,
    interface=interface,
    code=code,
    name=name,
    help_text=help_text,
    entries=list(entries),
    traversed_states=traversed_states,
  )


def error_from_hoi(command: str, error: HoiError, object_names: Dict[Address, str]) -> MPE2Error:
  """The exception for a command the firmware refused at once."""
  return error_from_entries(command, list(error.entries), object_names)


def simulated_refusal(
  address: Address, interface_id: int, interface: str, code: int, action: int = 3
) -> HoiError:
  """A HoiError as the transport raises it for a refused command, for the simulators.

  Args:
    address: the (module, node, object) of the refusing object.
    interface_id: the HOI interface id the command went to.
    interface: the interface's name in ``ERRORS``.
    code: its error code there.
    action: the HOI action of the command.
  """
  name, _help = ERRORS[(interface, code)]
  entry = HcResultEntry(
    address[0], address[1], address[2], interface_id, action, INTERFACE_ERROR_BASE | code
  )
  return HoiError(
    exceptions={0: RuntimeError(name)}, entries=[entry], raw_response=b"", action=action
  )


def simulated_result_entry(address: Address, interface_id: int, code: int) -> HcResultEntry:
  """An HcResult entry for an interface error, as GetLastCommandResults reports it."""
  return HcResultEntry(
    address[0], address[1], address[2], interface_id, 3, INTERFACE_ERROR_BASE | code
  )
