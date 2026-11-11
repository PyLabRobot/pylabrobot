"""ACE messages: what the Biomek i7's boards are sent and what they answer.

A request is a 5-byte header and its parameters, little-endian::

  device (1) | index (1) | 0x00 | sequence (1) | opcode (1) | parameters

`device` names the kind of thing addressed on the board (the module itself, one of its boards, a
motor, a syringe pump, an input...), `index` which one of them, and `opcode` the operation, whose
meaning depends on `device`. A board answers with the same header, then a status byte and the
status's data::

  device | index | 0x00 | sequence | opcode | status (1) | data

A command that starts something answers twice, ACCEPTED and then DONE; anything else answers
DONE once. Boards also send messages nobody asked for, with sequence 0: EVENT (an input changed,
the interlock tripped), NOTICE (the host-alive heartbeat, a move stopped by the interlock) and
EMERGENCY (CANopen EMCY from a drive, SAGIAN CAN traffic).

The requests here are the ones the Master Controller was logged sending, plus the reads in
`FIRMWARE_READS`, and nothing else: `Request` refuses any device/opcode pair not in `COMMANDS`,
so firmware upgrade and flash commands cannot be sent through this module. A firmware read is one the Master Controller never sends, which only reads a state and changes
nothing.
"""

import enum
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import INTERLOCK_TRIPPED_CODE, ace_error


class Device(enum.IntEnum):
  """The device byte: what kind of thing on a board a message addresses."""

  MODULE = 0x00
  INTERLOCK = 0x01
  BOARD = 0x02
  MOTION_SERVICE = 0x10
  MOTOR = 0x11
  PUMP = 0x12
  TIP_SENSOR = 0x13
  IO_SERVICE = 0x20
  DIGITAL_INPUT = 0x21
  ANALOG_INPUT = 0x22
  DIGITAL_OUTPUT = 0x28
  STATUS_DISPLAY = 0x2D
  ACCUFRAME = 0xAF
  LEGACY_ALP = 0xFE
  IO_BOX = 0xFF


class Status(enum.IntEnum):
  """A reply's status byte."""

  ERROR = 0x00
  ACCEPTED = 0x01
  DONE = 0x02
  EVENT = 0x03
  NOTICE = 0xFE
  EMERGENCY = 0xFF


