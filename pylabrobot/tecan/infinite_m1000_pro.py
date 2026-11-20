"""Tecan Infinite M1000 Pro plate reader."""

import asyncio
import logging
import math
import re
import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import reduce
from operator import xor
from typing import Literal

from pylabrobot.io.usb import USB
from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.lib.fluorescence.results import FluorescenceResult
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well

logger = logging.getLogger(__name__)


def _format_absorbance_result(
  plate: Plate,
  wells: Sequence[Well],
  values: Sequence[float],
  wavelength: int,
  start: datetime,
  end: datetime,
) -> AbsorbanceResult:
  """Place already-calculated measurements in PLR's shared absorbance result."""
  if len(wells) != len(values):
    raise ValueError("A value is required for each well")
  if any(well.parent is not plate for well in wells):
    raise ValueError("Every well must belong to the specified plate")
  entries = sorted(zip(wells, values), key=lambda pair: (pair[0].get_row(), pair[0].get_column()))
  data = {well.get_identifier(): value for well, value in entries}
  if len(data) != len(wells):
    raise ValueError("Duplicate wells are not allowed")
  return AbsorbanceResult(
    data=data,
    wavelength=wavelength,
    temperature=None,
    start_timestamp=start,
    end_timestamp=end,
  )


def _decode_binary_block(block: bytes, payload_size: int, *, expected_sequence: int = 0) -> bytes:
  """Check a binary block's payload checksum and sequence."""
  if payload_size < 0 or len(block) != payload_size + 4:
    raise ValueError("Invalid binary block length")
  payload = block[:payload_size]
  if reduce(xor, payload, 0) != block[-1]:
    raise ValueError("Binary block checksum mismatch")
  if block[payload_size] != expected_sequence:
    raise ValueError("Binary block sequence mismatch")
  if int.from_bytes(block[payload_size + 1 : payload_size + 3], "big") != payload_size:
    raise ValueError("Binary block length mismatch")
  return payload


@dataclass(frozen=True)
class _BinaryDataHeader:
  """Raw reader binary packet header fields."""

  flags: int
  filler: int
  data_size: int
  max_block_size: int
  data_type: int
  timeout_raw: int


def _decode_binary_header(header: bytes) -> _BinaryDataHeader:
  """Decode a 20-byte reader binary header; units and packet types remain device-specific."""
  if len(header) != 20:
    raise ValueError("Invalid binary header length")
  identifier, flags, size, filler, data_size, max_block, data_type, timeout = struct.unpack(
    ">HHBBIIHI", header
  )
  if identifier != 0x0100 or size != len(header):
    raise ValueError("Invalid binary header identifier or size")
  return _BinaryDataHeader(flags, filler, data_size, max_block, data_type, timeout)


def _validate_absorbance_burst(events: Sequence[str | bytes], *, expected_type: int) -> list[bytes]:
  """Validate one completed binary burst before decoding measurement data."""
  meaningful = [
    event for event in events if not (isinstance(event, str) and event.startswith(("MSG", "LOG:")))
  ]
  if not meaningful or meaningful[-1] != "ST":
    raise ValueError("Binary burst is missing its terminal standby response")
  body = meaningful[:-1]
  if len(body) < 4 or len(body) % 2:
    raise ValueError("Binary burst has incomplete frame pairs")
  blocks: list[bytes] = []
  for i in range(0, len(body), 2):
    announcement, payload = body[i], body[i + 1]
    if not isinstance(announcement, str) or not isinstance(payload, bytes):
      raise TypeError("Unexpected binary burst event order")
    match = re.fullmatch(r"([0-9]+),BIN:", announcement)
    if match is None or int(match.group(1)) != len(payload):
      raise ValueError("Binary burst length announcement mismatch")
    blocks.append(payload)
  header = _decode_binary_header(blocks[0])
  if header.flags != 0 or header.data_type != expected_type:
    raise ValueError("Unexpected binary burst type or flags")
  if header.max_block_size <= 0 or any(len(block) > header.max_block_size for block in blocks[1:]):
    raise ValueError("Invalid binary burst block size")
  if sum(map(len, blocks[1:])) != header.data_size:
    raise ValueError("Incomplete binary burst payload")
  return blocks[1:]


@dataclass(frozen=True)
class _AbsorbancePrepareItem4Channel:
  """Raw preparation values for four measurement channels."""

  ticker_overflows: int
  ticker_counter: int
  measurement_gain: tuple[int, int, int, int]
  measurement_dark: tuple[int, int, int, int]
  measurement_bright: tuple[int, int, int, int]
  reference_gain: int
  reference_dark: int
  reference_bright: int


@dataclass(frozen=True)
class _AbsorbancePreparePacket4Channel:
  """Preparation payload used by the device's absorbance calculation."""

  label: int
  ex_wavelength_raw: int
  items: list[_AbsorbancePrepareItem4Channel]


