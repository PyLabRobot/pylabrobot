"""The Mantis driver: the link on the FTDI's interface B, discovery, the device reads, events
and the sequencer.

The features in ``features/`` send the commands that act; the sequencer here plays what they
queue, and the LED bar shows setup and stop. Reads return the device's units; the arm converts
its motors' reads, the pump holds the pressure reads.
"""

import asyncio
import contextlib
import json
import logging
import struct
import time
from typing import Any, AsyncContextManager, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from pylabrobot.io.ftdi import FTDI

from .configuration import (
  FITTED_FIELDS,
  MOTION_CALIBRATION_BLOCKS,
  RECORDING_MANTIS_V3,
  RECORDING_MANTIS_V4,
  UNIT_BLOCKS,
  DeviceConfiguration,
  _fill_declared_configuration,
  compute_generation,
  configuration_to_json,
  read_configuration,
)
from .confirmed_firmware_versions import CONFIRMED_FIRMWARE_VERSIONS
from .errors import (
  CommandRefusedError,
  FrameError,
  InvalidQueueItemError,
  MantisConfigurationError,
  MantisConnectionLostError,
  MantisMotionError,
  MantisNotConnectedError,
  MantisTimeoutError,
)
from .events import (
  EVENT_PAYLOAD_FORMATS,
  MOTION_EVENTS,
  MOTOR_ERROR_EVENT,
  SEQUENCE_PROGRESS_EVENT,
  SEQUENCE_STOPPED_EVENT,
  decode_motor_error,
  decode_sequence_progress,
  describe_event,
)
from .features.arm import Arm
from .features.led_bar import LedBar
from .features.plate_clamp import UNCLAMPING_DONE, PlateClamp
from .features.pump import Pump
from .features.rfid import RFID_BAUD_RATE, RFID_FTDI_INTERFACE, Rfid, _initialize_rfid_module
from .framing import (
  ABSOLUTE_POSITION_SENSOR_BOARD,
  FIRST_EVENT_CODE,
  LC3_BOARD,
  MOTION_BOARD,
  PRESSURE_BOARD,
  build_frame,
  decode_string,
  parse_frame,
  split_frames,
)
from .oem_config import UNKNOWN_ZONE, WASH_STATIONS, ZONES, _load_unit_configuration
from .sequence_files import Waypoint

logger = logging.getLogger(__name__)

MOTORS = (0, 1, 2)
# The arm motors; board 11 reads their absolute encoders. Z has none.
ABSOLUTE_ENCODER_MOTORS = (0, 1)
# How often an absolute encoder is read for a valid position; each motor's first after connecting
# is not.
ABSOLUTE_ENCODER_READS = 5
# The sequencer's queue holds 200 items: a v4 not started took 200 before answering -1, and every
# progress event's queued and free counts add to it.
SEQUENCER_QUEUE_SIZE = 200
# Queue items are numbered from 1 to 0x7FFF, the largest int16, then from 1 again.
LAST_QUEUE_ITEM = 0x7FFF
# The PPI address every queued write seen has gone to: the valve bank.
QUEUED_PPI_ADDRESSES = (40,)