# Every request this driver may send, by (device, opcode). The names are the Master Controller's.
COMMANDS: Dict[Tuple[int, int], str] = {
  (Device.MODULE, 0x00): "Get Module ID",
  (Device.MODULE, 0x02): "Get Module Serial Number",
  (Device.MODULE, 0x03): "Get Module Hardware Version",
  (Device.MODULE, 0x08): "Get Module Device Count",
  (Device.MODULE, 0x0F): "Stop Module Execution",
  (Device.MODULE, 0x11): "Get Module Application Image Length",
  (Device.MODULE, 0x12): "Get Module Application Image CRC",
  (Device.BOARD, 0x02): "Get Board Serial Number",
  (Device.BOARD, 0x03): "Get Board Hardware Version",
  (Device.BOARD, 0x04): "Get Board Bootloader Version",
  (Device.BOARD, 0x10): "Get Board Application Version",
  (Device.BOARD, 0x11): "Get Board Application Image Length",
  (Device.BOARD, 0x12): "Get Board Application Image CRC",
  (Device.INTERLOCK, 0x11): "Notify On Interlock Trip",
  (Device.INTERLOCK, 0x50): "Trip Interlock",
  (Device.INTERLOCK, 0x51): "Re-Arm Interlock",
  (Device.MOTION_SERVICE, 0x0F): "Stop Motion Service",
  (Device.MOTION_SERVICE, 0x50): "Move All",
  (Device.MOTOR, 0x10): "Configure",
  (Device.MOTOR, 0x12): "Set State",
  (Device.MOTOR, 0x21): "Get Homed State",
  (Device.MOTOR, 0x24): "Get Current Position",
  (Device.MOTOR, 0x31): "Get Control Effort",
  (Device.MOTOR, 0x40): "Set Move Parameters",
  (Device.MOTOR, 0x50): "Move",
  (Device.MOTOR, 0x51): "Home",
  (Device.PUMP, 0x10): "Configure",
  (Device.PUMP, 0x12): "Set State",
  (Device.PUMP, 0x21): "Get Homed State",
  (Device.PUMP, 0x24): "Get Current Position",
  (Device.PUMP, 0x40): "Set Move Parameters",
  (Device.PUMP, 0x51): "Home",
  (Device.TIP_SENSOR, 0x10): "Get Tip Status",
  (Device.TIP_SENSOR, 0x41): "Set LLS/CD Sensitivity",
  (Device.IO_SERVICE, 0x08): "Get IO Service Device Count",
  (Device.DIGITAL_INPUT, 0x01): "Get Digital Input Name",
  (Device.DIGITAL_INPUT, 0x10): "Read Digital Input",
  (Device.DIGITAL_INPUT, 0x11): "Notify On Digital Input Change",
  (Device.ANALOG_INPUT, 0x01): "Get Analog Input Name",
  (Device.ANALOG_INPUT, 0x10): "Read Analog Input",
  (Device.ANALOG_INPUT, 0x12): "Get Analog Input Units",
  (Device.DIGITAL_OUTPUT, 0x01): "Get Digital Output Name",
  (Device.DIGITAL_OUTPUT, 0x10): "Read Digital Output",
  (Device.DIGITAL_OUTPUT, 0x11): "Notify On Digital Output Change",
  (Device.STATUS_DISPLAY, 0x10): "Set Status Display",
  (Device.ACCUFRAME, 0xF7): "Get AccuFrame Offsets",
  (Device.LEGACY_ALP, 0x18): "List Legacy ALP Devices",
  (Device.IO_BOX, 0x10): "Send Digital I/O Box",
}

# Reads the Master Controller never sends. Get State answers an axis's state byte (`AxisState`)
# with no precondition, and changes nothing. Get Interlock Status answers the interlock's state
# byte; the Master Controller's log names it "Get Interlock Status".
FIRMWARE_READS: Dict[Tuple[int, int], str] = {
  (Device.MOTOR, 0x11): "Get State",
  (Device.PUMP, 0x11): "Get State",
  (Device.INTERLOCK, 0x10): "Get Interlock Status",
}
COMMANDS.update(FIRMWARE_READS)

# Requests that answer ACCEPTED before DONE.
TWO_STAGE: frozenset = frozenset(
  {(Device.MOTION_SERVICE, 0x50), (Device.MOTOR, 0x50), (Device.MOTOR, 0x51), (Device.PUMP, 0x51)}
)

# The opcode a board's unsolicited messages carry, by (device, opcode).
HOST_ALIVE = (Device.MODULE, 0x40)
EMCY = (Device.MODULE, 0x31)
SAGIAN_CAN = (Device.MODULE, 0x30)
INTERLOCK_TRIPPED = (Device.INTERLOCK, 0x10)
DIGITAL_INPUT_VALUE = (Device.DIGITAL_INPUT, 0x10)

# A SAGIAN CAN message a Span-8 board passes up: the message's kind and status bytes, named as the
# Master Controller's log names them ("SAGIAN CAN TO_NODE 05:00 READ SUCCESS F7 ..."). The board
# shares the CAN bus with the SAGIAN devices behind it (the AccuFrame is node 05) and forwards what
# it sees there, the answers to the host's own requests included: reading the AccuFrame's offsets
# through the Multichannel's board brings the AccuFrame's answer back from the Span-8's as well.
# Every such message in the Master Controller's logs (2026-07-27 .. 10-27) is a success or a
# request; any other status is not known to be harmless.
SAGIAN_CAN_KINDS = {0x00: "ACTION", 0x01: "READ", 0x02: "WRITE", 0x03: "EVENT"}
SAGIAN_CAN_KNOWN_STATUSES = {0x00: "REQUEST", 0x01: "SUCCESS"}


