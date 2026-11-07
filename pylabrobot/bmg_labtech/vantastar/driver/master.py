"""VANTAstar driver: the USB connection and the command layer.

The VANTAstar talks native USB only (VID 0x0483 / PID 0xA29A): one interface with bulk OUT 0x01 and
bulk IN 0x81, both 64-byte packets.  Logical payloads travel in packets framed by ``framing.py``; a
payload is the command id (outgoing) or the response id (incoming) followed by its fields.  Replies
are parsed in ``responses.py``.

The commands are sent with the fields and in the order BMG's Voyager software uses for the reader
(firmware 1.12).
"""

import asyncio
import contextvars
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.protocol.framing import (
  USB_ACK,
  USB_CONTINUATION,
  USB_FIRST,
  USB_HEADER_SIZE,
  USB_PACKET_SIZE,
  USB_SINGLE,
  usb_packet_type,
  usb_wrap_payload,
)
from pylabrobot.io.usb import USB

from .configuration import (
  FITTED_FIELDS,
  HARDWARE_INFO_ID,
  VANTAstarConfiguration,
  configuration_to_json,
  parse_hardware_info,
  read_configuration,
)
from .errors import (
  BUSY_CODES,
  WARNING_CODES,
  Codes,
  CommandRefusedError,
  DeviceError,
  describe_codes,
)
from .features.absorbance import Absorbance
from .features.drawer import Drawer
from .features.fluorescence import Fluorescence
from .features.luminescence import Luminescence
from .features.measurement import (
  DEFAULT_APERTURE_DISTANCE,
  STANDARD_96,
  Corner,
  MonochromatorSettings,
  PlateGeometry,
  ReadingDirection,
  WellScan,
  _focal_height_bytes,
  _hundredth_mm,
  _tenth_nm,
  encode_monochromator,
  encode_plate_geometry,
  encode_reading_mode,
  encode_well_mask,
  encode_well_scan,
)
from .features.shaker import Shaker
from .features.temperature_controller import TemperatureController
from .framing import DEVICE_ID, HOST_ACKNOWLEDGEMENT, get_packet_value, is_acceptance
from .responses import (
  CONFIRMATION_ID,
  TIME_DATA_ID,
  GainFocus,
  MeasurementData,
  MwBlockStatus,
  ZProfileData,
  parse_confirmation,
  parse_gain_data,
  parse_measurement_data,
  parse_mw_block_data,
  parse_run_time,
  parse_well_list,
  parse_z_profile,
  split_reply,
)

logger = logging.getLogger(__name__)


def _configure_usb_device(dev: Any) -> None:
  """Detach a kernel driver from interface 0 where the OS has one, and set the configuration."""
  try:
    if dev.is_kernel_driver_active(0):
      dev.detach_kernel_driver(0)
  except NotImplementedError:
    pass
  dev.set_configuration()


# ---------------------------------------------------------------------------
# Reply status
# ---------------------------------------------------------------------------

# Bits of the status header's first byte (its low nibble is the header length, see responses.py).
_REFUSED_BIT = 1 << 4
_BUSY_BIT = 1 << 5
_ERROR_BIT = 1 << 6
_WARNING_BIT = 1 << 7

# Status flags as (name, index of the flag byte, bit).
_STATUS_FLAGS = [
  ("new_data_ready", 0, 0),  # NewMeasDataReady
  ("meas_data_ready", 0, 1),  # MeasDataReady
  ("time_data_ready", 0, 2),  # TimeDataReady
  ("blank_data_ready", 0, 3),  # BlankDataReady
  ("absorb_data_ready", 0, 4),  # AbsorbDataReady
  ("gain_data_ready", 0, 5),  # GainDataReady
  ("offset_data_ready", 0, 6),  # OffsetDataReady
  ("drawer_open", 1, 0),  # MicroplateCarrierOut
  ("plate_detected", 1, 1),  # MicroplateInserted
  ("plate_sensor_valid", 1, 2),  # MicroplateSensorFlagValid
  ("curr_state_meas_data_ready", 1, 3),  # CurrStateMeasDataRdy: a data page can be requested
  ("motors_enabled", 1, 5),  # MotorsEnabled
  ("reader_cover_open", 1, 6),  # ReaderCoverOpen
  ("device_stopped", 2, 0),  # DeviceStopped
  ("check_filter_data_ready", 2, 7),  # CheckFilterDataReady
]


@dataclass(frozen=True)
class VANTAstarStatus:
  """The status flags of a reply (see _STATUS_FLAGS) and what the reader is doing.

  ``busy_command`` names the command running when the reader is busy (the busy codes are command
  ids, see ``BUSY_CODES``).  ``temperatures`` are the three incubator sensor readings in degrees C
  (the first is the one Voyager displays); both come only with a ConfirmationResponse.
  """

  busy: bool
  plate_detected: bool
  drawer_open: bool
  motors_enabled: bool
  new_data_ready: bool = False
  meas_data_ready: bool = False
  absorb_data_ready: bool = False
  curr_state_meas_data_ready: bool = False
  reader_cover_open: bool = False
  device_stopped: bool = False
  busy_command: Optional[str] = None
  temperatures: Optional[Tuple[float, float, float]] = None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class _Estimated(Exception):
  """Ends a read under ``VANTAstarDriver.estimate_duration`` at its RUN, with the estimate."""

  def __init__(self, seconds: int):
    super().__init__(seconds)
    self.seconds = seconds


class CommandId:
  """Command ids: the first byte of every outgoing payload."""

  INITIALIZE = 0x01
  PLATE = 0x03  # drawer and plate moves
  RUN = 0x04  # measurement
  VALUES = 0x05  # data request
  INCUBATOR = 0x06  # temperature control
  STOP = 0x0B
  GAIN_PLATE = 0x0C  # gain and focus adjustment
  IDLE_MOVE = 0x27  # shaking
  MW_BLOCK = 0x2D  # measurement data block status
  STATUS = 0x80


class ValuesType:
  """ValuesCommand byte 1: which data the reader sends."""

  MEASUREMENT_DATA = 0x02
  TIME_DATA = 0x03
  GAIN_DATA = 0x05
  LAYOUT_WELL_LIST = 0x1D


# INIT flag bytes: group 2 (motor and hardware disables, bit 7 = MoveToLockPos), group 10, group 15
# (bit 4 = ReadMpSensor).  Voyager connects with ReadMpSensor set and nothing disabled.
_INIT_READ_MICROPLATE_SENSOR = 0x10

