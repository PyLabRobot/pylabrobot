"""CLARIOstar driver: the connection, the command layer, and the reader's features.

The command layer speaks logical payloads and is the same for both interfaces; only the
framing differs (see ``framing.py``), and it is chosen once, from ``use_io``.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Optional, Tuple, Union, cast

import anyio

from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.transport.ftdi_interface import BMGFTDIInterface
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.io.usb import USB

from .configuration import (
  BARCODE_READER_POSITIONS,
  DECODED_FIRMWARE,
  FITTED_FIELDS,
  DeviceConfiguration,
  configuration_to_json,
  read_configuration,
)
from .confirmed_firmware_versions import CONFIRMED_FIRMWARE_VERSIONS
from .errors import (
  CommandRefusedError,
  describe_quit_codes,
  parse_quit_codes,
  parse_warning_codes,
)
from .features.absorbance import Absorbance
from .features.drawer import Drawer
from .features.fluorescence import Fluorescence
from .features.luminescence import Luminescence
from .features.temperature_controller import TemperatureController
from .framing import (
  FTDI_HEADER,
  USB_ACK,
  USB_CONTINUATION,
  USB_FIRST,
  USB_HEADER_SIZE,
  USB_HOST_ACK,
  USB_PACKET_SIZE,
  USB_SINGLE,
  usb_packet_type,
  usb_total_length,
  usb_wrap_payload,
)
from .parameter_table import (
  PARAMETER_TABLE,
  REPLY_OFFSET,
  SERIAL_NUMBER_ADDRESSES,
  SIGNED_PARAMETERS,
)

logger = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class CLARIOstarStatus:
  """A STATUS reply: the status flags and the heating plate temperatures.

  Temperatures are in degrees C, None unless the temperature controller is heating or monitoring.
  """

  busy: bool
  plate_detected: bool
  drawer_open: bool
  motors_enabled: bool
  temperature_bottom: Optional[float]
  temperature_top: Optional[float]


def _configure_usb_device(dev: Any) -> None:
  """Free interface 0 from the kernel driver (Linux), then select the first configuration."""
  try:
    if dev.is_kernel_driver_active(0):
      dev.detach_kernel_driver(0)
  except NotImplementedError:  # libusb on Windows and macOS has no kernel-driver query
    pass
  dev.set_configuration()


def _flag(channels: Optional[int]) -> Optional[bool]:
  """Whether a reported channel count is non-zero; None if it was not read."""
  return None if channels is None else bool(channels)


class CLARIOstarDriver(HasLifespan):
  """Owns the CLARIOstar connection, the command layer, and the reader's features.

  Features are reached as attributes, e.g. ``driver.drawer``.
  """

  # Seconds a command may keep the reader busy, when waited on (``read_timeout`` None).
  _default_read_timeout: float = 150.0
  # Seconds between status polls while waiting, with a ~38 ms exchange.
  default_poll_interval: float = 0.25

  _PACKET_READ_TIMEOUT = 1.0  # seconds to wait for the start of an FTDI reply
  # Seconds without new bytes before abandoning a started frame. The longest pause measured
  # inside a reply, over about 26,000 replies including measurement data, was 0.44 s.
  _FRAME_STALL_TIMEOUT = 2.0
  _FRAME_HARD_CAP = 60.0  # seconds; ceiling on reading one FTDI frame
  _USB_PACKET_TIMEOUT = 5  # seconds to wait for one USB packet
  _RETRY_SETTLE = 0.08  # seconds between send_command attempts

  class CommandFamily:
    """Command family: byte 0 of every logical payload."""

    INITIALIZE = 0x01
    DRAWER = 0x03
    RUN = 0x04
    REQUEST = 0x05
    TEMPERATURE_CONTROLLER = 0x06
    PLATE_MAP_SCAN = 0x07
    FOCUS_WELL = 0x09
    STOP = 0x0B
    SET_PARAMETER = 0x0E
    FILTER_SCAN = 0x24
    STATUS = 0x80

  class Command:
    """Command byte (payload byte 1) within a family; values recur across families."""

    # INITIALIZE
    INIT = 0x00
    # DRAWER
    DRAWER_CLOSE = 0x00
    DRAWER_OPEN = 0x01
    # REQUEST
    DATA = 0x02
    PLATE_MAP_XY = 0x04
    FOCUS_RESULT = 0x05
    PARAMETERS = 0x07
    FIRMWARE_INFO = 0x09
    PLATE_MAP_CONFIG = 0x0D
    SPECTRAL_DATA = 0x11
    FILTER_RESULT = 0x1B
    # SET_PARAMETER; seen only writing the two-byte test-run counter
    SET_PARAMETER_WORD = 0x0B

  # Families whose payload is the family byte plus parameters, without a command byte; FILTER_SCAN's
  # first parameter is a mode bit field.
  _NO_COMMAND_FAMILIES = frozenset(
    {
      CommandFamily.RUN,
      CommandFamily.STOP,
      CommandFamily.STATUS,
      CommandFamily.TEMPERATURE_CONTROLLER,
      CommandFamily.PLATE_MAP_SCAN,
      CommandFamily.FOCUS_WELL,
      CommandFamily.FILTER_SCAN,
    }
  )

  # Families send_command refuses: SET_PARAMETER writes the reader's stored parameters.
  _REFUSED_FAMILIES = frozenset({CommandFamily.SET_PARAMETER})

  # Status flags checked against replies from a reader: (name, payload byte, bit mask).
  _STATUS_FLAGS = [
    ("busy", 1, 1 << 5),
    ("plate_detected", 3, 1 << 1),
    ("drawer_open", 3, 1 << 0),
    ("motors_enabled", 3, 1 << 5),
  ]

  @property
  def default_read_timeout(self) -> float:
    """Seconds a command may keep the reader busy when waited on and ``read_timeout`` is None."""
    return self._default_read_timeout

  @default_read_timeout.setter
  def default_read_timeout(self, value: float) -> None:
    if value <= 0:
      raise ValueError(f"default_read_timeout must be positive, got {value}")
    self._default_read_timeout = value

  def _build_io(self, device_id: Optional[str]) -> Union[FTDI, USB]:
    """Build the link ``use_io`` names, to the reader ``device_id`` names or the first found."""
    if self.use_io == "ftdi":
      return FTDI(
        human_readable_device_name="BMG CLARIOstar",
        device_id=device_id,
        vid=0x0403,
        pid=0xBB68,
      )
    return USB(
      id_vendor=0x0483,
      id_product=0xA29A,
      human_readable_device_name="BMG CLARIOstar",
      serial_number=device_id,
      read_endpoint_address=0x81,
      write_endpoint_address=0x01,
      configuration_callback=_configure_usb_device,
    )

  def __init__(
    self,
    use_io: Optional[Literal["ftdi", "usb"]] = None,
    device_id: Optional[str] = None,
    io: Optional[Union[FTDI, USB]] = None,
    name: str = "CLARIOstar",
    declared_configuration_json: Optional[str] = None,
  ):
    """
    Args:
      use_io: ``"ftdi"`` for readers with the FTDI serial bridge (VID 0x0403 / PID 0xBB68),
        ``"usb"`` for readers with native USB (VID 0x0483 / PID 0xA29A). None takes it from
        ``io``, or ``"ftdi"`` when no ``io`` is given.
      device_id: the FTDI device id, or the USB serial number. None uses the first reader
        found.
      io: an already-built ``FTDI`` or ``USB`` (or a validator of either), used instead of
        building one from the arguments above. Its type sets ``use_io``.
      name: the device name, heading its setup summary.
      declared_configuration_json: a file `save_configuration` wrote. Setup refuses a reader that
        disagrees with it on what is fitted.

    Raises:
      TypeError: if ``io`` is neither an ``FTDI`` nor a ``USB``.
      ValueError: if ``use_io`` is not ``"ftdi"`` or ``"usb"``, or disagrees with ``io``.
    """
    super().__init__()
    if use_io is not None and use_io not in ("ftdi", "usb"):
      raise ValueError(f"use_io must be 'ftdi' or 'usb', got {use_io!r}")
    if io is not None:
      if isinstance(io, FTDI):
        io_kind: Literal["ftdi", "usb"] = "ftdi"
      elif isinstance(io, USB):
        io_kind = "usb"
      else:
        raise TypeError(f"io must be an FTDI or a USB, got {type(io).__name__}")
      if use_io is not None and use_io != io_kind:
        raise ValueError(f"use_io is {use_io!r} but io is {type(io).__name__}")
      use_io = io_kind
    self.use_io: Literal["ftdi", "usb"] = use_io or "ftdi"
    self.io = io if io is not None else self._build_io(device_id)
    self.name = name
    self.declared_configuration_json = declared_configuration_json
    self.configuration = DeviceConfiguration()
    # One lock over the link: a reply belongs to the last command written, so exchanges never
    # overlap.
    self._lock = anyio.Lock()
    # Whether a measurement is running; the reader runs only one at a time
    # (`_enforce_one_measurement_at_a_time`).
    self._measuring = False
    self._last_warning: Optional[Tuple[int, int, int, int, int]] = None
    # Built on first use over the current io (`_ftdi`).
    self._ftdi_interface: Optional[BMGFTDIInterface] = None
    self.drawer = Drawer(self)
    # Built by `discover` only if the reader reports a temperature controller, absorbance, a
    # fluorescence channel, and a luminescence channel.
    self.temperature_controller: Optional[TemperatureController] = None
    self.absorbance: Optional[Absorbance] = None
    self.fluorescence: Optional[Fluorescence] = None
    self.luminescence: Optional[Luminescence] = None

  # -- FTDI interface --------------------------------------------------------

  @property
  def _ftdi(self) -> BMGFTDIInterface:
    """The FTDI interface over the current io; one built over an earlier io is stopped first.

    Raises:
      RuntimeError: if the reader is connected over USB.
    """
    if self.use_io != "ftdi":
      raise RuntimeError("the CLARIOstar is connected over USB, not FTDI")
    interface = self._ftdi_interface
    if interface is None or interface.io is not self.io:
      if interface is not None:
        interface._stop_queued_reads()
      interface = BMGFTDIInterface(
        cast(FTDI, self.io),
        header=FTDI_HEADER,
        baudrate=125000,
        packet_read_timeout=self._PACKET_READ_TIMEOUT,
        frame_stall_timeout=self._FRAME_STALL_TIMEOUT,
        frame_hard_cap=self._FRAME_HARD_CAP,
      )
      self._ftdi_interface = interface
    return interface

  # -- USB interface ---------------------------------------------------------

  @property
  def _usb(self) -> USB:
    """The io, as a USB."""
    return cast(USB, self.io)

  async def _enter_usb_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the USB device, then send the two vendor requests it needs before bulk traffic."""
    await stack.enter_async_context(self._usb.lifespan())
    # USB.ctrl_transfer types its data as int (a read length); pyusb also takes bytes to send.
    await self._usb.ctrl_transfer(0x22, 0x8B, 0, 0, b"\x01")  # type: ignore[arg-type]  # bulk protocol on
    now = int(time.time()).to_bytes(4, "big")
    await self._usb.ctrl_transfer(0x22, 0x88, 0, 0, b"\x00\x00\x00\x00" + now)  # type: ignore[arg-type]

  async def _usb_read_packet(self) -> bytes:
    """Read one USB packet; raise FrameError if none arrives in time."""
    try:
      return await self._usb.read(timeout=self._USB_PACKET_TIMEOUT, size=USB_PACKET_SIZE)
    except TimeoutError as e:
      raise FrameError(f"No USB packet within {self._USB_PACKET_TIMEOUT} s") from e

  async def _usb_exchange(self, payload: bytes) -> bytes:
    """Send one payload and return the reply payload, reassembled from its packets.

    A message longer than one packet waits for the reader's acknowledgement before its
    continuation packets; a long reply is acknowledged by the host before its continuations.
    """
    packets = usb_wrap_payload(payload)
    await self._usb.write(packets[0])
    if len(packets) > 1:
      ack = await self._usb_read_packet()
      if usb_packet_type(ack) != USB_ACK:
        raise FrameError(f"Expected a USB acknowledgement, got {ack.hex()}")
      for packet in packets[1:]:
        await self._usb.write(packet)

    first = await self._usb_read_packet()
    packet_type = usb_packet_type(first)
    if packet_type == USB_SINGLE:
      return bytes(first[USB_HEADER_SIZE:])
    if packet_type != USB_FIRST:
      raise FrameError(f"Unexpected USB packet type 0x{packet_type:02x}: {first.hex()}")

    total = usb_total_length(first)
    reply = bytearray(first[USB_HEADER_SIZE:])
    await self._usb.write(USB_HOST_ACK)
    while len(reply) < total:
      packet = await self._usb_read_packet()
      if usb_packet_type(packet) != USB_CONTINUATION:
        raise FrameError(f"Expected a USB continuation packet, got {packet.hex()}")
      reply += packet[USB_HEADER_SIZE:]
    return bytes(reply[:total])

  # -- command layer ---------------------------------------------------------

  async def _purge_rx(self) -> None:
    """Discard unread reply bytes."""
    if self.use_io == "ftdi":
      await self._ftdi.purge_rx()
    else:
      await self._usb.drain()

  async def _drain_rx(self) -> None:
    """Discard the rest of a reply the reader is still sending (`BMGFTDIInterface.drain_rx`)."""
    if self.use_io == "ftdi":
      await self._ftdi.drain_rx()
    else:
      await self._usb.drain()

  async def exchange(self, payload: bytes) -> bytes:
    """Send one logical payload and return the reply payload.

    Raises:
      FrameError: if the reply is missing or malformed.
    """
    async with self._lock:
      if self.use_io == "ftdi":
        return await self._ftdi.exchange(payload)
      return await self._usb_exchange(payload)

  async def _exchange_with_retries(self, payload: bytes, retries: int) -> bytes:
    """Exchange a payload, retrying on a missing or malformed reply.

    Raises:
      ValueError: if ``retries`` is below 1.
      FrameError: if every attempt gets a missing or malformed reply.
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
        await self._purge_rx()
        await anyio.sleep(self._RETRY_SETTLE)
    raise AssertionError("unreachable")

  def _check_reply(self, payload: bytes, reply: bytes) -> None:
    """Raise if the reader refused `payload`; log a device warning once per change of codes.

    Raises:
      CommandRefusedError: if the reply carries the refused bit.
    """
    quit_codes = parse_quit_codes(reply)
    if quit_codes is not None:
      raise CommandRefusedError(payload, quit_codes)
    warning = parse_warning_codes(reply)
    if warning is not None and warning != self._last_warning:
      logger.warning("device warning: %s", describe_quit_codes(warning))
    self._last_warning = warning

  @classmethod
  def _parse_status(cls, payload: bytes) -> CLARIOstarStatus:
    """Parse a STATUS payload into its flags and temperatures.

    Bytes 11-12 are the bottom heating plate and 13-14 the top, u16 BE in 0.1 C; 0 parses as None.
    """
    flags = {
      name: index < len(payload) and bool(payload[index] & mask)
      for name, index, mask in cls._STATUS_FLAGS
    }
    temperatures = []
    for offset in (11, 13):
      raw = int.from_bytes(payload[offset : offset + 2], "big") if len(payload) >= offset + 2 else 0
      temperatures.append(raw / 10 if raw else None)
    return CLARIOstarStatus(
      **flags, temperature_bottom=temperatures[0], temperature_top=temperatures[1]
    )

  async def _wait_until_ready(
    self,
    timeout: Optional[float],
    poll_interval: float,
    until: Optional[Callable[[CLARIOstarStatus], bool]] = None,
  ) -> None:
    """Poll status until the reader is not busy, or until ``until`` holds.

    Args:
      timeout: seconds to wait; None waits indefinitely.
      poll_interval: seconds between status polls.
      until: the condition on the status to wait for; None waits for not busy.

    Raises:
      TimeoutError: if the condition does not hold after ``timeout`` seconds.
      CommandRefusedError: if the reader refuses the status query (e.g. before INIT).
    """
    status_payload = bytes([self.CommandFamily.STATUS])
    start = anyio.current_time()
    while True:
      reply = await self._exchange_with_retries(status_payload, retries=5)
      self._check_reply(status_payload, reply)
      status = self._parse_status(reply)
      if until(status) if until is not None else not status.busy:
        return
      if timeout is not None and anyio.current_time() - start >= timeout:
        raise TimeoutError(f"CLARIOstar status still {status} after {timeout} s")
      if poll_interval:
        await anyio.sleep(poll_interval)

  async def send_command(
    self,
    command_family: int,
    command: Optional[int] = None,
    *,
    parameters: bytes = b"",
    read_timeout: Optional[float] = None,
    wait: bool = False,
    poll_interval: float = 0.0,
    retries: int = 3,
    when_idle: bool = True,
  ) -> bytes:
    """Build a logical payload, exchange it, and return the reply payload.

    Args:
      command_family: the family byte, from ``CommandFamily``.
      command: the command byte, from ``Command``; None for families that take none.
      parameters: bytes appended after the family and command bytes.
      read_timeout: seconds ``wait`` may last; None uses ``default_read_timeout``.
      wait: poll status until the reader is no longer busy, for commands that move it.
      poll_interval: seconds between status polls while waiting.
      retries: attempts on a missing or malformed reply, purging unread bytes in between.
      when_idle: first poll status until the reader is not busy, as a busy reader refuses
        most commands. False for commands meant for a busy reader. STATUS never waits.

    Raises:
      ValueError: if the family is one of ``_REFUSED_FAMILIES``, or ``command`` is given for a
        family without one, or missing otherwise.
      FrameError: if every attempt gets a missing or malformed reply.
      CommandRefusedError: if the reader refused the command.
    """
    if command_family in self._REFUSED_FAMILIES:
      raise ValueError(f"family 0x{command_family:02x} writes the reader's stored parameters")
    if command_family in self._NO_COMMAND_FAMILIES:
      if command is not None:
        raise ValueError(f"family 0x{command_family:02x} takes no command byte")
      payload = bytes([command_family]) + parameters
    else:
      if command is None:
        raise ValueError(f"family 0x{command_family:02x} requires a command byte")
      payload = bytes([command_family, command]) + parameters

    if when_idle and command_family != self.CommandFamily.STATUS:
      await self._wait_until_ready(
        timeout=self.default_read_timeout, poll_interval=self.default_poll_interval
      )
    reply = await self._exchange_with_retries(payload, retries)
    self._check_reply(payload, reply)

    if wait:
      await self._wait_until_ready(
        timeout=self.default_read_timeout if read_timeout is None else read_timeout,
        poll_interval=poll_interval,
      )
    return reply

  async def request_status(self, retries: int = 5) -> CLARIOstarStatus:
    """Request the status flags and the heating plate temperatures (see ``CLARIOstarStatus``)."""
    return self._parse_status(await self.send_command(self.CommandFamily.STATUS, retries=retries))

  async def request_is_ready(self) -> bool:
    """Request whether the reader is idle, not busy."""
    return not (await self.request_status()).busy

  async def initialize(self) -> None:
    """Run the hardware initialization (homes the axes, about 13 s) and wait until ready.

    INIT is sent once, without waiting for the reader to be idle first: it is always allowed, and
    an uninitialized reader refuses even STATUS (quit code 201). A malformed reply does not mean
    the reader missed the command, and sending it again would restart a homing already under
    way, so the reply is discarded and status decides when the reader is ready.
    """
    try:
      reply = await self.send_command(
        self.CommandFamily.INITIALIZE,
        self.Command.INIT,
        parameters=b"\x00\x10\x03",
        retries=1,
        when_idle=False,
      )
    except FrameError as e:
      logger.warning("INIT reply malformed (%s); waiting on status instead", e)
      await self._purge_rx()
    else:
      try:
        self.configuration.update_from_init_reply(reply)
      except ValueError as e:
        logger.warning("INIT reply not decoded (%s); configuration not read", e)
    await self._wait_until_ready(
      timeout=self.default_read_timeout, poll_interval=self.default_poll_interval
    )

  async def _request_parameters_reply(self) -> bytes:
    """Request the reader's stored parameters (REQUEST `07`) and return the whole reply.

    Raises:
      ValueError: if the reply is not a whole stored parameters reply.
    """
    payload = await self.send_command(
      self.CommandFamily.REQUEST, self.Command.PARAMETERS, parameters=b"\x00\x00\x00\x00\x00"
    )
    if not payload or payload[0] != self.Command.PARAMETERS or len(payload) < 260:
      raise ValueError(f"not a whole stored parameters reply: {len(payload)} bytes")
    return payload

  @staticmethod
  def _decode_parameters(payload: bytes) -> Dict[str, int]:
    """A stored parameters reply's values, by key (see ``PARAMETER_TABLE``).

    Raises:
      ValueError: if the reply comes from firmware outside ``DECODED_FIRMWARE``.
    """
    parameters = {
      name: int.from_bytes(
        payload[address + REPLY_OFFSET : address + REPLY_OFFSET + size],
        "little",
        signed=name in SIGNED_PARAMETERS,
      )
      for name, address, size in PARAMETER_TABLE
    }
    if not DECODED_FIRMWARE[0] <= parameters["firmware_version"] <= DECODED_FIRMWARE[1]:
      raise ValueError(
        f"stored parameters of firmware {parameters['firmware_version']} are not decoded"
      )
    return parameters

  @staticmethod
  def _decode_serial_number(payload: bytes) -> str:
    """A stored parameters reply's serial number, "NNN-NNNN" (``SERIAL_NUMBER_ADDRESSES``)."""
    first, second = (
      int.from_bytes(payload[address + REPLY_OFFSET : address + REPLY_OFFSET + 2], "little")
      for address in SERIAL_NUMBER_ADDRESSES
    )
    return f"{first:03d}-{second:04d}"

  def _get_link_serial_number(self) -> Optional[str]:
    """The serial number the reader was opened by: the FTDI chip's or the USB device's; None if
    it was opened by address."""
    fields = self.io.serialize()
    if fields.get("usb_address") or fields.get("device_address"):
      return None
    return fields.get("device_id") or fields.get("serial_number")

  async def request_parameters(self) -> Dict[str, int]:
    """Request the reader's stored parameters, by key (see ``PARAMETER_TABLE``).

    The reader answers all of them in one reply. Its serial number is left out: `discover` reads
    it.

    Raises:
      ValueError: if the reply is not a whole stored parameters reply, or comes from firmware
        outside ``DECODED_FIRMWARE``.
    """
    return self._decode_parameters(await self._request_parameters_reply())

  async def request_fluorescence_channels(self) -> int:
    """Request the number of fluorescence measurement channels."""
    return (await self.request_parameters())["fluorescence_measurement_channel_count"]

  async def request_luminescence_channels(self) -> int:
    """Request the number of luminescence measurement channels."""
    return (await self.request_parameters())["luminescence_measurement_channel_count"]

  async def request_time_resolved_fluorescence_channels(self) -> int:
    """Request the number of time-resolved fluorescence measurement channels."""
    parameters = await self.request_parameters()
    return parameters["time_resolved_fluorescence_measurement_channel_count"]

  async def request_absorbance_available(self) -> bool:
    """Request whether absorbance measurements are available."""
    return (await self.request_parameters())["absorbance_measurement_option"] != 0

  async def request_alpha_screen_available(self) -> bool:
    """Request whether AlphaScreen measurements are available."""
    return (await self.request_parameters())["alpha_screen_measurement_option"] != 0

  async def request_fluorescence_polarization_available(self) -> bool:
    """Request whether fluorescence polarization measurements are available."""
    parameters = await self.request_parameters()
    return parameters["fluorescence_polarization_measurement_option"] != 0

  async def request_bottom_measurement_available(self) -> bool:
    """Request whether measurements from below the plate are available."""
    return (await self.request_parameters())["bottom_measurement_option"] != 0

  async def request_extended_temperature_range(self) -> bool:
    """Request whether the extended temperature range (10 to 65 C) is built in."""
    return (await self.request_parameters())["extended_incubator"] != 0

  async def request_high_speed_shaking_allowed(self) -> bool:
    """Request whether shaking at 800-1100 rpm is allowed."""
    return (await self.request_parameters())["high_speed_shaking_allowed"] != 0

  async def request_well_1536_plates_supported(self) -> bool:
    """Request whether the reader is set up for 1536-well plates, an upgrade fitted to it."""
    return (await self.request_parameters())["well_1536_measurements_enabled"] != 0

  @staticmethod
  def _decode_firmware_build_date(payload: bytes) -> str:
    """A firmware info reply's compile date ("Nov 20 2020" at bytes 8-19), as "YYYY-MM-DD".

    Raises:
      ValueError: if the payload is not a firmware info reply, or its date is not one.
    """
    if not payload or payload[0] != 0x0A or len(payload) < 20:
      raise ValueError(f"not a firmware info reply: {payload[:1].hex()}, {len(payload)} bytes")
    text = payload[8:20].split(b"\x00")[0].decode("ascii", errors="replace")
    try:
      month, day, year = text.split()
      return f"{int(year):04d}-{_MONTHS.index(month) + 1:02d}-{int(day):02d}"
    except ValueError:
      raise ValueError(f"not a firmware build date: {text!r}") from None

  async def request_firmware_build_date(self) -> str:
    """Request the day the reader's firmware was compiled (REQUEST `09`), as "YYYY-MM-DD".

    Raises:
      ValueError: if the reply is not a firmware info reply with a date.
    """
    payload = await self.send_command(
      self.CommandFamily.REQUEST, self.Command.FIRMWARE_INFO, parameters=b"\x00" * 5
    )
    return self._decode_firmware_build_date(payload)

  async def _set_parameter(self, name: str, value: int) -> None:
    """Write one of the reader's stored parameters (see ``PARAMETER_TABLE``).

    Args:
      name: the parameter's key in ``PARAMETER_TABLE``.
      value: the new value, unsigned, within the parameter's size.

    Raises:
      NotImplementedError: always, until the write format is confirmed.
    """
    # TODO(device): one write recorded (`0e 0b 12 00 00 hh ll`); confirm `0b` and the layout on a
    # harmless address, then lift SET_PARAMETER from `_REFUSED_FAMILIES` and enable this.
    # address, size = next((a, s) for n, a, s in PARAMETER_TABLE if n == name)
    # if not 0 <= value < 1 << (8 * size):
    #   raise ValueError(f"{name} takes {size} bytes, got {value}")
    # await self.send_command(
    #   self.CommandFamily.SET_PARAMETER,
    #   self.Command.SET_PARAMETER_WORD,
    #   parameters=bytes([address, 0x00, 0x00]) + value.to_bytes(size, "big"),
    # )
    raise NotImplementedError("writing the reader's stored parameters is not confirmed yet")

  def _discover_features(self) -> None:
    """Build the features the configuration says are fitted; a feature already built is kept."""
    if self.configuration.temperature_controller and self.temperature_controller is None:
      self.temperature_controller = TemperatureController(self)
    if self.configuration.absorbance_available and self.absorbance is None:
      self.absorbance = Absorbance(self)
    if self.configuration.fluorescence_channels and self.fluorescence is None:
      self.fluorescence = Fluorescence(self)
    if self.configuration.luminescence_channels and self.luminescence is None:
      self.luminescence = Luminescence(self)

  def _check_firmware_compatibility(self) -> None:
    """Log a warning if the firmware version has not been checked on hardware."""
    version = self.configuration.firmware_version
    if version and version not in CONFIRMED_FIRMWARE_VERSIONS:
      logger.warning(
        "firmware %s has not been tested with this driver (tested: %s); please report how it works",
        version,
        ", ".join(sorted(CONFIRMED_FIRMWARE_VERSIONS)),
      )

  async def discover(self) -> None:
    """Read the stored parameters into ``configuration``, the serial number with them, and the
    firmware build date.

    Fields stay unchanged when a reply is not decoded. The firmware version is reported twice
    (INIT reply, stored parameters); logs a warning if they disagree, or for a version not checked
    on hardware, and if the serial number is not the one the reader was opened by.
    """
    versions = {"INIT reply": self.configuration.firmware_version}
    # The stored parameters first, right after INIT.
    try:
      payload = await self._request_parameters_reply()
      parameters = self._decode_parameters(payload)
    except ValueError as e:
      logger.warning("stored parameters not decoded (%s)", e)
    else:
      versions["stored parameters"] = f"{parameters['firmware_version'] / 1000:.2f}"
      self.configuration.update_from_parameters(parameters)
      self.configuration.serial_number = self._decode_serial_number(payload)
      opened_by = self._get_link_serial_number()
      if opened_by is not None and opened_by != self.configuration.serial_number:
        logger.warning(
          "the reader's serial number %s is not the one it was opened by, %s",
          self.configuration.serial_number,
          opened_by,
        )
    try:
      self.configuration.firmware_build_date = await self.request_firmware_build_date()
    except ValueError as e:
      logger.warning("firmware build date not read (%s)", e)
    reported = {source: version for source, version in versions.items() if version is not None}
    if len(set(reported.values())) > 1:
      logger.warning(
        "firmware versions disagree: %s",
        ", ".join(f"{source} {version}" for source, version in reported.items()),
      )
    self._check_firmware_compatibility()
    self._discover_features()

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
    """Write what this reader reported to a file, to be declared later.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      RuntimeError: if nothing has been read off the reader yet.
    """
    if self.configuration.model is None:
      raise RuntimeError("nothing read off the reader yet; call `setup` first")
    with open(path, "w", encoding="utf-8") as f:
      json.dump({"device": configuration_to_json(self.configuration)}, f, indent=indent)
      f.write("\n")

  # -- lifecycle -------------------------------------------------------------

  def _describe_link(self) -> str:
    """How this reader is reached, in whatever terms its transport is addressed by."""
    fields = self.io.serialize()
    link = type(self.io).__name__
    vendor = fields.get("id_vendor", fields.get("vid"))
    product = fields.get("id_product", fields.get("pid"))
    if vendor is not None and product is not None:
      link += f" {vendor:#06x}:{product:#06x}"
    named = [
      f"{label} {fields[key]}"
      for label, key in (
        ("address", "device_address"),
        ("address", "usb_address"),
        ("serial", "serial_number"),
        ("device", "device_id"),
      )
      if fields.get(key)
    ]
    return link + (f" ({', '.join(named)})" if named else "")

  def format_setup_summary(self) -> str:
    """One block describing the reader that was found: how it is reached and every field of its
    configuration; a field that was not read shows None.

    Returns:
      A multi-line summary, or a note that setup has not run.
    """
    c = self.configuration
    if c.model is None:
      return f"[{self.name}] not discovered yet"
    standard = c.extended_red_photomultiplier_tube
    if standard is not None:
      standard = not c.extended_red_photomultiplier_tube or bool(c.dual_photomultiplier_tubes)
    pumps = c.pumps or (None, None, None)
    barcode_readers = c.barcode_readers
    groups: Tuple[Tuple[str, Tuple[Tuple[str, Any], ...]], ...] = (
      (
        "Measurement modes",
        (
          ("absorbance", c.absorbance_available),
          ("fluorescence", _flag(c.fluorescence_channels)),
          ("luminescence", _flag(c.luminescence_channels)),
          ("time-resolved fluorescence", _flag(c.time_resolved_fluorescence_channels)),
          ("fluorescence polarization, visible", c.fluorescence_polarization_available),
          (
            "fluorescence polarization, ultraviolet",
            c.fluorescence_polarization_ultraviolet_available,
          ),
          ("AlphaScreen", c.alpha_screen_available),
        ),
      ),
      (
        "Detectors",
        (
          ("standard photomultiplier", standard),
          ("extended-red photomultiplier", c.extended_red_photomultiplier_tube),
          ("dual photomultipliers", c.dual_photomultiplier_tubes),
          ("crossover", c.photomultiplier_tube_crossover),
        ),
      ),
      (
        "Optics",
        (
          ("monochromators", c.linear_variable_filters),
          ("spectrometer", c.spectrometer),
          ("bottom optic", c.bottom_measurement_available),
        ),
      ),
    )
    details = [f"build {c.firmware_build}"] if c.firmware_build is not None else []
    details += [f"built {c.firmware_build_date}"] if c.firmware_build_date else []
    firmware_details = f" ({', '.join(details)})" if details else ""
    lines = [
      f"[{self.name}] Connected on {self._describe_link()}",
      f"  Model: {c.model}",
      f"  Serial number: {c.serial_number}",
      f"  Firmware: {c.firmware_version}{firmware_details}",
      f"  Mainboard revision: {c.mainboard_revision}",
    ]

    def add_group(title: str, rows: Tuple[Tuple[str, Any], ...]) -> None:
      lines.append(f"  {title}:")
      lines.extend(f"    - {label}: {value}" for label, value in rows)

    for title, rows in groups:
      add_group(title, rows)
    if self.fluorescence is not None:
      slots = self.fluorescence.configuration
      defaults = Fluorescence._compute_filter_slots(c.monochromator_slides) is None
      lines.append(
        f"  Filter slots: excitation {slots.excitation_filter_slots}, dichroic "
        f"{slots.dichroic_filter_slots}, emission {slots.emission_filter_slots}"
        + (" (defaults: the slides are not reported)" if defaults else "")
      )
    add_group(
      "Temperature control",
      (
        ("temperature controller", c.temperature_controller),
        ("channels", c.temperature_controller_channels),
        ("extended range (10-65 C)", c.extended_temperature_range),
      ),
    )
    lines.append(f"  High-speed shaking: {c.high_speed_shaking_allowed}")
    lines.append(f"  1536-well plates: {c.well_1536_plates_supported}")
    add_group(
      "Pumps",
      tuple(
        (f"pump {index}", None if c.pumps is None else kind or False)
        for index, kind in enumerate(pumps, 1)
      ),
    )
    add_group(
      "Barcode readers",
      tuple(
        (position, None if barcode_readers is None else barcode_readers.get(position, False))
        for position in BARCODE_READER_POSITIONS
      ),
    )
    lines.append(f"  Stacker: {c.stacker_connected}")
    lines.append(f"  Atmospheric control unit: {c.atmospheric_control_unit_connected}")
    return "\n".join(lines)

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    accept_plate_left_in_device: bool = False,
  ) -> None:
    """Connect, initialize the reader (about 13 s), and read its configuration.

    Raises:
      RuntimeError: if the motors are still off after INIT, or the reader does not match the
        declared configuration.
    """
    await super()._enter_lifespan(stack)
    logger.debug("setting up the CLARIOstar on %s ...", self._describe_link())
    if self.use_io == "ftdi":
      await stack.enter_async_context(self._ftdi.lifespan())
    else:
      logger.warning(
        "the CLARIOstar USB interface has not been tested on hardware with this driver; please "
        "report how it works"
      )
      await self._enter_usb_lifespan(stack)
    await self._purge_rx()

    logger.debug("[PHASE 1] Device initialization")
    await self.initialize()
    if not (await self.request_status()).motors_enabled:
      raise RuntimeError(
        "CLARIOstar motors are still off after INIT. A power cycle may be required."
      )
    stack.push_shielded_async_callback(self._shut_down, accept_plate_left_in_device)

    logger.debug("[PHASE 2] Discovery")
    await self.discover()
    logger.debug("[PHASE 3] Declared configuration")
    self._check_declared_configuration()
    logger.debug("[PHASE 4] Temperature monitoring")
    await self._start_temperature_monitoring()
    logger.info("%s", self.format_setup_summary())

  async def _start_temperature_monitoring(self) -> None:
    """Start the temperature sensors, so every reading carries a temperature; a failure only
    warns."""
    if self.temperature_controller is None:
      return
    try:
      await self.temperature_controller.start_monitoring()
    except (CommandRefusedError, FrameError, TimeoutError) as e:
      logger.warning("could not start temperature monitoring: %s", e)

  async def _stop_run(self) -> None:
    """Stop a measurement still running on the reader; warn about anything else still running.

    While busy, STATUS reply byte 6 is the family of the command running (`04` for a RUN).
    """
    payload = await self.send_command(self.CommandFamily.STATUS, retries=5)
    if not self._parse_status(payload).busy:
      return
    if len(payload) > 6 and payload[6] == self.CommandFamily.RUN:
      logger.warning("a measurement is still running; stopping it")
      await self.send_command(self.CommandFamily.STOP, parameters=b"\x00", when_idle=False)
    else:
      logger.warning("the reader is busy with a command other than a measurement; it keeps running")

  async def _shut_down(self, accept_plate_left_in_device: bool) -> None:
    """Stop a running measurement, switch heating off and close the drawer.

    A step that fails is logged and the next one runs.

    Raises:
      RuntimeError: if a plate is still in the reader, unless `accept_plate_left_in_device`; the
        drawer is opened for it.
    """
    try:
      await self._stop_run()
    except (CommandRefusedError, FrameError, TimeoutError):
      logger.warning("could not check for a running measurement", exc_info=True)
    temperature_controller = self.temperature_controller
    if temperature_controller is not None:
      try:
        status = await self.request_status()
        sensors_on = status.temperature_bottom is not None
        if sensors_on or temperature_controller.target_temperature is not None:
          await temperature_controller.stop_monitoring()
      except (CommandRefusedError, FrameError, TimeoutError):
        logger.warning("could not switch the temperature controller off", exc_info=True)
    if await self.drawer.sense_drawer_open():
      try:
        await self.drawer.close()
      except (CommandRefusedError, TimeoutError):
        logger.warning("the drawer did not close", exc_info=True)
    if not accept_plate_left_in_device and await self.drawer.sense_plate_present():
      try:
        await self.drawer.open()
      except (CommandRefusedError, TimeoutError):
        logger.warning("the drawer did not open", exc_info=True)
      raise RuntimeError(
        "a plate is still in the reader; take it out, or pass accept_plate_left_in_device=True"
      )