def _decode_absorbance_prepare_4channel(payload: bytes) -> _AbsorbancePreparePacket4Channel:
  """Decode a four-channel preparation payload without calculating absorbance."""
  if len(payload) < 4 or (len(payload) - 4) % 36:
    raise ValueError("Invalid four-channel preparation payload length")
  label, wavelength = struct.unpack_from(">HH", payload)
  items = []
  for values in struct.iter_unpack(">I16H", payload[4:]):
    (
      overflow,
      counter,
      g0,
      g1,
      g2,
      g3,
      d0,
      d1,
      d2,
      d3,
      b0,
      b1,
      b2,
      b3,
      ref_gain,
      ref_dark,
      ref_bright,
    ) = values
    items.append(
      _AbsorbancePrepareItem4Channel(
        overflow,
        counter,
        (g0, g1, g2, g3),
        (d0, d1, d2, d3),
        (b0, b1, b2, b3),
        ref_gain,
        ref_dark,
        ref_bright,
      )
    )
  return _AbsorbancePreparePacket4Channel(label, wavelength, items)


@dataclass(frozen=True)
class _AbsorbanceRawItem4Channel:
  """Uncalculated values from one four-channel absorbance data item."""

  ticker_overflows: int
  ticker_counter: int
  measurement_counts: tuple[int, int, int, int]
  reference_count: int


@dataclass(frozen=True)
class _AbsorbanceRawPacket4Channel:
  """Decoded payload, without calibration or well-position mapping."""

  label: int
  ex_wavelength_raw: int
  number_of_packets: int | None
  items: list[_AbsorbanceRawItem4Channel]


def _decode_absorbance_data_4channel(
  payload: bytes, *, multi_packet: bool = False
) -> _AbsorbanceRawPacket4Channel:
  """Decode a four-channel payload; this does not calculate absorbance."""
  header_size = 6 if multi_packet else 4
  if len(payload) < header_size or (len(payload) - header_size) % 16:
    raise ValueError("Invalid four-channel absorbance payload length")
  label, wavelength = struct.unpack_from(">HH", payload)
  number_of_packets = struct.unpack_from(">H", payload, 4)[0] if multi_packet else None
  items = []
  for overflow, counter, c0, c1, c2, c3, reference in struct.iter_unpack(
    ">I6H", payload[header_size:]
  ):
    items.append(_AbsorbanceRawItem4Channel(overflow, counter, (c0, c1, c2, c3), reference))
  return _AbsorbanceRawPacket4Channel(label, wavelength, number_of_packets, items)


def _calculate_absorbance_4channel(
  calibration: _AbsorbancePreparePacket4Channel,
  measurement: _AbsorbanceRawPacket4Channel,
  *,
  channel: int,
) -> float:
  """Calculate OD for a single raw channel; reject ambiguous or invalid samples.

  This conservative offline calculation is not validated against a reader or i-control.
  """
  if channel not in range(4):
    raise ValueError("Absorbance channel must be between 0 and 3")
  if (calibration.label, calibration.ex_wavelength_raw) != (
    measurement.label,
    measurement.ex_wavelength_raw,
  ):
    raise ValueError("Calibration and measurement label or wavelength differ")
  if not calibration.items or not measurement.items:
    raise ValueError("Absorbance calibration and measurement must not be empty")
  cal = calibration.items[0]
  dark = cal.measurement_dark[channel]
  bright = cal.measurement_bright[channel]
  if bright <= dark or cal.reference_bright <= cal.reference_dark:
    raise ValueError("Invalid absorbance calibration")
  transmissions = []
  for item in measurement.items:
    count = item.measurement_counts[channel]
    if count >= 65535 or item.reference_count >= 65535:
      raise ValueError("Absorbance signal saturated")
    if item.reference_count - cal.reference_dark < 500:
      raise ValueError("Absorbance reference signal too low")
    transmissions.append((count - dark) / (item.reference_count - cal.reference_dark))
  factor = (cal.reference_bright - cal.reference_dark) / (bright - dark)
  transmission = max(sum(transmissions) / len(transmissions) * factor, 0.0001)
  return -math.log10(transmission)


def _assemble_full_plate_absorbance(
  plate: Plate,
  calibration: _AbsorbancePreparePacket4Channel,
  packets: Sequence[_AbsorbanceRawPacket4Channel],
  wavelength: int,
  start: datetime,
  end: datetime,
) -> AbsorbanceResult:
  """Map two 12-position, four-channel scans into PLR's 96-well result."""
  if (plate.num_items_x, plate.num_items_y) != (12, 8):
    raise ValueError("A 96-well plate is required")
  if len(packets) != 24:
    raise ValueError("Expected exactly 24 four-channel scan packets")
  if calibration.ex_wavelength_raw != wavelength * 10:
    raise ValueError("Calibration wavelength differs from requested wavelength")
  wells: list[Well] = []
  values: list[float] = []
  for index, packet in enumerate(packets):
    if packet.number_of_packets is not None or len(packet.items) != 1:
      raise ValueError("Unsupported four-channel scan packet layout")
    column = index + 1 if index < 12 else 24 - index
    row_offset = 0 if index < 12 else 4
    for channel in range(4):
      well = plate.get_item(f"{'ABCDEFGH'[row_offset + channel]}{column}")
      wells.append(well)
      values.append(_calculate_absorbance_4channel(calibration, packet, channel=channel))
  return _format_absorbance_result(plate, wells, values, wavelength, start, end)


