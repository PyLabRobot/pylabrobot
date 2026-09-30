import asyncio
import dataclasses
import logging
import re
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Tuple

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

# Every reply is framed between STX (0x02) and ETX (0x03). A command is answered
# with an ACK frame (a lone 0x06), a command-echo frame ("<cmd>OK"), and then a
# result frame (an operation-specific "...DONE"/"...ERROR" word, a "Status..."
# word, or "CommandIgnore" when the command is a no-op or refused). Motions run
# asynchronously: the status word is "StatusBUSY" while moving, then changes to
# the operation-specific terminal state.
STX = b"\x02"
ETX = b"\x03"
ACK = "\x06"

# Single-character commands the decapper understands.
STATUS = "a"
DECAP_START = "h"
RECAP_START = "i"
WASTE = "b"
OPEN_TRAY = "f"
CLOSE_TRAY = "g"
HOME_ALL = "Z"
INITIALIZE_KEEP_CAPS = "z"
STANDBY = "j"
READY = "k"
CARTRIDGE_EJECT = "c"
CARTRIDGE_LOAD = "C"
# Newer-firmware tray opcodes, intentionally not sent until verified there:
# TRAY_EXTEND = "t"
# TRAY_RETRACT = "T"
TRAY_STEP_OUT = "s"
TRAY_STEP_IN = "S"
CARTRIDGE_COUNTER_RESET = "X"
FIRMWARE_QUERY = "V"
CARTRIDGE_QUERY = "N"
EJECT_CAPS = "5"
HEAD_UP = "6"
SAFETY_DOOR_OPEN = "7"
ERROR_QUERY = "8"
SAFETY_DOOR_DISABLE = "-"
SAFETY_DOOR_ENABLE = "+"
ERROR_DETECTION_OFF = "l"
ERROR_DETECTION_ON = "L"
DRY_RUN_ON = "d"
DRY_RUN_OFF = "D"
EXTENDED_STATUS = "e"
PROFILE_QUERY = "E"
RETRY_DECAP = "Q"

COMMAND_IGNORE = "CommandIgnore"

# "ErrorDetectON" and "ErrorDetectOFF" are ordinary success replies to the
# error-detection commands, so a bare "ERROR" substring is not a fault. A fault
# frame ends the word there: "DecapERROR", "StatusERROR", "tERROR".
_ERROR_FRAME_RE = re.compile(r"ERROR\b")

# Fault descriptions as reported by the instrument firmware.
ERROR_MESSAGES = {
  "StatusNotOK": (
    "Status is not ok. The device is in an error state. Check whether the plate is already "
    "decapped (if so, recap and initialize again); otherwise clear the error on the device and "
    "cycle the power."
  ),
  "NeedToRecap": "Decap operation is already done. Select the recap operation on the device.",
  "NeedToDecap": "Recap operation is already executed.",
  "CapsOnPins": "Caps are held on the pins. Recap or waste them first.",
  "DecapWasNotSuccesful": (
    "Decapping was not successful. Fix the error on the device manually and check whether any "
    "tubes are still on the head."
  ),
  "RecapWasNotSuccesful": "Recapping was not successful. Fix the error on the device manually.",
  "StoreWasNotSuccesful": "Storing was not successful. Fix the error on the device manually.",
  "OpenTrayWasNotSuccesful": (
    "Opening the tray was not successful. Fix the error on the device manually."
  ),
  "CloseTrayWasNotSuccesful": (
    "Closing the tray was not successful. Fix the error on the device manually."
  ),
  "TrayMoveWasNotSuccesful": (
    "Moving the tray was not successful. The tray can only travel between the load position "
    "(setpoint 3) and the extended position (setpoint 127)."
  ),
  "HomeNotSuccesful": (
    "Device was not able to reach the home position. The device is in an error state. "
    "Restart the device."
  ),
  "CartridgeEjectWasNotSuccesful": (
    "Ejecting the cartridge was not successful. The tray must be empty and no caps may be held "
    "on the pins."
  ),
  "CartridgeLoadWasNotSuccesful": (
    "Loading the cartridge was not successful. Check that a cartridge is present on the tray and "
    "seated at the expected height."
  ),
  "EjectCapsWasNotSuccesful": "Ejecting the held caps was not successful.",
  "HeadUpWasNotSuccesful": "Homing the cap head was not successful.",
  "SafetyDoorWasNotSuccesful": "Operating the safety door was not successful.",
  "RetryDecapWasNotSuccesful": "The forced decap retry was not successful.",
  "CannotGoInStandbyMode": "Cannot go to standby mode. Check the errors on the device.",
  "NotInManualMode": (
    "This command is only available while the device is in manual recovery mode (StatusMANUAL)."
  ),
  "StatusManual": (
    "The device is in manual recovery mode. Inspect the rack and cap head, confirm that axis "
    "motion is safe, then recover with reset_error(), or with head_up(), eject_caps() or "
    "initialize_keeping_caps_on_pins(), before sending another motion command."
  ),
  "CommandIgnore": "Command was ignored by the device.",
  "NoAck": "Device did not acknowledge the command.",
}

