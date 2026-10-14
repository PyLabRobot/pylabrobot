"""Typed client for PyLabRobot's Mettler Toledo OPC UA interface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Optional, Type, cast

try:
  from asyncua import Client, ua  # type: ignore[import-untyped]

  USE_ASYNCUA = True
except ImportError as exc:
  USE_ASYNCUA = False
  _ASYNCUA_IMPORT_ERROR = exc

_Method = Literal[
  "ReadStableWeight", "ReadWeightImmediately", "ReadTareWeight", "Zero", "Tare", "ClearTare"
]


@dataclass(frozen=True)
class BalanceInfo:
  """Balance identity and capacity; capacity is expressed in grams."""

  device_type: str
  serial_number: str
  firmware_version: str
  capacity: float
  unit: str


class BalanceClient:
  """Use a remote balance without handling OPC UA namespaces, nodes, or variants.

  Use ``async with BalanceClient(endpoint) as balance`` or explicitly call
  ``connect()`` and ``disconnect()``. All weights are in grams. ``client`` exposes
  the underlying asyncua client for security configuration before connecting.

  Calls preserve asyncua's ``UaStatusCodeError`` and transport exceptions. This
  client does not retry commands or automatically reconnect. Reconnect explicitly
  to rediscover the namespace after restarting the server. A timed-out request
  may still be executing on the server.
  """

  DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/"
  NAMESPACE_URI = "https://pylabrobot.org/opcua/mettler-toledo/scales"

  def __init__(self, endpoint: str = DEFAULT_ENDPOINT, timeout: float = 70) -> None:
    """Configure the endpoint and per-request timeout in seconds without connecting."""
    if not USE_ASYNCUA:
      raise RuntimeError(
        "OPC UA support requires asyncua. Install it with pip install 'PyLabRobot[opcua]'."
      ) from _ASYNCUA_IMPORT_ERROR
    if timeout <= 0:
      raise ValueError("timeout must be greater than zero")
    self.client = Client(endpoint, timeout=timeout)
    self._namespace: Optional[int] = None
    self._lock = asyncio.Lock()

  def _node_id(self, name: str) -> ua.NodeId:
    """Resolve an internal node name using the namespace discovered for this session."""
    if self._namespace is None:
      raise RuntimeError("Balance client is not connected; use connect() or async with")
    return ua.NodeId(name, self._namespace)

  async def connect(self) -> None:
    """Open a session and discover the balance namespace; do nothing if already connected."""
    async with self._lock:
      if self._namespace is not None:
        return
      await self.client.connect()
      try:
        namespace = await self.client.get_namespace_index(self.NAMESPACE_URI)
        node = self.client.get_node(ua.NodeId("Balance", namespace))
        if await node.read_node_class() != ua.NodeClass.Object:
          raise ValueError("The server's Balance node is not an OPC UA object")
      except BaseException:
        await self.client.disconnect()
        raise
      self._namespace = namespace

  async def disconnect(self) -> None:
    """Close this client session after active calls; leave the remote server running."""
    async with self._lock:
      if self._namespace is None:
        return
      self._namespace = None
      await self.client.disconnect()

  async def __aenter__(self) -> BalanceClient:
    """Connect on entry to an async context manager."""
    await self.connect()
    return self

  async def __aexit__(
    self,
    exc_type: Optional[Type[BaseException]],
    exc_value: Optional[BaseException],
    traceback: Optional[TracebackType],
  ) -> None:
    """Disconnect on exit, including when the context body raises an exception."""
    await self.disconnect()

  async def _call(self, method: _Method, returns_weight: bool) -> Optional[float]:
    """Invoke a method and validate its status, result count, and wire type."""
    async with self._lock:
      request = ua.CallMethodRequest(
        ObjectId=self._node_id("Balance"),
        MethodId=self._node_id(f"Balance.{method}"),
        InputArguments=[],
      )
      results = await self.client.uaclient.call([request])
      if len(results) != 1:
        raise ValueError(f"{method}: expected one OPC UA method result")
      result = results[0]
      result.StatusCode.check()
      outputs = result.OutputArguments or []
      if len(outputs) != (1 if returns_weight else 0):
        raise ValueError(f"{method}: unexpected number of output arguments ({len(outputs)})")
      if not returns_weight:
        return None
      output = outputs[0]
      if output.VariantType != ua.VariantType.Double or not isinstance(output.Value, float):
        raise ValueError(f"{method}: expected a scalar Double weight in grams")
      return cast(float, output.Value)

  async def get_info(self) -> BalanceInfo:
    """Read typed device identity and capacity, rejecting incompatible property values."""
    async with self._lock:
      names = ("DeviceType", "SerialNumber", "FirmwareVersion", "Capacity", "Unit")
      nodes = [self.client.get_node(self._node_id(f"Balance.{name}")) for name in names]
      results = await self.client.read_attributes(nodes, ua.AttributeIds.Value)
      if len(results) != 5:
        raise ValueError("Expected five balance properties")
      values = []
      for name, result in zip(names, results):
        result.StatusCode.check()
        if result.Value is None:
          raise ValueError(f"{name}: missing property value")
        values.append(result.Value.Value)
      device_type, serial_number, firmware_version, capacity, unit = values
      if not (
        isinstance(device_type, str)
        and isinstance(serial_number, str)
        and isinstance(firmware_version, str)
        and isinstance(capacity, float)
        and unit == "g"
      ):
        raise ValueError(
          "Invalid balance properties: expected identity strings and capacity in grams"
        )
      return BalanceInfo(device_type, serial_number, firmware_version, capacity, unit)

  async def read_weight(self) -> float:
    """Return a stable net weight in grams."""
    return cast(float, await self._call("ReadStableWeight", returns_weight=True))

  async def read_weight_immediately(self) -> float:
    """Return the current net weight in grams without waiting for stability."""
    return cast(float, await self._call("ReadWeightImmediately", returns_weight=True))

  async def read_tare_weight(self) -> float:
    """Return the stored tare weight in grams."""
    return cast(float, await self._call("ReadTareWeight", returns_weight=True))

  async def zero(self) -> None:
    """Zero the balance once its reading is stable."""
    await self._call("Zero", returns_weight=False)

  async def tare(self) -> None:
    """Tare the current load once its reading is stable."""
    await self._call("Tare", returns_weight=False)

  async def clear_tare(self) -> None:
    """Clear the stored tare without changing the zero reference."""
    await self._call("ClearTare", returns_weight=False)