def describe_sagian_can(data: bytes) -> Tuple[str, bool]:
  """A forwarded SAGIAN CAN message as the Master Controller's log words it
  (`TO_NODE 05:00 READ SUCCESS F7 28 0F BB 40`), and whether its status is one of those seen in
  normal operation (`SAGIAN_CAN_KNOWN_STATUSES`)."""
  if len(data) < 7:
    return f"short message {data.hex()}", False
  kind = SAGIAN_CAN_KINDS.get(data[5], f"kind 0x{data[5]:02X}")
  status = SAGIAN_CAN_KNOWN_STATUSES.get(data[6])
  words = [
    "TO_NODE" if data[1] == 0x01 else f"direction 0x{data[1]:02X}",
    f"{data[2]:02X}:{data[4]:02X}",
    kind,
    status or f"status 0x{data[6]:02X}",
  ]
  if len(data) > 7:
    words.append(data[7:].hex(" ").upper())
  return " ".join(words), status is not None and data[1] == 0x01


# A drive's own error code while its motor power is off, the interlock tripped ("interlock active").
INTERLOCK_ACTIVE = 0x0416


def describe_routine_unsolicited(reply: "Reply") -> Optional[str]:
  """What an unsolicited message every session sees says, or None for one worth showing.

  Routine: a drive's EMCY with error 0000 (its fault cleared, as it is enabled), a drive's EMCY
  naming `interlock active`, and a Move All notice ending in `interlock tripped`. The last two
  follow every interlock trip, the driver's own at `stop` included. So does the chassis's trip event
  (Notify On Interlock Trip), which is logged at INFO; `BiomekI7Driver` tells a trip it made from
  one it did not."""
  if (reply.device, reply.opcode) == EMCY and reply.status == Status.EMERGENCY:
    if len(reply.data) < 8:
      return None
    emcy = parse_emcy(reply.data)
    if emcy.error_code == 0:
      return f"drive node 0x{emcy.node:02X}: fault cleared (EMCY 0000)"
    if emcy.device_error == INTERLOCK_ACTIVE:
      return f"drive node 0x{emcy.node:02X}: interlock active (EMCY {emcy.error_code:04X})"
    return None
  if reply.status == Status.NOTICE and reply.error_code == INTERLOCK_TRIPPED_CODE:
    return f"device 0x{reply.device:02X} index {reply.index}: move ended, interlock tripped"
  return None


@dataclass(frozen=True)
class Request:
  """One request, before a sequence number is given to it."""

  device: int
  index: int
  opcode: int
  params: bytes = b""

  def __post_init__(self) -> None:
    if (self.device, self.opcode) not in COMMANDS:
      raise ValueError(
        f"device 0x{self.device:02X} opcode 0x{self.opcode:02X} is not a command this driver sends"
      )
    if not 0 <= self.index <= 0xFF:
      raise ValueError(f"index must be 0-255, got {self.index}")

  @property
  def name(self) -> str:
    return COMMANDS[(self.device, self.opcode)]

  @property
  def two_stage(self) -> bool:
    """Whether the board answers ACCEPTED before DONE."""
    return (self.device, self.opcode) in TWO_STAGE

  def encode(self, sequence: int) -> bytes:
    """The message bytes, with `sequence` (1-255; 0 is the boards' own)."""
    if not 1 <= sequence <= 0xFF:
      raise ValueError(f"sequence must be 1-255, got {sequence}")
    return bytes([self.device, self.index, 0x00, sequence, self.opcode]) + self.params

  def __str__(self) -> str:
    return f"{self.name} (device 0x{self.device:02X}, index {self.index})"


