"""Connected UDP transport preserving datagram boundaries and IO captures."""

import logging
import warnings
from typing import TYPE_CHECKING, Any, Optional, Union

import anyio
from anyio.abc import ConnectedUDPSocket

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.capture import capturer, get_capture_or_validation_active
from pylabrobot.io.errors import ValidationError
from pylabrobot.io.io import ReconnectableConnectionIO
from pylabrobot.io.socket import SocketCommand
from pylabrobot.io.validation_utils import LOG_LEVEL_IO, align_sequences

if TYPE_CHECKING:
  from pylabrobot.io.capture import CaptureReader

logger = logging.getLogger(__name__)


class _UnsetTimeout:
  """Distinguish an omitted compatibility timeout from an explicit value."""


_UNSET_TIMEOUT = _UnsetTimeout()


class UDP(ReconnectableConnectionIO):
  """Send and receive whole datagrams from one peer, without automatic retries.

  A connected UDP socket filters incoming packets by peer address and port. Entering
  the lifespan does not establish that the peer is present or listening.
  """

  def __init__(
    self,
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    write_timeout: float = 30,
    source_ip: Optional[str] = None,
  ) -> None:
    """Configure a disconnected UDP socket with an optional local IPv4 address."""
    super().__init__()
    if get_capture_or_validation_active():
      raise RuntimeError("Cannot create a UDP object while capture or validation is active")
    self.human_readable_device_name = human_readable_device_name
    self._host = host
    self._port = port
    if isinstance(read_timeout, _UnsetTimeout):
      self._read_timeout_explicit = False
      self._read_timeout: Optional[float] = None
    else:
      self._read_timeout_explicit = True
      warnings.warn(
        "UDP.read_timeout is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      self._read_timeout = read_timeout
    self._write_timeout = write_timeout
    self._source_ip = source_ip
    self._socket: Optional[ConnectedUDPSocket] = None
    self._read_lock = anyio.Lock()
    self._write_lock = anyio.Lock()
    self._connection_lock = anyio.Lock()
    self._connection_lost = False
    self._unique_id = f"{host}:{port}"

  def _mark_connection_lost(self) -> None:
    """Invalidate this UDP handle after an observed hard failure."""
    self._connection_lost = True

  def _require_connection_usable(self) -> None:
    """Reject I/O after an observed hard failure or failed reconnect."""
    if self._connection_lost:
      raise ConnectionError("UDP socket connection lost; reconnect or enter a new lifespan")

  async def _connect(self) -> None:
    """Bind and connect a fresh UDP socket to the configured peer."""
    self._socket = await anyio.create_connected_udp_socket(
      remote_host=self._host,
      remote_port=self._port,
      local_host=self._source_ip,
    )
    self._connection_lost = False

  async def _disconnect_current(self) -> None:
    """Close the current UDP socket while holding the connection lock."""
    async with self._write_lock:
      sock = self._socket
      if sock is None:
        return
      if self._socket is sock:
        self._socket = None
      try:
        with anyio.CancelScope(shield=True):
          await sock.aclose()
      except OSError as error:
        logger.warning("Error while closing UDP socket %s:%d: %s", self._host, self._port, error)

  async def _disconnect(self) -> None:
    """Wait for active writes and close the UDP socket, waking any blocked reader."""
    async with self._connection_lock:
      await self._disconnect_current()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the connected UDP socket and register bounded shielded cleanup."""
    await super()._enter_lifespan(stack)
    async with self._connection_lock:
      await self._connect()
    stack.push_shielded_async_callback(self._disconnect, shield_timeout=5.0)

  async def reconnect(self) -> None:
    """Discard the current UDP socket and open a fresh one on a new ephemeral port."""
    if not self.is_active:
      raise RuntimeError("UDP socket is closed; enter its lifespan first")
    async with self._connection_lock:
      self._mark_connection_lost()
      await self._disconnect_current()
      await self._connect()

  def serialize(self) -> dict[str, Any]:
    """Describe the configured transport."""
    serialized: dict[str, Any] = {
      "human_readable_device_name": self.human_readable_device_name,
      "host": self._host,
      "port": self._port,
      "write_timeout": self._write_timeout,
      "source_ip": self._source_ip,
      "type": "UDP",
    }
    if self._read_timeout_explicit:
      serialized["read_timeout"] = self._read_timeout
    return serialized

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Send one nonempty IPv4 datagram and record it, without retransmission."""
    if not 1 <= len(data) <= 65507:
      raise ValueError("UDP data must contain 1–65507 bytes")
    timeout = self._write_timeout if timeout is None else timeout
    async with self._write_lock:
      self._require_connection_usable()
      sock = self._socket
      if sock is None:
        raise RuntimeError("UDP socket is closed; enter its lifespan first")
      await anyio.lowlevel.checkpoint()
      logger.log(LOG_LEVEL_IO, "[%s:%d UDP] write %s", self._host, self._port, data)
      capturer.record(SocketCommand(self._unique_id, "write", data.hex(), module="udp"))
      try:
        with anyio.CancelScope(shield=True):
          with anyio.fail_after(timeout):
            await sock.send(data)
        await anyio.lowlevel.checkpoint_if_cancelled()
      except TimeoutError as error:
        self._mark_connection_lost()
        raise TimeoutError(
          f"Timeout while writing to UDP socket after {timeout} seconds"
        ) from error
      except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
        self._mark_connection_lost()
        raise

  async def read(self, timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT) -> bytes:
    """Receive exactly one datagram, including empty datagrams, without truncation."""
    if isinstance(timeout, _UnsetTimeout):
      effective_timeout = self._read_timeout
    else:
      warnings.warn(
        "UDP.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
      effective_timeout = self._read_timeout if timeout is None else timeout
    async with self._read_lock:
      self._require_connection_usable()
      sock = self._socket
      if sock is None:
        raise RuntimeError("UDP socket is closed; enter its lifespan first")
      try:
        with anyio.fail_after(effective_timeout):
          data = await sock.receive()
      except TimeoutError as error:
        raise TimeoutError(
          f"Timeout while reading from UDP socket after {effective_timeout} seconds"
        ) from error
      except (OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
        self._mark_connection_lost()
        raise
      logger.log(LOG_LEVEL_IO, "[%s:%d UDP] read %s", self._host, self._port, data)
      capturer.record(SocketCommand(self._unique_id, "read", data.hex(), module="udp"))
      return data


class UDPValidator(UDP):
  """Replay and validate captured UDP datagrams without opening a network socket."""

  def __init__(
    self,
    cr: "CaptureReader",
    human_readable_device_name: str,
    host: str,
    port: int,
    read_timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT,
    write_timeout: float = 30,
    source_ip: Optional[str] = None,
  ) -> None:
    """Initialize a UDP replay validator bound to ``cr``."""
    super().__init__(
      human_readable_device_name=human_readable_device_name,
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      source_ip=source_ip,
    )
    self.cr = cr

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Enter validation without opening a network socket."""
    await ReconnectableConnectionIO._enter_lifespan(self, stack)
    self._connection_lost = False

  async def _connect(self) -> None:
    """Skip network connection during validation."""

  async def _disconnect(self) -> None:
    """Skip network cleanup during validation."""

  async def reconnect(self) -> None:
    """Reset validation continuity within an active lifespan."""
    if not self.is_active:
      raise RuntimeError("UDP socket is closed; enter its lifespan first")
    self._connection_lost = False

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Validate a datagram write against the next captured UDP command."""
    if not 1 <= len(data) <= 65507:
      raise ValueError("UDP data must contain 1–65507 bytes")
    self._require_connection_usable()
    if not self.is_active:
      raise RuntimeError("UDP socket is closed; enter its lifespan first")
    await anyio.lowlevel.checkpoint()
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "udp"
      and next_command.device_id == self._unique_id
      and next_command.action == "write"
    ):
      raise ValidationError(
        f"Expected UDP write command to {self._unique_id}, "
        f"got {next_command.module} {next_command.action} to {next_command.device_id}"
      )
    expected = bytes.fromhex(next_command.data)
    if expected != data:
      align_sequences(expected=expected.decode("latin-1"), actual=data.decode("latin-1"))
      raise ValidationError("Data mismatch: difference was written to stdout.")

  async def read(self, timeout: Union[float, None, _UnsetTimeout] = _UNSET_TIMEOUT) -> bytes:
    """Replay the next captured UDP datagram read."""
    if not isinstance(timeout, _UnsetTimeout):
      warnings.warn(
        "UDP.read(timeout=...) is deprecated; use a caller-owned AnyIO deadline",
        DeprecationWarning,
        stacklevel=2,
      )
    self._require_connection_usable()
    if not self.is_active:
      raise RuntimeError("UDP socket is closed; enter its lifespan first")
    await anyio.lowlevel.checkpoint()
    next_command = SocketCommand(**self.cr.next_command())
    if not (
      next_command.module == "udp"
      and next_command.device_id == self._unique_id
      and next_command.action == "read"
    ):
      raise ValidationError(
        f"Expected UDP read command from {self._unique_id}, "
        f"got {next_command.module} {next_command.action} from {next_command.device_id}"
      )
    return bytes.fromhex(next_command.data)
