import logging
import unittest
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from unittest.mock import AsyncMock, patch

import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.inheco.scila.scila import SCILA, SCILADrawerLoadingTray
from pylabrobot.inheco.transport.sila import InhecoSiLAInterface
from pylabrobot.testing.concurrency import AnyioTestBase

_TEMPERATURE_RESPONSE = (
  "<Response>"
  "  <Parameter name='CurrentTemperature'><Float64>25.0</Float64></Parameter>"
  "  <Parameter name='TargetTemperature'><Float64>37.0</Float64></Parameter>"
  "  <Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
  "</Response>"
)


class _FakeSiLAInterface:
  """Software-only SiLA interface that records its lifespan and commands."""

  def __init__(self, trace: list[str], initialize_error: Optional[Exception] = None) -> None:
    self.trace = trace
    self.initialize_error = initialize_error
    self.bound_port = 80
    self.client_ip = "127.0.0.1"
    self.machine_ip = "127.0.0.1"

  @asynccontextmanager
  async def lifespan(self) -> AsyncIterator["_FakeSiLAInterface"]:
    self.trace.append("transport-enter")
    try:
      yield self
    finally:
      self.trace.append("transport-exit")

  async def send_command(self, command: str, **kwargs: Any) -> Any:
    self.trace.append(command)
    if command == "Initialize" and self.initialize_error is not None:
      raise self.initialize_error
    return {}


class _SCILATestCase(unittest.IsolatedAsyncioTestCase):
  """Builds a SCILA whose SiLA transport is mocked."""

  def setUp(self):
    self.patcher = patch("pylabrobot.inheco.scila.scila.InhecoSiLAInterface")
    self.MockInhecoSiLAInterface = self.patcher.start()
    self.mock_sila_interface = AsyncMock(spec=InhecoSiLAInterface)
    self.mock_sila_interface.bound_port = 80
    self.mock_sila_interface.client_ip = "127.0.0.1"
    self.MockInhecoSiLAInterface.return_value = self.mock_sila_interface
    self.scila = SCILA(name="scila", scila_ip="127.0.0.1")

  def tearDown(self):
    self.patcher.stop()


class TestSCILA(_SCILATestCase):
  async def test_request_status(self):
    self.mock_sila_interface.send_command.return_value = {"GetStatusResponse": {"state": "Standby"}}
    self.assertEqual(await self.scila.request_status(), "Standby")
    self.mock_sila_interface.send_command.assert_called_with("GetStatus")

  async def test_request_liquid_level(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response><Parameter name='LiquidLevel'><String>Ok</String></Parameter></Response>"
    )
    self.assertEqual(await self.scila.request_liquid_level(), "Ok")
    self.mock_sila_interface.send_command.assert_called_with("GetLiquidLevel")

  async def test_request_drawer_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='Drawer1'><String>Closed</String></Parameter>"
      "  <Parameter name='Drawer2'><String>Opened</String></Parameter>"
      "  <Parameter name='Drawer3'><String>Closed</String></Parameter>"
      "  <Parameter name='Drawer4'><String>Closed</String></Parameter>"
      "</Response>"
    )
    self.assertEqual(
      await self.scila.request_drawer_statuses(),
      {1: "Closed", 2: "Opened", 3: "Closed", 4: "Closed"},
    )
    self.mock_sila_interface.send_command.assert_called_with("GetDoorStatus")

  async def test_request_drawer_status_single(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='Drawer1'><String>Closed</String></Parameter>"
      "  <Parameter name='Drawer2'><String>Opened</String></Parameter>"
      "  <Parameter name='Drawer3'><String>Closed</String></Parameter>"
      "  <Parameter name='Drawer4'><String>Closed</String></Parameter>"
      "</Response>"
    )
    self.assertEqual(await self.scila.request_drawer_status(2), "Opened")

  async def test_request_drawer_status_invalid_id(self):
    with self.assertRaises(ValueError):
      await self.scila.request_drawer_status(5)

  async def test_request_co2_flow_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response><Parameter name='CO2FlowStatus'><String>Ok</String></Parameter></Response>"
    )
    self.assertEqual(await self.scila.request_co2_flow_status(), "Ok")
    self.mock_sila_interface.send_command.assert_called_with("GetCO2FlowStatus")

  async def test_request_valve_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='H2O'><String>Open</String></Parameter>"
      "  <Parameter name='CO2 Normal'><String>Closed</String></Parameter>"
      "  <Parameter name='CO2 Boost'><String>Closed</String></Parameter>"
      "</Response>"
    )
    self.assertEqual(
      await self.scila.request_valve_status(),
      {"H2O": "Open", "CO2 Normal": "Closed", "CO2 Boost": "Closed"},
    )
    self.mock_sila_interface.send_command.assert_called_with("GetValveStatus")

  def test_serialize(self):
    self.mock_sila_interface.machine_ip = "169.254.1.117"
    self.mock_sila_interface.client_ip = "192.168.1.10"
    data = self.scila.serialize()
    self.assertEqual(data["scila_ip"], "169.254.1.117")
    self.assertEqual(data["client_ip"], "192.168.1.10")
    self.assertIs(data["gas_mixer_connected"], True)

  def test_serialize_no_client_ip(self):
    self.mock_sila_interface.machine_ip = "127.0.0.1"
    self.mock_sila_interface.client_ip = None
    data = self.scila.serialize()
    self.assertEqual(data["scila_ip"], "127.0.0.1")
    self.assertIsNone(data["client_ip"])

  def test_serialize_no_gas_mixer(self):
    scila = SCILA(name="scila2", scila_ip="127.0.0.1", gas_mixer_connected=False)
    self.assertIs(scila.gas_mixer_connected, False)
    self.assertIs(scila.serialize()["gas_mixer_connected"], False)