@dataclass(frozen=True)
class Reply:
  """One message from a board."""

  device: int
  index: int
  sequence: int
  opcode: int
  status: int
  data: bytes
  # Replies the board sent under this request's sequence for other devices: a Move All's axes
  # report there when they end other than plainly. Set by `AceBoard` on the final reply.
  related: Tuple["Reply", ...] = field(default=(), compare=False)

  @classmethod
  def parse(cls, message: bytes) -> "Reply":
    """Split a message into its header, status and data.

    Raises:
      ValueError: if the message is shorter than a header and a status byte.
    """
    if len(message) < 6:
      raise ValueError(f"ACE reply too short: {message.hex()}")
    device, index, _, sequence, opcode, status = message[:6]
    return cls(device, index, sequence, opcode, status, bytes(message[6:]))

  @property
  def key(self) -> Tuple[int, int, int, int]:
    """What a reply shares with the request it answers."""
    return (self.device, self.index, self.sequence, self.opcode)

  @property
  def unsolicited(self) -> bool:
    return self.sequence == 0

  @property
  def error_code(self) -> Optional[int]:
    """The ACE error code, or None when the reply reports success."""
    if self.status == Status.ERROR:
      return self.data[0] if self.data else 0
    if self.status in (Status.ACCEPTED, Status.DONE, Status.NOTICE) and self.data and self.data[0]:
      return self.data[0]
    return None

  @property
  def device_error(self) -> Optional[int]:
    """The drive's own error code that follows the ACE error code, when there is one."""
    if self.error_code is None or len(self.data) < 2:
      return None
    return int.from_bytes(self.data[1:], "little")

  @property
  def payload(self) -> bytes:
    """What a successful reply returns, after its zero error byte."""
    return self.data[1:]

  def raise_for_error(self, request: str) -> None:
    """Raise the `AceError` this reply reports, if it reports one."""
    code = self.error_code
    if code is not None:
      raise ace_error(code, self.device_error, request, self.device, self.index)

  def check(self, request: str) -> "Reply":
    """Return this reply, or raise the `AceError` it reports."""
    self.raise_for_error(request)
    return self


# -- requests ----------------------------------------------------------------


def get_module_id() -> Request:
  return Request(Device.MODULE, 0, 0x00)


def get_module_serial_number() -> Request:
  return Request(Device.MODULE, 0, 0x02)


def get_module_hardware_version() -> Request:
  return Request(Device.MODULE, 0, 0x03)


def get_module_device_count() -> Request:
  return Request(Device.MODULE, 0, 0x08)


def get_module_application_image_length() -> Request:
  return Request(Device.MODULE, 0, 0x11)


def get_module_application_image_crc() -> Request:
  return Request(Device.MODULE, 0, 0x12)


def stop_module_execution() -> Request:
  return Request(Device.MODULE, 0, 0x0F)


def get_board_serial_number(board: int) -> Request:
  return Request(Device.BOARD, board, 0x02)


def get_board_hardware_version(board: int) -> Request:
  return Request(Device.BOARD, board, 0x03)


def get_board_bootloader_version(board: int) -> Request:
  return Request(Device.BOARD, board, 0x04)


def get_board_application_version(board: int) -> Request:
  return Request(Device.BOARD, board, 0x10)


def get_board_application_image_length(board: int) -> Request:
  return Request(Device.BOARD, board, 0x11)


def get_board_application_image_crc(board: int) -> Request:
  return Request(Device.BOARD, board, 0x12)


def get_interlock_status(interlock: int = 1) -> Request:
  return Request(Device.INTERLOCK, interlock, 0x10)


def notify_on_interlock_trip(interlock: int = 1, notify: bool = True) -> Request:
  return Request(Device.INTERLOCK, interlock, 0x11, bytes([int(notify)]))


def trip_interlock(interlock: int = 1) -> Request:
  return Request(Device.INTERLOCK, interlock, 0x50)


def rearm_interlock(interlock: int = 1) -> Request:
  return Request(Device.INTERLOCK, interlock, 0x51)


def stop_motion_service() -> Request:
  return Request(Device.MOTION_SERVICE, 0, 0x0F)


def move_all(timeout_ms: int) -> Request:
  """Start every move whose parameters were set since the last move, together."""
  return Request(Device.MOTION_SERVICE, 0, 0x50, struct.pack("<I", timeout_ms))


def configure(device: int, axis: int, payload: bytes) -> Request:
  """Load an axis's motor table. `payload` is the table exactly as the Master Controller sent it."""
  return Request(device, axis, 0x10, payload)