# ValuesCommand bodies for a read's data, as Voyager requests them: the current data block while
# the read runs (ValuesType 2 with its three headers at 0xFFFF, MeasurementDataCurrentDataBlock),
# and the next new data block once the reader announces new data (headers 0,
# MeasurementDataNextNewDataBlock); the reader refuses the first then (quit code 100).
_CURRENT_DATA_BLOCK = bytes([ValuesType.MEASUREMENT_DATA]) + b"\xff" * 6
_NEXT_NEW_DATA_BLOCK = bytes([ValuesType.MEASUREMENT_DATA]) + bytes(6)
# A ValuesCommand for one well's part of a block (MeasurementDataSpecificDataBlock) sets this bit
# of its sub-block number to leave out the 0%/100% calibration values.
_WITHOUT_CALIBRATION = 0x8000
# RUN fields: the reading-mode byte and its CalcTimeOnly bit, set for a time estimate without a read.
_READING_MODE = 66
_CALC_TIME_ONLY = 0x01
# Set while ``VANTAstarDriver.estimate_duration`` runs its read, in that task only.
_estimating: contextvars.ContextVar[bool] = contextvars.ContextVar(
  "vantastar_estimating", default=False
)
# MwBlockCommand GetStatusOfMeasDataBlock with a send block size of 65535.
_MW_BLOCK_STATUS = bytes([CommandId.MW_BLOCK, 0x00, 0x00, 0x00, 0xFF, 0xFF])
# MwBlockCommand ReleaseMeasDataBlock, of kinetic cycle 1, as Voyager sends it after the last well of
# an absorbance spectrum.
_RELEASE_BLOCK = bytes([0x01, 0x00, 0x01, 0x00, 0x00])

# IncubatorCommand channels: all three.
_ALL_INCUBATOR_CHANNELS = 0x07


# Gain and focus adjustment settings Voyager sends: a focus scan from 16 mm down in 0.2 mm steps
# (1 mm stop, unused: the reader stops at a lowest height it chooses), a smoothed and interpolated
# focal curve, integration time 12.  Without the focus it leaves the scan start at the field's
# default, 15 mm.
_FOCUS_SCAN_START = 1600
_FOCUS_SCAN_START_UNUSED = 1500
_FOCUS_SCAN_STEP = 0.2  # mm
_FOCUS_SCAN_STOP = 100
_FOCAL_CURVE_FLAGS = 0x03
_GAIN_FOCUS_INTEGRATION_TIME = 12
# GainPlateCommand flag bytes: UseFocusAdjust; AutoSetFocalHeight (the well scan mode in bits 0-2).
_USE_FOCUS_ADJUST = 0x20
_AUTO_SET_FOCAL_HEIGHT = 0x40

# Monochromator positions per chromatic, for a monochromator without fixed filters.
_MONO_POSITIONS = 7
# RUN chromatic configuration of a fluorescence scan: ChromaticScan1 and ChromaticsType; the scan
# sends the monochromator settings of its first and its last point.
_FLUORESCENCE_SCAN = 0x82