class TestSCILALifecycle(AnyioTestBase):
  """Verify SCILA owns the lifespan of its SiLA transport."""

  _anyio_backends = ["asyncio"]

  def _make_scila(self, trace: list[str], initialize_error: Optional[Exception] = None) -> SCILA:
    interface = _FakeSiLAInterface(trace, initialize_error)
    with patch("pylabrobot.inheco.scila.scila.InhecoSiLAInterface", return_value=interface):
      return SCILA(name="scila", scila_ip="127.0.0.1")

  def test_managed_methods_are_inherited(self) -> None:
    assert SCILA.setup is HasLifespan.setup
    assert SCILA.stop is HasLifespan.stop
    assert SCILA.__aenter__ is HasLifespan.__aenter__
    assert SCILA.__aexit__ is HasLifespan.__aexit__

  async def test_transport_is_entered_before_reset_and_initialize(self) -> None:
    trace: list[str] = []
    scila = self._make_scila(trace)

    async with scila:
      assert trace == ["transport-enter", "Reset", "Initialize"]

    assert trace == ["transport-enter", "Reset", "Initialize", "transport-exit"]

  async def test_initialize_failure_unwinds_transport_before_reaching_caller(self) -> None:
    trace: list[str] = []
    initialize_error = RuntimeError("initialize failed")
    scila = self._make_scila(trace, initialize_error)

    with pytest.raises(RuntimeError, match="initialize failed") as error_info:
      async with scila:
        pytest.fail("failed initialization reached the lifespan body")

    assert error_info.value is initialize_error
    assert trace == ["transport-enter", "Reset", "Initialize", "transport-exit"]

  async def test_setup_and_stop_drive_the_transport_lifespan(self) -> None:
    trace: list[str] = []
    scila = self._make_scila(trace)

    with pytest.warns(DeprecationWarning, match="setup.*deprecated"):
      await scila.setup()
    assert trace == ["transport-enter", "Reset", "Initialize"]

    await scila.stop()
    assert trace == ["transport-enter", "Reset", "Initialize", "transport-exit"]


class TestSCILATrioLifespan(AnyioTestBase):
  """Verify the SCILA owner accepts a Trio-backed transport lifespan."""

  _anyio_backends = ["trio"]

  async def test_parent_enters_and_releases_transport(self) -> None:
    trace: list[str] = []
    interface = _FakeSiLAInterface(trace)
    with patch("pylabrobot.inheco.scila.scila.InhecoSiLAInterface", return_value=interface):
      scila = SCILA(name="scila", scila_ip="127.0.0.1")

    async with scila:
      assert trace == ["transport-enter", "Reset", "Initialize"]
    assert trace == ["transport-enter", "Reset", "Initialize", "transport-exit"]