def set_state_enabled(device: int, axis: int) -> Request:
  return Request(device, axis, 0x12, b"\x00")


def get_state(device: int, axis: int) -> Request:
  """Ask a motor or pump for its state byte; see `AxisState`."""
  return Request(device, axis, 0x11)


def get_homed_state(device: int, axis: int) -> Request:
  """Whether a motor or a pump (`device`) has been homed since the board started."""
  return Request(device, axis, 0x21)


def get_current_position(device: int, axis: int) -> Request:
  return Request(device, axis, 0x24)


def get_control_effort(axis: int) -> Request:
  return Request(Device.MOTOR, axis, 0x31)


# The largest force limit Set Move Parameters can carry: a u32 of the Master Controller's units x1000.
MAX_FORCE = 4294967.295


def set_motor_move_parameters(
  axis: int,
  position: int,
  velocity: int,
  acceleration: int,
  jerk: int,
  force: int = 0,
  sensor: int = 0,
  trigger: int = 0,
) -> Request:
  """Set a motor's next move, in counts, laid out as the Master Controller's logged requests lay
  it out. `force` is the force limit x1000 (grip and tip loading), 0 for none. `sensor`
  and `trigger` make the move stop where a sensor fires: liquid-level sensing on a Span-8 Z, the
  AccuFrame when framing (0, 0: no sensor)."""
  params = struct.pack("<IIIIiBB", velocity, acceleration, jerk, force, position, sensor, trigger)
  return Request(Device.MOTOR, axis, 0x40, params)


# The pump valve positions, as the Master Controller's `ace_io` log decodes them.
Valve = Literal["input", "output", "bypass", "extra"]
VALVE_POSITIONS: Dict[str, int] = {"input": 0, "output": 1, "bypass": 2, "extra": 3}


def set_pump_move_parameters(
  axis: int,
  position: int,
  start_velocity: int,
  max_velocity: int,
  cutoff_velocity: int,
  acceleration: int,
  valve: Valve,
) -> Request:
  """Set a syringe pump's next move, in counts, with the valve position it moves through."""
  params = struct.pack(
    "<IIIIiB",
    start_velocity,
    max_velocity,
    cutoff_velocity,
    acceleration,
    position,
    VALVE_POSITIONS[valve],
  )
  return Request(Device.PUMP, axis, 0x40, params)


def move(axis: int, timeout_ms: int) -> Request:
  """Start one motor's move on its own."""
  return Request(Device.MOTOR, axis, 0x50, struct.pack("<I", timeout_ms))


def home(device: int, axis: int) -> Request:
  return Request(device, axis, 0x51)


def get_tip_status(sensor: int = 1) -> Request:
  return Request(Device.TIP_SENSOR, sensor, 0x10)


def set_lls_cd_sensitivity(clot_detection: bool, value: int) -> Request:
  """Liquid-level (or, with `clot_detection`, clot-detection) sensitivity, 0-4095, on the Span-8's
  LLS/CD board (the device that also answers Get Tip Status): what the Master Controller sends
  for `SS`."""
  if not 0 <= value <= 4095:
    raise ValueError(f"sensitivity {value} is outside 0..4095")
  return Request(Device.TIP_SENSOR, 1, 0x41, struct.pack("<BH", int(clot_detection), value))


def get_io_service_device_count() -> Request:
  return Request(Device.IO_SERVICE, 0, 0x08)


def get_input_output_name(device: int, index: int) -> Request:
  return Request(device, index, 0x01)


def read_input_output(device: int, index: int) -> Request:
  return Request(device, index, 0x10)


def notify_on_change(device: int, index: int, notify: bool = True) -> Request:
  return Request(device, index, 0x11, bytes([int(notify)]))


def get_analog_input_units(index: int) -> Request:
  return Request(Device.ANALOG_INPUT, index, 0x12)


# ERROR is what the Master Controller shows after a failed move (2026-07-31, `2D 01 00 seq 10 05`).
StatusDisplayState = Literal["ready", "running", "paused", "error"]
STATUS_DISPLAY_STATES = {"ready": 0x02, "running": 0x03, "paused": 0x04, "error": 0x05}


