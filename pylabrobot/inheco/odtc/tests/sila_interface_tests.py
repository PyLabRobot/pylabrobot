"""Tests for ODTCDriver event handling and ODTCThermocyclerBackend."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.capabilities.thermocycling.standard import Protocol, Ramp, Stage, Step
from pylabrobot.inheco.odtc.backend import ODTCThermocyclerBackend
from pylabrobot.inheco.odtc.driver import ODTCDriver
from pylabrobot.inheco.odtc.model import ODTCPID, ODTCProtocol
from pylabrobot.inheco.scila.inheco_sila_interface import SiLAError, SiLAState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interface() -> ODTCDriver:
  """Create ODTCDriver without starting the HTTP server."""
  iface = ODTCDriver.__new__(ODTCDriver)
  iface._machine_ip = "127.0.0.1"
  iface._client_ip = "127.0.0.1"
  import logging
  iface._logger = logging.getLogger("test")
  iface._pending_by_id = {}
  iface._data_events_by_request_id = {}
  iface._loop = None
  iface._httpd = None
  iface._server_task = None
  iface._closed = False
  iface._lock_id = None
  return iface


def _add_pending(iface: ODTCDriver, command: str, request_id: int):
  """Register a pending async command future on the interface."""
  from pylabrobot.inheco.scila.inheco_sila_interface import InhecoSiLAInterface
  fut = asyncio.get_event_loop().create_future()
  iface._pending_by_id[request_id] = InhecoSiLAInterface._SiLACommand(
    name=command, request_id=request_id, fut=fut
  )
  return fut


# ---------------------------------------------------------------------------
# Event handling tests
# ---------------------------------------------------------------------------


class TestODTCDriverEvents(unittest.TestCase):
  def setUp(self):
    self.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self.loop)
    self.iface = _make_interface()

  def tearDown(self):
    self.loop.close()

  def test_error_event_raises_sila_error_not_runtime_error(self):
    """ErrorEvent must complete pending future with SiLAError, not RuntimeError."""
    fut = _add_pending(self.iface, "ExecuteMethod", 42)
    self.iface._on_error_event({
      "requestId": 42,
      "returnValue": {"returnCode": 9, "message": "Device error"},
    })
    self.assertTrue(fut.done())
    exc = fut.exception()
    self.assertIsInstance(exc, SiLAError, f"Expected SiLAError, got {type(exc)}")
    self.assertEqual(exc.code, 9)

  def test_error_event_no_pending_does_not_crash(self):
    """ErrorEvent for unknown requestId should log and not raise."""
    self.iface._on_error_event({
      "requestId": 999,
      "returnValue": {"returnCode": 9, "message": "Unknown"},
    })

  def test_status_event_error_handling_state_rejects_pending(self):
    """StatusEvent with errorHandling state should reject pending ExecuteMethod future."""
    fut = _add_pending(self.iface, "ExecuteMethod", 100)
    self.iface._on_status_event({
      "eventDescription": {
        "DeviceState": "errorHandling",
        "Extensions": ["DeviceError", 2001, "0x7D1", "MotorError", "Motor fault detected"],
      }
    })
    self.assertTrue(fut.done())
    exc = fut.exception()
    self.assertIsInstance(exc, SiLAError)
    self.assertEqual(exc.code, 2001)
    self.assertIn("Motor fault", exc.message)

  def test_status_event_in_error_includes_recovery_hint(self):
    """StatusEvent with inError should include power-cycle hint in message."""
    fut = _add_pending(self.iface, "ExecuteMethod", 101)
    self.iface._on_status_event({
      "eventDescription": {
        "DeviceState": "inError",
        "Extensions": ["DeviceError", 1000, "0x3E8", "ThermalRunaway", "Block overheated"],
      }
    })
    exc = fut.exception()
    self.assertIsInstance(exc, SiLAError)
    self.assertIn("power cycle", exc.message.lower())

  def test_status_event_idle_does_not_reject_pending(self):
    """StatusEvent with IDLE state should NOT reject pending futures."""
    fut = _add_pending(self.iface, "ExecuteMethod", 102)
    self.iface._on_status_event({
      "eventDescription": {"DeviceState": "idle", "Extensions": []}
    })
    self.assertFalse(fut.done())

  def test_response_event_code_1_completes_normally(self):
    """ResponseEvent with code 1 (success no data) should complete future with None."""
    fut = _add_pending(self.iface, "StopMethod", 200)
    self.iface._on_response_event({
      "requestId": 200,
      "returnValue": {"returnCode": 1, "message": "Success"},
    })
    self.assertTrue(fut.done())
    self.assertIsNone(fut.result())

  def test_response_event_code_3_with_data_completes(self):
    """ResponseEvent with code 3 and responseData should complete future with parsed XML."""
    import xml.etree.ElementTree as ET
    fut = _add_pending(self.iface, "GetParameters", 201)
    xml_data = "<Root><Value>test</Value></Root>"
    self.iface._on_response_event({
      "requestId": 201,
      "returnValue": {"returnCode": 3, "message": ""},
      "responseData": xml_data,
    })
    self.assertTrue(fut.done())
    result = fut.result()
    self.assertIsNotNone(result)
    self.assertIsInstance(result, ET.Element)

  def test_response_event_non_success_raises_sila_error(self):
    """ResponseEvent with code != 1 or 3 should reject with SiLAError."""
    fut = _add_pending(self.iface, "SetParameters", 202)
    self.iface._on_response_event({
      "requestId": 202,
      "returnValue": {"returnCode": 9, "message": "Invalid state"},
    })
    self.assertTrue(fut.done())
    exc = fut.exception()
    self.assertIsInstance(exc, SiLAError)
    self.assertEqual(exc.code, 9)

  def test_device_error_code_raises_sila_error(self):
    """_handle_device_error_code (1000+ codes) must raise SiLAError."""
    with self.assertRaises(SiLAError) as ctx:
      self.iface._handle_device_error_code(2001, "Motor fault", "ExecuteMethod")
    self.assertEqual(ctx.exception.code, 2001)

  def test_unknown_device_error_code_also_raises(self):
    """Unknown 1000+ codes (not in original whitelist) must also raise SiLAError."""
    with self.assertRaises(SiLAError):
      self.iface._handle_device_error_code(2010, "Unknown error", "ExecuteMethod")


# ---------------------------------------------------------------------------
# Backend params tests
# ---------------------------------------------------------------------------


class TestRunProtocolParams(unittest.TestCase):
  def test_default_params(self):
    p = ODTCThermocyclerBackend.RunProtocolParams()
    self.assertEqual(p.variant, 96)
    self.assertEqual(p.fluid_quantity, 1)
    self.assertTrue(p.post_heating)
    self.assertTrue(p.dynamic_pre_method_duration)
    self.assertIsNone(p.name)

  def test_custom_params(self):
    p = ODTCThermocyclerBackend.RunProtocolParams(
      variant=384, fluid_quantity=2, name="PCR_384"
    )
    self.assertEqual(p.variant, 384)
    self.assertEqual(p.fluid_quantity, 2)
    self.assertEqual(p.name, "PCR_384")

  def test_step_params_default(self):
    sp = ODTCThermocyclerBackend.StepParams()
    self.assertEqual(sp.pid_number, 1)


class TestBackendResolvesProtocol(unittest.TestCase):
  def _make_backend(self) -> ODTCThermocyclerBackend:
    driver = MagicMock(spec=ODTCDriver)
    backend = ODTCThermocyclerBackend(driver=driver, variant=96)
    return backend

  def test_plain_protocol_compiles_to_odtc_protocol(self):
    backend = self._make_backend()
    protocol = Protocol(
      stages=[Stage(steps=[Step(95.0, 30.0)], repeats=1)],
      name="TestPCR",
    )
    params = ODTCThermocyclerBackend.RunProtocolParams(variant=96, fluid_quantity=1)
    odtc = backend._resolve_odtc_protocol(protocol, params)
    self.assertIsInstance(odtc, ODTCProtocol)
    self.assertEqual(len(odtc.stages), 1)

  def test_odtc_protocol_used_directly(self):
    backend = self._make_backend()
    odtc = ODTCProtocol(
      stages=[Stage(steps=[Step(95.0, 30.0)], repeats=1)],
      name="DirectPCR",
      variant=96, plate_type=0, fluid_quantity=1, post_heating=True,
      start_block_temperature=25.0, start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)],
    )
    params = ODTCThermocyclerBackend.RunProtocolParams()
    resolved = backend._resolve_odtc_protocol(odtc, params)
    self.assertIs(resolved, odtc)


if __name__ == "__main__":
  unittest.main()
