"""OPC UA client/server tests using a mocked balance serial transport."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from pylabrobot.io.serial import Serial
from pylabrobot.mettler_toledo.scales import MettlerToledoOPCUAServer, MTSICSDriver
from pylabrobot.mettler_toledo.scales.opcua import USE_ASYNCUA

if USE_ASYNCUA:
  from asyncua import Client, ua  # type: ignore[import-untyped]


@unittest.skipUnless(USE_ASYNCUA, "Install PyLabRobot[opcua] to test the OPC UA interface")
class MettlerToledoOPCUAServerTests(unittest.IsolatedAsyncioTestCase):
  """Exercise real OPC UA messages without opening a hardware connection."""

  async def asyncSetUp(self) -> None:
    """Serve a balance with a mocked serial transport on an ephemeral loopback port."""
    self.device = MTSICSDriver(port="mock")
    self.io = Mock(spec=Serial)
    self.io.human_readable_device_name = "Mock balance"
    self.device.io = self.io
    self.device.device_type = "WXS205SDU"
    self.device.serial_number = "123456"
    self.device.firmware_version = "1.10"
    self.device.capacity = 220.009
    self.adapter = MettlerToledoOPCUAServer(self.device, endpoint="opc.tcp://127.0.0.1:0/")
    await self.adapter.setup()
    self.addAsyncCleanup(self.adapter.stop)
    assert self.adapter.server.bserver is not None
    self.endpoint = f"opc.tcp://127.0.0.1:{self.adapter.server.bserver.port}/"
    self.client = Client(self.endpoint)
    await self.client.connect()
    self.addAsyncCleanup(self.client.disconnect)
    self.namespace = await self.client.get_namespace_index(self.adapter.NAMESPACE_URI)
    self.balance = await self.client.nodes.objects.get_child(f"{self.namespace}:Balance")

  async def test_identity_and_method_metadata(self) -> None:
    """Clients can discover stable node IDs, read-only properties, and Double outputs."""
    self.assertEqual(self.balance.nodeid, ua.NodeId("Balance", self.namespace))
    for name, expected in {
      "DeviceType": "WXS205SDU",
      "SerialNumber": "123456",
      "FirmwareVersion": "1.10",
      "Capacity": 220.009,
      "Unit": "g",
    }.items():
      with self.subTest(property=name):
        node = await self.balance.get_child(f"{self.namespace}:{name}")
        self.assertEqual(node.nodeid, ua.NodeId(f"Balance.{name}", self.namespace))
        self.assertEqual(await node.read_value(), expected)
        with self.assertRaises(ua.UaStatusCodeError) as caught:
          await node.write_value(expected)
        self.assertEqual(caught.exception.code, ua.StatusCodes.BadUserAccessDenied)
    method = await self.balance.get_child(f"{self.namespace}:ReadStableWeight")
    output = await method.get_child("0:OutputArguments")
    arguments = await output.read_value()
    self.assertEqual(len(arguments), 1)
    self.assertEqual(arguments[0].DataType, ua.NodeId(ua.ObjectIds.Double))
    self.io.write.assert_not_called()
    self.io.setup.assert_not_called()

  async def test_operations_use_existing_driver(self) -> None:
    """Each UA method sends the expected MT-SICS command and returns grams or no value."""
    for name, command, response, expected in [
      ("ReadStableWeight", b"S\r\n", b"S S 1.234 g\r\n", 1.234),
      ("ReadWeightImmediately", b"SI\r\n", b"S D -0.002 g\r\n", -0.002),
      ("ReadTareWeight", b"TA\r\n", b"TA A 10.5 g\r\n", 10.5),
      ("Zero", b"Z\r\n", b"Z A\r\n", None),
      ("Tare", b"T\r\n", b"T S 10.5 g\r\n", None),
      ("ClearTare", b"TAC\r\n", b"TAC A\r\n", None),
    ]:
      with self.subTest(method=name):
        self.io.readline.return_value = response
        actual = await self.balance.call_method(f"{self.namespace}:{name}")
        self.assertEqual(actual, expected)
        self.io.write.assert_awaited_with(command)

  async def test_device_failures_are_bad_statuses(self) -> None:
    """Overload, invalid units, timeout, and disconnects never return a successful weight."""
    for response, exception, expected in [
      (b"S +\r\n", None, ua.StatusCodes.BadDeviceFailure),
      (b"S S 1.0 kg\r\n", None, ua.StatusCodes.BadDeviceFailure),
      (b"", TimeoutError("Timed out"), ua.StatusCodes.BadTimeout),
      (b"", OSError("Disconnected"), ua.StatusCodes.BadCommunicationError),
    ]:
      with self.subTest(status=expected, response=response):
        self.io.readline.return_value = response
        self.io.readline.side_effect = exception
        with self.assertLogs("pylabrobot.mettler_toledo.scales.opcua", level="ERROR"):
          with self.assertRaises(ua.UaStatusCodeError) as caught:
            await self.balance.call_method(f"{self.namespace}:ReadStableWeight")
        self.assertEqual(caught.exception.code, expected)
    self.io.readline.side_effect = None
    self.io.readline.return_value = b"S S 2.5 g\r\n"
    self.assertEqual(await self.balance.call_method(f"{self.namespace}:ReadStableWeight"), 2.5)

  async def test_extra_arguments_do_not_touch_device(self) -> None:
    """Reject unexpected input before any serial command is issued."""
    with self.assertRaises(ua.UaStatusCodeError) as caught:
      await self.balance.call_method(f"{self.namespace}:Zero", 42)
    self.assertEqual(caught.exception.code, ua.StatusCodes.BadTooManyArguments)
    self.io.write.assert_not_called()

  async def test_wrong_parent_does_not_touch_device(self) -> None:
    """A method must be invoked on its owning balance object."""
    with self.assertRaises(ua.UaStatusCodeError) as caught:
      await self.client.nodes.objects.call_method(ua.NodeId("Balance.Zero", self.namespace))
    self.assertEqual(caught.exception.code, ua.StatusCodes.BadMethodInvalid)
    self.io.write.assert_not_called()

  async def test_concurrent_clients_serialize_commands(self) -> None:
    """A second client cannot send its command until the first response has arrived."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read_response() -> bytes:
      """Hold the first serial response until both clients have submitted a request."""
      entered.set()
      await release.wait()
      return b"S S 1.5 g\r\n"

    self.io.readline.side_effect = read_response
    async with Client(self.endpoint) as second_client:
      second_balance = second_client.get_node(self.balance.nodeid)
      first = asyncio.create_task(self.balance.call_method(f"{self.namespace}:ReadStableWeight"))
      second = None
      try:
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(
          second_balance.call_method(f"{self.namespace}:ReadWeightImmediately")
        )
        await asyncio.sleep(0.05)
        self.io.write.assert_awaited_once_with(b"S\r\n")
      finally:
        release.set()
        self.assertEqual(await first, 1.5)
        if second is not None:
          self.assertEqual(await second, 1.5)
    self.assertEqual(self.io.write.await_count, 2)

  async def test_lifecycle_leaves_device_connection_to_caller(self) -> None:
    """Repeated setup/stop calls are harmless and never reset or close the balance."""
    await self.adapter.setup()
    self.io.write.assert_not_called()
    await self.client.disconnect()
    await self.adapter.stop()
    await self.adapter.stop()
    self.io.stop.assert_not_called()
    self.io.write.assert_not_called()
    await self.adapter.setup()
    assert self.adapter.server.bserver is not None
    endpoint = f"opc.tcp://127.0.0.1:{self.adapter.server.bserver.port}/"
    async with Client(endpoint) as client:
      self.io.readline.return_value = b"S S 1.0 g\r\n"
      balance = client.get_node(self.balance.nodeid)
      self.assertEqual(await balance.call_method(f"{self.namespace}:ReadStableWeight"), 1.0)

  async def test_stop_waits_for_active_command(self) -> None:
    """Shutdown finishes an active serial exchange and rejects queued operations."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def read_response() -> bytes:
      """Hold a response until shutdown is waiting for the command lock."""
      entered.set()
      await release.wait()
      return b"S S 3.0 g\r\n"

    self.io.readline.side_effect = read_response
    balance = self.adapter.server.get_node(self.balance.nodeid)
    first = asyncio.create_task(balance.call_method(f"{self.namespace}:ReadStableWeight"))
    stopping = None
    try:
      await asyncio.wait_for(entered.wait(), 2)
      stopping = asyncio.create_task(self.adapter.stop())
      await asyncio.sleep(0)
      self.assertFalse(stopping.done())
    finally:
      release.set()
      self.assertEqual(await first, 3.0)
      if stopping is not None:
        await stopping
    with self.assertRaises(ua.UaStatusCodeError) as caught:
      await balance.call_method(f"{self.namespace}:Zero")
    self.assertEqual(caught.exception.code, ua.StatusCodes.BadNotConnected)
    self.io.write.assert_awaited_once_with(b"S\r\n")

  async def test_start_failure_can_be_retried(self) -> None:
    """A failed bind does not mark the adapter as running or duplicate balance nodes."""
    await self.client.disconnect()
    await self.adapter.stop()
    with patch.object(self.adapter.server, "start", new=AsyncMock(side_effect=OSError("In use"))):
      with self.assertRaisesRegex(OSError, "In use"):
        await self.adapter.setup()
    await self.adapter.setup()
    children = await self.adapter.server.nodes.objects.get_children()
    self.assertEqual(sum(node.nodeid == self.balance.nodeid for node in children), 1)
    self.io.write.assert_not_called()