def _encode(command: str, *, sequence: int = 0) -> bytes:
  """Frame an ASCII command using the reader's RDNet protocol."""
  payload = command.encode("ascii")
  if len(payload) > 65535 or any(byte in payload for byte in (2, 3, 13)):
    raise ValueError("Invalid command payload")
  if not 0 <= sequence <= 254:
    raise ValueError("Invalid command sequence")
  body = b"\x02" + payload + b"\x03"
  return (
    body + bytes([sequence]) + len(payload).to_bytes(2, "big") + bytes([reduce(xor, body, 0), 13])
  )


def _decode(frame: bytes, *, expected_sequence: int = 0) -> str:
  """Validate and decode one reader response for the current command sequence."""
  if len(frame) < 7 or frame[0] != 2 or frame[-1] != 13 or frame[-6] != 3:
    raise ValueError("Malformed reader response")
  if frame[-5] != expected_sequence:
    raise ValueError(f"Reader response sequence mismatch: {frame[-5]} != {expected_sequence}")
  if int.from_bytes(frame[-4:-2], "big") != len(frame) - 6:
    raise ValueError("Reader response length mismatch")
  if reduce(xor, frame[:-5], 0) != frame[-2]:
    raise ValueError("Reader response checksum mismatch")
  return frame[1:-6].decode("ascii")


class _ReaderStreamParser:
  """Incrementally decode framed text and announced binary blocks, without I/O."""

  def __init__(self, *, expected_sequence: int = 0) -> None:
    self._pending = bytearray()
    self._binary_size: int | None = None
    self._expected_sequence = expected_sequence

  def feed(self, chunk: bytes) -> list[str | bytes]:
    """Return complete events; retain incomplete frames for the next chunk."""
    self._pending.extend(chunk)
    events: list[str | bytes] = []
    while self._pending:
      if self._binary_size is not None:
        need = self._binary_size + 4
        if len(self._pending) < need:
          break
        block = bytes(self._pending[:need])
        del self._pending[:need]
        events.append(
          _decode_binary_block(block, self._binary_size, expected_sequence=self._expected_sequence)
        )
        self._binary_size = None
        continue
      if self._pending[0] != 2:
        raise ValueError("Unexpected reader stream prefix")
      end = self._pending.find(b"\x03", 1)
      if end >= 0 and end + 6 > 4096:
        raise ValueError("Reader response too long")
      if end < 0 or len(self._pending) < end + 6:
        if len(self._pending) > 4096:
          raise ValueError("Reader response too long")
        break
      frame = bytes(self._pending[: end + 6])
      del self._pending[: end + 6]
      text = _decode(frame, expected_sequence=self._expected_sequence)
      events.append(text)
      match = re.fullmatch(r"([0-9]+),BIN:", text)
      if match:
        size = int(match.group(1))
        if size > 1048576:
          raise ValueError("Binary block too large")
        self._binary_size = size
    return events