class MantisDriver:
  """Talks to a Mantis: one command in flight, events logged as they come."""

  # Where `default_queue_full_timeout` starts.
  _default_queue_full_timeout: float = 60.0

  def _build_io(self, *, device_id: Optional[str], ftdi_library: Optional[str]) -> FTDI:
    """Build the link to the Mantis ``device_id`` names, or to the first one found."""
    return FTDI(
      human_readable_device_name="Formulatrix Mantis",
      device_id=device_id,
      vid=0x0403,
      pid=0x6010,
      interface_select=2,  # interface B carries the command link
      ftdi_library=ftdi_library,
    )

  def _build_rfid_io(
    self, *, device_id: Optional[str], ftdi_library: Optional[str]
  ) -> Optional[FTDI]:
    """Build the link to the RFID reader, interface A of the same FTDI bridge."""
    return FTDI(
      human_readable_device_name="Formulatrix Mantis RFID reader",
      device_id=device_id,
      vid=0x0403,
      pid=0x6010,
      interface_select=RFID_FTDI_INTERFACE,
      ftdi_library=ftdi_library,
    )

  def __init__(
    self,
    *,
    device_id: Optional[str] = None,
    io: Optional[FTDI] = None,
    rfid_io: Optional[FTDI] = None,
    name: str = "Mantis",
    declared_configuration_json: Optional[str] = None,
    oem_config_directory: Optional[str] = None,
    ftdi_library: Optional[str] = None,
    show_actions: bool = True,
  ):
    """
    Args:
      device_id: the FTDI serial number. None uses the first Mantis found.
      io: an already-built ``FTDI`` for interface B, used instead of building one.
      rfid_io: an already-built ``FTDI`` for interface A, the RFID reader. None builds one for
        ``device_id``, unless ``io`` is given: then there is no RFID reader.
      name: the device name, heading its log messages.
      declared_configuration_json: a file ``save_configuration`` wrote. Setup refuses a device
        that disagrees with its ``device`` block on what is fitted (``FITTED_FIELDS``).
      oem_config_directory: the unit's vendor ``Data/Device/Configs`` folder, read at every setup.
      ftdi_library: "libftdi" or "d2xx", for both links; None picks D2XX on Windows when FTDI's
        D2XX library loads, else libftdi. Ignored for a link given as ``io`` or ``rfid_io``.
      show_actions: whether the v4's LED bar shows each action's colour, and the idle animation
        between actions; False shows the idle animation from setup on.

    The unit's own values (``UNIT_BLOCKS``) come from ``oem_config_directory`` and the declared
    file; where both have a block, they must agree. A unit block or non-fitted device field the
    declared file lacks comes from the folder, else the generation's recording (another unit's
    values, less accurate on this one), with one warning naming each and where it came from; a
    motion calibration (``MOTION_CALIBRATION_BLOCKS``) never comes from a recording.
    """
    self.io: FTDI = (
      io if io is not None else self._build_io(device_id=device_id, ftdi_library=ftdi_library)
    )
    self.name = name
    self.declared_configuration_json = declared_configuration_json
    self.oem_config_directory = oem_config_directory
    self._unit_blocks: Dict[str, Dict[str, Any]] = {}
    self._unit_blocks_from_recording: Set[str] = set()
    self.configuration = DeviceConfiguration()
    self._default_read_timeout = 5.0  # PyLabRobot design choice; replies take under 0.1 s
    self._lock = asyncio.Lock()
    self._packet_number = 0
    self._buffer = bytearray()
    self._pending: Optional[Tuple[int, int, "asyncio.Future[Tuple[int, bytes]]"]] = None
    # Each waiter: board, code, the match its payload must pass (None: any) and its future.
    self._event_waiters: List[
      Tuple[int, int, Optional[Callable[[bytes], bool]], "asyncio.Future[bytes]"]
    ] = []
    self._next_item = 1  # the next queue item, per opening of the link
    self._last_drained_item: Optional[int] = None  # the last item played with none left queued
    self._reader: Optional["asyncio.Task[None]"] = None
    self.rfid_io: Optional[FTDI] = (
      rfid_io
      if rfid_io is not None or io is not None
      else self._build_rfid_io(device_id=device_id, ftdi_library=ftdi_library)
    )
    self.plate_clamp: Optional[PlateClamp] = None
    self.pump: Optional[Pump] = None
    self.arm: Optional[Arm] = None
    self.rfid: Optional[Rfid] = None
    self.led_bar: Optional[LedBar] = None
    self._show_actions = show_actions
    # The latest sequencer item the device reported played; the sequencer sets it.
    self._sequence_progress: Optional[int] = None
    self._rfid_link_open = False
    self._connected = False

  @property
  def default_read_timeout(self) -> float:
    """Seconds one attempt of a command waits for its reply when ``timeout`` is None."""
    return self._default_read_timeout

  @default_read_timeout.setter
  def default_read_timeout(self, value: float) -> None:
    if value <= 0:
      raise ValueError(f"default_read_timeout must be positive, got {value}")
    self._default_read_timeout = value

  @property
  def default_queue_full_timeout(self) -> float:
    """Seconds a queue write is sent again under a new item while the sequencer's queue is full."""
    return self._default_queue_full_timeout

  @default_queue_full_timeout.setter
  def default_queue_full_timeout(self, value: float) -> None:
    if value <= 0:
      raise ValueError(f"default_queue_full_timeout must be positive, got {value}")
    self._default_queue_full_timeout = value

  # -- link -----------------------------------------------------------------

  async def _set_up_line(self) -> None:
    """Reset the bridge and set 115 200 baud, 8N1, RTS/CTS, RTS on; purge both buffers."""
    await self.io.usb_reset()
    await self.io.set_baudrate(baudrate=115_200)
    await self.io.set_line_property(bits=8, stopbits=0, parity=0)
    await self.io.set_flowctrl(flowctrl=0x100)  # RTS/CTS
    await self.io.set_rts(level=True)
    await self.io.set_latency_timer(latency=2)  # PyLabRobot design choice, in ms
    await self.io.usb_purge_rx_buffer()
    await self.io.usb_purge_tx_buffer()

  def _handle_event(self, *, board: int, code: int, payload: bytes) -> None:
    """Log an event by its content and hand it to every waiter for its board and code whose match
    accepts it. A motor error fails every waiter for a motion event (``MOTION_EVENTS``) at once.

    A motor error, a sequencer stop and a code not decoded log a warning; the rest log at debug.

    Raises:
      FrameError: if a known event's payload does not match its layout; no waiter sees it.
    """
    description = describe_event(code=code, payload=payload)
    failure: Optional[Tuple[str, int, int]] = None
    if code == MOTOR_ERROR_EVENT:
      logger.warning("[%s] board %d: %s", self.name, board, description)
      motor, error = decode_motor_error(payload=payload)
      failure = (f"{self.name}: {description}", motor, error)
    elif code == SEQUENCE_PROGRESS_EVENT:
      logger.debug("[%s] board %d: %s", self.name, board, description)
      item, queued, _ = decode_sequence_progress(payload=payload)
      if board == MOTION_BOARD:
        self._sequence_progress = item  # the LED bar's dispense progress follows it
        if queued == 0:
          self._last_drained_item = item
    elif code in EVENT_PAYLOAD_FORMATS and code != SEQUENCE_STOPPED_EVENT:
      logger.debug("[%s] board %d: %s", self.name, board, description)
    else:
      logger.warning("[%s] board %d: %s", self.name, board, description)
    waiting, self._event_waiters = self._event_waiters, []
    for waiter in waiting:
      waiter_board, waiter_code, match, future = waiter
      if future.done():
        continue
      if waiter_board != board:
        self._event_waiters.append(waiter)
      elif failure is not None and waiter_code in MOTION_EVENTS:
        message, motor, error = failure
        future.set_exception(MantisMotionError(message, motor=motor, code=error))
      elif waiter_code != code:
        self._event_waiters.append(waiter)
      else:
        try:
          accepted = match is None or match(payload)
        except Exception as match_error:
          future.set_exception(match_error)
          continue
        if accepted:
          future.set_result(payload)
        else:
          self._event_waiters.append(waiter)

  def _handle_frame(self, *, frame: bytes) -> None:
    """Route one device frame: an event by its board and code, a reply to the command it echoes."""
    packet_number, board, code, payload = parse_frame(frame=frame)
    # The code is an int16: from 0x0200 an event, negative (0x8000 up) a reply's device error.
    if FIRST_EVENT_CODE <= code < 0x8000:
      self._handle_event(board=board, code=code, payload=payload)
      return
    if self._pending is not None:
      pending_packet_number, pending_board, future = self._pending
      if (pending_packet_number, pending_board) == (packet_number, board) and not future.done():
        future.set_result((code, payload))
        return
    logger.debug("[%s] unmatched reply ignored: %s", self.name, frame.hex())

  async def _read_frames(self) -> None:
    """Read the link until cancelled, routing every frame; runs from setup to stop.

    A failed read ends it: the failure is logged and handed to the command in flight and to every
    event waiter.
    """
    while True:
      try:
        data = await self.io.read(num_bytes=1024)
      except Exception as error:
        logger.error("[%s] reading the link failed: %r", self.name, error)
        lost = MantisConnectionLostError(f"{self.name}: reading the link failed: {error!r}")
        lost.__cause__ = error
        if self._pending is not None:
          _, _, future = self._pending
          if not future.done():
            future.set_exception(lost)
        waiting, self._event_waiters = self._event_waiters, []
        for _, _, _, waiter in waiting:
          if not waiter.done():
            waiter.set_exception(lost)
        raise
      if not data:
        await asyncio.sleep(0.01)  # PyLabRobot design choice: poll every 10 ms
        continue
      self._buffer.extend(data)
      for frame in split_frames(buffer=self._buffer):
        try:
          self._handle_frame(frame=frame)
        except FrameError as error:
          logger.warning("[%s] frame dropped: %s", self.name, error)

  async def _write_frame(self, *, frame: bytes) -> None:
    """Write one frame to the link.

    Raises:
      MantisNotConnectedError: if the driver is not set up.
      MantisConnectionLostError: if reading the link has failed.
    """
    if self._reader is None:
      raise MantisNotConnectedError(f"{self.name} is not set up; call setup() first")
    if self._reader.done():
      failure = None if self._reader.cancelled() else self._reader.exception()
      raise MantisConnectionLostError(
        f"{self.name}: reading the link has failed; call setup() again"
      ) from failure
    await self.io.write(frame)

  async def _send_command(
    self,
    *,
    board: int,
    command: int,
    payload: bytes = b"",
    timeout: Optional[float] = None,
    attempts: int = 1,
  ) -> bytes:
    """Send one command and return its reply's payload; one command is in flight at a time.

    A command is sent once unless ``attempts`` says otherwise; a read passes ``attempts=3`` and is
    resent with the same packet number, as the device answers a resend like the first send.

    Args:
      board: the board the command goes to.
      command: the command number.
      payload: the command's arguments.
      timeout: seconds per attempt; None uses ``default_read_timeout``.
      attempts: how often the command is sent before giving up.

    Raises:
      MantisNotConnectedError: if the driver is not set up, or it stopped.
      MantisConnectionLostError: if reading the link has failed.
      MantisTimeoutError: if no attempt got a reply.
      CommandRefusedError: if the reply's status is not 0.
    """
    timeout = self.default_read_timeout if timeout is None else timeout
    async with self._lock:
      packet_number = self._packet_number
      self._packet_number = (self._packet_number + 1) & 0xFFFF
      frame = build_frame(
        packet_number=packet_number, board=board, command=command, payload=payload
      )
      future: "asyncio.Future[Tuple[int, bytes]]" = asyncio.get_running_loop().create_future()
      self._pending = (packet_number, board, future)
      try:
        for attempt in range(attempts):
          if attempt > 0:
            logger.warning(
              "[%s] no reply to board %d command %d; resending", self.name, board, command
            )
          await self._write_frame(frame=frame)
          try:
            code, reply = await asyncio.wait_for(asyncio.shield(future), timeout)
            break
          except asyncio.TimeoutError:
            continue
        else:
          raise MantisTimeoutError(
            f"{self.name}: no reply to board {board} command {command} after {attempts} "
            f"attempt(s) of {timeout} s"
          )
      finally:
        self._pending = None
    if code != 0:
      status = code - 0x10000 if code >= 0x8000 else code
      message = f"{self.name}: board {board} refused command {command} with status {status}"
      if status < 0:
        try:
          message += f": {decode_string(payload=reply)}"
        except (FrameError, UnicodeDecodeError):
          message += f" ({reply.hex()})"
      raise CommandRefusedError(message, board=board, command=command, status=status)
    return reply

  def _expect_event(
    self, *, board: int, code: int, match: Optional[Callable[[bytes], bool]] = None
  ) -> "asyncio.Future[bytes]":
    """Register a waiter for the next event with ``board`` and ``code`` that ``match`` accepts,
    before the command that causes it is sent; pass it to ``_wait_for_event``.

    Args:
      board: the board the event comes from.
      code: the event's code.
      match: takes the event's payload and returns whether it is the one awaited; None takes any.
    """
    future: "asyncio.Future[bytes]" = asyncio.get_running_loop().create_future()
    self._event_waiters.append((board, code, match, future))
    return future

  async def _wait_for_event(self, *, waiter: "asyncio.Future[bytes]", timeout: float) -> bytes:
    """Wait for the event ``waiter`` was registered for and return its payload.

    Raises:
      MantisTimeoutError: if the event does not come within ``timeout`` seconds.
      MantisMotionError: at once, if a motor reports an error while it waits for a motion event.
      MantisNotConnectedError: if the driver stops first.
      MantisConnectionLostError: if reading the link fails first.
    """
    try:
      return await asyncio.wait_for(waiter, timeout)
    except asyncio.TimeoutError as error:
      raise MantisTimeoutError(f"{self.name}: no event within {timeout} s") from error
    finally:
      self._event_waiters = [entry for entry in self._event_waiters if entry[3] is not waiter]

  # -- device reads ---------------------------------------------------------

  def _check_index(self, *, name: str, value: int, allowed: Tuple[int, ...]) -> None:
    """Raise ``ValueError`` if ``value`` is not one of ``allowed``, before anything is sent."""
    if value not in allowed:
      raise ValueError(f"{name} must be one of {allowed}, got {value}")

  async def request_firmware_version(self, *, board: int = MOTION_BOARD) -> str:
    """The firmware version of a board.

    Args:
      board: 0 (motion), 10 (pressure), 11 (absolute position sensors) or 12 (LC3).
    """
    self._check_index(
      name="board",
      value=board,
      allowed=(MOTION_BOARD, PRESSURE_BOARD, ABSOLUTE_POSITION_SENSOR_BOARD, LC3_BOARD),
    )
    return decode_string(payload=await self._send_command(board=board, command=1, attempts=3))

  async def request_name(self) -> str:
    """The name the motion firmware reports, ``LiquidHandler`` on the v4 we have run.

    Whether it differs between units is not known; it does not identify this Mantis.
    """
    return decode_string(
      payload=await self._send_command(board=MOTION_BOARD, command=2, attempts=3)
    )

  async def request_motor_limits(self, *, motor: int) -> Tuple[float, float]:
    """A motor's (lower, upper) limit.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=10, payload=struct.pack("<h", motor), attempts=3
    )
    lower, upper = struct.unpack_from("<dd", reply)
    return lower, upper

  async def request_motor_currents(self, *, motor: int) -> Tuple[float, float, float]:
    """A motor's three currents, in the order the device sends them.

    1.5, 1.0 and 0.6 on the v4's arms.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=12, payload=struct.pack("<h", motor), attempts=3
    )
    first, second, third = struct.unpack_from("<ddd", reply)
    return first, second, third

  async def request_motor_configuration(
    self, *, motor: int
  ) -> Tuple[int, float, float, float, int, int]:
    """A motor's configuration, its six fields as the device sends them.

    A flag (1 on motor 1, 0 on motors 0 and 2 of the v4), three values (2000.0, 200.0, 0.0 on its
    arms), the microsteps (100) and a last word (1 on every motor).

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=14, payload=struct.pack("<h", motor), attempts=3
    )
    flag, first, second, third, microsteps, last = struct.unpack_from("<HdddHH", reply)
    return flag, first, second, third, microsteps, last

  async def request_motor_status(self, *, motor: int) -> int:
    """A motor's status word; ``MOTOR_STATUS_ERROR_BITS`` names the bits that report a fault.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=20, payload=struct.pack("<h", motor), attempts=3
    )
    (status,) = struct.unpack_from("<H", reply)
    return int(status)

  async def request_motor_position(self, *, motor: int) -> float:
    """A motor's demand position, in the device's units: where the controller drives it.

    It is not measured. It reads back the last position declared to the motor until the motor
    moves, wherever the motor sits; an arm moved by hand still reads it.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=27, payload=struct.pack("<h", motor), attempts=3
    )
    (position,) = struct.unpack_from("<d", reply)
    return float(position)

  async def request_following_error_configuration(self, *, motor: int) -> Tuple[bool, float]:
    """A motor's following-error check: (enabled, the largest following error), device units.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=82, payload=struct.pack("<h", motor), attempts=3
    )
    enabled, maximum = struct.unpack_from("<Hd", reply)
    return bool(enabled), float(maximum)

  async def request_encoder_position(self, *, motor: int) -> float:
    """A motor's servo encoder position, in the device's units.

    It counts only while the device is powered: after power-on it reads about 0 wherever the motor
    sits. ``request_absolute_encoder_position`` reads where an arm is.

    Args:
      motor: 0, 1 (arm) or 2 (Z).
    """
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    reply = await self._send_command(
      board=MOTION_BOARD, command=84, payload=struct.pack("<h", motor), attempts=3
    )
    (position,) = struct.unpack_from("<d", reply)
    return float(position)

  async def request_absolute_encoder_position(self, *, motor: int) -> float:
    """An arm motor's raw absolute encoder reading, from board 11; kept across power-off.

    Not an angle yet: the unit's encoder correction and absolute homing position make it one. A
    read the device marks not valid, as each motor's first after connecting is, is read again.

    Args:
      motor: 0 or 1 (arm).

    Raises:
      MantisTimeoutError: if none of ``ABSOLUTE_ENCODER_READS`` reads is valid.
    """
    self._check_index(name="motor", value=motor, allowed=ABSOLUTE_ENCODER_MOTORS)
    for _ in range(ABSOLUTE_ENCODER_READS):
      reply = await self._send_command(
        board=ABSOLUTE_POSITION_SENSOR_BOARD,
        command=30,
        payload=struct.pack("<h", motor),
        attempts=3,
      )
      valid, position = struct.unpack_from("<Hd", reply)
      if valid:
        return float(position)
      logger.info(
        "[%s] motor %d's absolute encoder read not valid; reading again", self.name, motor
      )
    raise MantisTimeoutError(
      f"{self.name}: motor {motor}'s absolute encoder read no valid position in "
      f"{ABSOLUTE_ENCODER_READS} reads"
    )

  # -- sequencer ------------------------------------------------------------

  def _decode_sequencer_reply(self, *, command: int, reply: bytes) -> int:
    """The int16 a sequencer command answers.

    Raises:
      FrameError: if the reply is shorter than 2 bytes.
    """
    if len(reply) < 2:
      raise FrameError(f"{self.name}: command {command}'s reply is 2 bytes, got {reply.hex()}")
    (word,) = struct.unpack_from("<h", reply)
    return int(word)

  async def _unchecked_fw_clear_sequencer(self) -> int:
    """Clear the sequencer's queue (board 0, command 61), sent once; return the reply's word.

    Only a clear drops queued items. A running sequencer keeps running: stop it first to halt it.
    As seen on a v4, the word is 2 when sent while running and 0 when idle; 8 and 9, also seen,
    are not explained.
    """
    reply = await self._send_command(board=MOTION_BOARD, command=61, attempts=1)
    return self._decode_sequencer_reply(command=61, reply=reply)

  async def _unchecked_fw_start_sequencer(self) -> int:
    """Start the sequencer (board 0, command 62), sent once; return the reply's word, 0 as seen."""
    reply = await self._send_command(board=MOTION_BOARD, command=62, attempts=1)
    return self._decode_sequencer_reply(command=62, reply=reply)

  async def _unchecked_fw_stop_sequencer(self) -> int:
    """Stop the sequencer (board 0, command 63), sent once; return the reply's word.

    As seen on a v4: a running sequencer plays what is queued, then reports it stopped
    (``SEQUENCE_STOPPED_EVENT``), and the word is 2. An idle one keeps its queue, reports nothing
    and answers 0.
    """
    reply = await self._send_command(board=MOTION_BOARD, command=63, attempts=1)
    return self._decode_sequencer_reply(command=63, reply=reply)

  async def _unchecked_fw_abort_sequencer(self) -> int:
    """Abort the sequencer (board 0, command 64), sent once; return the reply's word.

    As seen on a v4: a running sequencer finishes the item playing, reports it stopped
    (``SEQUENCE_STOPPED_EVENT``) and keeps the items still queued, which play at the next start;
    the word is 2. An idle one keeps its queue, reports nothing and answers 0.
    """
    reply = await self._send_command(board=MOTION_BOARD, command=64, attempts=1)
    return self._decode_sequencer_reply(command=64, reply=reply)

  async def _unchecked_fw_queue_write_ppi(
    self, *, item: int, duration: int, start_address: int, values: List[int]
  ) -> Tuple[int, int]:
    """Queue a PPI write on the sequencer (board 0, command 65), sent once.

    Never resent: a v4 queued a frame written twice under one packet number as two items.
    Returns the reply's free slots, -1 when the queue was full and the item not queued, and its
    error word.

    Args:
      item: the item's number, 1 to ``LAST_QUEUE_ITEM``.
      duration: how long the sequencer holds the write, in its time steps of 1 ms.
      start_address: the first PPI address written.
      values: the values written from there.

    Raises:
      FrameError: if the reply is shorter than 4 bytes.
    """
    payload = struct.pack("<hhhh", item, duration, start_address, len(values)) + b"".join(
      struct.pack("<H", value) for value in values
    )
    reply = await self._send_command(board=MOTION_BOARD, command=65, payload=payload, attempts=1)
    if len(reply) < 4:
      raise FrameError(f"{self.name}: a queue write's reply is 4 bytes, got {reply.hex()}")
    free, error = struct.unpack_from("<hh", reply)
    return int(free), int(error)

  def _get_next_item(self) -> int:
    """Number the next queue item: 1 to ``LAST_QUEUE_ITEM``, then 1 again."""
    item = self._next_item
    self._next_item = item % LAST_QUEUE_ITEM + 1
    return item

  async def _unchecked_fw_queue_move_item(
    self, *, item: int, relative: bool, wait: bool, targets: List[Tuple[float, float, float]]
  ) -> Tuple[int, int]:
    """Queue a move of all three motors on the sequencer (board 0, command 68), sent once.

    Returns the reply's free slots, -1 when the queue was full and the item not queued, and its
    error word.

    Args:
      item: the item's number, 1 to ``LAST_QUEUE_ITEM``.
      relative: whether the positions are from where the motors are.
      wait: whether the sequencer waits for the move to end before the next item.
      targets: each motor's (position, speed, acceleration), in device units; a motor sent all
        zeros does not move.

    Raises:
      FrameError: if the reply is shorter than 4 bytes.
    """
    payload = struct.pack("<hHH", item, relative, wait) + b"".join(
      struct.pack("<ddd", *target) for target in targets
    )
    reply = await self._send_command(board=MOTION_BOARD, command=68, payload=payload, attempts=1)
    if len(reply) < 4:
      raise FrameError(f"{self.name}: a queued move's reply is 4 bytes, got {reply.hex()}")
    free, error = struct.unpack_from("<hh", reply)
    return int(free), int(error)

  async def _queue_item(
    self, *, command: int, queue: Callable[[int], Awaitable[Tuple[int, int]]]
  ) -> int:
    """Queue one item under the next number and return it.

    An item the device answers -1 free slots to was not queued: it is sent again under the next
    item until one is, for at most ``default_queue_full_timeout``. No item is sent twice: a v4
    queues a write resent with its packet number as a second item, so a lost reply raises.

    Args:
      command: the queue command, for an error message.
      queue: sends the item under the number it is given; returns its free slots and error word.

    Raises:
      InvalidQueueItemError: if a reply's error word is not 0.
      MantisTimeoutError: if the queue stays full for ``default_queue_full_timeout``.
    """
    deadline = time.monotonic() + self.default_queue_full_timeout
    while True:
      item = self._get_next_item()
      free, error = await queue(item)
      if error != 0:
        raise InvalidQueueItemError(
          f"{self.name}: queue item {item} answered error {error}", command=command, error=error
        )
      if free >= 0:
        return item
      if time.monotonic() >= deadline:
        raise MantisTimeoutError(
          f"{self.name}: the sequencer's queue stayed full for {self.default_queue_full_timeout} s"
        )
      logger.debug("[%s] queue full, item %d not queued; sending it again", self.name, item)

  async def _queue_ppi_write(self, *, duration: int, start_address: int, values: List[int]) -> int:
    """Queue a PPI write on the sequencer and return its item, as ``_queue_item``.

    Args:
      duration: how long the sequencer holds the write, in its time steps of 1 ms, 1 to 0x7FFF.
      start_address: the first PPI address written, one of ``QUEUED_PPI_ADDRESSES``.
      values: the values written from there, each 0 to 0xFFFF; at least one.

    Raises:
      ValueError: if an argument is out of range; nothing is sent.
      InvalidQueueItemError: if a reply's error word is not 0.
      MantisTimeoutError: if the queue stays full for ``default_queue_full_timeout``.
    """
    self._check_index(name="start_address", value=start_address, allowed=QUEUED_PPI_ADDRESSES)
    if not 1 <= duration <= 0x7FFF:
      raise ValueError(f"duration must be 1 to 0x7FFF, an int16, got {duration}")
    if len(values) == 0 or any(not 0 <= value <= 0xFFFF for value in values):
      raise ValueError(f"values must be one or more uint16, got {values}")
    return await self._queue_item(
      command=65,
      queue=lambda item: self._unchecked_fw_queue_write_ppi(
        item=item, duration=duration, start_address=start_address, values=values
      ),
    )

  async def _queue_move_item(self, *, targets: List[Tuple[float, float, float]]) -> int:
    """Queue a move of all three motors, waited for, to absolute targets; as ``_queue_item``.

    Args:
      targets: each motor's (position, speed, acceleration), in device units, motors 0 to 2.

    Raises:
      ValueError: unless there is one target per motor; nothing is sent.
      InvalidQueueItemError: if a reply's error word is not 0.
      MantisTimeoutError: if the queue stays full for ``default_queue_full_timeout``.
    """
    if len(targets) != len(MOTORS):
      raise ValueError(f"a queued move has one target per motor {MOTORS}, got {targets}")
    return await self._queue_item(
      command=68,
      queue=lambda item: self._unchecked_fw_queue_move_item(
        item=item, relative=False, wait=True, targets=targets
      ),
    )

  async def _wait_for_queue_item(self, *, item: int, timeout: float) -> None:
    """Wait until the sequencer reports ``item`` played with no item left queued.

    Returns at once if it was reported already: a short item's report can come with the reply
    that queued it, before the wait begins.

    Args:
      item: the last item queued.
      timeout: seconds to wait.

    The simulation answers at once: it returns or raises ``MantisTimeoutError``.

    Raises:
      MantisMotionError: at once, if a motor reports an error first.
      MantisTimeoutError: if it is not reported within ``timeout``.
    """
    if self._last_drained_item == item:
      return
    waiter = self._expect_event(
      board=MOTION_BOARD,
      code=SEQUENCE_PROGRESS_EVENT,
      match=lambda payload: decode_sequence_progress(payload=payload)[:2] == (item, 0),
    )
    try:
      await self._wait_for_event(waiter=waiter, timeout=timeout)
    except MantisTimeoutError as error:
      raise MantisTimeoutError(
        f"{self.name}: queue item {item} not reported played within {timeout} s"
      ) from error

  # -- discovery and setup --------------------------------------------------

  async def _request_configuration(self) -> DeviceConfiguration:
    """Read the firmware versions and the motor limits off the device.

    Only the motion board must answer. Each other board gets one attempt; one that times out,
    refuses or answers a malformed version is absent, not a fault.
    """
    firmware_versions: Dict[int, Optional[str]] = {
      MOTION_BOARD: await self.request_firmware_version(board=MOTION_BOARD)
    }
    for board in (PRESSURE_BOARD, ABSOLUTE_POSITION_SENSOR_BOARD, LC3_BOARD):
      try:
        reply = await self._send_command(board=board, command=1, attempts=1)
        firmware_versions[board] = decode_string(payload=reply)
      except (TimeoutError, CommandRefusedError, FrameError, UnicodeDecodeError) as error:
        logger.info("[%s] board %d taken as absent: %r", self.name, board, error)
        firmware_versions[board] = None
    return DeviceConfiguration(
      generation=compute_generation(firmware_versions=firmware_versions),
      firmware_versions=firmware_versions,
      motor_limits={motor: await self.request_motor_limits(motor=motor) for motor in MOTORS},
    )

  def _get_arm_calibration(self) -> Dict[str, Any]:
    """The ``arm`` block as the arm takes it, by argument; each None without the block.

    Raises:
      MantisConfigurationError: if the block is malformed, or a correction table does not have
        two rows or more, measured ascending.
    """
    block = self._unit_blocks.get("arm")
    if block is None:
      return {"offsets": None, "homing_position_a": None, "encoder_correction": None}
    try:
      calibration: Dict[str, Any] = {
        field: {int(motor): float(value) for motor, value in block[field].items()}
        for field in ("offsets", "homing_position_a")
      }
      calibration["encoder_correction"] = {
        int(motor): [(float(ideal), float(measured)) for ideal, measured in rows]
        for motor, rows in block["encoder_correction"].items()
      }
    except (KeyError, TypeError, ValueError, AttributeError) as error:
      raise MantisConfigurationError(f"malformed arm block: {error!r}") from error
    for field, values in calibration.items():
      if set(values) != set(ABSOLUTE_ENCODER_MOTORS):
        raise MantisConfigurationError(
          f"arm block: {field} is for motors {ABSOLUTE_ENCODER_MOTORS}"
        )
    for motor, rows in calibration["encoder_correction"].items():
      if len(rows) < 2 or any(later[1] <= earlier[1] for earlier, later in zip(rows, rows[1:])):
        raise MantisConfigurationError(
          f"arm block: motor {motor}'s correction needs two rows or more, measured ascending"
        )
    return calibration

  def _get_route_blocks(self) -> Dict[str, Any]:
    """The ``zones`` and ``stations`` blocks as the arm takes them, by argument; each None without
    its block.

    Raises:
      MantisConfigurationError: if a block is malformed: a zone missing, a rectangle not four
        numbers, a waypoint not three numbers or None, or a route to safe XY that moves Z.
    """

    def to_route(lines: Any) -> List[Waypoint]:
      route = []
      for line in lines:
        x, y, z = (None if value is None else float(value) for value in line)
        route.append((x, y, z))
      return route

    routes: Dict[str, Any] = {
      "zone_rectangles": None,
      "safe_xy_routes": None,
      "idle_routes": None,
      "waste_station_route": None,
      "wash_station_routes": None,
    }
    zones = self._unit_blocks.get("zones")
    stations = self._unit_blocks.get("stations")
    try:
      if zones is not None:
        rectangles = {}
        for zone in ZONES:
          minimum_x, maximum_x, minimum_y, maximum_y = (float(v) for v in zones["rectangles"][zone])
          rectangles[zone] = (minimum_x, maximum_x, minimum_y, maximum_y)
        routes["zone_rectangles"] = rectangles
        for field in ("safe_xy_routes", "idle_routes"):
          routes[field] = {zone: to_route(zones[field][zone]) for zone in (*ZONES, UNKNOWN_ZONE)}
      if stations is not None:
        if "waste" in stations:
          routes["waste_station_route"] = to_route(stations["waste"])
        routes["wash_station_routes"] = {
          int(station): to_route(lines) for station, lines in stations.get("wash", {}).items()
        }
    except (KeyError, TypeError, ValueError, AttributeError) as error:
      raise MantisConfigurationError(f"malformed zones or stations block: {error!r}") from error
    if routes["safe_xy_routes"] is not None and any(
      x is None or z is not None for route in routes["safe_xy_routes"].values() for x, _, z in route
    ):
      raise MantisConfigurationError("zones block: a route to safe XY moves the arms only")
    if routes["wash_station_routes"] is not None and not set(routes["wash_station_routes"]) <= set(
      WASH_STATIONS
    ):
      raise MantisConfigurationError(f"stations block: the wash stations are {WASH_STATIONS}")
    return routes

  def _discover_features(self) -> None:
    """Build the features the configuration says are fitted, with the unit's values.

    A feature already built is kept.

    Raises:
      MantisConfigurationError: if the ``plate_clamp``, ``led_bar``, ``arm``, ``zones`` or
        ``stations`` block is malformed.
    """
    if self.configuration.firmware_versions.get(LC3_BOARD) is not None and self.plate_clamp is None:
      levels: Optional[Dict[int, Tuple[float, int]]] = None
      block = self._unit_blocks.get("plate_clamp")
      if block is not None:
        try:
          levels = {
            int(level): (float(pwm), int(timeout_ms))
            for level, (pwm, timeout_ms) in block["levels"].items()
          }
        except (KeyError, TypeError, ValueError) as error:
          raise MantisConfigurationError(f"malformed plate_clamp block: {error!r}") from error
      self.plate_clamp = PlateClamp(driver=self, levels=levels)
    if self.configuration.firmware_versions.get(PRESSURE_BOARD) is not None and self.pump is None:
      self.pump = Pump(driver=self)
    # The LED bar is the v4's, on board 12: a v3 has none.
    if (
      self.configuration.generation == "v4"
      and self.configuration.firmware_versions.get(LC3_BOARD) is not None
      and self.led_bar is None
    ):
      led_bar_block = self._unit_blocks.get("led_bar")
      if led_bar_block is None:
        raise MantisConfigurationError(f"{self.name}: no led_bar block for this v4")
      try:
        number_of_leds = int(led_bar_block["number_of_leds"])
      except (KeyError, TypeError, ValueError) as error:
        raise MantisConfigurationError(f"malformed led_bar block: {error!r}") from error
      if number_of_leds < 1:
        raise MantisConfigurationError(f"malformed led_bar block: {number_of_leds} LEDs")
      self.led_bar = LedBar(
        driver=self, number_of_leds=number_of_leds, show_actions=self._show_actions
      )
    sensor_board = self.configuration.firmware_versions.get(ABSOLUTE_POSITION_SENSOR_BOARD)
    if sensor_board is not None and self.arm is None:
      self.arm = Arm(driver=self, **self._get_arm_calibration(), **self._get_route_blocks())

  def _show_action(
    self, *, action: str, show_inner_actions: bool = True, only_when_idle: bool = False
  ) -> AsyncContextManager[None]:
    """Show ``action``'s colour on the LED bar while the block runs; nothing without a bar or with
    its ``show_actions`` off.

    Args:
      action: a key ``LedBar._show`` takes, other than "idle".
      show_inner_actions: whether actions inside the block change the bar.
      only_when_idle: change the bar only when no other action runs.
    """
    if self.led_bar is None or not self.led_bar.show_actions:
      return contextlib.nullcontext()
    return self.led_bar._show_action(
      action=action, show_inner_actions=show_inner_actions, only_when_idle=only_when_idle
    )

  def _check_declared_configuration(self) -> None:
    """Compare what is fitted with the declared configuration's ``device`` block, if there is one.

    Raises:
      MantisConfigurationError: if the device and the declaration disagree on what is fitted.
    """
    if self.declared_configuration_json is None:
      return
    declared = read_configuration(path=self.declared_configuration_json).get("device")
    if declared is None:
      return
    answered = configuration_to_json(configuration=self.configuration)
    differences = [
      f"{field}: declared {declared.get(field)!r}, device {answered[field]!r}"
      for field in FITTED_FIELDS
      if declared.get(field) != answered[field]
    ]
    # A board the declaration lists with a version must answer; versions are not compared.
    differences += [
      f"board {board}: declared {version!r}, device did not answer"
      for board, version in declared.get("firmware_versions", {}).items()
      if version is not None and answered["firmware_versions"].get(board) is None
    ]
    if differences:
      raise MantisConfigurationError(
        f"{self.name} does not match {self.declared_configuration_json}: " + "; ".join(differences)
      )

  def _load_unit_values(
    self, *, generation: Optional[str]
  ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """The declared file, or without one the folder's blocks, with each value it lacks taken from
    the folder, else the generation's recording (never a motion calibration); and where each taken
    one came from, by key.

    Args:
      generation: picks the recording.

    Raises:
      MantisConfigurationError: if the folder and the declared file disagree on a block.
    """
    folder = (
      _load_unit_configuration(oem_config_directory=self.oem_config_directory)
      if self.oem_config_directory is not None
      else {}
    )
    recording_path = RECORDING_MANTIS_V4 if generation == "v4" else RECORDING_MANTIS_V3
    recording = {
      block: value
      for block, value in read_configuration(path=recording_path).items()
      if block not in MOTION_CALIBRATION_BLOCKS
    }
    if self.declared_configuration_json is None:
      return _fill_declared_configuration(declared=folder, sources=[(recording_path, recording)])
    declared = read_configuration(path=self.declared_configuration_json)
    differences = [
      f"{block}: declared {declared[block]!r}, folder {folder[block]!r}"
      for block in UNIT_BLOCKS
      if block in folder
      and block in declared
      and json.loads(json.dumps(folder[block])) != json.loads(json.dumps(declared[block]))
    ]
    if differences:
      raise MantisConfigurationError(
        f"{self.oem_config_directory} and {self.declared_configuration_json} disagree: "
        + "; ".join(differences)
      )
    sources = [(recording_path, recording)]
    if self.oem_config_directory is not None:
      sources.insert(0, (self.oem_config_directory, folder))
    return _fill_declared_configuration(declared=declared, sources=sources)

  async def discover(self) -> None:
    """Read the configuration into ``configuration``, check it against the declared one, then
    build the features it lists, read the pump's sensor calibration into ``configuration``, and
    build the RFID reader if it answers on interface A.

    The reader is set up again after every opening of the link; one that fails is absent. A
    calibration the pressure board does not answer is left empty.

    Raises:
      MantisConfigurationError: if the device does not match the declared configuration, the
        unit's folder and declared file disagree on a block, or a block is malformed.
    """
    self.configuration = await self._request_configuration()
    self._check_declared_configuration()
    values, taken = self._load_unit_values(generation=self.configuration.generation)
    recordings = (RECORDING_MANTIS_V3, RECORDING_MANTIS_V4)
    if taken:
      given = self.declared_configuration_json or self.oem_config_directory
      logger.warning(
        "[%s] %s: %s%s",
        self.name,
        f"not in {given}" if given is not None else "none given",
        "; ".join(f"{key} from {where}" for key, where in taken.items()),
        ", another unit's values, less accurate on this unit"
        if any(where in recordings for where in taken.values())
        else "",
      )
    self._unit_blocks = {block: values[block] for block in UNIT_BLOCKS if block in values}
    self._unit_blocks_from_recording = {
      block for block in UNIT_BLOCKS if taken.get(block) in recordings
    }
    self._discover_features()
    if self.pump is not None:
      # Nothing uses the calibration yet: a board 10 that does not answer it does not fail setup.
      try:
        self.configuration.feedback_sensor_parameters = await self.pump._request_calibration()
      except (TimeoutError, CommandRefusedError, FrameError, struct.error) as error:
        logger.warning("[%s] the pressure sensors' calibration not read: %r", self.name, error)
    if self.rfid is None and self.rfid_io is not None and self._rfid_link_open:
      try:
        await _initialize_rfid_module(io=self.rfid_io)
      except Exception as error:
        logger.info("[%s] no RFID reader: %r", self.name, error)
        return
      slot_to_antenna: Optional[Dict[int, int]] = None
      multiplexer_delay_raw = 0.0  # unused while slot_to_antenna is None: every read refuses
      rfid_block = self._unit_blocks.get("rfid")
      if rfid_block is not None:
        try:
          slot_to_antenna = {
            int(slot): int(antenna) for slot, antenna in rfid_block["slot_to_antenna"].items()
          }
          multiplexer_delay_raw = float(rfid_block["multiplexer_delay_raw"])
        except (KeyError, TypeError, ValueError) as error:
          raise MantisConfigurationError(f"malformed rfid block: {error!r}") from error
      self.rfid = Rfid(
        driver=self, slot_to_antenna=slot_to_antenna, multiplexer_delay_raw=multiplexer_delay_raw
      )

  def _check_firmware_compatibility(self) -> None:
    """Log a warning if the boards' firmware versions are not confirmed for the generation."""
    generation = self.configuration.generation
    confirmed = CONFIRMED_FIRMWARE_VERSIONS.get(generation or "", ())
    if self.configuration.firmware_versions not in confirmed:
      logger.warning(
        "[%s] firmware %s on a Mantis %s is not confirmed; please report how it works",
        self.name,
        self.configuration.firmware_versions,
        generation,
      )

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this device reported to a file, to be declared later
    (``declared_configuration_json``), with the unit's blocks; none taken from a recording. The
    sensor calibration is not written: it is read at every setup.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      MantisNotConnectedError: if nothing has been read off the device yet.
    """
    if self.configuration.generation is None:
      raise MantisNotConnectedError("nothing read off the device yet; call `setup` first")
    saved: Dict[str, Any] = {"device": configuration_to_json(configuration=self.configuration)}
    for block, values in self._unit_blocks.items():
      if block not in self._unit_blocks_from_recording:
        saved[block] = values
    with open(path, "w", encoding="utf-8") as f:
      json.dump(saved, f, indent=indent)
      f.write("\n")

  async def _open(self) -> None:
    """Open the link, set up the line and start reading it; then open the RFID reader's link."""
    await self.io.setup()
    await self._set_up_line()
    self._buffer.clear()
    self._next_item, self._last_drained_item, self._sequence_progress = 1, None, None
    self._reader = asyncio.create_task(self._read_frames())
    logger.info("[%s] connected to FTDI %s", self.name, self.io.device_id)
    if self.rfid_io is not None:
      # The reader is optional: a link that does not open leaves it absent, not setup failed.
      try:
        await self.rfid_io.setup()
        await self.rfid_io.set_baudrate(baudrate=RFID_BAUD_RATE)
        await self.rfid_io.set_line_property(bits=8, stopbits=0, parity=0)  # 8N1
        self._rfid_link_open = True
      except Exception:
        logger.warning("[%s] the RFID reader's link did not open", self.name, exc_info=True)

  async def _close(self) -> None:
    """Stop reading the link and close it, then close the RFID reader's link."""
    reader, self._reader = self._reader, None
    if reader is not None and reader.done():
      if not reader.cancelled():
        reader.exception()  # a failed read was logged when it failed
    elif reader is not None:
      reader.cancel()
      try:
        await reader
      except asyncio.CancelledError:
        pass
      except Exception:
        logger.warning("[%s] the link reader had failed", self.name, exc_info=True)
    self.rfid = None  # the reader is set up again when the link reopens
    rfid_link_open, self._rfid_link_open = self._rfid_link_open, False
    try:
      await self.io.stop()
    finally:
      if self.rfid_io is not None and rfid_link_open:
        try:
          await self.rfid_io.stop()
        except Exception:
          logger.warning("[%s] the RFID reader's link did not close", self.name, exc_info=True)

  async def _halt(self) -> None:
    """Abort the sequencer, then stop the motors and park an arm brought up (``Arm._halt``).

    The arm halts whether or not the abort succeeded; a failed abort is logged.
    """
    if self.arm is None:
      return
    try:
      await self._unchecked_fw_abort_sequencer()
    except Exception:
      logger.warning("[%s] could not abort the sequencer", self.name, exc_info=True)
    await self.arm._halt()

  async def stop(self) -> None:
    """Fail the command in flight and every event waiter; on a v4, abort the sequencer, stop the
    motors and park an arm brought up; release the plate clamp, turn the LED bar off, stop reading
    the link and close it.

    Each step runs whether or not the one before it succeeded. After reading the link failed,
    nothing is sent: the link is closed.
    """
    if self._reader is not None and self._reader.done() and self._connected:
      failure = None if self._reader.cancelled() else self._reader.exception()
      logger.warning("[%s] the link was lost (%r); closing it, sending nothing", self.name, failure)
      self._connected = False
    stopped = f"{self.name} stopped"
    if self._pending is not None:
      _, _, future = self._pending
      if not future.done():
        future.set_exception(MantisNotConnectedError(stopped))
    waiting, self._event_waiters = self._event_waiters, []
    for _, _, _, waiter in waiting:
      if not waiter.done():
        waiter.set_exception(MantisNotConnectedError(stopped))
    if self._connected:
      await self._halt()
    if self.plate_clamp is not None and self._connected:
      try:
        if await self.plate_clamp.request_state(attempts=1) != UNCLAMPING_DONE:
          await self.plate_clamp.unlock()
      except Exception:
        logger.warning("[%s] could not release the plate clamp", self.name, exc_info=True)
    if self.led_bar is not None and self._connected:
      try:
        await self.led_bar.turn_off()
      except Exception:
        logger.warning("[%s] could not turn the LED bar off", self.name, exc_info=True)
    self._connected = False
    await self._close()

  async def setup(self, *, skip_arm: bool = False) -> None:
    """Open the link if it is not open, read the device's configuration and check it, then bring
    the arm and Z up on a v4 (``Arm.initialize``). This moves the device.

    On a v4 the LED bar shows the setup, the arm's bring-up with it, then "setup_done" for
    ``SETUP_DONE_HOLD``, then idle.

    Args:
      skip_arm: do not bring the arm up; nothing moves.

    Raises:
      MantisConfigurationError: if the device does not match the declared configuration, or the arm
        is brought up without the unit's ``arm`` block.
      MantisMotionError: if the arm is not where it is moved straight to idle from, or faults.
    """
    if self._reader is not None and self._reader.done():
      await self.stop()  # reading the link failed: close it before opening it again
    try:
      if not self._connected:
        await self._open()
        self._connected = True
      await self.discover()
      if self.led_bar is not None and not self.led_bar.show_actions:
        await self.led_bar._show(key="idle")  # the idle animation from setup on
      async with self._show_action(action="setup", show_inner_actions=False):
        if self.arm is not None and not skip_arm:
          await self.arm.initialize()
        if self.led_bar is not None and self.led_bar.show_actions:
          await self.led_bar._show_setup_done()
    except BaseException:
      try:
        await self.stop()
      except Exception:
        logger.warning("[%s] stop after a failed setup failed", self.name, exc_info=True)
      raise
    self._check_firmware_compatibility()
    logger.info(
      "[%s] generation %s, firmware %s",
      self.name,
      self.configuration.generation,
      self.configuration.firmware_versions,
    )
