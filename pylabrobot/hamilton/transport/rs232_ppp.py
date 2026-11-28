"""Hamilton's HOI over a serial line (USB virtual COM port), as Hamilton's ComLink does it.

Hamilton instruments on USB (the MPE2, for one) carry the same packets as over TCP, framed by
``Rs232PppInterface`` (``Hamilton.Components.TransportLayer.Protocols.dll``):

- A frame is ``0xAA``, the escaped packet, ``0xCC``. Inside, 0xAA, 0xBB and 0xCC are sent as 0xBB
  followed by the byte's complement.
- A packet is a 12-byte header and a payload. The header holds the length (u32, header included),
  flags (u8; bit 0 set for data, clear for link frames), version (u8), the payload's CRC (u16),
  session id (u8), protocol (u8: 0 for data, 0xFF for link frames) and the header's own CRC (u16,
  taken with these two bytes zeroed), little-endian. The CRCs are CRC-16/XMODEM.
- Link frames carry 8 bytes: type, version, session id, 0, and the shortest and longest keep-alive
  interval in ms (u16 each). The instrument sends "discover" (type 0) about once a second with a
  new session id; the host answers "accept" (1) with that session and the interval it wants; the
  instrument answers "confirm" (2), and the link is up. The host then sends a keep-alive (5) within
  the interval, which the instrument acknowledges (6). Type 3 is a refusal, 4 a close.

``HamiltonSerialLink`` gives PLR's Hamilton TCP session the stream it expects: what is written goes
out as one data frame, and the data frames' payloads are read back as one stream. The port is
PLR's ``pylabrobot.io.serial.Serial``.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import math
import random
import struct
import sys
import time
from typing import Any, Dict, Optional, Tuple

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

START, END, ESCAPE = 0xAA, 0xCC, 0xBB
HEADER = struct.Struct("<IBBHBBH")
LINK_PROTOCOL = 0xFF
DATA_PROTOCOL = 0
VERSION = 0x10

DISCOVER, ACCEPT, CONFIRM, REFUSE, CLOSE, KEEP_ALIVE, KEEP_ALIVE_ACK = range(7)

# Serial protocol 0 (SerialProtocol0Frame), inside each data frame: protocol (the IP protocol: 7
# connection, 6 HARP), flags, version, response code, length (u32), sequence number (u32), data.
# Over serial it stands in for the IP header: the data is the IP packet's payload.
SP0_HEADER = struct.Struct("<BBBBII")
SP0_SYN, SP0_FIN, SP0_ACK, SP0_NAK = 1, 2, 4, 8
IP_VERSION_BYTE = 0x30  # what PLR writes and expects over TCP (major 3, minor 0)


def crc16(data: bytes) -> int:
  """CRC-16/XMODEM: polynomial 0x1021, starting at 0 (Rs232PppPacket.CalculateCrc)."""
  crc = 0
  for b in data:
    crc ^= b << 8
    for _ in range(8):
      crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
    crc &= 0xFFFF
  return crc


def encode_frame(payload: bytes, data: bool, version: int, session: int, protocol: int) -> bytes:
  """One frame, escaped and delimited."""
  header = HEADER.pack(
    12 + len(payload), 1 if data else 0, version, crc16(payload), session, protocol, 0
  )
  header = header[:10] + struct.pack("<H", crc16(header[:10] + b"\0\0"))
  out = bytearray([START])
  for b in header + payload:
    if b in (START, END, ESCAPE):
      out += bytes([ESCAPE, (~b) & 0xFF])
    else:
      out.append(b)
  out.append(END)
  return bytes(out)


def decode_frame(body: bytes) -> Optional[dict]:
  """A frame's fields from what stood between its delimiters, or None if it doesn't check out."""
  raw = bytearray()
  i = 0
  while i < len(body):
    if body[i] == ESCAPE and i + 1 < len(body):
      raw.append((~body[i + 1]) & 0xFF)
      i += 2
    else:
      raw.append(body[i])
      i += 1
  if len(raw) < HEADER.size:
    logger.debug("frame refused: %d bytes, shorter than a header", len(raw))
    return None
  length, flags, version, data_crc, session, protocol, header_crc = HEADER.unpack(raw[:12])
  payload = bytes(raw[12:])
  if length != len(raw) or crc16(payload) != data_crc:
    logger.debug("frame refused: length %d for %d bytes, or its data CRC", length, len(raw))
    return None
  if crc16(bytes(raw[:10]) + b"\0\0") != header_crc:
    logger.debug("frame refused: header CRC")
    return None
  return dict(
    data=bool(flags & 1), version=version, session=session, protocol=protocol, payload=payload
  )


def _sp0_flags(flags: int) -> str:
  """SP0 flags as text, for the logs."""
  names = ((SP0_SYN, "SYN"), (SP0_FIN, "FIN"), (SP0_ACK, "ACK"), (SP0_NAK, "NAK"))
  return "|".join(name for bit, name in names if flags & bit) or "-"


class HamiltonSerialLink:
  """A Hamilton instrument's serial link, read and written like PLR's ``Socket``.

  A task on the event loop reads the port (through ``pylabrobot.io.serial.Serial``), answers the
  link's frames and acknowledges the instrument's messages. Each complete message it receives goes
  to the stream ``read_exact`` reads from.

  The keep-alives are sent from the event loop, so a loop blocked by synchronous code sends none
  meanwhile; the MPE2 keeps the link through a loop blocked 12 s. The instrument
  acknowledges each keep-alive; when it stops doing so for ``_SILENT_KEEP_ALIVES`` of them, the
  link is taken as lost.

  Args:
    port: the serial port, such as "COM4"; None to find it by ``vid`` and ``pid`` (and
      ``serial_number``) at each ``setup()``, so a re-plugged instrument is found on its new port.
    vid, pid: the USB serial chip's ids, to find the port.
    serial_number: the USB serial number of the one wanted, when several are plugged in.
    human_readable_device_name: the instrument's name, for the logs and errors.
    baudrate: Hamilton's driver uses 921600 for the MPE2.
    keep_alive_ms: the keep-alive interval asked for; Hamilton's default is 1000 ms.
    link_timeout: how long to wait for the instrument to come up, in seconds.
    read_timeout: how long ``read_exact`` waits when it is given no timeout, in seconds.
  """

  # How long one read of the port waits for its first byte, in seconds.
  _POLL = 0.02
  # How long a write to the port may take, in seconds.
  _WRITE_TIMEOUT = 2.0
  # How long ``stop`` waits for the reading task and the port to close, in seconds.
  _STOP_TIMEOUT = 2.0
  # How long one way of agreeing a session is given before the other is tried, in seconds.
  _HANDSHAKE_TIMEOUT = 1.5
  # Keep-alives sent without an acknowledgement before the link is taken as lost.
  _SILENT_KEEP_ALIVES = 6

  def __init__(
    self,
    port: Optional[str] = None,
    *,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    serial_number: Optional[str] = None,
    human_readable_device_name: str = "Hamilton instrument",
    baudrate: int = 921600,
    keep_alive_ms: int = 1000,
    link_timeout: float = 5.0,
    read_timeout: float = 30.0,
  ):
    self.io = Serial(
      human_readable_device_name,
      port=port,
      vid=vid,
      pid=pid,
      serial_number=serial_number,
      baudrate=baudrate,
      timeout=self._POLL,
      write_timeout=self._WRITE_TIMEOUT,
      # As Hamilton's ComLink opens it.
      dtr=False,
      rts=False,
      # Acknowledgements go out while the next read waits for bytes.
      write_while_reading=True,
    )
    self.keep_alive_ms = keep_alive_ms
    self.link_timeout = link_timeout
    self.read_timeout = read_timeout
    self._reader: Optional[asyncio.Task] = None
    self._reset()

  def _reset(self) -> None:
    """Clear the link's state for a new setup."""
    self._stopping = False
    self._write_lock = asyncio.Lock()
    self._send_lock = asyncio.Lock()
    self._rx = bytearray()
    self._rx_changed = asyncio.Event()
    self._session: Optional[int] = None
    self._own_session: Optional[int] = None
    # How the session is being agreed: ("answered", time) after accepting the instrument's
    # discover, ("discovered", time) after sending our own; None when neither is under way.
    self._handshake: Optional[Tuple[str, float]] = None
    self._ack_changed = asyncio.Event()
    self._outstanding: Optional[int] = None
    self._ack: Optional[Tuple[int, int]] = None
    self._last_rx_sequence: Optional[int] = None
    self._rx_message = bytearray()
    self._up = asyncio.Event()
    self._error: Optional[BaseException] = None
    self._unanswered_keep_alives = 0

  @property
  def port(self) -> str:
    """The serial port, once found."""
    return self.io._port or "(not found yet)"

  @property
  def _unique_id(self) -> str:
    """The link's name in PLR's logs."""
    return f"serial:{self.port}"

  @property
  def connected(self) -> bool:
    """Whether the link to the instrument is up."""
    return self._up.is_set()

  async def setup(self) -> None:
    """Open the port and wait for the instrument to agree a link."""
    if self._reader is not None or self.io.is_open:
      await self.stop()
    self._reset()
    try:
      await self.io.setup()
    except Exception as exc:
      if isinstance(exc, OSError) and exc.errno == errno.EACCES and sys.platform != "win32":
        raise PermissionError(
          f"The {self.io.human_readable_device_name} is on {self.port}, but this user may not open "
          "it: add the user to the port's group (`sudo usermod -aG dialout $USER` on Linux), then "
          "log in again."
        ) from exc
      raise
    self._reader = asyncio.create_task(self._run(), name=self._unique_id)
    try:
      await asyncio.wait_for(self._wait_up(), self.link_timeout)
    except asyncio.TimeoutError:
      await self.stop()
      raise ConnectionError(
        f"No Hamilton link on {self.port} within {self.link_timeout} s."
      ) from None
    except BaseException:
      await self.stop()
      raise
    logger.info("Hamilton serial link up on %s, session %d", self.port, self._session)

  async def _wait_up(self) -> None:
    """Wait until the link is up, raising if it fails first."""
    while not self._up.is_set():
      self._raise_if_down()
      changed = self._rx_changed
      changed.clear()
      await changed.wait()

  async def stop(self) -> None:
    """Close the link and the port. A pending read or write raises ``ConnectionError``.

    It always returns: the reading task and the port's closing are waited for at most
    ``_STOP_TIMEOUT`` seconds each. Calling it twice, or before ``setup`` has finished, is harmless.
    """
    self._stopping = True
    was_up = self._up.is_set() and self._session is not None
    self._up.clear()
    self._wake()
    reader, self._reader = self._reader, None
    if reader is not None and reader is not asyncio.current_task():
      try:
        await asyncio.wait_for(asyncio.shield(reader), self._STOP_TIMEOUT)
      except asyncio.TimeoutError:
        logger.warning("The reading task on %s didn't end; closing the port anyway", self.port)
        reader.cancel()
      except (Exception, asyncio.CancelledError):  # its error is already kept in self._error
        if not reader.done():
          raise
    if not self.io.is_open:
      return
    if was_up:
      try:
        # Header version 0 routes a frame to the discovery handling (types 0 to 4); 0x10 to the
        # keep-alive handling.
        await asyncio.wait_for(self._send_link(CLOSE), self._STOP_TIMEOUT)
      except Exception as exc:
        logger.debug("No close frame sent on %s: %s", self.port, exc)
    try:
      await asyncio.wait_for(self.io.stop(), self._STOP_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - stop always returns
      logger.warning("Closing %s failed: %s", self.port, exc)

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Send PLR's IP packets, each as one acknowledged serial protocol 0 frame. ``timeout`` is
    not used: each frame waits at most 5 times 500 ms for its acknowledgement."""
    if not self._up.is_set() or self._session is None:
      raise ConnectionError(f"The Hamilton link on {self.port} is down.")
    offset = 0
    while offset < len(data):
      if len(data) - offset < 6:
        raise ValueError(f"A packet of {len(data) - offset} bytes is shorter than its header.")
      size, protocol, _version, options_length = struct.unpack_from("<HBBH", data, offset)
      payload = data[offset + 6 + options_length : offset + 2 + size]
      offset += 2 + size
      await self._send_sp0(protocol, payload)

  async def _send_sp0(self, protocol: int, payload: bytes) -> None:
    """Send one message and wait for its acknowledgement, resending every 500 ms up to 5 times
    (SerialProtocol0Interface.Send). One message at a time, as serial protocol 0 has it."""
    async with self._send_lock:
      session = self._session
      if session is None:
        raise ConnectionError(f"The Hamilton link on {self.port} is down.")
      sequence = random.randint(0, 0x7FFFFFFF)
      frame = SP0_HEADER.pack(protocol, SP0_SYN | SP0_FIN, 0, 0, len(payload), sequence) + payload
      logger.debug("-> sp0 protocol %d length %d sequence %08x", protocol, len(payload), sequence)
      self._outstanding = (sequence + len(payload)) & 0xFFFFFFFF
      self._ack = None
      for _ in range(5):
        self._raise_if_down()
        self._ack_changed.clear()
        await self._write(encode_frame(frame, True, VERSION, session, DATA_PROTOCOL))
        if self._ack is None:
          try:
            await asyncio.wait_for(self._ack_changed.wait(), 0.5)
          except asyncio.TimeoutError:
            pass
        ack = self._ack
        if ack is not None:
          flags, code = ack
          if flags & SP0_NAK:
            raise ConnectionError(
              f"The instrument refused a frame on {self.port} (NAK, code {code})."
            )
          return
      self._raise_if_down()
      raise TimeoutError(
        f"No acknowledgement on {self.port} after 5 tries (session {session}, the host's own "
        f"{self._own_session})."
      )

  async def _on_sp0(self, data: bytes) -> None:
    """A serial protocol 0 frame from the instrument: an acknowledgement of ours, or a message
    part to acknowledge and pass on (SerialProtocol0Interface.OnReceiveThread)."""
    if len(data) < SP0_HEADER.size:
      return
    protocol, flags, _version, code, length, sequence = SP0_HEADER.unpack_from(data)
    body = data[SP0_HEADER.size : SP0_HEADER.size + length]
    logger.debug(
      "<- sp0 protocol %d %s code %d length %d sequence %08x",
      protocol,
      _sp0_flags(flags),
      code,
      length,
      sequence,
    )
    if flags & (SP0_ACK | SP0_NAK):
      if sequence == self._outstanding:
        self._ack = (flags, code)
        self._ack_changed.set()
      return
    assert self._session is not None
    duplicate = sequence == self._last_rx_sequence
    ack = SP0_HEADER.pack(
      0, flags | SP0_ACK, 0, 1 if duplicate else 0, 0, (sequence + length) & 0xFFFFFFFF
    )
    await self._write(encode_frame(ack, True, VERSION, self._session, DATA_PROTOCOL))
    if duplicate:
      logger.debug("<- sp0 sequence %08x taken as a duplicate and dropped", sequence)
      return
    self._last_rx_sequence = sequence
    if flags & SP0_SYN:
      self._rx_message = bytearray()
    self._rx_message += body
    if flags & SP0_FIN:
      # Give PLR the IP packet it reads over TCP: size, protocol, version, no options, payload.
      packet = struct.pack("<HBBH", 4 + len(self._rx_message), protocol, IP_VERSION_BYTE, 0)
      self._rx += packet + bytes(self._rx_message)
      self._rx_changed.set()
      self._rx_message = bytearray()

  def _wake(self) -> None:
    """Let whatever waits for a read, an acknowledgement or the link look again."""
    self._rx_changed.set()
    self._ack_changed.set()

  def _fail(self, error: BaseException) -> None:
    """The link is lost: everything waiting raises ``ConnectionError``; ``setup()`` brings it
    back."""
    if self._error is None:
      self._error = error
    self._up.clear()
    self._wake()

  def _raise_if_down(self) -> None:
    """Raise ConnectionError when the link has failed, is stopping or isn't read."""
    if self._error is not None:
      raise ConnectionError(f"The Hamilton link on {self.port} failed: {self._error}")
    if self._stopping:
      raise ConnectionError(f"The Hamilton link on {self.port} was stopped.")
    if self._reader is None or self._reader.done():
      raise ConnectionError(f"The Hamilton link on {self.port} is not reading.")

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """The next bytes of the instrument's data stream.

    It waits at most ``timeout`` seconds (``read_timeout`` when None; an infinite timeout waits
    until the bytes come, the link fails or ``stop`` is called) and raises ``TimeoutError`` after.
    """
    if timeout is None:
      timeout = self.read_timeout
    if math.isinf(timeout):
      return await self._read_exact(num_bytes)
    try:
      return await asyncio.wait_for(self._read_exact(num_bytes), timeout)
    except asyncio.TimeoutError:
      raise TimeoutError(f"No reply on {self.port} within {timeout} s.") from None

  async def _read_exact(self, num_bytes: int) -> bytes:
    """Wait for ``num_bytes`` of received messages and take them."""
    while len(self._rx) < num_bytes:
      self._raise_if_down()
      changed = self._rx_changed
      changed.clear()
      await changed.wait()
    out = bytes(self._rx[:num_bytes])
    del self._rx[:num_bytes]
    return out

  async def _write(self, frame: bytes) -> None:
    """Write one frame to the port, one frame at a time (the port's write timeout bounds it)."""
    async with self._write_lock:
      await self.io.write(frame)

  async def _send_link(self, kind: int, version: int = 0, session: Optional[int] = None) -> None:
    """Send one link control frame (discover, accept, confirm, keep-alive)."""
    if session is None:
      session = self._session or 0
    payload = bytes([kind, VERSION, session, 0]) + struct.pack(
      "<HH", self.keep_alive_ms, self.keep_alive_ms
    )
    await self._write(
      encode_frame(payload, False, version, session if version else 0, LINK_PROTOCOL)
    )

  def _handshake_under_way(self, now: float) -> Optional[str]:
    """Which handshake is under way, if one started recently."""
    if self._handshake is None or now - self._handshake[1] > self._HANDSHAKE_TIMEOUT:
      return None
    return self._handshake[0]

  async def _run(self) -> None:
    """Read the port until ``stop``: frames to their handlers, keep-alives and discovery on time."""
    buffer = bytearray()
    last_keep_alive = 0.0
    last_discover = time.monotonic()  # Listen a second first: the instrument may announce itself.
    try:
      while not self._stopping:
        buffer += await self.io.read_available(4096)
        while True:
          start = buffer.find(START)
          if start < 0:
            buffer.clear()
            break
          end = buffer.find(END, start + 1)
          if end < 0:
            del buffer[:start]
            break
          frame = decode_frame(bytes(buffer[start + 1 : end]))
          del buffer[: end + 1]
          if frame is not None:
            await self._on_frame(frame)
        now = time.monotonic()
        if self._up.is_set():
          if now - last_keep_alive > self.keep_alive_ms / 2000:
            if self._unanswered_keep_alives >= self._SILENT_KEEP_ALIVES:
              self._fail(ConnectionError("the instrument stopped answering keep-alives"))
              logger.warning("Hamilton link on %s: no keep-alive answered", self.port)
              continue
            await self._send_link(KEEP_ALIVE, version=VERSION)
            self._unanswered_keep_alives += 1
            last_keep_alive = now
        elif (
          self._error is None
          and not self._stopping
          and self._handshake_under_way(now) is None
          and now - last_discover > 1.0
        ):
          # The instrument announces itself only while it holds no session; one still holding an
          # old session answers a discover from the host instead.
          self._own_session = random.randint(1, 255)
          self._handshake = ("discovered", now)
          await self._send_link(DISCOVER, session=self._own_session)
          last_discover = now
    except Exception as exc:  # the port went away
      if not self._stopping:
        logger.warning("Hamilton link on %s failed: %s", self.port, exc)
        self._fail(exc)
    finally:
      if not self._stopping:
        self._fail(ConnectionError("the reading task ended"))
      self._up.clear()
      self._wake()

  async def _on_frame(self, frame: Dict[str, Any]) -> None:
    """Handle a received frame: a message for PLR, or the link's own control."""
    payload = frame["payload"]
    if frame["data"]:
      if self._up.is_set():
        await self._on_sp0(payload)
      return
    kind = payload[0] if payload else None
    if kind in (DISCOVER, ACCEPT, CONFIRM) and (
      len(payload) < 8 or self._error is not None or self._stopping
    ):
      # A failed or stopping link isn't brought up again behind PLR's back: setup() does that.
      return
    now = time.monotonic()
    under_way = self._handshake_under_way(now)
    if kind == DISCOVER and not self._up.is_set() and under_way != "discovered":
      # One way at a time: while our own discover waits for its answer, the instrument's is let be.
      self._session = payload[2]
      self._handshake = ("answered", now)
      await self._send_link(ACCEPT)
    elif (
      kind == ACCEPT
      and not self._up.is_set()
      and under_way == "discovered"
      and payload[2] == self._own_session
    ):
      self._session = self._own_session
      await self._send_link(CONFIRM)
      self._come_up("the host's discover")
    elif (
      kind == CONFIRM
      and not self._up.is_set()
      and under_way == "answered"
      and payload[2] == self._session
    ):
      self._come_up("the instrument's discover")
    elif kind == KEEP_ALIVE and self._up.is_set():
      await self._send_link(KEEP_ALIVE_ACK, version=VERSION)
    elif kind == KEEP_ALIVE_ACK:
      self._unanswered_keep_alives = 0
    if kind is not None and kind not in (KEEP_ALIVE, KEEP_ALIVE_ACK):
      logger.debug("<- link frame type %d (link up: %s)", kind, self._up.is_set())
    if kind in (REFUSE, CLOSE):
      what = "refused" if kind == REFUSE else "closed"
      logger.warning("Hamilton link on %s %s by the instrument", self.port, what)
      # A session the instrument dropped can't be trusted (its HOI registration may be gone): fail
      # what waits now, and don't bring up a new session behind PLR's back; setup() does that.
      self._fail(ConnectionError(f"the instrument {what} the link"))

  def _come_up(self, how: str) -> None:
    """Mark the link up once a handshake has finished."""
    self._handshake = None
    self._unanswered_keep_alives = 0
    self._up.set()
    self._wake()
    logger.info("Hamilton link on %s agreed through %s, session %d", self.port, how, self._session)
