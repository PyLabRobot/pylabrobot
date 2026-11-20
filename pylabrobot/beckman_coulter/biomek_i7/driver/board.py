"""One ACE board: its USB link, its sequence numbers, and the replies it sends back.

A Biomek i7 has one ACE board per module (the chassis, and one per arm), each its own USB device
(VID 0x0AB5, PID 0xACE0). A board accepts many requests at once and answers them in whatever
order they finish, so replies are matched to requests by their header, not by order. Moves and
homes answer twice, ACCEPTED when they start and DONE when they finish. The board also sends
messages nobody asked for, with sequence 0; those go to subscribers.

When a Move All fails, the axes that failed answer with the Move All's sequence under their own
device and index, before the Move All's own DONE; those replies are kept with the Move All and
raised with it.
"""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import FrameError, ace_error
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import IN_PACKET_SIZE, FrameReader, pack
from pylabrobot.beckman_coulter.biomek_i7.driver.protocol import (
  SAGIAN_CAN,
  Reply,
  Request,
  Status,
  describe_routine_unsolicited,
  describe_sagian_can,
)
from pylabrobot.io.usb import USB

logger = logging.getLogger(__name__)

ACE_VENDOR_ID = 0x0AB5
ACE_PRODUCT_ID = 0xACE0
ACE_READ_ENDPOINT = 0x81
ACE_WRITE_ENDPOINT = 0x02

UnsolicitedCallback = Callable[[Reply], None]


@dataclass
class _Pending:
  request: Request
  done: "asyncio.Future[Reply]"
  accepted: "asyncio.Future[Reply]"
  related: List[Reply] = field(default_factory=list)


