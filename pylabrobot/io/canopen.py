"""Asynchronous CANopen transport with serialized SDO channels and frame subscriptions."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, TypeVar

from pylabrobot.io.capture import Command, capturer

try:
  import can
  import canopen
except ImportError:
  can = None  # type: ignore[assignment]
  canopen = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
T = TypeVar("T")
FrameCallback = Callable[[int, bytes, float], None]


async def finish_io(awaitable: "asyncio.Future[T]") -> T:
  """Join a pending transfer or cleanup despite cancellation, then propagate cancellation.

  A cancelled coroutine must not release an SDO channel while its worker is still using it.
  """
  cancelled = False
  while not awaitable.done():
    try:
      await asyncio.shield(awaitable)
    except asyncio.CancelledError:
      cancelled = True
  result = awaitable.result()
  if cancelled:
    raise asyncio.CancelledError()
  return result


@dataclass
class CANopenCommand(Command):
  """One CAN frame or SDO transaction, including its complete payload."""

  node_id: int
  index: int
  subindex: int
  data: str
  dlc: Optional[int] = None


class CANopen:
  """A CANopen network; device topology and register meanings belong to its caller.

  ``interface`` and ``channel`` use python-can's adapter names. Receiving callbacks run on
  the asyncio loop that opened the connection. SDO transfers on one node are serialized.
  """

  def __init__(
    self, interface: str, channel: Optional[str] = None, bitrate: int = 500000, timeout: float = 1.0
  ) -> None:
    """Store adapter parameters without opening a connection."""
    self.interface = interface
    self.channel = channel
    self.bitrate = bitrate
    self.timeout = timeout
    self._network: Optional[canopen.Network] = None
    self._nodes: Dict[int, canopen.RemoteNode] = {}
    self._locks: Dict[int, asyncio.Lock] = {}
    self._nmt_locks: Dict[int, asyncio.Lock] = {}
    self._callbacks: Dict[int, List[FrameCallback]] = {}
    self._loop: Optional[asyncio.AbstractEventLoop] = None
    self._generation = 0
    self._workers: set[asyncio.Task] = set()
    self._closing = False

  @property
  def connected(self) -> bool:
    """Whether this transport owns an open network."""
    return self._network is not None

  def _record(
    self,
    action: str,
    node_id: int,
    index: int,
    subindex: int,
    data: bytes,
    dlc: Optional[int] = None,
  ) -> None:
    """Record bytes and their addressing without interpreting device registers."""
    capturer.record(
      CANopenCommand(
        "canopen",
        f"{self.interface}:{self.channel}",
        action,
        node_id,
        index,
        subindex,
        data.hex(),
        dlc,
      )
    )
    logger.debug(
      "CANopen %s node=%d index=0x%04x:%d %s", action, node_id, index, subindex, data.hex()
    )

  def _dispatch(self, generation: int, cob_id: int, data: bytes, timestamp: float) -> None:
    """Discard callbacks from a closed session and deliver current frames."""
    if generation != self._generation or self._loop is None:
      return
    self._record("receive", 0, cob_id, 0, data)
    for callback in tuple(self._callbacks.get(cob_id, [])):
      try:
        callback(cob_id, data, timestamp)
      except Exception:
        logger.exception("CANopen callback failed for COB-ID 0x%x", cob_id)

  def subscribe(self, cob_id: int, callback: FrameCallback) -> None:
    """Subscribe before sending a request; callback delivery stays on the owning loop."""
    network = self._network
    if network is None or self._loop is None:
      raise RuntimeError("CANopen is not connected")
    if cob_id not in self._callbacks:
      generation, loop = self._generation, self._loop

      def receive(received_id: int, data: bytes, timestamp: float) -> None:
        """Marshal the library listener thread onto the connection's event loop."""
        if not loop.is_closed():
          loop.call_soon_threadsafe(self._dispatch, generation, received_id, bytes(data), timestamp)

      self._callbacks[cob_id] = []
      network.subscribe(cob_id, receive)
    self._callbacks[cob_id].append(callback)

  async def _transfer(self, operation: Callable[..., T], *args, **kwargs) -> T:
    """Join a worker before the connection or its SDO lock can be released."""
    if self._closing or self._network is None:
      raise RuntimeError("CANopen is not connected")
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    self._workers.add(task)
    try:
      return await finish_io(task)
    finally:
      self._workers.discard(task)

  async def _disconnect(self) -> None:
    """Wait for all accepted transfers, then release the adapter."""
    self._closing = True
    self._generation += 1
    self._loop = None
    network = self._network
    try:
      if self._workers:
        await asyncio.gather(*self._workers, return_exceptions=True)
      if network is not None:
        await asyncio.to_thread(network.disconnect)
    finally:
      self._network = None
      self._nodes.clear()
      self._locks.clear()
      self._nmt_locks.clear()
      self._callbacks.clear()

  async def stop(self) -> None:
    """Disconnect after outstanding workers finish, even if the caller is cancelled."""
    await finish_io(asyncio.create_task(self._disconnect()))

  async def setup(self, node_ids: Sequence[int]) -> None:
    """Open the adapter and prepare SDO channels, without resetting or starting any node."""
    if self.connected:
      raise RuntimeError("CANopen is already connected")
    if canopen is None:
      raise ImportError("Install CANopen support with pip install 'pylabrobot[canopen]'")
    if len(set(node_ids)) != len(node_ids) or any(not 1 <= nid <= 127 for nid in node_ids):
      raise ValueError("CANopen node IDs must be unique integers in 1..127")
    self._closing = False
    self._loop = asyncio.get_running_loop()
    network = canopen.Network()
    self._network = network
    try:
      await self._transfer(
        network.connect, interface=self.interface, channel=self.channel, bitrate=self.bitrate
      )
      for nid in node_ids:
        node = network.add_node(nid, canopen.ObjectDictionary())
        node.sdo.RESPONSE_TIMEOUT = self.timeout
        self._nodes[nid] = node
        self._locks[nid] = asyncio.Lock()
        self._nmt_locks[nid] = asyncio.Lock()
    except BaseException:
      await finish_io(asyncio.ensure_future(self.stop()))
      raise

  def _send_remote(self, network: "canopen.Network", cob_id: int, dlc: int) -> None:
    """Send an RTR frame with its requested response length preserved.

    canopen.Network.send_message drops the DLC when python-can clears an RTR payload.
    """
    if network.bus is None or can is None:
      raise RuntimeError("CANopen is not connected")
    message = can.Message(
      arbitration_id=cob_id, is_extended_id=False, is_remote_frame=True, dlc=dlc
    )
    with network.send_lock:
      network.bus.send(message)
    network.check()

  async def write(
    self,
    cob_id: int,
    data: bytes,
    *,
    remote: bool = False,
    dlc: Optional[int] = None,
  ) -> None:
    """Send one CAN frame; RTR frames declare their requested response length with dlc."""
    network = self._network
    if network is None:
      raise RuntimeError("CANopen is not connected")
    if not 0 <= cob_id <= 0x7FF or len(data) > 8:
      raise ValueError("CANopen requires an 11-bit identifier and at most eight bytes")
    if remote:
      if data or dlc is None or not 0 <= dlc <= 8:
        raise ValueError("An RTR frame requires empty data and a DLC in 0..8")
      await self._transfer(self._send_remote, network, cob_id, dlc)
    else:
      if dlc is not None and dlc != len(data):
        raise ValueError("Data-frame DLC must match its payload")
      await self._transfer(network.send_message, cob_id, data)
    self._record("remote" if remote else "send", 0, cob_id, 0, data, dlc if remote else len(data))

  async def upload(self, node_id: int, index: int, subindex: int) -> bytes:
    """Read an object, including segmented replies, on the node's SDO channel."""
    if not self.connected or self._closing:
      raise RuntimeError("CANopen is not connected")
    async with self._locks[node_id]:
      if self._closing:
        raise RuntimeError("CANopen is closing")
      result = bytes(await self._transfer(self._nodes[node_id].sdo.upload, index, subindex))
    self._record("upload", node_id, index, subindex, result)
    return result

  async def download(self, node_id: int, index: int, subindex: int, data: bytes) -> None:
    """Write an object; wait for the acknowledgement before releasing its SDO channel."""
    if not self.connected or self._closing:
      raise RuntimeError("CANopen is not connected")
    async with self._locks[node_id]:
      if self._closing:
        raise RuntimeError("CANopen is closing")
      await self._transfer(self._nodes[node_id].sdo.download, index, subindex, data)
    self._record("download", node_id, index, subindex, data)

  async def _request_nmt_state(self, node_id: int) -> int:
    """Query one node while holding its NMT configuration lock."""
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    def receive(cob_id: int, data: bytes, timestamp: float) -> None:
      """Read the state while ignoring the node-guard toggle bit."""
      if data and not future.done():
        future.set_result(data[0] & 0x7F)

    cob_id = 0x700 + node_id
    self.subscribe(cob_id, receive)
    try:
      await self.write(cob_id, b"", remote=True, dlc=1)
      try:
        return await asyncio.wait_for(asyncio.shield(future), self.timeout)
      except asyncio.TimeoutError:
        original = await self.upload(node_id, 0x1017, 0)
        if len(original) != 2:
          raise ValueError(f"Node {node_id} returned an invalid heartbeat period")
        try:
          await self.download(node_id, 0x1017, 0, (100).to_bytes(2, "little"))
          return await asyncio.wait_for(future, self.timeout)
        finally:
          await finish_io(asyncio.create_task(self.download(node_id, 0x1017, 0, original)))
    finally:
      future.cancel()
      callbacks = self._callbacks.get(cob_id, [])
      if receive in callbacks:
        callbacks.remove(receive)

  async def request_nmt_state(self, node_id: int) -> int:
    """Read NMT state, temporarily enabling heartbeat if node guarding is unsupported.

    Restore the exact producer heartbeat period before returning, including on cancellation.
    """
    async with self._nmt_locks[node_id]:
      return await self._request_nmt_state(node_id)

  async def set_nmt_state(self, node_id: int, state: int) -> None:
    """Move one node to operational, stopped, or pre-operational and confirm it."""
    commands = {5: 1, 4: 2, 127: 128}
    if state not in commands:
      raise ValueError(f"Cannot restore NMT state {state}")
    await self.write(0, bytes([commands[state], node_id]))
    if await self.request_nmt_state(node_id) != state:
      raise RuntimeError(f"Node {node_id} did not enter NMT state {state}")