class TestSCILATemperature(_SCILATestCase):
  async def test_request_temperature_information(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(_TEMPERATURE_RESPONSE)
    self.assertEqual(
      await self.scila.request_temperature_information(),
      {"CurrentTemperature": 25.0, "TargetTemperature": 37.0, "TemperatureControl": True},
    )
    self.mock_sila_interface.send_command.assert_called_with("GetTemperature")

  async def test_request_current_temperature(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(_TEMPERATURE_RESPONSE)
    self.assertEqual(await self.scila.request_current_temperature(), 25.0)

  async def test_request_target_temperature(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(_TEMPERATURE_RESPONSE)
    self.assertEqual(await self.scila.request_target_temperature(), 37.0)

  async def test_is_temperature_control_enabled(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(_TEMPERATURE_RESPONSE)
    self.assertIs(await self.scila.is_temperature_control_enabled(), True)

  async def test_set_temperature(self):
    await self.scila.set_temperature(30.0)
    self.mock_sila_interface.send_command.assert_called_with(
      "SetTemperature", targetTemperature=30.0, temperatureControl=True
    )

  async def test_deactivate(self):
    await self.scila.deactivate()
    self.mock_sila_interface.send_command.assert_called_with(
      "SetTemperature", temperatureControl=False
    )

  def test_supports_active_cooling(self):
    self.assertFalse(self.scila.supports_active_cooling)


class TestSCILADrawerLoadingTray(_SCILATestCase):
  async def test_open(self):
    for drawer_id in [1, 2, 3, 4]:
      with self.subTest(drawer_id=drawer_id):
        self.mock_sila_interface.send_command.reset_mock()
        await self.scila.drawers[drawer_id].open()
        self.mock_sila_interface.send_command.assert_any_call("PrepareForInput", position=drawer_id)
        self.mock_sila_interface.send_command.assert_any_call("OpenDoor")

  async def test_close(self):
    for drawer_id in [1, 2, 3, 4]:
      with self.subTest(drawer_id=drawer_id):
        self.mock_sila_interface.send_command.reset_mock()
        await self.scila.drawers[drawer_id].close()
        self.mock_sila_interface.send_command.assert_any_call(
          "PrepareForOutput", position=drawer_id
        )
        self.mock_sila_interface.send_command.assert_any_call("CloseDoor")

  async def test_open_co2_warning_logged_when_gas_mixer_connected(self):
    self.scila.gas_mixer_connected = True

    async def side_effect(cmd, **kw):
      if cmd == "OpenDoor":
        raise RuntimeError("command OpenDoor failed with code 2: 'Warning: CO2 flow NOK'")

    self.mock_sila_interface.send_command.side_effect = side_effect
    with self.assertLogs("pylabrobot.inheco.scila.scila", level="WARNING") as logs:
      await self.scila.drawers[1].open()
    self.assertTrue(any("drawer 1 open" in m for m in logs.output))

  async def test_open_co2_warning_silenced_when_gas_mixer_not_connected(self):
    self.scila.gas_mixer_connected = False

    async def side_effect(cmd, **kw):
      if cmd == "OpenDoor":
        raise RuntimeError("command OpenDoor failed with code 2: 'Warning: CO2 flow NOK'")

    self.mock_sila_interface.send_command.side_effect = side_effect
    # assertNoLogs needs Python 3.10; emit a sentinel so assertLogs has something
    # to capture and assert the drawer added nothing of its own.
    logger_name = "pylabrobot.inheco.scila.scila"
    with self.assertLogs(logger_name, level="WARNING") as captured:
      logging.getLogger(logger_name).warning("sentinel")
      await self.scila.drawers[1].open()
    self.assertEqual(captured.output, [f"WARNING:{logger_name}:sentinel"])

  async def test_open_non_warning_error_always_raises(self):
    self.scila.gas_mixer_connected = False  # most permissive setting

    async def side_effect(cmd, **kw):
      if cmd == "OpenDoor":
        raise RuntimeError("command OpenDoor failed with code 4: 'Door obstructed'")

    self.mock_sila_interface.send_command.side_effect = side_effect
    with self.assertRaises(RuntimeError):
      await self.scila.drawers[1].open()

  def test_invalid_drawer_id(self):
    with self.assertRaises(ValueError):
      SCILADrawerLoadingTray(
        scila=self.scila,
        drawer_id=5,
        name="bad_drawer",
        size_x=0.0,
        size_y=0.0,
        size_z=0.0,
      )


if __name__ == "__main__":
  unittest.main()
