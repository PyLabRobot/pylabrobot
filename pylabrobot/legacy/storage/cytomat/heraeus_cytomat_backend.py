import logging
import warnings
from typing import Awaitable, Callable, List, Optional, Tuple

import anyio

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.serial import Serial
from pylabrobot.legacy.storage.backend import IncubatorBackend
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier

logger = logging.getLogger(__name__)

# Budget for one complete PLC command exchange: the command write and the reply read share it.
# It bounds the exchange as a single operation rather than giving each phase its own budget, so
# a lost connection fails the whole command instead of each step in turn. It is the sum of the
# two transport budgets it replaces (the `Serial` `write_timeout` plus its read `timeout`), so
# no command that used to succeed can newly fail under it.
_COMMAND_TIMEOUT = 2.0

# Default budget for one readiness poll loop, unchanged from the legacy `_wait_ready` default.
# It is named so the cleanup bound below can be derived from it rather than repeating it.
_READY_TIMEOUT = 60

# Bound for the shaking stop this lifespan registers.
#
# What it actually bounds, measured on both backends rather than assumed: it is
# `anyio.fail_after` around the whole callback (`AsyncExitStackWithShielding` implements it that
# way), and it does NOT cut a shielded stop short. Probed with a stop costing 2.0s under a 0.2s
# bound: all 98 of the stop's polls ran to completion and the callback then raised
# `TimeoutError` regardless. So it is a budget for the callback's total wall time, and
# undersizing it fails a cleanup that in fact succeeded.
#
# Sizing therefore follows what that total can legitimately contain. Unlike the Cytomat and
# Liconic owners, the registered cleanup here is not a single exchange: `_stop_shaking_hardware`
# is `RS 1607` followed by `_wait_ready`, a poll loop carrying `_READY_TIMEOUT`. The cleanup can
# additionally queue behind one in-flight shaking transition of the same shape. Two such
# transitions plus scheduling margin is the derivation below; copying Cytomat's
# `2 * _COMMAND_TIMEOUT + 4.0` would be far too small for the poll loop. It is a fixed budget
# and never derives from a shaking frequency or from how long the device was asked to shake.
_CLEANUP_TIMEOUT = 2 * (_READY_TIMEOUT + _COMMAND_TIMEOUT) + 4.0