# Fault descriptions per numeric error code, as read back with the error query.
#
# Source: Azenta IntelliXcap User Manual, part 319430 Rev. E, pp. 88-91:
# https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf
ERROR_CODE_MESSAGES: Dict[int, str] = {
  100: (
    "M1 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  101: "M2 initial homing failure. Likely to override other M1 homing error codes.",
  102: "M1 top switch stuck closed during homing sequence.",
  103: "M1 top switch second trigger not detected during homing sequence.",
  104: "M4 homing error: top switch not detected.",
  105: (
    "M3 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  106: "M3 stop switch stuck closed during homing sequence.",
  107: "M3 top switch second trigger not detected during homing sequence.",
  108: "M3 initial homing failure. Likely to override other M3 homing error codes.",
  109: (
    "M2 top switch not detected during homing sequence. Could get overwritten by other error "
    "codes within higher level sequencing logic, so it is most likely during startup."
  ),
  110: "M2 top switch stuck closed during homing sequence.",
  111: "M2 top switch second trigger not detected during homing sequence.",
  112: "Door close failure.",
  113: (
    "M1 moved to M1_SAFETY_LOW_POS (S33): no light curtain trigger was detected while "
    "scanning for caps. Reported by the decap sequence."
  ),
  114: "Invalid tube height detected. Reported by the decap sequence.",
  115: "Door open failure.",
  116: "Door close failure at start of sequence.",
  117: (
    "M1 moved to M1_SAFETY_LOW_POS (S33): no light curtain trigger was detected while "
    "scanning for caps. Reported by the recap sequence."
  ),
  118: "Invalid tube height detected. Reported by the recap sequence.",
  119: "Open door failure.",
  120: "Open door failure on entry to manual mode.",
  121: "Door close failure.",
  122: "M3 limit switch timeout on cartridge eject.",
  123: "Door open failure at end of cartridge eject sequence.",
  124: "Door close failure at end of cartridge eject sequence.",
  125: "M1 failed to reach the waste position within S4 during the auto-waste sequence.",
  133: "M1 homing error.",
  134: "Open door failure.",
  135: (
    "Cap detected at valid height. Suppressed while light curtain error detection is off; "
    "in dry-run mode the instrument halts here and waits for the operator."
  ),
  136: (
    "Maximum decap attempts exceeded (S46). Suppressed while light curtain error detection is "
    "off; in dry-run mode the instrument halts here and waits for the operator."
  ),
  137: "Maximum recap attempts exceeded (S45).",
  138: (
    "M3 bottom switch closed while the motor was still moving; extended-stage lead-screw "
    "protection activated."
  ),
  139: "Open tray failure; no cartridge detected after initial homing.",
  140: (
    "Cartridge-ejected notification; or the door should be up but the top switch was not detected."
  ),
  141: "The door should be down but the bottom switch was not detected.",
  142: "Unexpected object on tray during cartridge eject.",
  143: "Cartridge not detected during cartridge load sequence.",
  144: (
    "Cartridge detection height incorrect during cartridge load sequence: detected height "
    "was less than S73 - S59."
  ),
  145: "Light curtain calibration max retries exceeded.",
  146: "Light curtain calibration max retries exceeded.",
  147: "Light curtain calibration max retries exceeded.",
  148: "Tray open failure.",
  150: "M3 homing error during auto-waste sequence.",
  151: "Tray close failure.",
  152: "Tube detected after decap retry; caps were screwed back on.",
  153: "Close tray failure; M3 homing error.",
  154: "Close tray failure.",
  155: "Open door failure.",
  156: "M1 homing error.",
  157: "M2 homing error.",
  158: "M3 homing error.",
  159: "M2 homing error.",
  160: "Door close failure at end of sequence; tray open failure.",
  161: "M4 homing error.",
  164: "Tray open failure.",
  165: "Sequence-state error; the same firmware logic may report error 167.",
  166: "M2 homing error during tray decap-quit.",
  167: "Door-open or tray-close failure during decap-quit.",
  200: "Light curtain communications failure: no Modbus data received.",
  201: "Light curtain signal failure; check wiring between controller and light curtain.",
  202: (
    "Conflicting limit switches: top and bottom switches both appear closed. This usually "
    "indicates a power-supply failure or a faulty switch."
  ),
  238: "Emergency stop engaged or motor voltage low.",
}

# Codes the command list pins to StatusERROR, which homing clears, rather than
# to StatusMANUAL, which halts the instrument until an operator has inspected
# it: decap reports 113/114, recap 117/118, cartridge eject 142, and cartridge
# load 143/144. The command list states this only for those four commands, and
# it is inconsistent elsewhere (the tray and standby entries name StatusMANUAL
# in their state rows but StatusERROR in their comments), so a code outside this
# set is not proof that the instrument halted.
RECOVERABLE_ERROR_CODES: FrozenSet[int] = frozenset({113, 114, 117, 118, 142, 143, 144})


def get_error_message(code: int) -> str:
  """Return the documented meaning of an IntelliXcap error code.

  Some codes have multiple meanings because their interpretation depends on
  the firmware sequence that reported them.
  """
  message = ERROR_CODE_MESSAGES.get(code)
  if message is None:
    return "Unknown IntelliXcap error code."
  return message


def is_recoverable_error(code: int) -> bool:
  """Whether the command list pins this code to StatusERROR rather than StatusMANUAL.

  A StatusERROR is cleared by homing, which :meth:`FluidXIntelliXcap96.home` does
  and which operations do for themselves when ``auto_recover`` is enabled. A
  StatusMANUAL halts the instrument until an operator inspects it. False means
  the command list does not pin the code to StatusERROR, not that the instrument
  is certainly halted: it documents the mapping only for decap, recap, cartridge
  eject and cartridge load. Read :meth:`FluidXIntelliXcap96.request_status` for
  the state the instrument is actually in.
  """
  return code in RECOVERABLE_ERROR_CODES


class FluidXError(Exception):
  """Exceptions raised by a FluidX IntelliXcap 96 decapper."""

  def __init__(
    self,
    title: str,
    message: Optional[str] = None,
    error_code: Optional[int] = None,
  ) -> None:
    self.title = title
    self.message = message
    self.error_code = error_code

  @classmethod
  def from_error_code(cls, code: int, detail: Optional[str] = None) -> "FluidXError":
    """Build an exception from a numeric IntelliXcap error code."""
    meaning = get_error_message(code)
    message = f"{meaning} {detail}" if detail else meaning
    return cls(
      title=f"IntelliXcap error {code}",
      message=message,
      error_code=code,
    )

  @property
  def recoverable(self) -> bool:
    """Whether homing clears the reported error. False when there is no error code."""
    return self.error_code is not None and is_recoverable_error(self.error_code)

  def __str__(self) -> str:
    return f"{self.title}: {self.message}" if self.message else self.title


class FirmwareVersions(NamedTuple):
  """Firmware versions of the three IntelliXcap subsystems, each four digits."""

  unit: str
  touchscreen: str
  light_curtain: str


class CartridgeInfo(NamedTuple):
  """Identity and usage of the installed IntelliCartridge."""

  profile: int
  cycle_count: int
  serial: str


@dataclasses.dataclass(frozen=True)
class CartridgeProfile:
  """Settings of the cartridge profile the instrument is running."""

  number: int
  communication_protocol: int
  decap_max_retry: int
  recap_max_retry: int
  raw: str

  @classmethod
  def from_raw(cls, raw: str) -> "CartridgeProfile":
    """Parse the five-digit profile reply: 2-digit number then three 1-digit fields."""
    digits = raw.strip()
    if len(digits) != 5 or not digits.isdigit():
      raise FluidXError(
        title="Malformed profile reply",
        message=f"expected five digits, got {raw!r}",
      )
    return cls(
      number=int(digits[0:2]),
      communication_protocol=int(digits[2]),
      decap_max_retry=int(digits[3]),
      recap_max_retry=int(digits[4]),
      raw=digits,
    )


# The extended status bitmask, most significant bit first.
#
# The command list is inconsistent about the width: it names these twelve flags
# but shows an eleven-character answer field. Which end a short reply is missing
# is therefore unknown, and guessing costs correctness at the most significant
# bit, which is CAPS_ON_PINS. A reply of any other width is rejected rather than
# padded, so the discrepancy surfaces instead of silently reading caps as absent.
EXTENDED_STATUS_FLAGS: Tuple[str, ...] = (
  "caps_on_pins",
  "dry_run_enabled",
  "light_curtain_disabled",
  "safety_door_rs232_disabled",
  "stage_enabled",
  "screw_caps_on",
  "cartridge_installed",
  "standby_active",
  "stage_pos",
  "recover_mode",
  "estop_active",
  "time_for_service",
)


@dataclasses.dataclass(frozen=True)
class ExtendedStatus:
  """Instrument state flags that the plain status word does not carry.

  ``caps_on_pins`` reports directly what the status word only implies through
  ``StatusRECAP``, and ``cartridge_installed``, ``estop_active`` and
  ``recover_mode`` explain states that otherwise only show up as a refused
  command.

  ``stage_pos`` is the command list's ``STAGE_POS`` bit. Which position each
  value means is not documented.
  """

  caps_on_pins: bool
  dry_run_enabled: bool
  light_curtain_disabled: bool
  safety_door_rs232_disabled: bool
  stage_enabled: bool
  screw_caps_on: bool
  cartridge_installed: bool
  standby_active: bool
  stage_pos: bool
  recover_mode: bool
  estop_active: bool
  time_for_service: bool
  raw: str

  @classmethod
  def from_raw(cls, raw: str) -> "ExtendedStatus":
    bits = raw.strip()
    if not bits or any(character not in "01" for character in bits):
      raise FluidXError(
        title="Malformed extended status reply",
        message=f"expected a string of 0s and 1s, got {raw!r}",
      )
    width = len(EXTENDED_STATUS_FLAGS)
    if len(bits) != width:
      raise FluidXError(
        title="Unexpected extended status width",
        message=(
          f"got {len(bits)} bits, expected {width}: {bits!r}. The command list names {width} "
          f"flags but shows an {width - 1}-character answer, so which end is missing cannot be "
          "guessed without misreading CAPS_ON_PINS. Map this reply against the instrument's "
          "known state and fix EXTENDED_STATUS_FLAGS."
        ),
      )
    values = {flag: bits[index] == "1" for index, flag in enumerate(EXTENDED_STATUS_FLAGS)}
    return cls(raw=bits, **values)


def _fault(key: str, detail: Optional[str] = None) -> FluidXError:
  """Build a FluidXError carrying the firmware's own description for ``key``."""
  return FluidXError(title=ERROR_MESSAGES.get(key, key), message=detail)


def _is_error_frame(frame: str) -> bool:
  """Whether a reply frame reports a fault rather than a success or a setting."""
  return _ERROR_FRAME_RE.search(frame.upper()) is not None


def _error_code(frames: List[str]) -> Optional[int]:
  """Extract a known three-digit error code from serial reply frames."""
  for frame in frames:
    for value in re.findall(r"(?<!\d)(\d{3})(?!\d)", frame):
      code = int(value)
      if code in ERROR_CODE_MESSAGES:
        return code
  return None


def _loaded_profile(frames: List[str]) -> Optional[int]:
  """Read the profile number out of a cartridge load reply's ``onnOK`` frame."""
  for frame in frames:
    match = re.fullmatch(r"o(\d+)OK", frame)
    if match is not None:
      return int(match.group(1))
  return None


class FluidXIntelliXcap96:
  """FluidX IntelliXcap 96 automated screw-cap decapper.

  A benchtop instrument that decaps and recaps a 96-format rack of screw-cap
  tubes in a single stroke. It holds one nest; a plate mover loads the rack, the
  decapper unscrews all 96 caps (``decap``), holds them, and screws them back on
  (``recap``). Held caps can also be released into a separately positioned cap
  carrier (``waste``), and the loading tray opened and closed. ``waste`` does
  not verify that a carrier is present: remove the tube rack and position the
  correct carrier before using it. If the rack is left beneath the head, the
  released caps can fall back onto the tubes without being properly recapped.

  Serial settings:
    9600 baud, 8 data bits, no parity, 1 stop bit, no handshake. Replies are
    framed between STX (0x02) and ETX (0x03).

  The instrument only speaks this protocol once setpoint 86 is set to 2
  ("IntelliXcap mode"), which is done at the instrument touchscreen; there is no
  serial command for it.

  Tube type and volume are not sent over the serial protocol. The installed
  IntelliCartridge and its firmware profile define the supported tube/cap
  geometry and motion settings. Fit and configure the cartridge specified for
  the exact tube family; volume alone (for example, 0.5 mL) is not sufficient
  to select a compatible cartridge.

  Single-character commands, each written followed by ETX:
    a   request status
    b   release held caps into a carrier
    c   eject the cartridge onto the tray
    d   dry-run mode on
    D   dry-run mode off
    e   extended status bitmask
    E   cartridge profile settings
    f   open the tray
    g   close the tray
    h   start decapping
    i   start recapping
    j   enter standby
    k   leave standby (ready)
    l   light curtain detection off
    L   light curtain detection on
    N   cartridge profile/cycles/serial
    Q   force a decap retry
    s   step the tray out by S88
    S   step the tray in by S88
    V   firmware versions
    X   reset the cartridge cycle counter
    Z   home all axes
    z   initialize keeping caps on pins
    +   safety door on
    -   safety door off
    5   eject held caps (manual mode)
    6   home the cap head (manual mode)
    7   open the safety door (manual mode)
    8   latched error code

  A command is answered with an ACK frame (0x06), a ``<cmd>OK`` echo frame, and a
  result frame. The status word, in the priority the firmware reports it, is
  ``StatusMANUAL`` (halted, needs inspection), ``StatusERROR`` (error code
  latched), ``StatusSLEEP`` (standby), ``StatusBUSY`` (motion running),
  ``StatusRECAP`` (decapped, caps held on the pins) or ``StatusOK`` (idle).
  Cartridge ejection reports ``StatusCAREJECT``. A refused or no-op command
  answers with ``CommandIgnore``. Motions complete when the status word returns
  from ``StatusBUSY`` to the operation's terminal state.

  Which state a failure lands in depends on the error code: 113/114 for decap,
  117/118 for recap, 142 for cartridge eject and 143/144 for cartridge load
  latch ``StatusERROR``, and the command list says anything else halts those
  four commands in ``StatusMANUAL``. It does not document the mapping for the
  other commands. :meth:`request_error_code` reads the latched code, and every
  raised :class:`FluidXError` carries it in ``error_code`` when the instrument
  reported one.

  Homing clears a ``StatusError``. With ``auto_recover`` enabled (the default),
  an operation issued while the device is latched in error homes to recover and
  then proceeds.

  Newer firmware documents ``t``/``T`` commands for absolute tray travel
  between S3 and S127. Unit firmware V49 ignored both commands completely, so
  :meth:`extend_tray` and :meth:`retract_tray` raise
  :class:`NotImplementedError` without writing to the instrument.

  Verified against hardware: connection, queries, tray open/close, home,
  standby/ready, settings, cartridge eject/load, the decap error/recovery path,
  forced decap retry, and decap/recap with a loaded 0.5 mL rack, including
  release of held caps with ``waste``. Unit firmware V49 did not acknowledge
  the four tray travel commands. The cartridge counter reset and manual
  recovery commands have not been exercised on an instrument.

  See the Azenta IntelliXcap user manual for the required carrier and physical
  setup:
  https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf
  """

  def __init__(
    self,
    port: str,
    timeout: float = 5.0,
    command_delay: float = 0.3,
    frame_gap: float = 0.5,
    poll_interval: float = 1.0,
    auto_recover: bool = True,
    recover_timeout: float = 30.0,
  ) -> None:
    """
    Args:
      port: serial port the decapper is connected to.
      timeout: serial read/write timeout in seconds.
      command_delay: pause after writing a command before reading its reply.
      frame_gap: how long to wait for another reply frame before concluding the
        reply is complete.
      poll_interval: pause between status polls while a motion runs.
      auto_recover: when an operation finds the device latched in StatusError,
        home it to clear the error and continue. A latched error is only cleared
        by homing. Disable to make a latched error raise instead.
      recover_timeout: timeout in seconds for the recovery home.
    """
    self.command_delay = command_delay
    self.frame_gap = frame_gap
    self.poll_interval = poll_interval
    self.auto_recover = auto_recover
    self.recover_timeout = recover_timeout
    self.io = Serial(
      human_readable_device_name="FluidX IntelliXcap 96",
      port=port,
      baudrate=9600,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=timeout,
    )

  async def setup(self) -> None:
    await self.io.setup()
    status = await self.request_status()
    up = status.upper()
    if "BUSY" in up:
      # At connect there is no motion in flight, so StatusBUSY means the
      # instrument is locked out, commonly by an engaged e-stop; the safety
      # guard/hood and other interlocks do the same. The extended status carries
      # an e-stop bit, so read it before giving up.
      estop = await self._estop_hint()
      logger.error(
        "[IntelliXcap96 %s] reports StatusBUSY at connect and will ignore commands. %s "
        "Also check the safety guard/hood and interlocks, and retry.",
        self.io.port,
        estop,
      )
      raise FluidXError(
        title="Decapper is not ready (StatusBUSY)",
        message=(
          f"The device reports BUSY and ignores commands. {estop} Also check the safety "
          "guard/hood and interlocks, then retry."
        ),
      )
    if "MANUAL" in up:
      raise await self._latched_fault("StatusManual", status)
    if "ERROR" in up:
      raise await self._latched_fault("StatusNotOK", status)
    logger.info("[IntelliXcap96 %s] connected: %s", self.io.port, status)

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()

  # === Framed command layer ===

  async def _send(self, command: str) -> None:
    """Discard pending input, write a command with its terminator, then pace."""
    await self.io.reset_input_buffer()
    await self.io.write(command.encode("ascii") + ETX)
    await asyncio.sleep(self.command_delay)

  async def _read_frame(self) -> Optional[str]:
    """Read one STX..ETX frame and return its payload, or None if none arrives."""
    while True:
      byte = await self.io.read(1)
      if byte == b"":
        return None
      if byte == STX:
        break
    buf = bytearray()
    while True:
      byte = await self.io.read(1)
      if byte in (b"", ETX):
        break
      buf += byte
    return buf.decode("ascii", errors="replace")

  async def send_command(self, command: str) -> List[str]:
    """Send a raw command and collect every reply frame until the reply goes quiet."""
    await self._send(command)
    frames: List[str] = []
    with self.io.temporary_timeout(self.frame_gap):
      while True:
        frame = await self._read_frame()
        if frame is None:
          break
        frames.append(frame)
    logger.debug("[IntelliXcap96] %r -> %r", command, frames)
    return frames

  @staticmethod
  def _status_frame(frames: List[str]) -> Optional[str]:
    return next((f for f in frames if f.upper().startswith("STATUS")), None)

  @staticmethod
  def _payload_frame(frames: List[str], command: str) -> Optional[str]:
    """Return the data frame of a query reply, skipping the ack and command echo."""
    echo = f"{command}OK"
    for frame in frames:
      if frame == ACK or frame == echo or frame.upper().startswith("STATUS"):
        continue
      return frame
    return None

  def _require_accepted(
    self,
    command: str,
    frames: List[str],
    name: str,
    idempotent: bool = False,
    echo: Optional[str] = None,
  ) -> bool:
    """Check a command's reply. Return True if it started a motion.

    Raises if the device did not ack and echo the command. A ``CommandIgnore``
    reply means the command was a no-op (the device is already in the requested
    state): for an ``idempotent`` command that is success and returns False (no
    motion to wait for); otherwise it is raised. ``echo`` overrides the expected
    echo frame for commands the firmware acknowledges under another name.
    """
    if ACK not in frames or f"{echo or command}OK" not in frames:
      raise _fault("NoAck", f"{name}: {frames!r}")
    if any(COMMAND_IGNORE in f for f in frames):
      if idempotent:
        return False
      raise _fault("CommandIgnore", f"{name}: device already in that state or not ready")
    return True

  @staticmethod
  def _require_answer(frames: List[str], expected: str, name: str) -> None:
    """Raise unless the device confirmed a setting with its documented reply frame."""
    if expected not in frames:
      raise _fault("NoAck", f"{name}: expected {expected!r}, got {frames!r}")

  async def _try_request_error_code(self) -> Optional[int]:
    """Read the latched error code, returning None if the query itself fails."""
    try:
      return await self.request_error_code()
    except FluidXError as exception:
      logger.debug("[IntelliXcap96 %s] error code query failed: %s", self.io.port, exception)
      return None

  async def _latched_fault(
    self, fail_key: str, detail: str, frames: Optional[List[str]] = None
  ) -> FluidXError:
    """Build the error for a failed operation, enriched with the latched error code.

    The code is taken from the reply frames when the firmware inlined it there,
    and otherwise read back with the error query, which is the documented way to
    find out why an operation failed.
    """
    code = _error_code(frames) if frames else None
    if code is None:
      code = await self._try_request_error_code()
    if code is not None:
      return FluidXError.from_error_code(code, detail=detail)
    return _fault(fail_key, f"{detail}. The device did not report a numeric error code.")

  async def _estop_hint(self) -> str:
    """Describe the e-stop state for a lockout message, if the device will say."""
    try:
      extended = await self.request_extended_status()
    except FluidXError:
      return "The E-STOP could not be read; it is a common cause."
    if extended.estop_active:
      return "The E-STOP is engaged; release it."
    return "The E-STOP reads as released."

  async def _wait_for_answer(
    self,
    frames: List[str],
    done_frame: str,
    timeout: float,
    name: str,
    fail_key: str,
  ) -> None:
    """Wait for an operation's own answer frame instead of polling status.

    Some operations do not announce completion through the status word: the
    status word is ``StatusMANUAL`` throughout a manual recovery command because
    the halt outranks ``StatusBUSY``, and the tray travel commands change no
    status at all. Their answer frame is the only completion signal, so this
    keeps reading frames rather than sending status polls, which would reset the
    input buffer and drop the answer.

    ``frames`` is the command's own reply, which often already carries the
    answer.
    """
    done = done_frame.upper()
    if any(_is_error_frame(f) for f in frames):
      raise await self._latched_fault(fail_key, f"{name}: {frames!r}", frames)
    if any(f.upper() == done for f in frames):
      return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    failure: Optional[List[str]] = None
    with self.io.temporary_timeout(self.frame_gap):
      while loop.time() < deadline:
        frame = await self._read_frame()
        if frame is None:
          await asyncio.sleep(self.poll_interval)
          continue
        if _is_error_frame(frame):
          failure = [frame]
          break
        if frame.upper() == done:
          return
    if failure is not None:
      raise await self._latched_fault(fail_key, f"{name}: {failure!r}", failure)
    raise FluidXError(
      title=f"{name} timed out",
      message=f"did not answer with {done_frame!r} within {timeout}s",
    )

  async def _wait_for_idle(
    self,
    timeout: float,
    name: str,
    fail_key: str,
    terminal_statuses: Tuple[str, ...] = ("StatusOK",),
    done_frames: Tuple[str, ...] = (),
  ) -> List[str]:
    """Poll status until it reaches an expected idle state.

    ``fail_key`` names the firmware error message to raise if the status word
    reports an error while waiting. ``terminal_statuses`` accounts for
    operation state retained while the instrument is idle: hardware reports
    ``StatusRECAP`` after decapping and after tray motion with caps held.
    ``done_frames`` are the operation's own completion frames, which end the
    wait as soon as one is seen.

    Returns every frame seen while waiting, so a caller can pick up an answer
    the instrument sends alongside its completion.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    expected = {status.upper() for status in terminal_statuses}
    done = {frame.upper() for frame in done_frames}
    seen: List[str] = []
    while loop.time() < deadline:
      frames = await self.send_command(STATUS)
      seen.extend(frames)
      for frame in frames:
        if frame.upper() in ("RETRYDECAP", "RETRYRECAP"):
          # The instrument retries a failed stroke by itself; report the attempt.
          logger.info("[IntelliXcap96 %s] %s: instrument reports %s", self.io.port, name, frame)
      if any(_is_error_frame(f) for f in frames):
        raise await self._latched_fault(fail_key, f"{name}: {frames!r}", frames)
      if any(f.upper() in done for f in frames):
        return seen
      status = self._status_frame(frames)
      if status is not None:
        up = status.upper()
        if up in expected:
          return seen
        if "MANUAL" in up:
          # A halt needs an operator, so waiting out the timeout only delays the report.
          raise await self._latched_fault("StatusManual", f"{name}: {frames!r}", frames)
      await asyncio.sleep(self.poll_interval)
    raise FluidXError(
      title=f"{name} timed out",
      message=f"did not reach {terminal_statuses!r} within {timeout}s",
    )

  # === Status and queries ===

  async def request_status(self) -> str:
    """Poll the device and return its status word (e.g. ``StatusOK``)."""
    frames = await self.send_command(STATUS)
    status = self._status_frame(frames)
    if status is None:
      raise FluidXError(title="No status reply", message=repr(frames))
    return status

  async def request_error_code(self) -> Optional[int]:
    """Read the error code the instrument has latched, or None if there is none.

    This is the documented way to find out *why* an operation failed:
    ``StatusERROR`` and ``StatusMANUAL`` say only that something went wrong.
    :func:`get_error_message` turns the code into its documented meaning, and
    :func:`is_recoverable_error` says whether homing will clear it.

    The command list documents the reply only as a three-digit error code and
    does not say how "no error" is expressed; a code of 0 is read as none.
    """
    frames = await self.send_command(ERROR_QUERY)
    self._require_accepted(ERROR_QUERY, frames, "Reading the error code", idempotent=True)
    payload = self._payload_frame(frames, ERROR_QUERY)
    if payload is None or not payload.strip().isdigit():
      raise FluidXError(title="No error code reply", message=repr(frames))
    code = int(payload.strip())
    return code if code != 0 else None

  async def request_extended_status(self) -> ExtendedStatus:
    """Read the extended status bitmask.

    Reports state the status word omits, including whether caps are held on the
    pins, whether a cartridge is installed and whether the e-stop is engaged.
    """
    frames = await self.send_command(EXTENDED_STATUS)
    self._require_accepted(EXTENDED_STATUS, frames, "Reading the extended status", idempotent=True)
    payload = self._payload_frame(frames, EXTENDED_STATUS)
    if payload is None:
      raise FluidXError(title="No extended status reply", message=repr(frames))
    return ExtendedStatus.from_raw(payload)

  async def request_firmware_versions(self) -> FirmwareVersions:
    """Read the unit, touchscreen and light curtain firmware versions.

    Dry-run mode requires touchscreen firmware V14 or above. The store operation
    is broken in V44; the command list does not say which of the three
    firmwares that version refers to.
    """
    frames = await self.send_command(FIRMWARE_QUERY)
    self._require_accepted(FIRMWARE_QUERY, frames, "Reading the firmware versions", idempotent=True)
    payload = self._payload_frame(frames, FIRMWARE_QUERY)
    parts = [part.strip() for part in payload.split(",")] if payload else []
    if len(parts) != 3:
      raise FluidXError(title="Malformed firmware reply", message=repr(frames))
    return FirmwareVersions(unit=parts[0], touchscreen=parts[1], light_curtain=parts[2])

  async def request_cartridge_info(self) -> CartridgeInfo:
    """Read the installed cartridge's profile, cycle count and serial.

    The serial always reads ``"00000000"``: the firmware does not implement it.
    """
    frames = await self.send_command(CARTRIDGE_QUERY)
    self._require_accepted(
      CARTRIDGE_QUERY, frames, "Reading the cartridge details", idempotent=True
    )
    payload = self._payload_frame(frames, CARTRIDGE_QUERY)
    parts = [part.strip() for part in payload.split(",")] if payload else []
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
      raise FluidXError(title="Malformed cartridge reply", message=repr(frames))
    return CartridgeInfo(profile=int(parts[0]), cycle_count=int(parts[1]), serial=parts[2])

  async def request_profile(self) -> CartridgeProfile:
    """Read the active profile number and its decap/recap retry limits."""
    frames = await self.send_command(PROFILE_QUERY)
    self._require_accepted(PROFILE_QUERY, frames, "Reading the profile", idempotent=True)
    payload = self._payload_frame(frames, PROFILE_QUERY)
    if payload is None:
      raise FluidXError(title="No profile reply", message=repr(frames))
    return CartridgeProfile.from_raw(payload)

  async def caps_on_pins(self) -> bool:
    """Whether caps are currently held on the ejector pins.

    Reads the ``CAPS_ON_PINS`` bit. The decap, recap and waste guards instead
    gate on the ``StatusRECAP`` word, which the command list ties to the same
    condition and which is hardware-verified.
    """
    return (await self.request_extended_status()).caps_on_pins

  # === Operations ===

  async def open_tray(self, timeout: float = 15.0) -> None:
    """Open the loading tray. Also opens the safety door."""
    await self._ensure_ready()
    frames = await self.send_command(OPEN_TRAY)
    if self._require_accepted(OPEN_TRAY, frames, "Opening the tray", idempotent=True):
      await self._wait_for_idle(
        timeout,
        "Opening the tray",
        "OpenTrayWasNotSuccesful",
        ("StatusOK", "StatusRECAP"),
        done_frames=("OpenDONE",),
      )
    logger.info("[IntelliXcap96 %s] tray open", self.io.port)

  async def close_tray(self, timeout: float = 15.0) -> None:
    """Close the loading tray, moving it to the decap/recap position."""
    await self._ensure_ready()
    frames = await self.send_command(CLOSE_TRAY)
    if self._require_accepted(CLOSE_TRAY, frames, "Closing the tray", idempotent=True):
      await self._wait_for_idle(
        timeout,
        "Closing the tray",
        "CloseTrayWasNotSuccesful",
        ("StatusOK", "StatusRECAP"),
        done_frames=("CloseDONE",),
      )
    logger.info("[IntelliXcap96 %s] tray closed", self.io.port)

  async def _tray_move(self, command: str, name: str, timeout: float) -> None:
    """Run one of the tray travel commands.

    These acknowledge and answer with the same ``<cmd>OK`` frame, so a completed
    move is a second echo and a failed one is ``<cmd>ERROR``.

    Unit firmware V49 does not acknowledge these commands.
    """
    await self._ensure_ready()
    echo = f"{command}OK"
    frames = await self.send_command(command)
    self._require_accepted(command, frames, name)
    if frames.count(echo) < 2:
      await self._wait_for_answer(
        [f for f in frames if f != echo], echo, timeout, name, "TrayMoveWasNotSuccesful"
      )
    logger.info("[IntelliXcap96 %s] %s", self.io.port, name.lower())

  async def extend_tray(self, timeout: float = 15.0) -> None:
    """Move the tray from the load position (S3) out to the extended position (S127).

    This command is documented for newer firmware but is not implemented here:
    unit firmware V49 ignored it completely. The method raises without writing
    to the instrument.
    """
    raise NotImplementedError(
      "extend_tray() uses the newer-firmware 't' command, which is not supported by "
      "IntelliXcap unit firmware V49"
    )

  async def retract_tray(self, timeout: float = 15.0) -> None:
    """Move the tray from the extended position (S127) back to the load position (S3).

    This command is documented for newer firmware but is not implemented here:
    unit firmware V49 ignored it completely. The method raises without writing
    to the instrument.
    """
    raise NotImplementedError(
      "retract_tray() uses the newer-firmware 'T' command, which is not supported by "
      "IntelliXcap unit firmware V49"
    )

  async def step_tray_out(self, timeout: float = 15.0) -> None:
    """Move the tray further out by the step distance in setpoint 88.

    Relative, so calling it twice moves the tray twice. The instrument offers no
    way to command an intermediate position directly, and no way to read the
    current one back, so stepping is the only access to the range between the
    two ends; :meth:`extend_tray` and :meth:`retract_tray` are the absolute
    moves. Travel is bounded by the load position (S3) and the extended position
    (S127); a step that would leave that range fails.
    """
    await self._tray_move(TRAY_STEP_OUT, "Stepping the tray out", timeout)

  async def step_tray_in(self, timeout: float = 15.0) -> None:
    """Move the tray back in by the step distance in setpoint 88.

    Relative, so calling it twice moves the tray twice. See
    :meth:`step_tray_out`. Travel is bounded by the load position (S3) and the
    extended position (S127); a step that would leave that range fails.
    """
    await self._tray_move(TRAY_STEP_IN, "Stepping the tray in", timeout)

  async def _home_sequence(self, timeout: float, name: str) -> None:
    """Send the home command and wait for it to finish. Also clears a latched error."""
    frames = await self.send_command(HOME_ALL)
    self._require_accepted(HOME_ALL, frames, name)
    await self._wait_for_idle(timeout, name, "HomeNotSuccesful", done_frames=("ZDONE",))

  async def _ensure_ready(self) -> str:
    """Return the current status, first clearing a latched error by homing.

    A ``StatusError`` is only cleared by homing. With ``auto_recover`` enabled,
    an operation that finds the device latched in error homes to recover and
    then proceeds; otherwise the latched error is raised.
    """
    status = await self.request_status()
    up = status.upper()
    if "MANUAL" in up:
      raise await self._latched_fault("StatusManual", status)
    if "ERROR" not in up:
      return status
    if not self.auto_recover:
      raise await self._latched_fault("StatusNotOK", status)
    logger.warning(
      "[IntelliXcap96 %s] latched in StatusError; homing to recover before continuing.",
      self.io.port,
    )
    await self._home_sequence(self.recover_timeout, "Homing (error recovery)")
    status = await self.request_status()
    if "ERROR" in status.upper():
      raise await self._latched_fault("StatusNotOK", "error persisted after recovery home")
    return status

  async def _require_manual_mode(self, name: str) -> None:
    """Raise unless the instrument is in manual recovery mode."""
    status = await self.request_status()
    if "MANUAL" not in status.upper():
      raise _fault("NotInManualMode", f"{name}: device reports {status}")

  async def _manual_command(
    self, command: str, name: str, fail_key: str, done_frame: str, timeout: float
  ) -> None:
    """Run a command that is only accepted while the instrument is halted.

    Manual recovery keeps the status word at ``StatusMANUAL`` for the whole
    operation, so completion is read from the answer frame.
    """
    await self._require_manual_mode(name)
    frames = await self.send_command(command)
    self._require_accepted(command, frames, name)
    await self._wait_for_answer(frames, done_frame, timeout, name, fail_key)

  async def reset_error(self, timeout: Optional[float] = None) -> None:
    """Recover from ``StatusError`` or ``StatusMANUAL`` by homing all axes.

    Hardware testing confirmed that the home-all command transitions
    ``StatusMANUAL`` through ``StatusBUSY`` to ``StatusOK``. Call this only after
    inspecting the rack and cap head and confirming that axis motion is safe.

    What homing does to caps held on the pins is unsettled. The command list
    says the home sequence "will not drop caps", yet it also carries a separate
    command to initialize without dropping them, which that would make
    redundant. Neither behaviour has been checked on an instrument. With caps
    held, prefer :meth:`initialize_keeping_caps_on_pins`, which is the command
    documented for that case.

    This method is a no-op when the instrument is not in an error or manual
    recovery state.

    Args:
      timeout: maximum recovery time in seconds. Defaults to
        ``recover_timeout`` configured on this instance.
    """
    status = await self.request_status()
    up = status.upper()
    if "ERROR" not in up and "MANUAL" not in up:
      return
    await self._home_sequence(
      self.recover_timeout if timeout is None else timeout,
      "Resetting error",
    )
    logger.info("[IntelliXcap96 %s] error reset by homing", self.io.port)

  async def home(self, timeout: float = 30.0) -> None:
    """Home all axes. Also clears all errors and a latched StatusError."""
    await self._home_sequence(timeout, "Homing")
    logger.info("[IntelliXcap96 %s] homed", self.io.port)

  async def initialize_keeping_caps_on_pins(self, timeout: float = 30.0) -> None:
    """Clear the error state and home without dropping caps held on the pins.

    For recovering an instrument that stopped mid-cycle with caps held, after
    which :meth:`recap` can put them back. The firmware marks this command
    deprecated, and it is only accepted in manual recovery mode.
    """
    await self._manual_command(
      INITIALIZE_KEEP_CAPS,
      "Initializing while keeping caps on pins",
      "HomeNotSuccesful",
      "zDONE",
      timeout,
    )
    logger.info("[IntelliXcap96 %s] initialized with caps on pins", self.io.port)

  async def decap(self, timeout: float = 60.0) -> None:
    """Unscrew and hold all 96 caps.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" in status:
      raise _fault("NeedToRecap")
    frames = await self.send_command(DECAP_START)
    self._require_accepted(DECAP_START, frames, "Decapping")
    await self._wait_for_idle(
      timeout,
      "Decapping",
      "DecapWasNotSuccesful",
      ("StatusRECAP",),
      done_frames=("DecapDONE",),
    )
    logger.info("[IntelliXcap96 %s] decap complete", self.io.port)

  async def retry_decap(self, timeout: float = 60.0) -> None:
    """Force one more decap stroke on tubes that a completed decap left capped.

    Only useful with light curtain error detection off. With detection on the
    instrument sees the tubes that failed to decap and retries inside
    :meth:`decap` by itself, up to the profile's decap retry limit, so there is
    nothing left to force. With detection off it cannot see them, ends the
    stroke as successful, and this is the only way to ask for another attempt:
    :meth:`decap` refuses while caps are held.
    :meth:`set_error_detection_enabled` controls detection.

    The command list documents only that this command is used "after a
    successful Decap" and "requires lightcurtain OFF". That those are the same
    condition is inferred from the automatic-retry and error-detection commands
    rather than stated by the vendor. The instrument finishes in
    ``StatusRECAP`` because it is still holding the caps for a subsequent
    :meth:`recap`.
    """
    await self._ensure_ready()
    frames = await self.send_command(RETRY_DECAP)
    # The firmware acknowledges a forced retry as if it were a fresh decap.
    self._require_accepted(RETRY_DECAP, frames, "Retrying the decap", echo=DECAP_START)
    await self._wait_for_idle(
      timeout,
      "Retrying the decap",
      "RetryDecapWasNotSuccesful",
      ("StatusRECAP",),
      done_frames=("DecapDONE",),
    )
    logger.info("[IntelliXcap96 %s] decap retry complete", self.io.port)

  async def recap(self, timeout: float = 60.0) -> None:
    """Screw the held caps back on.

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" not in status:
      raise _fault("NeedToDecap")
    frames = await self.send_command(RECAP_START)
    self._require_accepted(RECAP_START, frames, "Recapping")
    await self._wait_for_idle(
      timeout,
      "Recapping",
      "RecapWasNotSuccesful",
      ("StatusOK",),
      done_frames=("RecapDONE",),
    )
    logger.info("[IntelliXcap96 %s] recap complete", self.io.port)

  async def waste(self, timeout: float = 60.0) -> None:
    """Release the currently held caps into a separately positioned cap carrier.

    This is irreversible. Before calling, remove the sample-tube rack and
    position the correct cap carrier/collection vessel as specified in the
    Azenta IntelliXcap user manual. The instrument does not detect or verify the
    carrier. If the tube rack remains beneath the head, released caps can fall
    back onto the tube openings and look recapped even though they may not be
    threaded or torqued.

    The instrument picks the store sequence from the height of the cap carrier
    and otherwise performs a recap. Firmware V44 does not implement this command
    -- the command list does not say which of the three firmwares it means -- so
    on that version load a store rack and use :meth:`decap` and :meth:`recap`,
    which select the store sequence themselves. A store that times out presents
    the tray, answering ``LoadOK`` alongside the ``StoreERROR``.

    User manual:
    https://web.azenta.com/hubfs/azenta-files/resources/manuals-guides/319430-IXC-User-Manual.pdf

    Args:
      timeout: maximum time in seconds to wait for the stroke to finish.
    """
    status = (await self._ensure_ready()).upper()
    if "RECAP" not in status:
      raise _fault("NeedToDecap", "waste requires caps held after decapping")
    frames = await self.send_command(WASTE)
    self._require_accepted(WASTE, frames, "Wasting caps")
    await self._wait_for_idle(
      timeout,
      "Wasting caps",
      "StoreWasNotSuccesful",
      ("StatusOK",),
      done_frames=("StoreDONE",),
    )
    logger.info("[IntelliXcap96 %s] waste complete", self.io.port)

  async def standby(self, timeout: float = 15.0) -> None:
    """Put the decapper into low-power standby (sleep) mode.

    A no-op when the instrument is already asleep. The instrument requires
    StatusOK to sleep, and refuses while caps are held on the pins.
    """
    if "SLEEP" in (await self.request_status()).upper():
      return
    frames = await self.send_command(STANDBY)
    self._require_accepted(STANDBY, frames, "Entering standby")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
      if "SLEEP" in (await self.request_status()).upper():
        logger.info("[IntelliXcap96 %s] standby", self.io.port)
        return
      await asyncio.sleep(self.poll_interval)
    raise _fault("CannotGoInStandbyMode", "standby timed out")

  async def ready(self, timeout: float = 30.0) -> None:
    """Wake the decapper from standby if it is asleep."""
    if "SLEEP" not in (await self.request_status()).upper():
      return
    frames = await self.send_command(READY)
    self._require_accepted(READY, frames, "Waking from standby")
    await self._wait_for_idle(
      timeout, "Waking from standby", "StatusNotOK", done_frames=("ReadyDONE",)
    )
    logger.info("[IntelliXcap96 %s] ready", self.io.port)

  # === Cartridge handling ===

  async def eject_cartridge(self, timeout: float = 60.0) -> None:
    """Eject the installed IntelliCartridge onto the tray.

    A no-op when no cartridge is installed. The tray must be empty and no caps
    may be held on the pins.
    """
    extended = await self.request_extended_status()
    if not extended.cartridge_installed:
      return
    if extended.caps_on_pins:
      raise _fault("CapsOnPins", "the cartridge cannot be ejected while caps are held")
    await self._ensure_ready()
    frames = await self.send_command(CARTRIDGE_EJECT)
    self._require_accepted(CARTRIDGE_EJECT, frames, "Ejecting the cartridge")
    await self._wait_for_idle(
      timeout,
      "Ejecting the cartridge",
      "CartridgeEjectWasNotSuccesful",
      ("StatusCAREJECT",),
      done_frames=("CarEjectDONE",),
    )
    logger.info("[IntelliXcap96 %s] cartridge ejected", self.io.port)

  async def load_cartridge(self, timeout: float = 60.0) -> Optional[int]:
    """Pick up the cartridge resting on the tray.

    A no-op when a cartridge is already installed. Loading a stored profile
    (16-96) answers ``ProfileLoadERROR`` if that profile does not exist or is
    invalid.

    Returns:
      The loaded profile number when the instrument reports one, else None. It
      answers with ``onnOK`` at the end of the motion, so this is None if that
      frame never arrives.
    """
    if (await self.request_extended_status()).cartridge_installed:
      return None
    frames = await self.send_command(CARTRIDGE_LOAD)
    self._require_accepted(CARTRIDGE_LOAD, frames, "Loading the cartridge")
    if any(_is_error_frame(f) for f in frames):
      raise await self._latched_fault(
        "CartridgeLoadWasNotSuccesful", f"Loading the cartridge: {frames!r}", frames
      )
    seen = await self._wait_for_idle(
      timeout,
      "Loading the cartridge",
      "CartridgeLoadWasNotSuccesful",
      ("StatusOK",),
      done_frames=("CarLoadDONE", "ExtCarLoadDONE"),
    )
    profile = _loaded_profile(frames + seen)
    logger.info("[IntelliXcap96 %s] cartridge loaded (profile %s)", self.io.port, profile)
    return profile

  async def reset_cartridge_counter(self) -> None:
    """Reset the installed cartridge's cycle counter to zero."""
    name = "Resetting the cartridge counter"
    frames = await self.send_command(CARTRIDGE_COUNTER_RESET)
    if self._require_accepted(CARTRIDGE_COUNTER_RESET, frames, name, idempotent=True):
      self._require_answer(frames, "CarResetDONE", name)
    logger.info("[IntelliXcap96 %s] cartridge counter reset", self.io.port)

  # === Settings ===

  async def set_error_detection_enabled(self, enabled: bool) -> None:
    """Turn light curtain error detection during decap and recap on or off.

    With detection off the instrument finishes every decap and recap stroke
    instead of stopping on error 135 or 136, and :meth:`retry_decap` becomes
    available. Detection is on at power-up.
    """
    command = ERROR_DETECTION_ON if enabled else ERROR_DETECTION_OFF
    name = "Enabling error detection" if enabled else "Disabling error detection"
    frames = await self.send_command(command)
    if self._require_accepted(command, frames, name, idempotent=True):
      self._require_answer(frames, "ErrorDetectON" if enabled else "ErrorDetectOFF", name)
    logger.info("[IntelliXcap96 %s] error detection %s", self.io.port, "on" if enabled else "off")

  async def set_dry_run_enabled(self, enabled: bool) -> None:
    """Turn dry-run mode on or off.

    In dry-run mode the instrument stops and waits for the operator when the
    light curtain reports error 114, 118, 135 or 136 instead of failing the
    operation. Requires touchscreen firmware V14 or above, which
    :meth:`request_firmware_versions` reports.
    """
    command = DRY_RUN_ON if enabled else DRY_RUN_OFF
    name = "Enabling dry-run mode" if enabled else "Disabling dry-run mode"
    frames = await self.send_command(command)
    if self._require_accepted(command, frames, name, idempotent=True):
      self._require_answer(frames, "DryRunON" if enabled else "DryRunOFF", name)
    logger.info("[IntelliXcap96 %s] dry-run mode %s", self.io.port, "on" if enabled else "off")

  async def set_safety_door_enabled(self, enabled: bool) -> None:
    """Turn safety door operation on or off.

    Disabling it opens the door and keeps it open. Call this with ``True`` to
    re-enable the door; it is also enabled again at power-up.
    """
    command = SAFETY_DOOR_ENABLE if enabled else SAFETY_DOOR_DISABLE
    name = "Enabling the safety door" if enabled else "Disabling the safety door"
    frames = await self.send_command(command)
    if self._require_accepted(command, frames, name, idempotent=True):
      self._require_answer(frames, "DoorONDONE" if enabled else "DoorOFFDONE", name)
    logger.info("[IntelliXcap96 %s] safety door %s", self.io.port, "on" if enabled else "off")

  # === Manual recovery ===

  async def eject_caps(self, timeout: float = 30.0) -> None:
    """Home the cap head, dropping any caps held on the cap drivers.

    Closes the tray first if it is open, and opens the safety door. Only
    available in manual recovery mode, and the instrument stays there
    afterwards. The caps fall wherever the head is standing, so clear the deck
    and read :meth:`waste` before using this to get rid of caps deliberately.
    """
    await self._manual_command(
      EJECT_CAPS, "Ejecting the caps", "EjectCapsWasNotSuccesful", "EjectDONE", timeout
    )
    logger.info("[IntelliXcap96 %s] caps ejected", self.io.port)

  async def head_up(self, timeout: float = 30.0) -> None:
    """Home the Z axis, keeping any held caps on the pins.

    Only available in manual recovery mode, and the instrument stays there
    afterwards.
    """
    await self._manual_command(
      HEAD_UP, "Homing the cap head", "HeadUpWasNotSuccesful", "HeadDONE", timeout
    )
    logger.info("[IntelliXcap96 %s] cap head homed", self.io.port)

  async def open_safety_door(self, timeout: float = 15.0) -> None:
    """Open the safety door to reach into the instrument.

    Only available in manual recovery mode, and the instrument stays there
    afterwards. :meth:`open_tray` opens the door as part of presenting the tray.
    """
    await self._manual_command(
      SAFETY_DOOR_OPEN, "Opening the safety door", "SafetyDoorWasNotSuccesful", "DoorDONE", timeout
    )
    logger.info("[IntelliXcap96 %s] safety door open", self.io.port)
