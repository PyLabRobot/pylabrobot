import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, patch

from pylabrobot.storage.inheco.scila.inheco_sila_interface import InhecoSiLAInterface
from pylabrobot.storage.inheco.scila.scila_backend import SCILABackend


class TestSCILABackend(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.patcher = patch("pylabrobot.storage.inheco.scila.scila_backend.InhecoSiLAInterface")
    self.MockInhecoSiLAInterface = self.patcher.start()
    self.mock_sila_interface = AsyncMock(spec=InhecoSiLAInterface)
    self.mock_sila_interface.bound_port = 80
    self.mock_sila_interface.client_ip = "127.0.0.1"
    self.MockInhecoSiLAInterface.return_value = self.mock_sila_interface
    self.backend = SCILABackend(scila_ip="127.0.0.1")

  def tearDown(self):
    self.patcher.stop()

  async def test_setup(self):
    await self.backend.setup()
    self.mock_sila_interface.setup.assert_called_once()
    self.mock_sila_interface.send_command.assert_any_call(
      command="Reset",
      deviceId="MyController",
      eventReceiverURI="http://127.0.0.1:80/",
      simulationMode=False,
    )
    self.mock_sila_interface.send_command.assert_any_call("Initialize")

  async def test_stop(self):
    await self.backend.stop()
    self.mock_sila_interface.close.assert_called_once()

  async def test_request_status(self):
    self.mock_sila_interface.send_command.return_value = {"GetStatusResponse": {"state": "standBy"}}
    status = await self.backend.request_status()
    self.assertEqual(status, "standBy")
    self.mock_sila_interface.send_command.assert_called_with("GetStatus")

  async def test_request_liquid_level(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response><Parameter name='LiquidLevel'><String>High</String></Parameter></Response>"
    )
    level = await self.backend.request_liquid_level()
    self.assertEqual(level, "High")
    self.mock_sila_interface.send_command.assert_called_with("GetLiquidLevel")

  async def test_request_temperature_information(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='CurrentTemperature'><Float64>25.0</Float64></Parameter>"
      "  <Parameter name='TargetTemperature'><Float64>37.0</Float64></Parameter>"
      "  <Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
      "</Response>"
    )
    info = await self.backend.request_temperature_information()
    self.assertEqual(
      info, {"CurrentTemperature": 25.0, "TargetTemperature": 37.0, "TemperatureControl": True}
    )
    self.mock_sila_interface.send_command.assert_called_with("GetTemperature")

  async def test_measure_temperature(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='CurrentTemperature'><Float64>25.0</Float64></Parameter>"
      "  <Parameter name='TargetTemperature'><Float64>37.0</Float64></Parameter>"
      "  <Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
      "</Response>"
    )
    temp = await self.backend.measure_temperature()
    self.assertEqual(temp, 25.0)

  async def test_request_target_temperature(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='CurrentTemperature'><Float64>25.0</Float64></Parameter>"
      "  <Parameter name='TargetTemperature'><Float64>37.0</Float64></Parameter>"
      "  <Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
      "</Response>"
    )
    temp = await self.backend.request_target_temperature()
    self.assertEqual(temp, 37.0)

  async def test_is_temperature_control_enabled(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='CurrentTemperature'><Float64>25.0</Float64></Parameter>"
      "  <Parameter name='TargetTemperature'><Float64>37.0</Float64></Parameter>"
      "  <Parameter name='TemperatureControl'><Boolean>true</Boolean></Parameter>"
      "</Response>"
    )
    enabled = await self.backend.is_temperature_control_enabled()
    self.assertIs(enabled, True)

  async def test_open(self):
    for drawer_id in [1, 2, 3, 4]:
      with self.subTest(drawer_id=drawer_id):
        self.mock_sila_interface.send_command.reset_mock()
        await self.backend.open(drawer_id)
        self.mock_sila_interface.send_command.assert_any_call("PrepareForInput", position=drawer_id)
        self.mock_sila_interface.send_command.assert_any_call("OpenDoor")

  async def test_open_invalid_id(self):
    with self.assertRaises(ValueError):
      await self.backend.open(5)

  async def test_close(self):
    for drawer_id in [1, 2, 3, 4]:
      with self.subTest(drawer_id=drawer_id):
        self.mock_sila_interface.send_command.reset_mock()
        await self.backend.close(drawer_id)
        self.mock_sila_interface.send_command.assert_any_call(
          "PrepareForOutput", position=drawer_id
        )
        self.mock_sila_interface.send_command.assert_any_call("CloseDoor")

  async def test_close_invalid_id(self):
    with self.assertRaises(ValueError):
      await self.backend.close(5)

  async def test_request_drawer_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='Drawer1'><String>Opened</String></Parameter>"
      "  <Parameter name='Drawer2'><String>Closed</String></Parameter>"
      "  <Parameter name='Drawer3'><String>Opened</String></Parameter>"
      "  <Parameter name='Drawer4'><String>Closed</String></Parameter>"
      "</Response>"
    )
    positions = await self.backend.request_drawer_statuses()
    self.assertEqual(
      positions,
      {
        1: "Opened",
        2: "Closed",
        3: "Opened",
        4: "Closed",
      },
    )
    self.mock_sila_interface.send_command.assert_called_with("GetDoorStatus")

  async def test_request_drawer_status_single(self):
    for drawer_id, expected_position in [
      (1, "Opened"),
      (2, "Closed"),
      (3, "Opened"),
      (4, "Closed"),
    ]:
      with self.subTest(drawer_id=drawer_id):
        self.mock_sila_interface.send_command.reset_mock()
        self.mock_sila_interface.send_command.return_value = ET.fromstring(
          "<Response>"
          "  <Parameter name='Drawer1'><String>Opened</String></Parameter>"
          "  <Parameter name='Drawer2'><String>Closed</String></Parameter>"
          "  <Parameter name='Drawer3'><String>Opened</String></Parameter>"
          "  <Parameter name='Drawer4'><String>Closed</String></Parameter>"
          "</Response>"
        )
        position = await self.backend.request_drawer_status(drawer_id)
        self.assertEqual(position, expected_position)

  async def test_request_drawer_status_invalid_id(self):
    with self.assertRaises(ValueError):
      await self.backend.request_drawer_status(5)

  async def test_request_co2_flow_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response><Parameter name='CO2FlowStatus'><String>OK</String></Parameter></Response>"
    )
    status = await self.backend.request_co2_flow_status()
    self.assertEqual(status, "OK")
    self.mock_sila_interface.send_command.assert_called_with("GetCO2FlowStatus")

  async def test_request_valve_status(self):
    self.mock_sila_interface.send_command.return_value = ET.fromstring(
      "<Response>"
      "  <Parameter name='H2O'><String>Opened</String></Parameter>"
      "  <Parameter name='CO2 Normal'><String>Opened</String></Parameter>"
      "  <Parameter name='CO2 Boost'><String>Closed</String></Parameter>"
      "</Response>"
    )
    status = await self.backend.request_valve_status()
    self.assertEqual(
      status,
      {
        "H2O": "Opened",
        "CO2 Normal": "Opened",
        "CO2 Boost": "Closed",
      },
    )
    self.mock_sila_interface.send_command.assert_called_with("GetValveStatus")

  async def test_start_temperature_control(self):
    await self.backend.start_temperature_control(30.0)
    self.mock_sila_interface.send_command.assert_called_with(
      "SetTemperature", targetTemperature=30.0, temperatureControl=True
    )

  async def test_stop_temperature_control(self):
    await self.backend.stop_temperature_control()
    self.mock_sila_interface.send_command.assert_called_with(
      "SetTemperature", temperatureControl=False
    )

  def test_serialize(self):
    self.mock_sila_interface.machine_ip = "169.254.1.117"
    self.mock_sila_interface.client_ip = "192.168.1.10"
    data = self.backend.serialize()
    self.assertEqual(data["scila_ip"], "169.254.1.117")
    self.assertEqual(data["client_ip"], "192.168.1.10")

  def test_serialize_no_client_ip(self):
    self.mock_sila_interface.machine_ip = "127.0.0.1"
    self.mock_sila_interface.client_ip = None
    data = self.backend.serialize()
    self.assertEqual(data["scila_ip"], "127.0.0.1")
    self.assertIsNone(data["client_ip"])

  def test_deserialize(self):
    data = {"scila_ip": "169.254.1.117", "client_ip": "192.168.1.10"}
    SCILABackend.deserialize(data)
    self.MockInhecoSiLAInterface.assert_called_with(
      client_ip="192.168.1.10", machine_ip="169.254.1.117"
    )

  def test_deserialize_no_client_ip(self):
    data = {"scila_ip": "169.254.1.117"}
    SCILABackend.deserialize(data)
    self.MockInhecoSiLAInterface.assert_called_with(client_ip=None, machine_ip="169.254.1.117")


if __name__ == "__main__":
  unittest.main()
