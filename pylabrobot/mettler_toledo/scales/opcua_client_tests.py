"""Typed client tests against a loopback server with mocked serial I/O."""

import unittest
from typing import cast
from unittest.mock import Mock, patch

from pylabrobot.io.serial import Serial
from pylabrobot.mettler_toledo import (
  BalanceClient,
  BalanceInfo,
  MettlerToledoOPCUAServer,
  MTSICSDriver,
)
from pylabrobot.mettler_toledo.scales.opcua_client import USE_ASYNCUA

if USE_ASYNCUA:
  from asyncua import ua  # type: ignore[import-untyped]


@unittest.skipUnless(USE_ASYNCUA, "Install PyLabRobot[opcua] to test the balance client")
class BalanceClientTests(unittest.IsolatedAsyncioTestCase):
  """Check namespace discovery, typed operations, errors, and session cleanup."""

  async def asyncSetUp(self) -> None:
    """Start a simulated transport behind a server with a non-default namespace index."""
    device = MTSICSDriver(port="mock")
    self.io = Mock(spec=Serial)
    self.io.human_readable_device_name = "Simulated balance"
    device.io = self.io
    device.device_type = "WXS205SDU (SIMULATED)"
    device.serial_number = "SIMULATED-001"
    device.firmware_version = "simulation"
    device.capacity = 220.009
    self.server = MettlerToledoOPCUAServer(device, "opc.tcp://127.0.0.1:0/")
    register = self.server.server.register_namespace

    async def register_after_padding(uri: str) -> int:
      """Occupy the usual index so hard-coded namespace indices fail."""
      await register("urn:pylabrobot:test:padding")
      return cast(int, await register(uri))

    with patch.object(self.server.server, "register_namespace", new=register_after_padding):
      await self.server.setup()
    self.addAsyncCleanup(self.server.stop)
    assert self.server.server.bserver is not None
    self.endpoint = f"opc.tcp://127.0.0.1:{self.server.server.bserver.port}/"
    self.balance = BalanceClient(self.endpoint)
    self.addAsyncCleanup(self.balance.disconnect)

  async def test_namespace_identity_and_operations(self) -> None:
    """Expose typed identity and every method without leaking namespace handling to callers."""
    async with self.balance as balance:
      self.assertEqual(balance._namespace, 3)
      self.assertEqual(
        await balance.get_info(),
        BalanceInfo("WXS205SDU (SIMULATED)", "SIMULATED-001", "simulation", 220.009, "g"),
      )
      for operation, command, response, expected in [
        (balance.read_weight, b"S\r\n", b"S S 12.345 g\r\n", 12.345),
        (balance.read_weight_immediately, b"SI\r\n", b"S D -1.5 g\r\n", -1.5),
        (balance.read_tare_weight, b"TA\r\n", b"TA A 4.0 g\r\n", 4.0),
        (balance.zero, b"Z\r\n", b"Z A\r\n", None),
        (balance.tare, b"T\r\n", b"T S 12.345 g\r\n", None),
        (balance.clear_tare, b"TAC\r\n", b"TAC A\r\n", None),
      ]:
        with self.subTest(command=command):
          self.io.readline.return_value = response
          self.assertEqual(await operation(), expected)
          self.io.write.assert_awaited_with(command)
    self.io.setup.assert_not_called()
    self.io.stop.assert_not_called()

  async def test_connect_disconnect_and_context_cleanup(self) -> None:
    """Reconnect rediscoveries and context exceptions leave no usable stale session."""
    with self.assertRaisesRegex(RuntimeError, "not connected"):
      await self.balance.read_weight()
    with patch.object(
      self.balance.client, "get_namespace_index", wraps=self.balance.client.get_namespace_index
    ) as lookup:
      await self.balance.connect()
      await self.balance.connect()
      await self.balance.disconnect()
      await self.balance.disconnect()
      with self.assertRaisesRegex(ValueError, "body failed"):
        async with self.balance:
          raise ValueError("body failed")
      self.assertEqual(lookup.await_count, 2)
    with self.assertRaisesRegex(RuntimeError, "not connected"):
      await self.balance.get_info()

  async def test_discovery_failure_disconnects(self) -> None:
    """A server with a different namespace does not leave an open session behind."""
    with patch.object(self.balance, "NAMESPACE_URI", "urn:missing"):
      with patch.object(
        self.balance.client, "disconnect", wraps=self.balance.client.disconnect
      ) as disconnect:
        with self.assertRaises(ValueError):
          await self.balance.connect()
        disconnect.assert_awaited_once()
    async with self.balance:
      self.assertEqual((await self.balance.get_info()).serial_number, "SIMULATED-001")

  async def test_status_codes_are_preserved_without_retries(self) -> None:
    """A device rejection remains a UaStatusCodeError and sends exactly one command."""
    self.io.readline.return_value = b"S +\r\n"
    async with self.balance:
      with self.assertLogs("pylabrobot.mettler_toledo.scales.opcua", level="ERROR"):
        with self.assertRaises(ua.UaStatusCodeError) as caught:
          await self.balance.read_weight()
      self.assertEqual(caught.exception.code, ua.StatusCodes.BadDeviceFailure)
      self.io.write.assert_awaited_once_with(b"S\r\n")

  async def test_malformed_method_results_are_rejected(self) -> None:
    """Reject missing, extra, wrongly typed, and array outputs instead of returning junk."""
    async with self.balance:
      namespace = self.balance._namespace
      for outputs in [
        [],
        [ua.Variant(1.0), ua.Variant(2.0)],
        [ua.Variant("12.345")],
        [ua.Variant([1.0], ua.VariantType.Double)],
        [ua.Variant(1.0, ua.VariantType.Float)],
      ]:
        with self.subTest(outputs=outputs):
          node = self.server.server.get_node(ua.NodeId("Balance.ReadStableWeight", namespace))

          async def malformed(parent: ua.NodeId) -> ua.CallMethodResult:
            """Return an incompatible server result for this test case."""
            return ua.CallMethodResult(OutputArguments=outputs)

          self.server.server.link_method(node, malformed)
          with self.assertRaises(ValueError):
            await self.balance.read_weight()
      self.io.write.assert_not_called()

  async def test_control_methods_reject_unexpected_output(self) -> None:
    """Void methods cannot silently discard an incompatible response."""
    async with self.balance:
      node = self.server.server.get_node(ua.NodeId("Balance.Tare", self.balance._namespace))

      async def malformed(parent: ua.NodeId) -> ua.CallMethodResult:
        """Return an output from a method whose contract has no outputs."""
        return ua.CallMethodResult(OutputArguments=[ua.Variant(1.0)])

      self.server.server.link_method(node, malformed)
      with self.assertRaisesRegex(ValueError, "output arguments"):
        await self.balance.tare()

  async def test_incompatible_units_are_rejected(self) -> None:
    """Identity cannot advertise another unit while the client promises grams."""
    async with self.balance:
      unit = self.server.server.get_node(ua.NodeId("Balance.Unit", self.balance._namespace))
      await unit.write_value("kg")
      with self.assertRaisesRegex(ValueError, "capacity in grams"):
        await self.balance.get_info()

  async def test_property_status_is_preserved(self) -> None:
    """A failed property read remains an OPC UA status error, not a missing-value error."""
    async with self.balance:
      unit = self.server.server.get_node(ua.NodeId("Balance.Unit", self.balance._namespace))
      await unit.write_value(
        ua.DataValue(StatusCode_=ua.StatusCode(ua.StatusCodes.BadNotConnected))
      )
      with self.assertRaises(ua.UaStatusCodeError) as caught:
        await self.balance.get_info()
      self.assertEqual(caught.exception.code, ua.StatusCodes.BadNotConnected)
