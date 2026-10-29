"""Device identity/status clients and adapters, independent of jobs and images."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Protocol

import grpc

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.rpc.transport import (
  API_VERSION,
  Updates,
  build_channel_options,
  copy_message,
  require_asyncio,
  require_device,
  translate_rpc_errors,
)
from pylabrobot.rpc.v1 import device_pb2 as pb
from pylabrobot.rpc.v1 import device_pb2_grpc as rpc

SERVICE_NAME = "pylabrobot.rpc.v1.DeviceService"


class DeviceBackend(Protocol):
  """Operational snapshots supplied by a device implementation without hardware polling."""

  @property
  def device_id(self) -> str:
    """Return the stable instrument identity."""
    ...

  def get_device_info(self) -> pb.DeviceInfo:
    """Describe identity and the registered optional services."""
    ...

  def get_status(self) -> pb.DeviceStatus:
    """Return the latest known operational snapshot with its observation timestamp."""
    ...

  def watch_status(self) -> AsyncGenerator[pb.DeviceStatus, None]:
    """Yield current state followed by subsequent snapshots."""
    ...


class DeviceService(rpc.DeviceServiceServicer):
  """Expose one device's identity and status without assuming any operation capability."""

  def __init__(self, backend: DeviceBackend) -> None:
    """Bind a provider whose lifecycle remains owned by the device application."""
    self.backend = backend

  async def GetDevice(
    self, request: pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.DeviceInfo:
    """Read device identity after checking the request's target."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return copy_message(self.backend.get_device_info())

  async def GetStatus(
    self, request: pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.DeviceStatus:
    """Read a cached operational snapshot; timestamps describe its actual freshness."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return copy_message(self.backend.get_status())

  async def WatchStatus(
    self, request: pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> AsyncIterator[pb.DeviceStatus]:
    """Forward bounded state updates without initializing or operating the device."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      async with aclosing(self.backend.watch_status()) as updates:
        async for status in updates:
          yield copy_message(status)


class DeviceServer(HasLifespan):
  """gRPC transport hosting identity/status and explicitly added services.

  `grpc.aio` is an asyncio-only dependency, so the lifespan rejects other AnyIO backends before
  it starts accepting RPCs. A server object is single-use: gRPC cannot restart a stopped server.
  """

  def __init__(
    self,
    backend: DeviceBackend,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    maximum_message_bytes: int = 4 * 1024 * 1024,
  ) -> None:
    """Configure transport only; the device application owns initialization and cleanup."""
    super().__init__()
    self.server = grpc.aio.server(
      options=build_channel_options(maximum_message_bytes), maximum_concurrent_rpcs=32
    )
    rpc.add_DeviceServiceServicer_to_server(DeviceService(backend), self.server)
    bind_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    self.port = self.server.add_insecure_port(f"{bind_host}:{port}")
    if not self.port:
      raise RuntimeError("unable to bind device endpoint")
    self.endpoint = f"{bind_host}:{self.port}"

  async def _stop_server(self) -> None:
    """Close transport; the device application separately finishes its accepted work."""
    await self.server.stop(0)

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Accept RPCs for the lifespan without operating the device."""
    await super()._enter_lifespan(stack)
    require_asyncio(type(self).__name__)
    await self.server.start()
    stack.push_shielded_async_callback(self._stop_server)


class DeviceClient(HasLifespan):
  """Connect to any PLR RPC device without importing device-specific or image modules.

  The connection lives for the client's lifespan; `grpc.aio` limits it to asyncio.
  """

  expected_device_type: str | None = None

  def __init__(
    self,
    endpoint: str,
    device_id: str,
    timeout: float = 20,
    *,
    maximum_message_bytes: int = 4 * 1024 * 1024,
  ) -> None:
    """Store an endpoint and expected device identity; construction performs no connection."""
    super().__init__()
    self.endpoint = endpoint
    self.device_id = device_id
    self.timeout = timeout
    self.maximum_message_bytes = maximum_message_bytes
    self.configuration_id = ""
    self.channel: grpc.aio.Channel | None = None
    self.devices: rpc.DeviceServiceAsyncStub

  @property
  def request(self) -> pb.DeviceRequest:
    """Build an identity-scoped read request."""
    return pb.DeviceRequest(device_id=self.device_id)

  async def get_device_info(self) -> pb.DeviceInfo:
    """Read identity and supported services from the connected endpoint."""
    return await self.devices.GetDevice(self.request, timeout=self.timeout)

  async def get_status(self) -> pb.DeviceStatus:
    """Read current server state without pretending it is a fresh hardware observation."""
    return await self.devices.GetStatus(self.request, timeout=self.timeout)

  async def _close_channel(self) -> None:
    """Close this connection without cancelling accepted remote jobs."""
    channel, self.channel = self.channel, None
    if channel is not None:
      await channel.close()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Connect and validate device identity, protocol version, and optional expected type."""
    await super()._enter_lifespan(stack)
    require_asyncio(type(self).__name__)
    self.channel = grpc.aio.insecure_channel(
      self.endpoint,
      options=build_channel_options(self.maximum_message_bytes),
    )
    stack.push_shielded_async_callback(self._close_channel)
    self.devices = rpc.DeviceServiceStub(self.channel)
    info = await self.get_device_info()
    if info.device_id != self.device_id or info.api_version != API_VERSION:
      raise ValueError("device identity or API version mismatch")
    if self.expected_device_type is not None and info.device_type != self.expected_device_type:
      raise ValueError(f"this client only accepts a {self.expected_device_type}")
    self.configuration_id = info.configuration_id

  @asynccontextmanager
  async def watch_status(self) -> AsyncIterator[Updates[pb.DeviceStatus]]:
    """Subscribe to snapshots; closing this context cancels only the watch."""
    call = self.devices.WatchStatus(self.request)
    try:
      yield Updates[pb.DeviceStatus](call)
    finally:
      call.cancel()