# RUN option byte after the mode byte: UseAutoGain, Voyager's enhanced dynamic range (fluorescence)
# and automatic gain (luminescence); no gain is sent with it.
_USE_AUTO_GAIN = 0x40
# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class VANTAstarDriver:
  """Owns the VANTAstar USB connection and the command layer."""

  _default_read_timeout: float = 150.0
  default_poll_interval: float = 0.25
  # Seconds between data block polls during a read (Voyager polls about every 0.17 s).
  measurement_poll_interval: float = 0.1
  _USB_PACKET_TIMEOUT: int = 5
  _RETRY_SETTLE: float = 0.08
  # The communication protocol version requested in INIT.
  PROTOCOL_VERSION: int = 1

  @property
  def default_read_timeout(self) -> float:
    """Seconds a wait for the reader to be ready may last."""
    return self._default_read_timeout

  @default_read_timeout.setter
  def default_read_timeout(self, value: float) -> None:
    """Set the wait timeout.

    Raises:
      ValueError: if ``value`` is not positive.
    """
    if value <= 0:
      raise ValueError(f"default_read_timeout must be positive, got {value}")
    self._default_read_timeout = value

  def _build_io(self, device_id: Optional[str]) -> USB:
    """The USB link to the reader with serial number ``device_id`` (None: the first found)."""
    return USB(
      id_vendor=0x0483,
      id_product=0xA29A,
      human_readable_device_name="BMG VANTAstar",
      serial_number=device_id,
      read_endpoint_address=0x81,
      write_endpoint_address=0x01,
      configuration_callback=_configure_usb_device,
      # The reader rejects a multi-packet command whose 64-byte packets are followed by zero-length
      # packets; Voyager sends none.
      zero_length_packets=False,
    )

  def __init__(
    self,
    device_id: Optional[str] = None,
    io: Optional[USB] = None,
    name: str = "VANTAstar",
    declared_configuration_json: Optional[str] = None,
  ):
    """
    Args:
      device_id: the USB serial number.  None uses the first reader found.
      io: an already-built ``USB``, used instead of building one.
      name: the device name, heading its log messages.
      declared_configuration_json: a file ``save_configuration`` wrote.  Setup refuses a reader
        that disagrees with it on what is fitted (``FITTED_FIELDS``).
    """
    if io is not None and not isinstance(io, USB):
      raise TypeError(f"io must be a USB, got {type(io).__name__}")
    self.io: USB = io if io is not None else self._build_io(device_id)
    self.name = name
    self.declared_configuration_json = declared_configuration_json
    self._lock = asyncio.Lock()
    self._measuring = False
    self._last_warning: Optional[Codes] = None
    self.configuration = VANTAstarConfiguration()
    self._hardware_info_reply: Optional[bytes] = None
    self.drawer = Drawer(self)
    self.absorbance = Absorbance(self)
    self.fluorescence = Fluorescence(self)
    self.luminescence = Luminescence(self)
    self.shaker = Shaker(self)
    self.temperature_controller = TemperatureController(self)

  # -- USB setup / teardown -------------------------------------------------

  async def _usb_setup(self) -> None:
    """Open the USB device and send the two vendor requests it needs before bulk traffic.

    1. Control OUT 0x22/0x8B data ``01``: enable the bulk protocol.
    2. Control OUT 0x22/0x88 data 8 bytes: four zero bytes, then the Unix time (u32 BE).
    """
    await self.io.setup()
    self.io.ctrl_transfer(0x22, 0x8B, 0, 0, b"\x01")
    now = int(time.time()).to_bytes(4, "big")
    self.io.ctrl_transfer(0x22, 0x88, 0, 0, b"\x00\x00\x00\x00" + now)

  async def _usb_close(self) -> None:
    """Disable the bulk protocol and close the USB device."""
    try:
      self.io.ctrl_transfer(0x22, 0x8B, 0, 0, b"\x00")
    except Exception:
      logger.warning("could not disable the bulk protocol before closing", exc_info=True)
    await self.io.stop()

  # -- packet exchange ------------------------------------------------------

  async def _read_packet(self) -> bytes:
    """Read one packet from the reader.

    Raises:
      FrameError: if no packet arrives in time.
    """
    try:
      return await self.io.read(timeout=self._USB_PACKET_TIMEOUT, size=USB_PACKET_SIZE)
    except TimeoutError as e:
      raise FrameError(f"No USB packet within {self._USB_PACKET_TIMEOUT} s") from e

  async def _exchange(self, payload: bytes) -> bytes:
    """Send one payload and return the reply payload, reassembled from its packets.

    A multi-packet command waits for the reader's acknowledgement before sending its
    continuation packets.  A multi-packet reply is acknowledged by the host before the
    reader sends its continuations.
    """
    packets = usb_wrap_payload(payload, header=DEVICE_ID)
    await self.io.write(packets[0])
    if len(packets) > 1:
      ack = await self._read_packet()
      if usb_packet_type(ack, header=DEVICE_ID) != USB_ACK:
        raise FrameError(f"expected an acknowledgement, got {ack.hex()}")
      if not is_acceptance(ack):
        raise FrameError(f"reader refused the continuation packets: {ack.hex()}")
      for packet in packets[1:]:
        await self.io.write(packet)

    first = await self._read_packet()
    ptype = usb_packet_type(first, header=DEVICE_ID)
    if ptype == USB_SINGLE:
      length = get_packet_value(first)
      return bytes(first[USB_HEADER_SIZE : USB_HEADER_SIZE + length])
    if ptype != USB_FIRST:
      raise FrameError(f"unexpected packet type 0x{ptype:02x}: {first.hex()}")

    total = get_packet_value(first)
    reply = bytearray(first[USB_HEADER_SIZE:])
    await self.io.write(HOST_ACKNOWLEDGEMENT)
    sequence = 0
    while len(reply) < total:
      packet = await self._read_packet()
      if usb_packet_type(packet, header=DEVICE_ID) != USB_CONTINUATION:
        raise FrameError(f"expected a continuation packet, got {packet.hex()}")
      sequence += 1
      if get_packet_value(packet) != sequence:
        raise FrameError(f"expected continuation packet {sequence}, got {packet.hex()}")
      reply.extend(packet[USB_HEADER_SIZE:])
    return bytes(reply[:total])

  async def exchange(self, payload: bytes) -> bytes:
    """Send one logical payload and return the reply payload, under the exchange lock."""
    async with self._lock:
      return await self._exchange(payload)

  async def _exchange_with_retries(self, payload: bytes, retries: int) -> bytes:
    """Exchange ``payload``, draining and trying again on a missing or malformed reply.

    Raises:
      ValueError: if ``retries`` is below 1.
      FrameError: if every attempt fails.
    """
    if retries < 1:
      raise ValueError(f"retries must be at least 1, got {retries}")
    for attempt in range(1, retries + 1):
      try:
        return await self.exchange(payload)
      except FrameError as e:
        logger.warning(
          "reply to %s failed (attempt %d/%d): %s",
          payload.hex(),
          attempt,
          retries,
          e,
        )
        if attempt == retries:
          raise
        await self.io.drain()
        await asyncio.sleep(self._RETRY_SETTLE)
    raise AssertionError("unreachable")

  # -- reply checking -------------------------------------------------------

  def _check_reply(self, payload: bytes, reply: bytes) -> None:
    """Raise if the reader refused the command or reports a device error; log a warning once
    per change of warning codes.

    Raises:
      CommandRefusedError: if the reply carries the refused bit.
      DeviceError: if the reply carries the device-error bit.
      FrameError: if the reply is shorter than its status header.
    """
    try:
      first, _, _ = split_reply(reply)
    except ValueError as e:
      raise FrameError(str(e)) from e
    if not first & (_REFUSED_BIT | _ERROR_BIT | _WARNING_BIT):
      self._last_warning = None
      return
    codes: Codes = (
      parse_confirmation(reply).codes if reply[0] == CONFIRMATION_ID else (0, 0, 0, 0, 0)
    )
    if first & _REFUSED_BIT:
      raise CommandRefusedError(payload, codes)
    if first & _ERROR_BIT:
      raise DeviceError(payload, codes)
    if codes != self._last_warning:
      logger.warning("device warning: %s", describe_codes(codes, WARNING_CODES))
    self._last_warning = codes

  # -- status ---------------------------------------------------------------

  @staticmethod
  def _parse_status(reply: bytes) -> VANTAstarStatus:
    """Parse a reply's status flags; with a ConfirmationResponse also its busy command and
    incubator temperatures.

    Raises:
      FrameError: if the reply is shorter than its status header.
    """
    try:
      first, status_bytes, _ = split_reply(reply)
    except ValueError as e:
      raise FrameError(str(e)) from e
    busy = bool(first & _BUSY_BIT)

    def flag(byte_index: int, bit: int) -> bool:
      """Whether the flag at ``bit`` of flag byte ``byte_index`` is set."""
      return byte_index < len(status_bytes) and bool(status_bytes[byte_index] & (1 << bit))

    flags = {name: flag(bi, bit) for name, bi, bit in _STATUS_FLAGS}
    busy_command: Optional[str] = None
    temperatures = None
    if reply[0] == CONFIRMATION_ID:
      confirmation = parse_confirmation(reply)
      temperatures = confirmation.temperatures
      if busy:
        code = confirmation.codes[0]
        busy_command = BUSY_CODES.get(code, f"unknown ({code})")
    return VANTAstarStatus(
      busy=busy,
      plate_detected=flags["plate_detected"],
      drawer_open=flags["drawer_open"],
      motors_enabled=flags["motors_enabled"],
      new_data_ready=flags["new_data_ready"],
      meas_data_ready=flags["meas_data_ready"],
      absorb_data_ready=flags["absorb_data_ready"],
      curr_state_meas_data_ready=flags["curr_state_meas_data_ready"],
      reader_cover_open=flags["reader_cover_open"],
      device_stopped=flags["device_stopped"],
      busy_command=busy_command,
      temperatures=temperatures,
    )

  async def request_status(self, retries: int = 5) -> VANTAstarStatus:
    """Request the reader's status flags.

    A reader not yet initialized refuses STATUS (quit code 201) and still reports its flags; a
    refused reply is parsed as any other.

    Raises:
      DeviceError: if the reply carries the device-error bit.
    """
    payload = bytes([CommandId.STATUS])
    reply = await self._exchange_with_retries(payload, retries)
    if len(reply) > 1 and reply[1] & _ERROR_BIT:
      codes = parse_confirmation(reply).codes if reply[0] == CONFIRMATION_ID else (0, 0, 0, 0, 0)
      raise DeviceError(payload, codes)
    return self._parse_status(reply)

  async def _wait_until_ready(
    self,
    timeout: Optional[float],
    poll_interval: float,
    until: Optional[Callable[[VANTAstarStatus], bool]] = None,
  ) -> VANTAstarStatus:
    """Poll status until the reader is not busy, or until ``until`` holds; return that status.

    Raises:
      TimeoutError: if the condition does not hold after ``timeout`` seconds.
    """
    start = time.time()
    while True:
      status = await self.request_status()
      if until(status) if until is not None else not status.busy:
        return status
      if timeout is not None and time.time() - start >= timeout:
        raise TimeoutError(f"VANTAstar still {status} after {timeout} s")
      if poll_interval:
        await asyncio.sleep(poll_interval)

  # -- command sending ------------------------------------------------------

  async def send_command(
    self,
    command_id: int,
    body: bytes = b"",
    *,
    retries: int = 3,
    when_idle: bool = True,
  ) -> bytes:
    """Send ``[command id] + body`` and return the reply payload.

    Args:
      command_id: the command id, from ``CommandId``.
      body: the command's fields, appended after the id.
      retries: attempts on a missing or malformed reply.
      when_idle: first poll status until the reader is not busy.

    Raises:
      FrameError: if every attempt gets a missing or malformed reply.
      CommandRefusedError: if the reader refused the command.
      DeviceError: if the reader reports a device error.
      RuntimeError: under ``estimate_duration``, for any command but STATUS and a time estimate.
    """
    if _estimating.get() and not (
      command_id == CommandId.STATUS
      or (command_id == CommandId.RUN and body[_READING_MODE] & _CALC_TIME_ONLY)
    ):
      raise RuntimeError(
        f"estimate_duration estimates a read only; the call sent command 0x{command_id:02x}"
      )
    payload = bytes([command_id]) + body
    if when_idle and command_id != CommandId.STATUS:
      await self._wait_until_ready(self.default_read_timeout, self.default_poll_interval)
    reply = await self._exchange_with_retries(payload, retries)
    self._check_reply(payload, reply)
    return reply

  # -- initialize -----------------------------------------------------------

  async def initialize(self) -> None:
    """Initialize the reader (homes the axes, about 10 s) and wait until it is ready.

    Sends STATUS first, then INIT with ReadMpSensor set, as Voyager connects: ``01 00 00 10 01``.
    A reader that is not initialized refuses STATUS (quit code 201); INIT is always allowed.  The
    INIT reply is the HardwareInfoResponse, kept for ``discover``.
    """
    await self.request_status()
    reply = await self.send_command(
      CommandId.INITIALIZE,
      bytes([0x00, 0x00, _INIT_READ_MICROPLATE_SENSOR, self.PROTOCOL_VERSION]),
      retries=1,
      when_idle=False,
    )
    if reply[:1] == bytes([HARDWARE_INFO_ID]):
      self._hardware_info_reply = reply
    else:
      logger.warning("INIT replied with response 0x%02x, not HardwareInfo", reply[0])
    await self._wait_until_ready(self.default_read_timeout, self.default_poll_interval)

  def discover(self) -> VANTAstarConfiguration:
    """Parse the INIT reply (HardwareInfoResponse) into ``configuration`` and return it.

    Raises:
      RuntimeError: if no INIT reply was kept.
    """
    if self._hardware_info_reply is None:
      raise RuntimeError("no HardwareInfoResponse; run initialize() first")
    self.configuration = parse_hardware_info(self._hardware_info_reply)
    c = self.configuration
    logger.info(
      "VANTAstar firmware %s build %s (package %s), serial %s",
      c.firmware_version,
      c.firmware_build,
      c.firmware_package,
      c.serial_number,
    )
    return c

  def _check_declared_configuration(self) -> None:
    """Compare what is fitted with the declared configuration, if there is one.

    Raises:
      RuntimeError: if the reader and the declaration disagree on what is fitted.
    """
    if self.declared_configuration_json is None:
      return
    declared = read_configuration(self.declared_configuration_json)["device"]
    answered = configuration_to_json(self.configuration)
    differences = [
      f"{name}: declared {declared.get(name)!r}, reader {answered[name]!r}"
      for name in FITTED_FIELDS
      if declared.get(name) != answered[name]
    ]
    if differences:
      raise RuntimeError(
        f"{self.name} does not match {self.declared_configuration_json}: " + "; ".join(differences)
      )

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this reader reported to a file, to be declared later
    (``declared_configuration_json``).  The serial number is written as
    ``SERIAL_NUMBER_PLACEHOLDER``.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      RuntimeError: if nothing has been read off the reader yet.
    """
    if self.configuration.firmware_version is None:
      raise RuntimeError("nothing read off the reader yet; call `setup` first")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({"device": configuration_to_json(self.configuration)}, f, indent=indent)
      f.write("\n")

  # -- drawer ---------------------------------------------------------------

  async def _move_plate(self, mode: int) -> None:
    """PlateCommand: the move mode, then X and Y positions (0, unused by these moves)."""
    await self.send_command(CommandId.PLATE, bytes([mode, 0x00, 0x00, 0x00, 0x00]))

  # -- stop -----------------------------------------------------------------

  async def stop_run(self) -> None:
    """Stop a measurement running on the reader (StopCommand, StopMode 0).

    Raises:
      CommandRefusedError: if nothing is running.
    """
    await self.send_command(CommandId.STOP, b"\x00", when_idle=False)

  async def _drain_and_stop(self) -> None:
    """Drain unread reply bytes, send STOP, drop what STOP left."""
    logger.warning("stopping the measurement run")
    try:
      await self.io.drain()
      await self.stop_run()
      await self.io.drain()
    except Exception as e:  # keep the error that ended the run
      logger.warning("STOP not accepted: %s", e)

  # -- RUN payload builders -------------------------------------------------

  @staticmethod
  def _encode_run_start(
    mode_byte: int,
    *,
    options: int,
    geometry: PlateGeometry,
    well_mask: Optional[bytes],
    settling_time: float,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN bytes shared by every measurement mode: plate(17) + layout mode(1) + well mask(48) +
    reading mode (0x02: WellPlateModeKinetic, not CalcTimeOnly) + mode byte (MeasurementMode in
    bits 0-1, BottomMeas bit 6) + option byte (UseAutoGain bit 6) + 1 + TimeStampMatchValue(4) +
    shaking(20, off: Voyager shakes with a separate command) + shaking position 9999/9999 + well
    scanning (``encode_well_scan``) + settling time (20 ms steps); the reading-mode byte carries
    the reading order (``encode_reading_mode``)."""
    mask = well_mask if well_mask is not None else encode_well_mask(geometry.columns, geometry.rows)
    return (
      encode_plate_geometry(geometry)
      + b"\x00"
      + mask
      + bytes(
        [
          0x02 | encode_reading_mode(corner, reading_direction, bidirectional),
          mode_byte,
          options,
          0x00,
        ]
      )
      + bytes(4)
      + bytes(20)
      + bytes.fromhex("270f270f")
      + encode_well_scan(well_scan, scan_diameter)
      + bytes([round(settling_time / 0.02)])
    )

  @staticmethod
  def _encode_run_tail(per_value: int) -> bytes:
    """RUN bytes ending every mode: Pause(4) + IdleMove(6) + one kinetic window (start 0, one value,
    ``per_value``, cycle time 0: one read of the plate) + no volume groups.  ``per_value`` is the
    flashes per well, or luminescence's integration time in 10 ms steps."""
    window = (
      (0).to_bytes(2, "big")
      + (1).to_bytes(2, "big")
      + per_value.to_bytes(2, "big")
      + (0).to_bytes(2, "big")
    )
    return bytes(4) + bytes(6) + b"\x01" + window + b"\x00"

  @staticmethod
  def _encode_absorbance_references() -> bytes:
    """The absorbance reference wavelengths: 900 and 993 nm (Voyager's pathlength-correction
    pair), no third, at 10.0 nm resolution."""
    return (
      (9000).to_bytes(2, "big")
      + (9930).to_bytes(2, "big")
      + (0).to_bytes(2, "big")
      + (100).to_bytes(2, "big")
    )

  def build_absorbance_run(
    self,
    wavelengths: Sequence[float],
    *,
    flashes: int,
    settling_time: float,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN fields for an absorbance endpoint read (MeasurementMode 2).

    Besides the wavelengths the reader reads two reference wavelengths, 900 and 993 nm (Voyager's
    pathlength-correction pair), and a reference channel.

    Args:
      wavelengths: wavelengths in nm (0.1 nm resolution).
      flashes: flashes per well.
      settling_time: seconds to settle before reading each well (20 ms steps).
      geometry: the plate.
      well_mask: the wells to read (``encode_well_mask``); None reads every well.
      well_scan: point, orbital or spiral (``encode_well_scan``); scan_diameter: its diameter, mm.
      corner, reading_direction, bidirectional: the reading order (``encode_reading_mode``).
    """
    optics = (
      bytes([len(wavelengths)])
      + b"".join(_tenth_nm(w) for w in wavelengths)
      + (100).to_bytes(2, "big")  # wavelength resolution, 10.0 nm
      + self._encode_absorbance_references()
    )
    return (
      self._encode_run_start(
        0x42,
        options=0x00,
        geometry=geometry,
        well_mask=well_mask,
        settling_time=settling_time,
        well_scan=well_scan,
        scan_diameter=scan_diameter,
        corner=corner,
        reading_direction=reading_direction,
        bidirectional=bidirectional,
      )
      + optics
      + self._encode_run_tail(flashes)
    )

  def build_absorbance_spectrum_run(
    self,
    start_wavelength: float,
    end_wavelength: float,
    step_size: float,
    *,
    flashes: int,
    settling_time: float,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN fields for an absorbance spectrum (MeasurementMode 2 with no discrete wavelengths): start,
    stop and step in 0.1 nm, then the reference wavelengths of an endpoint read.

    Args:
      start_wavelength: nm.
      end_wavelength: nm.
      step_size: nm between wavelengths.
      flashes: flashes per well.
      settling_time: seconds to settle before reading each well (20 ms steps).
      geometry: the plate.
      well_mask: the wells to read (``encode_well_mask``); None reads every well.
      well_scan: point, orbital or spiral (``encode_well_scan``); scan_diameter: its diameter, mm.
      corner, reading_direction, bidirectional: the reading order (``encode_reading_mode``).
    """
    optics = (
      b"\x00"
      + _tenth_nm(start_wavelength)
      + _tenth_nm(end_wavelength)
      + _tenth_nm(step_size)
      + self._encode_absorbance_references()
    )
    return (
      self._encode_run_start(
        0x42,
        options=0x00,
        geometry=geometry,
        well_mask=well_mask,
        settling_time=settling_time,
        well_scan=well_scan,
        scan_diameter=scan_diameter,
        corner=corner,
        reading_direction=reading_direction,
        bidirectional=bidirectional,
      )
      + optics
      + self._encode_run_tail(flashes)
    )

  def build_fluorescence_run(
    self,
    monochromators: Sequence[MonochromatorSettings],
    *,
    gains: Sequence[int],
    focal_height: float,
    flashes: int,
    settling_time: float,
    enhanced_dynamic_range: bool = False,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN fields for a top fluorescence-intensity endpoint read (MeasurementMode 0).

    Args:
      monochromators: one ``MonochromatorSettings`` per chromatic.
      gains: one gain per chromatic; ignored (sent as 0) with enhanced dynamic range.
      focal_height: mm, in 0.1 mm steps.
      flashes: flashes per well.
      settling_time: seconds to settle before reading each well (20 ms steps).
      enhanced_dynamic_range: read with enhanced dynamic range.
      geometry: the plate.
      well_mask: the wells to read (``encode_well_mask``); None reads every well.
      well_scan: point, orbital or spiral (``encode_well_scan``); scan_diameter: its diameter, mm.
      corner, reading_direction, bidirectional: the reading order (``encode_reading_mode``).
    """
    if len(gains) != len(monochromators):
      raise ValueError(f"{len(monochromators)} chromatics but {len(gains)} gains")
    optics = (
      _focal_height_bytes(focal_height)
      + b"\x00"  # chromatic configuration
      + len(monochromators).to_bytes(2, "big")
      + (0).to_bytes(2, "big")
      + bytes([_MONO_POSITIONS])
    )
    for settings, gain in zip(monochromators, gains):
      optics += (
        bytes(4)  # integration start and time
        + (0 if enhanced_dynamic_range else gain).to_bytes(2, "big")
        + encode_monochromator(settings)
      )
    return (
      self._encode_run_start(
        0x00,
        options=_USE_AUTO_GAIN if enhanced_dynamic_range else 0x00,
        geometry=geometry,
        well_mask=well_mask,
        settling_time=settling_time,
        well_scan=well_scan,
        scan_diameter=scan_diameter,
        corner=corner,
        reading_direction=reading_direction,
        bidirectional=bidirectional,
      )
      + optics
      + self._encode_run_tail(flashes)
    )

  def build_fluorescence_scan_run(
    self,
    first: MonochromatorSettings,
    last: MonochromatorSettings,
    points: int,
    *,
    gain: int,
    focal_height: float,
    flashes: int,
    settling_time: float,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN fields for a top fluorescence scan (MeasurementMode 0, ChromaticScan1): the number of
    points, then the monochromator settings of the first and the last point; the reader steps
    evenly between them.

    Args:
      first: the first point's settings (``compute_scan_monochromators``).
      last: the last point's settings.
      points: points of the scan, the first and the last included.
      gain: the detector gain.
      focal_height: mm, in 0.1 mm steps.
      flashes: flashes per well and point.
      settling_time: seconds to settle before reading each well (20 ms steps).
      geometry: the plate.
      well_mask: the wells to read (``encode_well_mask``); None reads every well.
      well_scan: point, orbital or spiral (``encode_well_scan``); scan_diameter: its diameter, mm.
      corner, reading_direction, bidirectional: the reading order (``encode_reading_mode``).

    Raises:
      ValueError: if ``points`` is not 2-65535.
    """
    if not 2 <= points <= 0xFFFF:
      raise ValueError(f"a scan has 2-65535 points, got {points}")
    optics = (
      _focal_height_bytes(focal_height)
      + bytes([_FLUORESCENCE_SCAN])
      + points.to_bytes(2, "big")
      + (0).to_bytes(2, "big")
      + bytes([_MONO_POSITIONS])
    )
    for settings in (first, last):
      optics += bytes(4) + gain.to_bytes(2, "big") + encode_monochromator(settings)
    return (
      self._encode_run_start(
        0x00,
        options=0x00,
        geometry=geometry,
        well_mask=well_mask,
        settling_time=settling_time,
        well_scan=well_scan,
        scan_diameter=scan_diameter,
        corner=corner,
        reading_direction=reading_direction,
        bidirectional=bidirectional,
      )
      + optics
      + self._encode_run_tail(flashes)
    )

  def build_luminescence_run(
    self,
    monochromators: Sequence[MonochromatorSettings],
    *,
    gains: Optional[Sequence[int]],
    integration_time: float,
    settling_time: float,
    aperture_distance: float = DEFAULT_APERTURE_DISTANCE,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    corner: Corner = "top_left",
    reading_direction: ReadingDirection = "row_major",
    bidirectional: bool = True,
  ) -> bytes:
    """RUN fields for a top luminescence endpoint read (MeasurementMode 1).  Not tested on a reader.

    The focal height field carries the aperture distance, each chromatic a gain and the
    monochromator positions (no integration window), then MeasIntervalTimeNorm 0; the kinetic
    window carries the integration time in 10 ms steps.

    Args:
      monochromators: one ``MonochromatorSettings`` per chromatic.
      gains: one gain per chromatic; None for automatic gain (UseAutoGain, as Voyager's default).
      integration_time: seconds of light collection per well (10 ms steps).
      settling_time: seconds to settle before reading each well (20 ms steps).
      aperture_distance: mm between the top optic's aperture and the plate (0.01 mm steps).
      geometry: the plate.
      well_mask: the wells to read (``encode_well_mask``); None reads every well.
      corner, reading_direction, bidirectional: the reading order (``encode_reading_mode``).
    """
    if gains is not None and len(gains) != len(monochromators):
      raise ValueError(f"{len(monochromators)} chromatics but {len(gains)} gains")
    optics = (
      _hundredth_mm(aperture_distance)
      + b"\x00"  # chromatic configuration
      + len(monochromators).to_bytes(2, "big")
      + (0).to_bytes(2, "big")
      + bytes([_MONO_POSITIONS])
    )
    for i, settings in enumerate(monochromators):
      optics += (0 if gains is None else gains[i]).to_bytes(2, "big") + encode_monochromator(
        settings
      )
    optics += (0).to_bytes(2, "big")  # MeasIntervalTimeNorm
    return (
      self._encode_run_start(
        0x01,
        options=_USE_AUTO_GAIN if gains is None else 0x00,
        geometry=geometry,
        well_mask=well_mask,
        corner=corner,
        reading_direction=reading_direction,
        bidirectional=bidirectional,
        settling_time=settling_time,
      )
      + optics
      + self._encode_run_tail(round(integration_time * 100))
    )

  # -- measurement flow -----------------------------------------------------

  @staticmethod
  def _count_saved_wells(progress: MwBlockStatus, wells: int) -> int:
    """The wells of a single-block read whose values the block status shows saved."""
    if progress.stored >= progress.required:
      return wells
    per_well = (progress.size - progress.calibration_values) // wells
    if per_well <= 0:
      return 0
    return max(0, (progress.saved - progress.calibration_values) // per_well)

  async def estimate_run_time(self, parameters: bytes) -> int:
    """The run time in s the reader expects for a read of the RUN fields ``parameters``, without
    reading: the RUN is sent with CalcTimeOnly set, as Voyager sends it while a method is edited.

    On the tested reader every read finished 2.5-7 s before its estimate (a read sent with the
    drawer out took longer: the reader first moves the plate in).  Each read logs its estimate at
    INFO.

    Raises:
      CommandRefusedError: if the reader refuses the fields, as it refuses them for a read.
    """
    fields = bytearray(parameters)
    fields[_READING_MODE] |= _CALC_TIME_ONLY
    return parse_run_time(await self.send_command(CommandId.RUN, bytes(fields)))

  @property
  def estimating(self) -> bool:
    """Whether the current task runs a read under ``estimate_duration``."""
    return _estimating.get()

  async def estimate_duration(self, read: Callable[[], Awaitable[Any]]) -> int:
    """The run time in s the reader expects for the read ``read`` makes, without reading.

    ``read`` is called as usual, e.g. ``lambda: reader.absorbance.measure([450])``.  When the read
    reaches its RUN, the RUN is sent with CalcTimeOnly set (``estimate_run_time``) and the call
    ends there with the reader's estimate: one read, the first.  Meanwhile shaking before the read
    and ``pause_before_reading`` are skipped, so the estimate leaves them out, and any other
    command (a drawer move, a gain and focus adjustment) is refused: nothing moves.

    On the tested reader every read finished 2.5-7 s before its estimate.  A reader without the
    luminescence module estimates a luminescence read too, which it then refuses (quit code 205).

    Raises:
      ValueError: if ``read`` ends without a read.
      RuntimeError: if ``read`` sends another command, or as the read raises it (the drawer is
        open, no plate).
      CommandRefusedError: if the reader refuses the read's fields, as it refuses them for a read.
    """
    token = _estimating.set(True)
    try:
      await read()
    except _Estimated as estimated:
      return estimated.seconds
    finally:
      _estimating.reset(token)
    raise ValueError("the call ended without a read to estimate")

  async def _run(
    self, parameters: bytes, *, timeout: float, by_well: bool
  ) -> List[MeasurementData]:
    """Run a read and collect its data: whole blocks, or each well's part of a single block
    (``run_blocks``, ``run_absorbance_spectrum``)."""
    status = await self.request_status()
    if status.drawer_open:
      raise RuntimeError("the drawer is open; close it first")
    if _estimating.get():
      raise _Estimated(await self.estimate_run_time(parameters))

    self._measuring = True
    try:
      # Sent once: the reader acts on the RUN and on each data request it answers, so sending one
      # again after a lost reply would start a second read or skip a block.
      reply = await self.send_command(CommandId.RUN, parameters, retries=1)
      if reply[:1] == bytes([TIME_DATA_ID]):
        logger.info("the reader expects the read to take %d s", parse_run_time(reply))
      wells = parse_well_list(
        await self.send_command(
          CommandId.VALUES, bytes([ValuesType.LAYOUT_WELL_LIST]) + bytes(6), when_idle=False
        )
      )
      await self.send_command(
        CommandId.VALUES, bytes([ValuesType.TIME_DATA]) + bytes(6), when_idle=False
      )
      start = time.monotonic()
      blocks: List[MeasurementData] = []
      while True:
        reply = await self._exchange_with_retries(_MW_BLOCK_STATUS, retries=5)
        self._check_reply(_MW_BLOCK_STATUS, reply)
        status = self._parse_status(reply)
        progress = parse_mw_block_data(reply)
        if by_well:
          saved = self._count_saved_wells(progress, len(wells))
          if saved > len(blocks):
            while len(blocks) < saved:
              column, row = wells[len(blocks)]
              sub_block = 1 if not blocks else 1 | _WITHOUT_CALIBRATION
              body = (
                bytes([ValuesType.MEASUREMENT_DATA, column, row])
                + (1).to_bytes(2, "big")
                + sub_block.to_bytes(2, "big")
              )
              page = await self.send_command(CommandId.VALUES, body, when_idle=False, retries=1)
              blocks.append(parse_measurement_data(page))
            if len(blocks) == len(wells):
              # The block was fetched part by part, so it is released explicitly, as Voyager does;
              # the reader stays busy until then.
              await self.send_command(
                CommandId.MW_BLOCK, _RELEASE_BLOCK, when_idle=False, retries=1
              )
              break
          elif not status.busy:
            raise RuntimeError("the run ended without its data")
        elif status.new_data_ready:
          page = await self.send_command(
            CommandId.VALUES, _NEXT_NEW_DATA_BLOCK, when_idle=False, retries=1
          )
          blocks.append(parse_measurement_data(page))
          if len(blocks) >= progress.required:
            break
        elif status.curr_state_meas_data_ready:
          await self.send_command(CommandId.VALUES, _CURRENT_DATA_BLOCK, when_idle=False)
        elif not status.busy:
          raise RuntimeError("the run ended without its data")
        if time.monotonic() - start >= timeout:
          raise TimeoutError(f"the measurement took longer than {timeout} s")
        await asyncio.sleep(self.measurement_poll_interval)
      await self._wait_until_ready(
        max(timeout - (time.monotonic() - start), 1.0), self.measurement_poll_interval
      )
      return blocks
    except BaseException:
      await self._drain_and_stop()
      raise
    finally:
      self._measuring = False

  async def run_blocks(self, parameters: bytes, *, timeout: float) -> List[MeasurementData]:
    """Run a read from prebuilt RUN fields and return its finished data blocks: one, or one per
    kinetic cycle.

    The flow Voyager uses: RUN, then the well list and the timing (both requested), then the data
    block status polled; the current block requested whenever a page of it is ready
    (CurrStateMeasDataRdy), and the next new block whenever the reader announces a finished one
    (NewMeasDataReady), until every block the read stores is in; then status polled until the
    reader is idle.  Any failure, timeout or cancellation stops the run.

    Raises:
      RuntimeError: if the drawer is open (nothing sent) or the run ends without its data.
      TimeoutError: if the run takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the RUN.
    """
    return await self._run(parameters, timeout=timeout, by_well=False)

  async def run_measurement(self, parameters: bytes, *, timeout: float) -> MeasurementData:
    """Run a read of one data block (an endpoint read or a fluorescence scan) from prebuilt RUN
    fields and return the block (``run_blocks``).

    Raises:
      RuntimeError: if the drawer is open (nothing sent) or the run ends without its data.
      TimeoutError: if the run takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the RUN.
    """
    return (await self.run_blocks(parameters, timeout=timeout))[0]

  async def run_absorbance_spectrum(
    self, parameters: bytes, *, timeout: float
  ) -> List[MeasurementData]:
    """Run an absorbance spectrum from prebuilt RUN fields and return one block per well, in the
    order the reader reads the wells; the first carries the calibration values.

    The reader stores the spectrum as one block; as Voyager does, each well's part is requested
    (MeasurementDataSpecificDataBlock) once the block status shows it saved, and the block is
    released (MwBlockCommand ReleaseMeasDataBlock) after the last well.

    Raises:
      RuntimeError: if the drawer is open (nothing sent) or the run ends without its data.
      TimeoutError: if the run takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the RUN.
    """
    return await self._run(parameters, timeout=timeout, by_well=True)

  # -- shaking --------------------------------------------------------------

  async def _idle_move(self, body: bytes) -> None:
    """IdleMoveCommand with ``body``: the shaker's command."""
    await self.send_command(CommandId.IDLE_MOVE, body)

  # -- gain and focus adjustment --------------------------------------------

  def build_gain_focus(
    self,
    monochromator: MonochromatorSettings,
    *,
    focal_height: float,
    target_percent: float,
    adjust_focus: bool,
    geometry: PlateGeometry = STANDARD_96,
    well_mask: Optional[bytes] = None,
    well_scan: WellScan = "point",
    scan_diameter: int = 3,
    flashes: int = 0,
    focal_height_step: float = _FOCUS_SCAN_STEP,
  ) -> bytes:
    """GainPlateCommand fields for a top fluorescence gain and focus adjustment, either alone.

    Args:
      monochromator: the read's monochromator settings.
      focal_height: mm; the height the gain is adjusted at when the focus is not.
      target_percent: the gain's target, percent of the detector range; 0 adjusts only the focus.
      adjust_focus: also find the focal height (a scan from 16 mm down in ``focal_height_step``).
      geometry: the plate.
      well_mask: the reference wells (``encode_well_mask``); None uses every well.
      well_scan: adjust over the area a well-scanning read reads (point: at the centre);
        scan_diameter: its diameter, mm.
      flashes: flashes per well, sent with an orbital or spiral scan.
      focal_height_step: mm between the focus scan's points (Voyager's 0.2 mm).

    Raises:
      ValueError: if ``target_percent`` is not 0-100.
    """
    if not 0 <= target_percent <= 100:
      raise ValueError(f"target_percent must be 0-100, got {target_percent}")
    mask = well_mask if well_mask is not None else encode_well_mask(geometry.columns, geometry.rows)
    scan = encode_well_scan(well_scan, scan_diameter)
    flags = (_AUTO_SET_FOCAL_HEIGHT if adjust_focus else 0x00) | scan[0]
    return (
      encode_plate_geometry(geometry)
      + mask
      + bytes([_USE_FOCUS_ADJUST if adjust_focus else 0x00])  # fluorescence
      + bytes([flags])
      + bytes([_FOCAL_CURVE_FLAGS])
      + _focal_height_bytes(focal_height)
      + (_FOCUS_SCAN_START if adjust_focus else _FOCUS_SCAN_START_UNUSED).to_bytes(2, "big")
      + round(focal_height_step * 100).to_bytes(2, "big")
      + _FOCUS_SCAN_STOP.to_bytes(2, "big")
      + round(target_percent * 100).to_bytes(2, "big")
      + (0).to_bytes(2, "big")
      + _GAIN_FOCUS_INTEGRATION_TIME.to_bytes(2, "big")
      + bytes([_MONO_POSITIONS])
      + encode_monochromator(monochromator)
      + (scan[1:] + flashes.to_bytes(2, "big") if well_scan != "point" else b"")
    )

  async def _run_gain_focus(self, parameters: bytes, timeout: float) -> bytes:
    """Run a gain and focus adjustment from prebuilt fields and return its GainDataResponse.

    Raises:
      RuntimeError: if the drawer is open (nothing is sent).
      TimeoutError: if the adjustment takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the adjustment.
    """
    status = await self.request_status()
    if status.drawer_open:
      raise RuntimeError("the drawer is open; close it first")
    await self.send_command(CommandId.GAIN_PLATE, parameters)
    await self._wait_until_ready(timeout, self.default_poll_interval)
    return await self.send_command(
      CommandId.VALUES, bytes([ValuesType.GAIN_DATA]) + bytes(6), when_idle=False
    )

  async def adjust_gain_and_focus(self, parameters: bytes, *, timeout: float = 300.0) -> GainFocus:
    """Run a gain and focus adjustment from prebuilt fields and return what it found.

    Raises:
      RuntimeError: if the drawer is open (nothing is sent).
      TimeoutError: if the adjustment takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the adjustment.
    """
    return parse_gain_data(await self._run_gain_focus(parameters, timeout))

  async def measure_z_profile(self, parameters: bytes, *, timeout: float = 300.0) -> ZProfileData:
    """Run a gain and focus adjustment from prebuilt fields with the focus, and return its focus
    curve.

    Raises:
      RuntimeError: if the drawer is open (nothing is sent).
      TimeoutError: if the adjustment takes longer than ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the adjustment.
      ValueError: if the reply holds no focus curve (the fields did not adjust the focus).
    """
    return parse_z_profile(await self._run_gain_focus(parameters, timeout))

  # -- temperature ----------------------------------------------------------

  @staticmethod
  def build_incubator(command_type: int, temperature: float) -> bytes:
    """IncubatorCommand fields: type, nominal temperature (0.1 C), channels (all)."""
    return (
      bytes([command_type])
      + round(temperature * 10).to_bytes(2, "big")
      + bytes([_ALL_INCUBATOR_CHANNELS])
    )

  async def _set_incubator(self, command_type: int, temperature: float) -> None:
    """Send an IncubatorCommand (``build_incubator``); the temperature controller's commands."""
    await self.send_command(CommandId.INCUBATOR, self.build_incubator(command_type, temperature))

  async def get_temperatures(self) -> Optional[Tuple[float, float, float]]:
    """The three incubator sensor readings in degrees C (the first is the one Voyager displays),
    or None when the reader reports none."""
    return (await self.request_status()).temperatures

  # -- lifecycle ------------------------------------------------------------

  def format_setup_summary(self) -> str:
    """One line describing the reader that was found."""
    fields: Dict[str, Any] = self.io.serialize()
    serial = fields.get("serial_number", "unknown")
    return f"[{self.name}] Connected on USB 0x0483:0xA29A (serial {serial})"

  async def setup(self) -> None:
    """Connect, initialize the reader (about 10 s), and read its configuration.

    Raises:
      RuntimeError: if the reader does not match the declared configuration.
    """
    logger.warning(
      "the VANTAstar driver has been tested on one reader (firmware 1.12) only; its luminescence "
      "read not yet (that reader has no luminescence module). If you verify it on your reader, "
      "please report the result or open a pull request to update this warning."
    )
    await self._usb_setup()
    await self.io.drain()
    await self.initialize()
    self.discover()
    self._check_declared_configuration()
    logger.info("%s", self.format_setup_summary())

  async def stop(self, accept_plate_left_in_device: bool = False) -> None:
    """Stop a running measurement, close the drawer, and close the connection.

    Raises:
      RuntimeError: if a plate is still in the reader, unless ``accept_plate_left_in_device``;
        the drawer is opened.  The connection is closed either way.
    """
    try:
      if self._measuring:
        await self._drain_and_stop()
      try:
        if await self.drawer.sense_drawer_open():
          try:
            await self.drawer.close()
          except (CommandRefusedError, TimeoutError):
            logger.warning("the drawer did not close", exc_info=True)

        plate_present = await self.drawer.sense_plate_present()
        if not accept_plate_left_in_device and plate_present:
          try:
            await self.drawer.open()
          except (CommandRefusedError, TimeoutError):
            logger.warning("the drawer did not open", exc_info=True)
          raise RuntimeError(
            "a plate is still in the reader; take it out, or pass accept_plate_left_in_device=True"
          )
      except (FrameError, TimeoutError):
        logger.warning("could not query the reader's state before closing", exc_info=True)
    finally:
      await self._usb_close()