def set_status_display(state: StatusDisplayState, display: int = 1) -> Request:
  return Request(Device.STATUS_DISPLAY, display, 0x10, bytes([STATUS_DISPLAY_STATES[state]]))


def get_accuframe_offsets(accuframe: int = 1) -> Request:
  return Request(Device.ACCUFRAME, accuframe, 0xF7)


def list_legacy_alp_devices() -> Request:
  return Request(Device.LEGACY_ALP, 0, 0x18)


def send_digital_io_box(params: bytes, box: int = 1) -> Request:
  """Pass a command to the digital I/O box. Its bytes are the box's own and are not decoded here."""
  return Request(Device.IO_BOX, box, 0x10, params)


# -- replies -----------------------------------------------------------------

# Get Module ID's two bytes, as the Master Controller's log decodes them ("Chassis", "Left
# Multichannel", "Right Span-8"). Only these three were seen, on one instrument.
MODULE_TYPES = {0x80: "chassis", 0xFD: "multichannel", 0xFB: "span8"}
MODULE_SIDES = {0x00: None, 0x01: "left", 0x02: "right"}

# Hardware version type codes, as the Master Controller names them.
HARDWARE_NAMES = {
  0x00: "Motion Board",
  0x01: "Transport Supervisor",
  0x02: "Chassis Supervisor",
  0xE0: "SAGIAN Motion Board",
  0xE1: "SAGIAN LLS Board",
  0xE2: "SAGIAN Syringe Pump Board",
  0xE3: "AccuFrame",
  0xE5: "Device Controller",
}

# Tip sensor codes. 1 is "cannot determine" in the Master Controller's `VT` replies.
TIP_STATES = {0x00: "loaded", 0x01: "unknown", 0x02: "unloaded"}

# What an axis reports when its part of a Move All ends other than plainly. A 1-byte report is
# one of the statuses; a 5-byte report is a type and the s32 position where its sensor fired.
LLS_LIQUID_SENSED = 69
LLS_LIQUID_NOT_SENSED = 70
CD_CLOT_NOT_DETECTED = 71
CD_CLOT_DETECTED = 72
ACCUFRAME = 175
MotionOutcome = Literal[
  "done", "liquid-sensed", "accuframe", "liquid-not-sensed", "clot-not-detected", "clot-detected"
]
MOTION_OUTCOMES: Dict[int, MotionOutcome] = {
  LLS_LIQUID_SENSED: "liquid-sensed",
  LLS_LIQUID_NOT_SENSED: "liquid-not-sensed",
  CD_CLOT_NOT_DETECTED: "clot-not-detected",
  CD_CLOT_DETECTED: "clot-detected",
  ACCUFRAME: "accuframe",
}


class AxisState(enum.IntEnum):
  """A motor's or pump's state byte, as Get State answers it.

  An axis starts NOT_CONFIGURED at power-up, and goes back to it when Configure starts or a SAGIAN
  drive reports it lost its parameters. Configure ends DISABLED; Set State moves between DISABLED
  and ENABLED; Home and moves need ENABLED. FAULT follows a drive error.
  """

  ENABLED = 0x00
  DISABLED = 0x01
  NOT_CONFIGURED = 0xFE
  FAULT = 0xFF


AxisStateName = Literal["enabled", "disabled", "not_configured", "fault"]
"""An `AxisState` as the public API gives it (`Pod.request_axis_states`): its name, lower case."""


@dataclass(frozen=True)
class ModuleId:
  """What Get Module ID answers: the kind of module and, for an arm's, its side."""

  type_code: int
  side_code: int

  @property
  def type(self) -> Optional[str]:
    """`chassis`, `multichannel` or `span8`; None for a code not seen yet."""
    return MODULE_TYPES.get(self.type_code)

  @property
  def side(self) -> Optional[str]:
    """`left` or `right` for an arm's module, None for the chassis."""
    return MODULE_SIDES.get(self.side_code)