class HeraeusCytomatBackend(IncubatorBackend):
  """
  Backend for legacy (Heraeus) Cytomats.
  Perhaps identical to Liconic backend...
  to configure stackers:
      WR DM25 00007 for setting a stacker config to 10 slots
      WR DM23 06900 for setting pitch to 69 mm
  ** stacker config must be reset after every restart of cytomat **
  """

  default_baud = 9600
  serial_message_encoding = "ascii"
  init_timeout = 1.0
  start_timeout = 15.0
  poll_interval = 0.2

  def __init__(self, port: str):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    super().__init__()
    self._transaction_lock = anyio.Lock()
    self._commands_dispatched = 0
    self._reply_alignment_unknown = False
    self.io = Serial(
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=True,
      human_readable_device_name="Heraeus Cytomat",
    )

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Own the Serial transport, stop shaking before it closes, and start the PLC.

    1. Open serial port (9600 8E1, RTS/CTS) via the Serial wrapper.
    2. Send >200 ms break, wait 150 ms, flush buffers.
    3. Handshake: CR → wait for CC<CR><LF>
    4. Activate handling: ST 1801 → expect OK<CR><LF>
    5. Poll ready-flag: RD 1915 → wait for "1"<CR><LF>

    The registration order reproduces the legacy `stop()`, which stopped shaking and then
    closed the transport: the shaking stop is pushed after Serial entry, so LIFO runs it
    first. `Incubator._pre_backend_stop` calls the same force stop, but it routes through
    `IncubatorBackend._stop_shaking_transition`, which returns early unless shaking is
    tracked as active or unknown, so a full frontend exit still puts one `RS 1607` on the
    wire.

    The legacy `setup()` returned `self.io` on its success path; a native lifespan returns
    nothing, so that value is gone. Nothing in the repository ever bound it and `io` remains
    a public attribute, so the transport stays reachable.

    The stack owns the transport from entry onwards, so the failure branches below raise
    their exception and leave the close to it. The legacy branches closed the port by hand
    and those calls are gone, but a retained one would not be a second close. Measured on
    both backends: on asyncio it does nothing at all, because `HasLifespan.stop()` routes
    to the global interactive manager, which returns early for a resource it never entered,
    and the stack still performs the one close; on trio it raises `RuntimeError` before
    closing anything, because interactive stop is asyncio-only. Such a call is dead,
    backend-dependent code.
    """
    if self._reply_alignment_unknown:
      raise RuntimeError("Heraeus reply alignment unknown; use a new backend after recovery")
    await super()._enter_lifespan(stack)
    try:
      await stack.enter_async_context(self.io.lifespan())
    except serial.SerialException as e:
      # The legacy wrapper re-raised without `from`, and that is preserved rather than
      # upgraded: the original failure is still attached, as `__context__`.
      raise RuntimeError(f"Could not open {self.io.port}: {e}")
    stack.push_shielded_async_callback(self._force_stop_shaking, shield_timeout=_CLEANUP_TIMEOUT)

    await self.io.send_break(duration=0.2)  # >100 ms required
    await anyio.sleep(0.15)
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    handshake_pending = False
    try:
      await anyio.lowlevel.checkpoint_if_cancelled()
      handshake_pending = True
      await self.io.write(b"CR\r")
      # The legacy loop was a `while ... else:` over a wall-clock deadline, whose `else` ran
      # once the deadline passed. A cancel scope expresses the same budget over the whole poll,
      # and `cancelled_caught` is the branch that `else` used to be.
      with anyio.move_on_after(self.init_timeout) as handshake_scope:
        while True:
          resp = await self.io.readline()  # reads through LF
          if resp.strip() == b"CC":
            handshake_pending = False
            break
    except BaseException:
      if handshake_pending:
        self._reply_alignment_unknown = True
      raise
    if handshake_scope.cancelled_caught:
      if handshake_pending:
        self._reply_alignment_unknown = True
      raise TimeoutError(f"No CC response from PLC within {self.init_timeout} seconds")

    activation_pending = False
    try:
      await anyio.lowlevel.checkpoint_if_cancelled()
      activation_pending = True
      await self.io.write(b"ST 1801\r")
      resp = await self.io.readline()
      activation_pending = False
    except BaseException:
      if activation_pending:
        self._reply_alignment_unknown = True
      raise
    if resp.strip() != b"OK":
      raise RuntimeError(f"Unexpected reply to ST 1801: {resp!r}")

    ready_pending = False
    try:
      with anyio.move_on_after(self.start_timeout) as ready_scope:
        while True:
          await anyio.lowlevel.checkpoint_if_cancelled()
          ready_pending = True
          await self.io.write(b"RD 1915\r")
          flag = await self.io.readline()
          ready_pending = False
          if flag.strip() == b"1":
            break
          await anyio.sleep(self.poll_interval)
    except BaseException:
      if ready_pending:
        self._reply_alignment_unknown = True
      raise
    if ready_scope.cancelled_caught:
      if ready_pending:
        self._reply_alignment_unknown = True
      raise TimeoutError(f"PLC did not signal ready within {self.start_timeout} seconds")

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    warnings.warn("Cytomat racks need to be configured manually on each setup")

  async def initialize(self):
    await self._send_command("ST 1900")
    await self._send_command("ST 1801")
    await self._wait_ready()

  async def open_door(self):
    await self._send_command("ST 1901")
    await self._wait_ready()

  async def close_door(self):
    await self._send_command("ST 1902")
    await self._wait_ready()

  async def fetch_plate_to_loading_tray(self, plate: Plate, **backend_kwargs):
    """Fetch a plate from storage onto the transfer station, with gate open/close."""
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")  # carousel pos
    await self._send_command(f"WR DM5 {n}")  # handler level
    await self._complete_transfer("ST 1905")  # plate to transfer station

  async def take_in_plate(self, plate: Plate, site: PlateHolder, **backend_kwargs):
    """Place a plate from the transfer station into storage at the given site."""
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")  # carousel pos
    await self._send_command(f"WR DM5 {n}")  # handler level
    await self._complete_transfer("ST 1904")  # plate to storage

  async def set_temperature(self, temperature: float):
    raise NotImplementedError("Temperature control not implemented yet")

  async def get_temperature(self) -> float:
    raise NotImplementedError("Temperature query not implemented yet")

  async def _start_shaking_hardware(
    self, frequency: float, shakers: Optional[List[int]] = None
  ) -> None:
    await self._send_command("ST 1607")
    await self._wait_ready()

  async def _stop_shaking_hardware(self) -> None:
    await self._send_command("RS 1607")
    await self._wait_ready()

  async def start_shaking(self, frequency: float = 1.0):
    """Start durable shaking through the shared operation owner."""
    return await super().start_shaking(frequency)

  async def stop_shaking(self):
    """Stop durable shaking through the shared operation owner."""
    return await super().stop_shaking()

  async def _force_stop_shaking(self) -> None:
    """Keep shaking unknown when replies cannot verify a stop."""
    if self._reply_alignment_unknown:
      self._shaking_scope_token = None
      if self._shaking_active:
        self._shaking_state_unknown = True
      return
    await super()._force_stop_shaking()

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1  # plr is 0-indexed, cytomat is 1-indexed
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1  # 1-indexed
    return rack_idx, site_idx

  async def _complete_transfer(self, command: str) -> None:
    """Finish readiness and access termination once a movement command is dispatched."""
    dispatched = False

    def mark_dispatched() -> None:
      nonlocal dispatched
      dispatched = True

    async def finish_transfer() -> None:
      await self._wait_ready()
      await self._send_command("ST 1903")

    await anyio.lowlevel.checkpoint_if_cancelled()
    try:
      await self._send_command(command, _on_dispatch=mark_dispatched)
    except anyio.get_cancelled_exc_class():
      if not dispatched:
        raise
      with anyio.CancelScope(shield=True):
        await finish_transfer()
      raise
    await self._finish_reconciliation(finish_transfer)

  async def _finish_reconciliation(self, finish: Callable[[], Awaitable[None]]) -> None:
    """Finish shielded work while preserving pending cancellation behind any failure."""
    failure: Optional[BaseException] = None
    with anyio.CancelScope(shield=True):
      try:
        await finish()
      except BaseException as error:
        failure = error

    try:
      await anyio.lowlevel.checkpoint()
    except anyio.get_cancelled_exc_class():
      if failure is not None:
        raise failure
      raise
    if failure is not None:
      raise failure

  async def _send_command(
    self,
    command: str,
    *,
    _on_dispatch: Optional[Callable[[], None]] = None,
    timeout: Optional[float] = None,
  ) -> str:
    """
    Send an ASCII command (without CR) and return the raw response string.

    The exchange runs as a single serialized transaction. Waiting for the transaction and
    the checkpoint before the write stay cancellable. Once the write may have committed, the
    write and the reply read are shielded under one finite budget, so cancellation cannot
    abandon a command the PLC has already received. The transaction is released before the
    final checkpoint, so a pending external cancellation wins over a stored overrun instead
    of being masked as a `TimeoutError`. Decoding and error mapping need no wire access and
    deliberately run after the release.
    """
    exchange_timeout = _COMMAND_TIMEOUT if timeout is None else timeout
    cmd = command.strip() + "\r"
    logger.debug("Sending Cytomat command: %r", cmd)
    await anyio.lowlevel.checkpoint_if_cancelled()
    await self._transaction_lock.acquire()
    try:
      # Intentionally redundant with the cancellable acquire above on a contended lock, and
      # the only stop on an uncontended one: anyio's fast-path `acquire` ends in a shielded
      # checkpoint that swallows a cancellation arriving while the task waits there, so the
      # pre-lock checkpoint cannot catch it. Nothing pins this line alone, so a green suite
      # is not evidence it is dead; deleting it does cause a real post-cancellation write.
      # The pre-lock checkpoint above is unpinned alone for the mirror-image reason: on a
      # contended lock this one catches the same cancellation, so deleting either one by
      # itself leaves the suite green. The two are complementary across the contended and
      # uncontended paths, not duplicates; keep both.
      # Do not remove it, above all if the acquire is ever shielded.
      await anyio.lowlevel.checkpoint_if_cancelled()
      if self._reply_alignment_unknown:
        raise RuntimeError("Heraeus reply alignment unknown; refusing another command")
      raw = b""
      with anyio.CancelScope(shield=True):
        with anyio.move_on_after(exchange_timeout) as exchange_scope:
          try:
            await self.io.write(cmd.encode(self.serial_message_encoding))
            # Published as soon as the write returns and before the reply is read or
            # interpreted, so no later failure can lose a command the PLC may already own.
            self._commands_dispatched += 1
            if _on_dispatch is not None:
              _on_dispatch()
            raw = await self.io.read(128)
          except TimeoutError:
            self._reply_alignment_unknown = True
            raise
        exchange_timed_out = exchange_scope.cancelled_caught
        if exchange_timed_out:
          self._reply_alignment_unknown = True
    finally:
      self._transaction_lock.release()
    await anyio.lowlevel.checkpoint()

    if exchange_timed_out:
      raise TimeoutError(f"Timed out waiting for a response to {command!r}")

    resp = raw.decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError("No response from Cytomat controller")
    resp = resp.strip()
    if resp.startswith("E"):
      raise RuntimeError(f"Cytomat controller error: {resp}")
    return resp

  async def wait_for_transfer_station(self, occupied: bool = False):
    while (await self.read_plate_detection_xfer()) != occupied:
      await anyio.sleep(1)

  async def read_plate_detection_xfer(self) -> bool:
    """Read Plate Detection Transfer Station (RD 1813)."""
    resp = await self._send_command("RD 1813")
    return resp == "1"

  async def _wait_ready(self, timeout: int = _READY_TIMEOUT):
    """
    Poll the ready flag (RD 1915) until it becomes '1' or timeout.

    The wait keeps its own elapsed-time check rather than moving into a cancel scope, and it
    keeps that check AFTER the poll gap, exactly as the legacy loop did. That ordering is
    load-bearing on the wire: a zero budget still performs exactly one poll, whereas a
    `while elapsed < deadline:` form, or moving the check ahead of the sleep, would poll
    nothing at all.
    """
    start = anyio.current_time()
    while True:
      resp = await self._send_command("RD 1915")
      if resp == "1":
        return
      await anyio.sleep(0.1)
      if anyio.current_time() - start > timeout:
        raise TimeoutError("Legacy Cytomat did not become ready in time")

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.io.port,
    }

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"])
