"""OPC UA server for a Mettler Toledo MT-SICS balance."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from functools import partial
from typing import Awaitable, Callable, Optional

from .driver import MTSICSDriver
from .errors import MettlerToledoError

try:
  from asyncua import Node, Server, ua  # type: ignore[import-untyped]
  from asyncua.common.instantiate_util import instantiate  # type: ignore[import-untyped]

  USE_ASYNCUA = True
except ImportError as exc:
  USE_ASYNCUA = False
  _ASYNCUA_IMPORT_ERROR = exc

logger = logging.getLogger(__name__)


class MettlerToledoOPCUAServer:
  """Expose an initialized MT-SICS balance, including the WXS205SDU, over OPC UA.

  The caller owns the balance connection: call ``device.setup()`` before ``setup()``
  and ``device.stop()`` after ``stop()``. While serving, use the balance exclusively
  through this server so its serial commands cannot interleave.

  The default endpoint uses anonymous, unencrypted access on loopback. Configure
  ``server`` with asyncua's certificate, security policy, and user manager APIs
  before setup when exposing it on a network.

  Args:
    device: An already initialized MT-SICS driver.
    endpoint: OPC UA endpoint URL.
    poll_interval: Seconds between immediate weight polls, or None for method-only updates.
  """

  NAMESPACE_URI = "https://pylabrobot.org/opcua/mettler-toledo/scales"

  def __init__(
    self,
    device: MTSICSDriver,
    endpoint: str = "opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/",
    poll_interval: Optional[float] = None,
  ) -> None:
    """Create the server without opening a network or hardware connection."""
    if not USE_ASYNCUA:
      raise RuntimeError(
        "OPC UA support requires asyncua. Install it with pip install 'PyLabRobot[opcua]'."
      ) from _ASYNCUA_IMPORT_ERROR
    if poll_interval is not None and (not math.isfinite(poll_interval) or poll_interval <= 0):
      raise ValueError("poll_interval must be finite and greater than zero")
    self.device = device
    self.poll_interval = poll_interval
    self.server = Server()
    self.server.set_endpoint(endpoint)
    self.server.set_server_name("PyLabRobot Mettler Toledo balance")
    self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    self.server.set_identity_tokens([ua.AnonymousIdentityToken])
    self._lifecycle_lock = asyncio.Lock()
    self._command_lock = asyncio.Lock()
    self._initialized = False
    self._started = False
    self._object_id: Optional[ua.NodeId] = None
    self._weight: Optional[Node] = None
    self._state: Optional[Node] = None
    self._fault: Optional[Node] = None
    self._fault_code: Optional[Node] = None
    self._poll_task: Optional[asyncio.Task[None]] = None
    self._stop_polling = asyncio.Event()

  async def _publish_weight(self, value: Optional[float], status: int) -> None:
    """Publish a measurement or invalid quality, timestamped when received by the adapter."""
    assert self._weight is not None
    await self._weight.write_value(
      ua.DataValue(
        Value=ua.Variant(
          value, ua.VariantType.Double if value is not None else ua.VariantType.Null
        ),
        StatusCode_=ua.StatusCode(status),
        SourceTimestamp=datetime.now(timezone.utc),
      )
    )

  async def _operate(
    self,
    operation: Callable[[], Awaitable[object]],
    updates_weight: bool,
    invalidates_weight: bool = False,
  ) -> tuple[object, ua.StatusCode]:
    """Run one serialized operation and publish its observed state and fault outcome."""
    assert self._state is not None and self._fault is not None and self._fault_code is not None
    await self._state.write_value("busy")
    try:
      value = await operation()
      if updates_weight:
        if not isinstance(value, float) or not math.isfinite(value):
          raise ValueError("Expected a finite weight in grams")
        await self._publish_weight(value, ua.StatusCodes.Good)
      elif invalidates_weight:
        await self._publish_weight(None, ua.StatusCodes.BadWaitingForInitialData)
    except Exception as error:
      if isinstance(error, TimeoutError):
        status = ua.StatusCodes.BadTimeout
      elif isinstance(error, OSError):
        status = ua.StatusCodes.BadCommunicationError
      elif isinstance(error, MettlerToledoError):
        status = ua.StatusCodes.BadDeviceFailure
      else:
        status = ua.StatusCodes.BadUnexpectedError
      logger.exception("Balance operation failed")
      await self._publish_weight(None, status)
      await self._fault.write_value(str(error))
      await self._fault_code.write_value(ua.StatusCode(status), ua.VariantType.StatusCode)
      await self._state.write_value("fault")
      return None, ua.StatusCode(status)
    await self._fault.write_value("")
    await self._fault_code.write_value(ua.StatusCode(), ua.VariantType.StatusCode)
    await self._state.write_value("idle")
    return value, ua.StatusCode()

  async def _poll(self) -> None:
    """Sample immediate net weight without interleaving serial exchanges with method calls."""
    assert self.poll_interval is not None
    while self._started:
      async with self._command_lock:
        if not self._started:
          return
        await self._operate(self.device.read_weight_value_immediately, updates_weight=True)
      try:
        await asyncio.wait_for(self._stop_polling.wait(), timeout=self.poll_interval)
      except asyncio.TimeoutError:
        continue
      return

  async def _call(
    self,
    operation: Callable[[], Awaitable[object]],
    returns_weight: bool,
    updates_weight: bool,
    parent: ua.NodeId,
    *inputs: ua.Variant,
  ) -> ua.CallMethodResult:
    """Validate and serialize a method call, translating device failures to UA status codes."""
    result = ua.CallMethodResult()
    if parent != self._object_id:
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadMethodInvalid)
      return result
    if inputs:
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadTooManyArguments)
      return result
    async with self._command_lock:
      if not self._started:
        result.StatusCode = ua.StatusCode(ua.StatusCodes.BadNotConnected)
        return result
      value, result.StatusCode = await self._operate(
        operation, updates_weight=updates_weight, invalidates_weight=not returns_weight
      )
      if result.StatusCode.is_good() and returns_weight:
        result.OutputArguments = [ua.Variant(value, ua.VariantType.Double)]
    return result

  async def _add_monitoring_nodes(self, balance: Node, namespace: int) -> None:
    """Create a standard analog measurement plus read-only adapter state and fault variables."""
    self._weight = (
      await instantiate(
        balance,
        self.server.get_node(ua.ObjectIds.AnalogItemType),
        nodeid=ua.NodeId("Balance.Weight", namespace),
        bname=ua.QualifiedName("Weight", namespace),
        instantiate_optional=False,
      )
    )[0]
    await self._weight.write_attribute(
      ua.AttributeIds.DataType,
      ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)),
    )
    await self._weight.write_value_rank(-1)
    await self._weight.write_array_dimensions([])
    await self._weight.set_read_only()
    await self._weight.write_attribute(
      ua.AttributeIds.Description,
      ua.DataValue(ua.Variant(ua.LocalizedText("Latest net weight in grams; may be unstable"))),
    )
    eu_range = await self._weight.get_child("0:EURange")
    await eu_range.write_value(ua.Range(Low=-self.device.capacity, High=self.device.capacity))
    await self._weight.add_property(
      ua.NodeId("Balance.Weight.EngineeringUnits", namespace),
      ua.QualifiedName("EngineeringUnits", 0),
      ua.EUInformation(
        NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
        UnitId=4674125,  # UN/CEFACT GRM.
        DisplayName=ua.LocalizedText("g"),
        Description=ua.LocalizedText("gram"),
      ),
    )
    self._state = await balance.add_variable(
      ua.NodeId("Balance.DeviceState", namespace),
      ua.QualifiedName("DeviceState", namespace),
      "idle",
    )
    self._fault = await balance.add_variable(
      ua.NodeId("Balance.Fault", namespace), ua.QualifiedName("Fault", namespace), ""
    )
    self._fault_code = await balance.add_variable(
      ua.NodeId("Balance.FaultCode", namespace),
      ua.QualifiedName("FaultCode", namespace),
      ua.StatusCode(),
      ua.VariantType.StatusCode,
    )

  async def setup(self) -> None:
    """Publish balance identity and methods, then start listening for OPC UA clients.

    Repeated calls while running have no effect. No balance setup or reset is
    issued. Background immediate-weight polling runs only when poll_interval is set.
    """
    async with self._lifecycle_lock:
      if self._started:
        return
      if not self._initialized:
        # Read identity before initializing the address space; setup belongs to the caller.
        properties = {
          "DeviceType": self.device.device_type,
          "SerialNumber": self.device.serial_number,
          "FirmwareVersion": self.device.firmware_version,
          "Unit": "g",
        }
        await self.server.init()
        namespace = await self.server.register_namespace(self.NAMESPACE_URI)
        balance = await self.server.nodes.objects.add_object(
          ua.NodeId("Balance", namespace), ua.QualifiedName("Balance", namespace)
        )
        self._object_id = balance.nodeid
        for name, value in properties.items():
          await balance.add_property(
            ua.NodeId(f"Balance.{name}", namespace),
            ua.QualifiedName(name, namespace),
            value,
            ua.VariantType.String,
          )
        await balance.add_property(
          ua.NodeId("Balance.Capacity", namespace),
          ua.QualifiedName("Capacity", namespace),
          self.device.capacity,
          ua.VariantType.Double,
        )
        await self._add_monitoring_nodes(balance, namespace)
        methods: dict[str, tuple[Callable[[], Awaitable[object]], bool, bool]] = {
          "ReadStableWeight": (self.device.read_stable_weight, True, True),
          "ReadWeightImmediately": (self.device.read_weight_value_immediately, True, True),
          "ReadTareWeight": (self.device.request_tare_weight, True, False),
          "Zero": (self.device.zero, False, False),
          "Tare": (self.device.tare, False, False),
          "ClearTare": (self.device.clear_tare, False, False),
        }
        for name, (operation, returns_weight, updates_weight) in methods.items():
          await balance.add_method(
            ua.NodeId(f"Balance.{name}", namespace),
            ua.QualifiedName(name, namespace),
            partial(self._call, operation, returns_weight, updates_weight),
            [],
            [ua.VariantType.Double] if returns_weight else [],
          )
        self._initialized = True
      await self._publish_weight(None, ua.StatusCodes.BadWaitingForInitialData)
      assert self._state is not None and self._fault is not None and self._fault_code is not None
      await self._state.write_value("idle")
      await self._fault.write_value("")
      await self._fault_code.write_value(ua.StatusCode(), ua.VariantType.StatusCode)
      await self.server.start()
      self._started = True
      self._stop_polling.clear()
      if self.poll_interval is not None:
        self._poll_task = asyncio.create_task(self._poll())
      logger.warning("The Mettler Toledo OPC UA interface has not been verified on real hardware")
      logger.info("Serving balance at %s", self.server.endpoint.geturl())

  async def stop(self) -> None:
    """Finish any active command and stop serving, leaving the balance connection open."""
    async with self._lifecycle_lock:
      if not self._started:
        return
      self._started = False
      self._stop_polling.set()
      if self._poll_task is not None:
        await self._poll_task
        self._poll_task = None
      async with self._command_lock:
        await self._publish_weight(None, ua.StatusCodes.BadNotConnected)
        assert self._state is not None
        await self._state.write_value("stopped")
        await self.server.stop()
      logger.info("Stopped the Mettler Toledo OPC UA server")
