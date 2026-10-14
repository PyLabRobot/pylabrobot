"""Monitoring and subscription tests against an in-memory balance."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, Callable
from unittest.mock import patch

from .opcua import USE_ASYNCUA, MettlerToledoOPCUAServer
from .opcua_simulator import SIMULATION_NAMESPACE_URI, SimulatedBalance, add_simulation_controls

if USE_ASYNCUA:
  from asyncua import Client, Node, ua  # type: ignore[import-untyped]


class Notifications:
  """Collect full data values, including bad quality, from a real UA subscription."""

  def __init__(self) -> None:
    """Create a queue for the subscription callback."""
    self.queue: asyncio.Queue[tuple[Node, ua.DataValue]] = asyncio.Queue()

  def datachange_notification(self, node: Node, value: object, data: Any) -> None:
    """Save the server-supplied status and timestamp along with the value."""
    self.queue.put_nowait((node, data.monitored_item.Value))

  async def wait_for(self, node: Node, predicate: Callable[[ua.DataValue], bool]) -> ua.DataValue:
    """Wait for a matching notification with a bounded test timeout."""

    async def receive() -> ua.DataValue:
      """Ignore unrelated notifications until the requested value arrives."""
      while True:
        changed_node, value = await self.queue.get()
        if changed_node == node and predicate(value):
          return value

    return await asyncio.wait_for(receive(), 3)


@unittest.skipUnless(USE_ASYNCUA, "Install PyLabRobot[opcua] to test subscriptions")
class MonitoringTests(unittest.IsolatedAsyncioTestCase):
  """Verify standard metadata, subscriptions, quality changes, and polling ownership."""

  async def start(self, poll_interval: float | None = None) -> None:
    """Start a simulator on a free loopback port and connect a generic OPC UA client."""
    self.device = SimulatedBalance()
    self.adapter = MettlerToledoOPCUAServer(
      self.device, "opc.tcp://127.0.0.1:0/", poll_interval=poll_interval
    )
    await self.adapter.setup()
    self.addAsyncCleanup(self.adapter.stop)
    await add_simulation_controls(self.adapter, self.device)
    assert self.adapter.server.bserver is not None
    self.client = Client(f"opc.tcp://127.0.0.1:{self.adapter.server.bserver.port}/")
    await self.client.connect()
    self.addAsyncCleanup(self.client.disconnect)
    self.namespace = await self.client.get_namespace_index(self.adapter.NAMESPACE_URI)
    self.balance = await self.client.nodes.objects.get_child(f"{self.namespace}:Balance")
    self.weight = await self.balance.get_child(f"{self.namespace}:Weight")
    self.state = await self.balance.get_child(f"{self.namespace}:DeviceState")
    self.fault = await self.balance.get_child(f"{self.namespace}:Fault")
    self.fault_code = await self.balance.get_child(f"{self.namespace}:FaultCode")
    self.sim_namespace = await self.client.get_namespace_index(SIMULATION_NAMESPACE_URI)
    self.simulation = await self.client.nodes.objects.get_child(f"{self.sim_namespace}:Simulation")

  async def test_standard_metadata_and_initial_quality(self) -> None:
    """A generic client can identify a scalar analog value in grams before any measurement."""
    await self.start()
    self.assertEqual(
      await self.weight.read_type_definition(), ua.NodeId(ua.ObjectIds.AnalogItemType)
    )
    self.assertEqual(await self.weight.read_data_type(), ua.NodeId(ua.ObjectIds.Double))
    self.assertEqual(await self.weight.read_value_rank(), -1)
    units = await (await self.weight.get_child("0:EngineeringUnits")).read_value()
    self.assertEqual(units.UnitId, 4674125)
    self.assertEqual(units.DisplayName.Text, "g")
    limits = await (await self.weight.get_child("0:EURange")).read_value()
    self.assertEqual((limits.Low, limits.High), (-220.009, 220.009))
    sample = await self.weight.read_data_value(raise_on_bad_status=False)
    self.assertEqual(sample.StatusCode.value, ua.StatusCodes.BadWaitingForInitialData)
    self.assertIsNone(sample.Value.Value)
    self.assertIsNotNone(sample.SourceTimestamp)
    self.assertEqual(await self.state.read_value(), "idle")
    for node, value in (
      (self.weight, 1.0),
      (self.state, "fault"),
      (self.fault, "fake"),
      (self.fault_code, ua.StatusCode()),
    ):
      with self.subTest(node=node):
        with self.assertRaises(ua.UaStatusCodeError):
          await node.write_value(value)

  async def test_method_updates_and_tare_invalidation(self) -> None:
    """Net measurements publish weight; tare readings do not replace it; controls invalidate it."""
    await self.start()
    await self.balance.call_method(f"{self.namespace}:ReadStableWeight")
    self.assertEqual(await self.weight.read_value(), 12.345)
    await self.balance.call_method(f"{self.namespace}:ReadTareWeight")
    self.assertEqual(await self.weight.read_value(), 12.345)
    await self.balance.call_method(f"{self.namespace}:Tare")
    self.assertEqual(
      (await self.weight.read_data_value(False)).StatusCode.value,
      ua.StatusCodes.BadWaitingForInitialData,
    )
    await self.balance.call_method(f"{self.namespace}:ReadWeightImmediately")
    self.assertEqual(await self.weight.read_value(), 0.0)

  async def test_subscription_observes_load_fault_and_recovery(self) -> None:
    """Polling pushes changes and bad quality to a client that never calls a read method."""
    await self.start(poll_interval=0.03)
    handler = Notifications()
    subscription = await self.client.create_subscription(20, handler)
    self.addAsyncCleanup(subscription.delete)
    for node in (self.weight, self.state, self.fault, self.fault_code):
      await subscription.subscribe_data_change(node, queuesize=50)
    initial = await handler.wait_for(
      self.weight, lambda data: data.StatusCode.is_good() and data.Value.Value == 12.345
    )
    await self.simulation.call_method(f"{self.sim_namespace}:SetLoad", 25.0)
    updated = await handler.wait_for(
      self.weight, lambda data: data.StatusCode.is_good() and data.Value.Value == 25.0
    )
    self.assertGreaterEqual(updated.SourceTimestamp, initial.SourceTimestamp)
    with self.assertLogs("pylabrobot.mettler_toledo.scales.opcua", level="ERROR"):
      await self.simulation.call_method(f"{self.sim_namespace}:SetFault", True)
      bad = await handler.wait_for(self.weight, lambda data: data.StatusCode.is_bad())
      self.assertEqual(bad.StatusCode.value, ua.StatusCodes.BadDeviceFailure)
      self.assertIsNone(bad.Value.Value)
      await handler.wait_for(self.state, lambda data: data.Value.Value == "fault")
      self.assertIn("overload", await self.fault.read_value())
      self.assertEqual((await self.fault_code.read_value()).value, ua.StatusCodes.BadDeviceFailure)
      await self.simulation.call_method(f"{self.sim_namespace}:SetFault", False)
      await handler.wait_for(
        self.weight, lambda data: data.StatusCode.is_good() and data.Value.Value == 25.0
      )
    self.assertEqual(await self.fault.read_value(), "")
    self.assertTrue((await self.fault_code.read_value()).is_good())

  async def test_failed_method_publishes_fault(self) -> None:
    """A fault is observable even with background polling disabled, and later reads recover."""
    await self.start()
    self.device.set_fault(True)
    with self.assertLogs("pylabrobot.mettler_toledo.scales.opcua", level="ERROR"):
      with self.assertRaises(ua.UaStatusCodeError):
        await self.balance.call_method(f"{self.namespace}:ReadStableWeight")
    self.assertEqual(await self.state.read_value(), "fault")
    self.assertEqual(
      (await self.weight.read_data_value(False)).StatusCode.value, ua.StatusCodes.BadDeviceFailure
    )
    self.device.set_fault(False)
    await self.balance.call_method(f"{self.namespace}:ReadStableWeight")
    self.assertEqual(await self.state.read_value(), "idle")
    self.assertEqual(await self.fault.read_value(), "")

  async def test_polls_serialize_and_shutdown_waits(self) -> None:
    """Shutdown lets a pending poll finish and prevents further background device access."""
    await self.start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_read() -> float:
      """Hold the polling read until shutdown is waiting."""
      entered.set()
      await release.wait()
      return 12.345

    await self.client.disconnect()
    await self.adapter.stop()
    self.adapter.poll_interval = 0.03
    with patch.object(
      self.device, "read_weight_value_immediately", side_effect=delayed_read
    ) as read:
      await self.adapter.setup()
      await asyncio.wait_for(entered.wait(), 3)
      stopping = asyncio.create_task(self.adapter.stop())
      try:
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
      finally:
        release.set()
        await asyncio.wait_for(stopping, 3)
      read.assert_awaited_once()
      self.assertIsNone(self.adapter._poll_task)
    assert self.adapter._weight is not None and self.adapter._state is not None
    self.assertEqual(
      (await self.adapter._weight.read_data_value(False)).StatusCode.value,
      ua.StatusCodes.BadNotConnected,
    )
    self.assertEqual(await self.adapter._state.read_value(), "stopped")

  async def test_simulation_parameters_and_validation(self) -> None:
    """Simulator methods expose parameter metadata and reject invalid values before mutation."""
    await self.start()
    method = await self.simulation.get_child(f"{self.sim_namespace}:SetLoad")
    argument = (await (await method.get_child("0:InputArguments")).read_value())[0]
    self.assertEqual(argument.Name, "load")
    self.assertEqual(argument.DataType, ua.NodeId(ua.ObjectIds.Double))
    self.assertEqual(argument.ValueRank, -1)
    self.assertIn("grams", argument.Description.Text)
    for value, status in (
      (-1.0, ua.StatusCodes.BadOutOfRange),
      (float("nan"), ua.StatusCodes.BadOutOfRange),
      (500.0, ua.StatusCodes.BadOutOfRange),
      (1, ua.StatusCodes.BadTypeMismatch),
      ([1.0], ua.StatusCodes.BadTypeMismatch),
    ):
      with self.subTest(value=value):
        with self.assertRaises(ua.UaStatusCodeError) as caught:
          await self.simulation.call_method(method, value)
        self.assertEqual(caught.exception.code, status)
    self.assertEqual(await self.balance.call_method(f"{self.namespace}:ReadStableWeight"), 12.345)

  async def test_invalid_poll_interval(self) -> None:
    """Reject intervals that could cause tight polling loops or never complete."""
    for interval in (0, -1, float("nan"), float("inf")):
      with self.subTest(interval=interval):
        with self.assertRaises(ValueError):
          MettlerToledoOPCUAServer(SimulatedBalance(), poll_interval=interval)