@dataclass(frozen=True)
class HardwareVersion:
  type_code: int
  revision: int

  @property
  def name(self) -> str:
    return HARDWARE_NAMES.get(self.type_code, f"hardware 0x{self.type_code:02X}")

  def __str__(self) -> str:
    return f"{self.name} {self.revision}"


@dataclass(frozen=True)
class Emcy:
  """A CANopen emergency message a drive sent through its board."""

  node: int
  error_code: int
  error_register: int
  device_error: int
  source: int


def parse_module_id(payload: bytes) -> ModuleId:
  return ModuleId(payload[0], payload[1])


def parse_string(payload: bytes) -> str:
  return payload.decode("ascii")


def parse_hardware_version(payload: bytes) -> HardwareVersion:
  return HardwareVersion(payload[0], payload[1])


def parse_device_count(payload: bytes) -> Dict[int, int]:
  """Each kind of device a module or service holds, by device byte, and how many."""
  if len(payload) % 3:
    raise ValueError(f"device count is (device, u16 count) triples, got {payload.hex()}")
  return {
    payload[i]: int.from_bytes(payload[i + 1 : i + 3], "little") for i in range(0, len(payload), 3)
  }


def parse_u32(payload: bytes) -> int:
  return int(struct.unpack_from("<I", payload)[0])


def parse_s32(payload: bytes) -> int:
  return int(struct.unpack_from("<i", payload)[0])


def parse_float(payload: bytes) -> float:
  return float(struct.unpack_from("<f", payload)[0])


def parse_accuframe_offsets(payload: bytes) -> Tuple[float, float, float]:
  x, y, z = struct.unpack_from("<fff", payload)
  return (x, y, z)


# The interlock's state byte. ARMED is what Re-Arm answers when it arms (`00`); TRIPPED is what the
# unsolicited trip event carries (`01 01 00 00 10 03 00 FF`). Get Interlock Status answers the same
# byte (seen 2026-11-09: `00` armed, `FF` tripped).
INTERLOCK_STATES = {0x00: "armed", 0xFF: "tripped"}
InterlockState = Literal["armed", "tripped"]


def parse_interlock_status(payload: bytes) -> InterlockState:
  """Raises `ValueError` for a byte not seen."""
  state = INTERLOCK_STATES.get(payload[0])
  if state is None:
    raise ValueError(f"interlock state 0x{payload[0]:02X} has not been seen")
  return state  # type: ignore[return-value]


def parse_axis_state(payload: bytes) -> AxisState:
  """Raises `ValueError` for a state byte the firmware does not set."""
  return AxisState(payload[0])


def parse_tip_status(payload: bytes) -> List[Optional[str]]:
  """Each channel's tip, `loaded` or `unloaded`; None for a code not seen yet."""
  return [TIP_STATES.get(b) for b in payload]


@dataclass(frozen=True)
class MotionComplete:
  """How one axis's part of a Move All ended: `done`, or a sensor outcome; `position` is where a
  sensor stopped it (liquid sensed, AccuFrame), else None."""

  device: int
  index: int
  outcome: MotionOutcome
  position: Optional[int] = None


def parse_motion_complete(reply: Reply) -> MotionComplete:
  """An axis's report inside a Move All."""
  payload = reply.payload
  if len(payload) == 5:
    (position,) = struct.unpack_from("<i", payload, 1)
    return MotionComplete(
      reply.device, reply.index, MOTION_OUTCOMES.get(payload[0], "done"), position
    )
  if len(payload) == 1:
    return MotionComplete(reply.device, reply.index, MOTION_OUTCOMES.get(payload[0], "done"))
  return MotionComplete(reply.device, reply.index, "done")


def parse_emcy(data: bytes) -> Emcy:
  """Decode an EMERGENCY reply's data: node, CANopen error code and register, then the drive's own
  error code and the source it names."""
  node = data[1]
  error_code, error_register, device_error, source = struct.unpack_from("<HBHB", data, 2)
  return Emcy(node, error_code, error_register, device_error, source)
