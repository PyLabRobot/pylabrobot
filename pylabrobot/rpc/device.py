"""Device identity/status clients and adapters, independent of jobs and images."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Protocol

import grpc
from typing_extensions import Self

from pylabrobot.rpc.transport import (
  API_VERSION,
  Updates,
  channel_options,
  copy_message,
  require_device,
  rpc_errors,
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

  def get_info(self) -> pb.DeviceInfo:
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
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return copy_message(self.backend.get_info())

  async def GetStatus(
    self, request: pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.DeviceStatus:
    """Read a cached operational snapshot; timestamps describe its actual freshness."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      return copy_message(self.backend.get_status())

  async def WatchStatus(
    self, request: pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> AsyncIterator[pb.DeviceStatus]:
    """Forward bounded state updates without initializing or operating the device."""
    async with rpc_errors(context):
      require_device(request.device_id, self.backend.device_id)
      async with aclosing(self.backend.watch_status()) as updates:
        async for status in updates:
          yield copy_message(status)


class DeviceServer:
  """Loopback gRPC transport hosting identity/status and explicitly added services."""

  def __init__(
    self,
    backend: DeviceBackend,
    *,
    port: int = 0,
    maximum_message_bytes: int = 4 * 1024 * 1024,
  ) -> None:
    """Configure transport only; the device application owns initialization and cleanup."""
    self.server = grpc.aio.server(
      options=channel_options(maximum_message_bytes), maximum_concurrent_rpcs=32
    )
    rpc.add_DeviceServiceServicer_to_server(DeviceService(backend), self.server)
    self.port = self.server.add_insecure_port(f"127.0.0.1:{port}")
    if not self.port:
      raise RuntimeError("unable to bind device endpoint")
    self.endpoint = f"127.0.0.1:{self.port}"

  async def start(self) -> None:
    """Start accepting RPCs without operating the device."""
    await self.server.start()

  async def stop(self) -> None:
    """Close transport; the device application separately finishes its accepted work."""
    await self.server.stop(0)


class DeviceClient:
  """Connect to any PLR RPC device without importing device-specific or image modules."""

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

  async def device_info(self) -> pb.DeviceInfo:
    """Read identity and supported services from the connected endpoint."""
    return await self.devices.GetDevice(self.request, timeout=self.timeout)

  async def status(self) -> pb.DeviceStatus:
    """Read current server state without pretending it is a fresh hardware observation."""
    return await self.devices.GetStatus(self.request, timeout=self.timeout)

  async def disconnect(self) -> None:
    """Close this connection without cancelling accepted remote jobs."""
    if self.channel is not None:
      await self.channel.close()
      self.channel = None

  async def connect(self) -> Self:
    """Connect and validate device identity, protocol version, and optional expected type."""
    await self.disconnect()
    self.channel = grpc.aio.insecure_channel(
      self.endpoint,
      options=channel_options(self.maximum_message_bytes),
    )
    self.devices = rpc.DeviceServiceStub(self.channel)
    try:
      info = await self.device_info()
      if info.device_id != self.device_id or info.api_version != API_VERSION:
        raise ValueError("device identity or API version mismatch")
      if self.expected_device_type is not None and info.device_type != self.expected_device_type:
        raise ValueError(f"this client only accepts a {self.expected_device_type}")
      self.configuration_id = info.configuration_id
    except BaseException:
      await self.disconnect()
      raise
    return self

  @asynccontextmanager
  async def status_updates(self) -> AsyncIterator[Updates[pb.DeviceStatus]]:
    """Subscribe to snapshots; closing this context cancels only the watch."""
    call = self.devices.WatchStatus(self.request)
    try:
      yield Updates[pb.DeviceStatus](call)
    finally:
      call.cancel()

  async def __aenter__(self) -> Self:
    """Connect for the duration of an asynchronous context."""
    return await self.connect()

  async def __aexit__(self, *_exc: object) -> None:
    """Release the connection at context exit."""
    await self.disconnect()