class AceBoard:
  """The link to one ACE board."""

  def __init__(
    self,
    serial_number: Optional[str] = None,
    device_address: Optional[int] = None,
    io: Optional[USB] = None,
    name: str = "Biomek i7 ACE board",
    default_timeout: float = 10.0,
  ):
    """
    Args:
      serial_number: the board's USB serial number. With neither it nor `device_address`, the
        first ACE board found is used.
      device_address: the board's USB address, an alternative to the serial number.
      io: a transport to use instead of opening the USB device; for tests and simulators.
      name: what logs and errors call this board.
      default_timeout: seconds to wait for a request's final reply when the caller gives none.
    """
    self.name = name
    self.usb_serial_number = serial_number
    self.default_timeout = default_timeout
    self.io = io or USB(
      id_vendor=ACE_VENDOR_ID,
      id_product=ACE_PRODUCT_ID,
      human_readable_device_name=name,
      serial_number=serial_number,
      device_address=device_address,
      read_endpoint_address=ACE_READ_ENDPOINT,
      write_endpoint_address=ACE_WRITE_ENDPOINT,
      zero_length_packets=False,
      packet_read_timeout=0.5,
    )
    self._frames = FrameReader()
    self._pending: Dict[Tuple[int, int, int, int], _Pending] = {}
    self._sequence = 0
    self._write_lock = asyncio.Lock()
    self._reader: Optional["asyncio.Task[None]"] = None
    self._subscribers: List[UnsolicitedCallback] = []

  # -- lifecycle -------------------------------------------------------------

  async def setup(self) -> None:
    """Open the board and start reading its replies."""
    await self.io.setup()
    self._frames.reset()
    self._reader = asyncio.create_task(self._read_loop(), name=f"{self.name} reader")

  async def stop(self) -> None:
    """Stop reading, fail every request still waiting, and release the board."""
    if self._reader is not None:
      self._reader.cancel()
      try:
        await self._reader
      except asyncio.CancelledError:
        pass
      self._reader = None
    self._fail_pending(ConnectionError(f"{self.name} stopped"))
    await self.io.stop()

  @property
  def connected(self) -> bool:
    return self._reader is not None and not self._reader.done()

  def subscribe(self, callback: UnsolicitedCallback) -> Callable[[], None]:
    """Call `callback` with every message the board sends unasked. Returns an unsubscribe."""
    self._subscribers.append(callback)
    return lambda: self._subscribers.remove(callback)

  # -- requests --------------------------------------------------------------

  async def send(self, request: Request, timeout: Optional[float] = None) -> Reply:
    """Send one request and return its final reply.

    Raises:
      AceError: if the board reports an error.
      TimeoutError: if no final reply arrives within `timeout` seconds.
    """
    return (await self.send_many([request], timeout=timeout))[0]

  async def send_many(
    self,
    requests: Sequence[Request],
    timeout: Optional[float] = None,
    raise_errors: bool = True,
  ) -> List[Reply]:
    """Send requests together, packed into as few transfers as fit, and return their final
    replies in request order. Every request is waited for before any error is raised; the first
    error in request order is raised.

    Args:
      requests: the requests, sent in this order.
      timeout: seconds to wait for every final reply. Defaults to `default_timeout`.
      raise_errors: when False, replies that report an error are returned instead of raised, for
        a caller that decides on them (such as a move refused because the axis is not homed).

    Raises:
      AceError: if `raise_errors` and the board reports an error for any of them.
      TimeoutError: if any final reply does not arrive within `timeout` seconds.
    """
    if not self.connected:
      raise ConnectionError(f"{self.name} is not set up")
    timeout = self.default_timeout if timeout is None else timeout
    loop = asyncio.get_running_loop()
    entries: List[Tuple[Tuple[int, int, int, int], _Pending]] = []
    messages = []
    for request in requests:
      sequence = self._next_sequence()
      key = (request.device, request.index, sequence, request.opcode)
      entry = _Pending(request, loop.create_future(), loop.create_future())
      self._pending[key] = entry
      entries.append((key, entry))
      messages.append(request.encode(sequence))
    try:
      async with self._write_lock:
        for message in messages:  # one line per message, unframed, as Biomek's `ace_io` logs them
          logger.debug("%s write %s", self.name, message.hex())
        for transfer in pack(messages):
          await self.io.write(transfer)
      done, not_done = await asyncio.wait([e.done for _, e in entries], timeout=timeout)
      if not_done:
        late = [str(e.request) for _, e in entries if not e.done.done()]
        raise TimeoutError(f"{self.name}: no reply within {timeout} s to {', '.join(late)}")
      replies = [replace(e.done.result(), related=tuple(e.related)) for _, e in entries]
      if raise_errors:
        for (_, entry), reply in zip(entries, replies):
          self._raise_for(entry, reply)
      return replies
    finally:
      for key, entry in entries:
        self._pending.pop(key, None)
        for future in (entry.done, entry.accepted):
          if not future.done():
            future.cancel()

  def _raise_for(self, entry: _Pending, reply: Reply) -> None:
    """Raise the reply's error, with the errors of the axes a failed Move All names."""
    if reply.error_code is None:
      return
    error = ace_error(
      reply.error_code, reply.device_error, str(entry.request), reply.device, reply.index
    )
    causes = [r for r in entry.related if r.error_code is not None]
    if causes:
      error.axis_errors = [
        ace_error(
          r.error_code or 0,
          r.device_error,
          f"device 0x{r.device:02X} index {r.index}",
          r.device,
          r.index,
        )
        for r in causes
      ]
      error.args = (str(error) + "; " + "; ".join(str(e) for e in error.axis_errors),)
    raise error

  def _next_sequence(self) -> int:
    """The next free sequence number, 1-255 then 1 again; 0 is the board's own."""
    in_use = {key[2] for key in self._pending}
    for _ in range(255):
      self._sequence = self._sequence % 255 + 1
      if self._sequence not in in_use:
        return self._sequence
    raise RuntimeError(f"{self.name}: all 255 sequence numbers are waiting for replies")

  # -- replies ---------------------------------------------------------------

  async def _read_loop(self) -> None:
    while True:
      try:
        data = await self.io.read(timeout=1, size=IN_PACKET_SIZE)
      except TimeoutError:
        continue
      except asyncio.CancelledError:
        raise
      except Exception as e:  # the link is gone; nobody will answer what is waiting
        logger.error("%s: read failed: %s", self.name, e)
        self._fail_pending(ConnectionError(f"{self.name}: read failed: {e}"))
        return
      try:
        messages = self._frames.feed(data)
      except FrameError as e:
        logger.error("%s: %s", self.name, e)
        continue
      for message in messages:
        self._dispatch(message)

  def _dispatch(self, message: bytes) -> None:
    logger.debug("%s read %s", self.name, message.hex())
    try:
      reply = Reply.parse(message)
    except ValueError as e:
      logger.error("%s: %s", self.name, e)
      return
    if reply.unsolicited:
      self._deliver_unsolicited(reply)
      return
    entry = self._pending.get(reply.key)
    if entry is None:
      related = self._pending_with_sequence(reply.sequence, reply.opcode)
      if related is not None:
        related.related.append(reply)
      else:
        logger.warning("%s: reply to no waiting request: %s", self.name, message.hex())
      return
    if reply.status == Status.ACCEPTED and reply.error_code is None:
      if not entry.accepted.done():
        entry.accepted.set_result(reply)
      return
    if not entry.done.done():
      entry.done.set_result(reply)

  def _pending_with_sequence(self, sequence: int, opcode: int) -> Optional[_Pending]:
    for key, entry in self._pending.items():
      if key[2] == sequence and key[3] == opcode:
        return entry
    return None

  def _deliver_unsolicited(self, reply: Reply) -> None:
    if (reply.device, reply.opcode) == SAGIAN_CAN:
      # A message the board saw on its SAGIAN CAN bus, passed up: normal when a success or a
      # request (see `describe_sagian_can`), flagged otherwise.
      text, normal = describe_sagian_can(reply.data)
      if normal:
        logger.debug("%s: SAGIAN CAN %s (forwarded, normal)", self.name, text)
      else:
        logger.info("%s: SAGIAN CAN %s: a status not seen in normal operation", self.name, text)
    elif (routine := describe_routine_unsolicited(reply)) is not None:
      logger.debug("%s: %s (routine)", self.name, routine)
    elif reply.status in (Status.EMERGENCY, Status.EVENT) or reply.error_code is not None:
      logger.info("%s: unsolicited %s", self.name, reply)
    for callback in list(self._subscribers):
      try:
        callback(reply)
      except Exception:
        logger.exception("%s: subscriber failed on %s", self.name, reply)

  def _fail_pending(self, error: Exception) -> None:
    for entry in self._pending.values():
      if not entry.done.done():
        entry.done.set_exception(error)
    self._pending.clear()