class InfiniteM1000Pro:
  """Control the tray of an Infinite M1000 Pro over USB."""

  def __init__(self) -> None:
    """Create a reader; setup must be called before operations."""
    self.usb = USB(
      id_vendor=0x0C47,
      id_product=0x8009,
      human_readable_device_name="Tecan Infinite M1000 Pro",
      read_endpoint_address=0x81,
      write_endpoint_address=0x02,
      packet_read_timeout=1,
      read_timeout=30,
      zero_length_packets=False,
    )
    self._buffer = bytearray()
    self._lock = asyncio.Lock()
    self._ready = False
    self._faulted = False
    self._initialized = False
    self._next_sequence = 0
    self._active_sequence = 0

  async def setup(self) -> None:
    """Connect and initialize internal mechanisms; supervision is required."""
    async with self._lock:
      if self._faulted:
        raise RuntimeError("Reader state is uncertain; assess the instrument before a new session")
      if self._ready:
        return
      self._ready = False
      try:
        await self.usb.setup(empty_buffer=False)
      except BaseException:
        self._faulted = True
        raise
      self._buffer.clear()
      self._next_sequence = 0
      self._active_sequence = 0
      self._ready = True
      logger.warning(
        "Tray opening has produced abnormal stuttering after power-up; do not operate until "
        "initialization and safe movement are understood. Reader-reported position may be unreliable."
      )
      logger.info("Connected to Tecan Infinite M1000 Pro")
      try:
        await self._initialize_locked()
        await self._startup_reference_sequence()
      except BaseException:
        self._ready = False
        self._initialized = False
        self._faulted = True
        raise

  async def stop(self) -> None:
    """Close the USB connection without moving the carrier."""
    async with self._lock:
      self._ready = False
      self._initialized = False
      self._buffer.clear()
      await self.usb.stop()
      logger.info("Disconnected from Tecan Infinite M1000 Pro")

  async def _write_command(self, command: str) -> None:
    """Write one command using the next RDNet sequence value."""
    self._active_sequence = self._next_sequence
    self._next_sequence = (self._next_sequence + 1) % 255
    await self.usb.write(_encode(command, sequence=self._active_sequence))

  async def _receive(self, timeout: float = 30) -> str:
    """Read one complete response, retaining any subsequent frame."""
    deadline = time.monotonic() + timeout
    while True:
      if time.monotonic() >= deadline:
        raise TimeoutError("Timed out waiting for reader response")
      if self._buffer and self._buffer[0] != 2:
        raise ValueError("Unexpected reader response prefix")
      end = self._buffer.find(b"\x03", 1)
      if end >= 0 and end + 6 > 4096:
        raise ValueError("Reader response too long")
      if end >= 0 and len(self._buffer) >= end + 6:
        frame = bytes(self._buffer[: end + 6])
        del self._buffer[: end + 6]
        response = _decode(frame, expected_sequence=self._active_sequence)
        if response.startswith(("MSG", "LOG:")):
          logger.info("Reader notification: %s", response)
          continue
        return response
      if len(self._buffer) > 4096:
        raise ValueError("Reader response too long")
      remaining = max(1, math.ceil(deadline - time.monotonic()))
      self._buffer.extend(await self.usb.read(timeout=remaining))

  async def initialize(self) -> None:
    """Initialize internal mechanisms once per connection; this can move the instrument.

    Only call with a clear instrument and a supervising operator. Draining the input does
    not establish that a prior interrupted operation has completed or that motion is safe.
    """
    async with self._lock:
      await self._initialize_locked()

  async def _initialize_locked(self) -> None:
    """Initialize once while holding the reader's session lock."""
    if not self._ready:
      raise RuntimeError("Reader is not ready; call setup first")
    if self._initialized:
      return
    try:
      await self.usb.drain(timeout=0.1, max_duration=2)
      await self._write_command("QQ")
      status = await self._receive()
      if status != "ST":
        raise RuntimeError(f"Reader not in standby: {status!r}")
      if self._buffer:
        raise RuntimeError("Unexpected pending input after standby response")
      await self._write_command("INIT FORCE")
      status = await self._receive()
      if not re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
        raise RuntimeError(f"Unexpected initialization acknowledgment: {status!r}")
      status = await self._receive(timeout=150)
      if status != "ST":
        raise RuntimeError(f"Unexpected initialization completion: {status!r}")
      if self._buffer:
        raise RuntimeError("Unexpected pending input after initialization completion")
      self._initialized = True
      logger.warning("Reader reported initialization complete; physical safety is not verified")
    except BaseException:
      self._ready = False
      self._faulted = True
      logger.exception("Initialization failed; reader state is uncertain")
      raise

  async def _startup_reference_sequence(self) -> None:
    """Replay the capability and calibration reads observed during i-control startup.

    Responses are validated and discarded; they are not used as calibration inputs.
    This device-specific sequence is experimental and may change instrument modes.
    """
    optics = ("WAVELENGTH", "USAGE", "NAME", "BANDWIDTH", "ATTENUATION", "DESCRIPTION")
    commands = [
      "?CONFIG TEMPERATURE,INTERVAL",
      "#FUNCTIONS",
      "#MODULE ADDRESS",
      "?VERSION MAI",
      "?VERSION MEX",
      "?VERSION MEM",
      "?VERSION ABS",
      "?VERSION TCAN",
      "MODE FI.TOP",
      "?CONFIG CHECK,FLASHCOUNT",
      *(f"#EMISSION {item}" for item in optics),
      *(f"#EXCITATION {item}" for item in optics),
      "#TIME INTEGRATION",
      "#TIME LAG",
      "#TIME READDELAY",
      "#GAIN VALUE",
      "#BEAM DIAMETER",
      "#READS SPEED",
      "#READS NUMBER",
      "#POSITION FIL,Z",
      "MODE ABS",
      "?CONFIG CHECK,FLASHCOUNT",
      *(f"#EXCITATION {item}" for item in optics),
      "#TIME READDELAY",
      "#READS SPEED",
      "#READS NUMBER",
      "#BEAM DIAMETER",
      "#SHAKING MODE",
    ]
    for mode in ("ORBITAL", "LINEAR", "DOUBLE"):
      commands.extend(
        (f"SHAKING MODE={mode}", "#SHAKING AMPLITUDE", "#SHAKING FREQUENCY", "#SHAKING TIME")
      )
    commands.extend(
      (
        "#TEMPERATURE PLATE",
        "?CONFIG CHECK,HOURCOUNTER",
        "?CONFIG CHECK,FLASHCOUNT",
        "?VERSION MAI",
        "QQ",
        "#FUNCTIONS",
        "?VERSION",
        "#MEMORY MAI",
        "?MEMORY MAI,0,8",
        "#MEMORY MAI",
        "?MEMORY MAI,8,128",
        "?MEMORY MAI,136,31",
      )
    )
    for module in ("EXCORR", "EMCORR"):
      commands.extend(
        f"?CONFIG {module} {offset} {size}"
        for offset, size in ((0, 1000), (1000, 1000), (2000, 1000), (3000, 204))
      )
    for module in ("MEX", "MEM"):
      commands.extend(f"SIBUS {module} GSE 4 {index}" for index in range(6))
    for command in commands:
      if self._buffer:
        raise RuntimeError("Unexpected pending input during startup")
      await self._write_command(command)
      if command.startswith(("?MEMORY ", "?CONFIG EXCORR ", "?CONFIG EMCORR ")):
        parser = _ReaderStreamParser(expected_sequence=self._active_sequence)
        events: list[str | bytes] = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
          chunk = await self.usb.read(timeout=max(1, math.ceil(deadline - time.monotonic())))
          events.extend(parser.feed(chunk))
          if "ST" in events:
            if (
              len(events) != 3
              or not isinstance(events[0], str)
              or not isinstance(events[1], bytes)
              or events[2] != "ST"
              or events[0] != f"{len(events[1])},BIN:"
              or parser._pending
              or parser._binary_size is not None
            ):
              raise ValueError(f"Unexpected startup binary response to {command!r}")
            break
        else:
          raise TimeoutError(f"Timed out waiting for {command!r}")
      else:
        response = await self._receive(timeout=60)
        if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", response):
          response = await self._receive(timeout=60)
        if command.startswith("MODE ") or command == "QQ":
          valid = response == "ST"
        elif command.startswith("SHAKING MODE="):
          valid = response == "+"
        else:
          valid = bool(response) and not response.startswith(("ER", "BY", "ST"))
        if not valid or self._buffer:
          raise RuntimeError(f"Unexpected startup response to {command!r}: {response!r}")

  async def request_instrument_status(self) -> Literal["standby", "busy"]:
    """Query whether the reader reports standby or a busy state."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader must be connected")
      try:
        if self._buffer:
          raise RuntimeError("Unexpected pending input before status query")
        await self._write_command("QQ")
        status = await self._receive()
        if self._buffer:
          raise RuntimeError("Unexpected pending input after status query")
        if status == "ST":
          return "standby"
        if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", status):
          return "busy"
        raise RuntimeError(f"Unknown reader status: {status!r}")
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Reader status query failed; connection state is uncertain")
        raise

  async def _position(self) -> str:
    """Query the carrier's reported logical position."""
    await self._write_command("?ABSOLUTE MTP,POS")
    return await self._receive()

  async def _move(self, target: Literal["IN", "OUT"]) -> None:
    """Move to a known state and verify the instrument-reported position."""
    async with self._lock:
      if not self._ready:
        raise RuntimeError("Reader is not ready; call setup first")
      if not self._initialized:
        raise RuntimeError("Reader is not initialized; call initialize first")
      try:
        position = await self._position()
        if position == target:
          return
        if position not in ("IN", "OUT"):
          raise RuntimeError(f"Unexpected carrier position {position!r}; refusing to move")
        logger.info("Moving carrier to %s", target)
        await self._write_command(f"ABSOLUTE MTP,{target}")
        status = await self._receive()
        if re.fullmatch(r"\+?BY(?:#.[0-9]+)?", status):
          status = await self._receive()
        if status != "ST":
          raise RuntimeError(f"Unexpected movement status: {status!r}")
        position = await self._position()
        if position != target:
          raise RuntimeError(f"Carrier reports {position!r}, not {target!r}")
        logger.info("Reader reports carrier %s", target)
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Reader operation failed; connection state is uncertain")
        raise

  async def _control_command(self, command: str, accepted: tuple[str, ...]) -> None:
    """Send one configuration or positioning command and validate its response."""
    if self._buffer:
      raise RuntimeError("Unexpected pending input before command")
    await self._write_command(command)
    response = await self._receive(timeout=60)
    if re.fullmatch(r"\+?BY(?:#T[0-9]+)?", response):
      response = await self._receive(timeout=60)
    if response not in accepted or self._buffer:
      raise RuntimeError(f"Unexpected response to {command!r}: {response!r}")

  async def _temperature_query(self, field: Literal["CURRENT", "TARGET", "STATUS"]) -> str:
    """Read one temperature control field without changing state."""
    if self._buffer:
      raise RuntimeError("Unexpected pending input before temperature query")
    await self._write_command(f"?TEMPERATURE PLATE,{field}")
    response = await self._receive()
    if self._buffer:
      raise RuntimeError("Unexpected pending input after temperature query")
    return response

  async def request_current_temperature(self) -> float:
    """Read the current plate temperature in degrees Celsius."""
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      try:
        response = await self._temperature_query("CURRENT")
        if not re.fullmatch(r"-?[0-9]+", response):
          raise ValueError(f"Unexpected temperature response: {response!r}")
        return int(response) / 10
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Temperature query failed; reader state is uncertain")
        raise

  async def set_temperature(self, temperature: float) -> None:
    """Set and enable plate temperature, limited to 20–40 °C.

    The reader reports temperatures in tenths of a degree. This operation is
    experimental and has not been verified on the M1000 Pro.
    """
    if not math.isfinite(temperature) or not 20 <= temperature <= 40:
      raise ValueError("Temperature must be between 20 and 40 °C")
    raw = round(temperature * 10)
    if not math.isclose(temperature * 10, raw, abs_tol=1e-8):
      raise ValueError("Temperature must be in increments of 0.1 °C")
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      try:
        target = await self._temperature_query("TARGET")
        status = await self._temperature_query("STATUS")
        if not re.fullmatch(r"-?[0-9]+", target) or status not in ("ON", "OFF"):
          raise RuntimeError("Unexpected temperature control state")
        if int(target) != raw:
          await self._control_command(f"TEMPERATURE PLATE,TARGET={raw}", ("ST", "+"))
          if await self._temperature_query("TARGET") != str(raw):
            raise RuntimeError("Temperature target was not confirmed")
        if status != "ON":
          await self._control_command("TEMPERATURE PLATE=ON", ("ST", "+"))
          if await self._temperature_query("STATUS") != "ON":
            raise RuntimeError("Temperature control was not confirmed ON")
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Temperature operation failed; reader state is uncertain")
        raise

  async def stop_temperature_control(self) -> None:
    """Switch off plate temperature control if it is currently on."""
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      try:
        status = await self._temperature_query("STATUS")
        if status not in ("ON", "OFF"):
          raise RuntimeError("Unexpected temperature control state")
        if status == "ON":
          await self._control_command("TEMPERATURE PLATE=OFF", ("ST", "+"))
          if await self._temperature_query("STATUS") != "OFF":
            raise RuntimeError("Temperature control was not confirmed OFF")
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Temperature operation failed; reader state is uncertain")
        raise

  async def shake_linear_reference(self) -> None:
    """Replay the captured linear-shaking reference; not hardware verified.

    The fixed reference is 10 seconds, 2 mm and 654 rpm. Only the captured
    positioning state is accepted. This operation moves the carrier and
    requires an explicitly approved, supervised run. Orbital and double
    shaking are not implemented.
    """
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      try:
        if self._buffer or await self._position() != "IN" or self._buffer:
          raise RuntimeError("Carrier must be reported IN before shaking")
        for command in (
          "SHAKING MODE=LINEAR",
          "SHAKING TIME=10",
          "SHAKING AMPLITUDE=2000",
          "SHAKING FREQUENCY=109",
        ):
          await self._control_command(command, ("+",))
        for command, expected in (
          ("?STEPABS MTP,X", "1864"),
          ("?STEPABS MTP,Y", "0"),
          ("?CONFIG OFFSET,MTP.HEAT.X", "2450"),
          ("?CONFIG OFFSET,MTP.HEAT.Y", "1000"),
        ):
          if self._buffer:
            raise RuntimeError("Unexpected pending input before shaking query")
          await self._write_command(command)
          response = await self._receive(timeout=60)
          if response != expected or self._buffer:
            raise RuntimeError(f"Unexpected reference positioning response to {command!r}")
        await self._control_command("STEPABS MTP,Y=0", ("ST",))
        await self._control_command("SHAKING ON", ("ST",))
        await self._control_command("STEPABS MTP,Y=0", ("ST",))
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Linear shaking failed; reader state is uncertain")
        raise

  async def _absorbance_burst(self, command: str, expected_type: int) -> list[bytes]:
    """Receive a complete bounded binary measurement burst."""
    if self._buffer:
      raise RuntimeError("Unexpected pending input before binary command")
    await self._write_command(command)
    parser = _ReaderStreamParser(expected_sequence=self._active_sequence)
    events: list[str | bytes] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
      chunk = await self.usb.read(timeout=max(1, math.ceil(deadline - time.monotonic())))
      logger.debug("Reader binary input: %s", bytes(chunk).hex())
      events.extend(parser.feed(chunk))
      if len(events) > 128:
        raise ValueError("Too many binary burst events")
      if "ST" in events:
        if events[-1] != "ST" or parser._pending or parser._binary_size is not None:
          raise ValueError("Unexpected data after binary burst")
        return _validate_absorbance_burst(events, expected_type=expected_type)
    raise TimeoutError("Timed out waiting for absorbance burst")

  async def read_absorbance(
    self,
    plate: Plate,
    wells: Sequence[Well] | None = None,
    wavelength: int = 600,
    *,
    plate_definition: str,
  ) -> AbsorbanceResult:
    """Read the GRE96ft reference method at 600 nm; experimental and unverified.

    The plate must be the uncovered Greiner GRE96ft plate, loaded and already IN.
    The profile uses fixed motor coordinates specific to the reference method.
    Setup connects and initializes the instrument. This method moves internal
    mechanisms and must only be used with an operator present. Omitted wells
    select the full plate; an empty or partial selection is not supported.
    """
    if plate_definition != "GRE96ft" or wavelength != 600:
      raise ValueError("Only the GRE96ft 600 nm reference method is supported")
    if (plate.num_items_x, plate.num_items_y) != (12, 8):
      raise ValueError("The GRE96ft method requires 96 wells")
    if wells is not None and (
      len(wells) != 96
      or any(well.parent is not plate for well in wells)
      or len({well.get_identifier() for well in wells}) != 96
    ):
      raise ValueError("The GRE96ft reference method requires all 96 wells of the plate")
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      start = datetime.now()
      try:
        if self._buffer:
          raise RuntimeError("Unexpected pending input before measurement")
        position = await self._position()
        if position != "IN" or self._buffer:
          raise RuntimeError(f"Plate carrier must be IN; reader reports {position!r}")
        commands = [
          ("MODE ABS", ("ST",)),
          ("EXCITATION CLEAR", ("+",)),
          ("TIME CLEAR", ("+",)),
          ("GAIN CLEAR", ("+",)),
          ("READS CLEAR", ("+",)),
          ("POSITION CLEAR", ("+",)),
          ("FLASH ENERGY=LOW", ("ST",)),
          ("EXCITATION 0,ABS,6000,50,0", ("+",)),
          ("TIME 0,READDELAY=0", ("+",)),
          ("READS 0,NUMBER=25", ("+",)),
          ("READS 0,SPEED=2500", ("+",)),
          ("POSITION 0,Z=17514", ("ST",)),
          ("SCAN DIRECTION=ALTUP", ("+",)),
          ("RATIO LABELS=1", ("+",)),
          ("BEAM DIAMETER=800", ("ST",)),
          ("EXCITATION 1,ABS,6000,50,0", ("+",)),
          ("TIME 1,READDELAY=0", ("+",)),
          ("READS 1,NUMBER=25", ("+",)),
          ("READS 1,SPEED=2500", ("+",)),
          ("POSITION 1,Z=17514", ("ST",)),
        ]
        for command, accepted in commands:
          await self._control_command(command, accepted)
        prepared = self._absorbance_burst("PREPARE REF", 1024)
        calibration_blocks = await prepared
        if len(calibration_blocks) != 1:
          raise ValueError("Unexpected number of calibration blocks")
        calibration = _decode_absorbance_prepare_4channel(calibration_blocks[0])
        packets: list[_AbsorbanceRawPacket4Channel] = []
        for y, x1, x2 in ((11240, 14380, 113380), (47240, 113380, 14380)):
          await self._control_command(f"ABSOLUTE MTP,Y={y}", ("ST",))
          await self._control_command(f"ABSOLUTE MTP,X={x1},Y={y}", ("ST",))
          blocks = await self._absorbance_burst(f"SCANX {x1},{x2},12", 1040)
          packets.extend(_decode_absorbance_data_4channel(block) for block in blocks)
        await self._control_command("TERMINATE", ("ST",))
        return _assemble_full_plate_absorbance(
          plate, calibration, packets, wavelength, start, datetime.now()
        )
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Absorbance operation failed; reader state is uncertain")
        raise

  async def read_absorbance_384_reference(
    self, plate: Plate, *, plate_definition: str
  ) -> AbsorbanceResult:
    """Read A1:H2 of the uncovered GRE384ft reference plate at 600 nm.

    This fixed-geometry method is experimental and has not been hardware tested.
    The carrier must already be IN, and the physical GRE384ft plate uncovered.
    Other plate geometries and well selections are not supported.
    """
    if plate_definition != "GRE384ft" or (plate.num_items_x, plate.num_items_y) != (24, 16):
      raise ValueError("Only the GRE384ft 384-well reference profile is supported")
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      start = datetime.now()
      try:
        if self._buffer or await self._position() != "IN" or self._buffer:
          raise RuntimeError("Carrier must be reported IN before measurement")
        commands = [
          ("MODE ABS", ("ST",)),
          ("EXCITATION CLEAR", ("+",)),
          ("TIME CLEAR", ("+",)),
          ("GAIN CLEAR", ("+",)),
          ("READS CLEAR", ("+",)),
          ("POSITION CLEAR", ("+",)),
          ("FLASH ENERGY=LOW", ("ST",)),
          ("EXCITATION 0,ABS,6000,50,0", ("+",)),
          ("TIME 0,READDELAY=0", ("+",)),
          ("READS 0,NUMBER=25", ("+",)),
          ("READS 0,SPEED=2500", ("+",)),
          ("POSITION 0,Z=17136", ("ST",)),
          ("SCAN DIRECTION=ALTUP", ("+",)),
          ("RATIO LABELS=1", ("+",)),
          ("BEAM DIAMETER=800", ("ST",)),
          ("EXCITATION 1,ABS,6000,50,0", ("+",)),
          ("TIME 1,READDELAY=0", ("+",)),
          ("READS 1,NUMBER=25", ("+",)),
          ("READS 1,SPEED=2500", ("+",)),
          ("POSITION 1,Z=17136", ("ST",)),
        ]
        for command, accepted in commands:
          await self._control_command(command, accepted)
        blocks = await self._absorbance_burst("PREPARE REF", 1024)
        if len(blocks) != 1:
          raise ValueError("Unexpected calibration blocks")
        calibration = _decode_absorbance_prepare_4channel(blocks[0])
        if calibration.ex_wavelength_raw != 6000:
          raise ValueError("Unexpected calibration wavelength")
        wells: list[Well] = []
        values: list[float] = []
        for scan, (y, x1, x2) in enumerate(((8990, 12130, 16630), (13490, 16630, 12130))):
          await self._control_command(f"ABSOLUTE MTP,Y={y}", ("ST",))
          await self._control_command(f"ABSOLUTE MTP,X={x1},Y={y}", ("ST",))
          blocks = await self._absorbance_burst(f"SCANX {x1},{x2},2", 1040)
          if len(blocks) != 2:
            raise ValueError("Expected two scan points")
          for point, block in enumerate(blocks):
            packet = _decode_absorbance_data_4channel(block)
            if packet.number_of_packets is not None or len(packet.items) != 1:
              raise ValueError("Unexpected scan packet layout")
            column = point + 1 if scan == 0 else 2 - point
            for channel in range(4):
              wells.append(plate.get_item(f"{'ABCDEFGH'[2 * channel + scan]}{column}"))
              values.append(_calculate_absorbance_4channel(calibration, packet, channel=channel))
        await self._control_command("TERMINATE", ("ST",))
        return _format_absorbance_result(plate, wells, values, 600, start, datetime.now())
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("384-well absorbance failed; reader state is uncertain")
        raise

  async def read_fluorescence_reference(
    self, plate: Plate, *, plate_definition: str
  ) -> FluorescenceResult:
    """Read the uncovered GRE96ft fluorescence reference; hardware unverified.

    This fixed profile scans all wells at 230 nm excitation and 280 nm emission,
    with 5 nm bandwidth, gain 100 and 50 flashes. It moves internal mechanisms.
    """
    if plate_definition != "GRE96ft" or (plate.num_items_x, plate.num_items_y) != (12, 8):
      raise ValueError("Only the GRE96ft 96-well fluorescence reference is supported")
    async with self._lock:
      if not self._ready or not self._initialized:
        raise RuntimeError("Reader must be connected and initialized")
      start = datetime.now()
      try:
        if self._buffer or await self._position() != "IN" or self._buffer:
          raise RuntimeError("Carrier must be reported IN before measurement")
        commands = [
          ("MODE FI.TOP", ("ST",)),
          ("EMISSION CLEAR", ("+",)),
          ("EXCITATION CLEAR", ("+",)),
          ("TIME CLEAR", ("+",)),
          ("GAIN CLEAR", ("+",)),
          ("READS CLEAR", ("+",)),
          ("POSITION CLEAR", ("+",)),
          ("FLASH ENERGY=LOW", ("ST",)),
        ]
        for label in (0, 1):
          commands.extend(
            [
              (f"EXCITATION {label},FI,2300,50,0", ("+",)),
              (f"EMISSION {label},FI,2800,50,0", ("+",)),
              (f"TIME {label},INTEGRATION=20", ("+",)),
              (f"TIME {label},LAG=0", ("+",)),
              (f"TIME {label},READDELAY=0", ("+",)),
              (f"GAIN {label},VALUE=100", ("+",)),
              (f"READS {label},NUMBER=50", ("+",)),
              (f"READS {label},SPEED=2500", ("+",)),
              (f"POSITION {label},Z=20000", ("ST",)),
            ]
          )
          if label == 0:
            commands.extend(
              [
                ("SCAN DIRECTION=UP", ("+",)),
                ("RATIO LABELS=1", ("+",)),
                ("BEAM DIAMETER=2500", ("ST",)),
              ]
            )
        for command, accepted in commands:
          await self._control_command(command, accepted)
        blocks = await self._absorbance_burst("PREPARE REF", 512)
        if len(blocks) != 1 or len(blocks[0]) != 18:
          raise ValueError("Invalid fluorescence calibration packet")
        ex, _, _, gain, dark, _, ref_dark, ref_bright = struct.unpack(">HI6H", blocks[0])
        if ex != 2300 or gain != 100 or ref_bright <= ref_dark:
          raise ValueError("Invalid fluorescence calibration")
        values: dict[str, float] = {}
        for row in range(8):
          y = 11240 + 9000 * row
          x1, x2 = (14380, 113380) if row % 2 == 0 else (113380, 14380)
          await self._control_command(f"ABSOLUTE MTP,Y={y}", ("ST",))
          await self._control_command(f"ABSOLUTE MTP,X={x1},Y={y}", ("ST",))
          blocks = await self._absorbance_burst(f"SCANX {x1},{x2},12", 528)
          if len(blocks) != 12:
            raise ValueError("Expected 12 fluorescence points")
          for index, block in enumerate(blocks):
            if len(block) != 16:
              raise ValueError("Invalid fluorescence scan packet length")
            label, ex, em, _, _, count, reference = struct.unpack(">HHHIHHH", block)
            if (label, ex, em) != (1, 2300, 2800) or count >= 65535 or reference >= 65535:
              raise ValueError("Invalid or saturated fluorescence signal")
            if reference - ref_dark < 500:
              raise ValueError("Fluorescence reference signal too low")
            column = index + 1 if row % 2 == 0 else 12 - index
            well = plate.get_item(f"{'ABCDEFGH'[row]}{column}").get_identifier()
            values[well] = (count - dark) * (ref_bright - ref_dark) / (reference - ref_dark)
        await self._control_command("TERMINATE", ("ST",))
        return FluorescenceResult(
          data=values,
          excitation_wavelength=230,
          excitation_bandwidth=5,
          emission_wavelength=280,
          emission_bandwidth=5,
          temperature=None,
          start_timestamp=start,
          end_timestamp=datetime.now(),
        )
      except BaseException:
        self._ready = False
        self._faulted = True
        logger.exception("Fluorescence operation failed; reader state is uncertain")
        raise

  async def open(self) -> None:
    """Move the plate carrier out, unless it is already reported out."""
    await self._move("OUT")

  async def close(self) -> None:
    """Move the plate carrier in, unless it is already reported in."""
    await self._move("IN")
