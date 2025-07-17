import unittest
from unittest.mock import patch

from pylabrobot.resources import Coordinate, ItemizedResource
from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerBackend
from pylabrobot.thermocycling.opentrons import OpentronsThermocyclerModuleV1
from pylabrobot.thermocycling.thermocycler import Thermocycler

class ThermocyclerTests(unittest.TestCase):
  def test_serialization(self):
    tc = Thermocycler(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=OpentronsThermocyclerBackend(opentrons_id="test_id"), # Dummy opentrons_id
      child_location=Coordinate(0, 0, 0),
    )

    serialized = tc.serialize()
    deserialized = Thermocycler.deserialize(serialized)
    self.assertEqual(tc, deserialized)
    self.assertEqual(tc.backend.opentrons_id, deserialized.backend.opentrons_id) # type: ignore

class OpentronsThermocyclerBackendTests(unittest.IsolatedAsyncioTestCase):
  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_open_lid")
  async def test_open_lid(self, mock_open_lid, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.open_lid()
    mock_open_lid.assert_called_once_with(module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_close_lid")
  async def test_close_lid(self, mock_close_lid, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.close_lid()
    mock_close_lid.assert_called_once_with(module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_block_temperature")
  async def test_set_block_temperature(self, mock_set_block_temp, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.set_block_temperature(95.0)
    mock_set_block_temp.assert_called_once_with(celsius=95.0, module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_lid_temperature")
  async def test_set_lid_temperature(self, mock_set_lid_temp, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.set_lid_temperature(105.0)
    mock_set_lid_temp.assert_called_once_with(celsius=105.0, module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_block")
  async def test_deactivate_block(self, mock_deactivate_block, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.deactivate_block()
    mock_deactivate_block.assert_called_once_with(module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_lid")
  async def test_deactivate_lid(self, mock_deactivate_lid, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    await backend.deactivate_lid()
    mock_deactivate_lid.assert_called_once_with(module_id="test_id") # Dummy module_id

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  @patch("ot_api.runs.enqueue_command")
  @patch("ot_api.run_id", "dummy_run_id") # Dummy run_id for the decorator
  async def test_run_profile(self, mock_enqueue_command, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    profile = [{"temperature": 95, "hold_time_seconds": 10}]
    await backend.run_profile(profile, 50.0)
    mock_enqueue_command.assert_called_once_with(
      "thermocycler/runProfile",
      {
        "profile": profile,
        "blockMaxVolumeUl": 50.0,
        "moduleId": "test_id", # Dummy module_id
      },
      intent="setup",
      run_id="dummy_run_id" # Dummy run_id
    )

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  async def test_get_block_current_temperature(self, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {"currentTemperature": 25.0}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    temp = await backend.get_block_current_temperature()
    self.assertEqual(temp, 25.0)

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  async def test_get_lid_status(self, mock_list_connected_modules):
    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {"lidStatus": "open"}}] # Mock return value for list_connected_modules
    backend = OpentronsThermocyclerBackend(opentrons_id="test_id") # Dummy opentrons_id
    await backend.setup()
    status = await backend.get_lid_status()
    self.assertEqual(status, "open")

  @patch("pylabrobot.thermocycling.thermocycler.Thermocycler.get_lid_status") # Patch the method in Thermocycler
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_run_profile_no_wait")
  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_lid_temperature")
  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  async def test_run_pcr_profile(self, mock_list_connected_modules, mock_set_lid_temp, mock_run_profile_no_wait, mock_get_lid_status):
    # Configure mock_get_lid_status to return "open" initially, then "idle"
    mock_get_lid_status.side_effect = ["open", "idle"]

    mock_list_connected_modules.return_value = [{"id": "test_id", "data": {"lidTemperatureStatus": "idle", "lidStatus": "open"}}] # Mock return value for list_connected_modules
    tc_dev = Thermocycler(
      name="test_tc",
      size_x=10,
      size_y=10,
      size_z=10,
      backend=OpentronsThermocyclerBackend(opentrons_id="test_id"), # Dummy opentrons_id
      child_location=Coordinate(0, 0, 0),
    )
    await tc_dev.setup()

    denaturation_temp=98.0
    denaturation_time=10.0
    annealing_temp=55.0
    annealing_time=30.0
    extension_temp=72.0
    extension_time=60.0
    num_cycles=2
    block_max_volume=25.0
    lid_temperature=105.0
    pre_denaturation_temp=95.0
    pre_denaturation_time=180.0
    final_extension_temp=72.0
    final_extension_time=300.0
    storage_temp=4.0
    storage_time=600.0

    await tc_dev.run_pcr_profile(
        denaturation_temp=denaturation_temp,
        denaturation_time=denaturation_time,
        annealing_temp=annealing_temp,
        annealing_time=annealing_time,
        extension_temp=extension_temp,
        extension_time=extension_time,
        num_cycles=num_cycles,
        block_max_volume=block_max_volume,
        lid_temperature=lid_temperature,
        pre_denaturation_temp=pre_denaturation_temp,
        pre_denaturation_time=pre_denaturation_time,
        final_extension_temp=final_extension_temp,
        final_extension_time=final_extension_time,
        storage_temp=storage_temp,
        storage_time=storage_time,
    )

    mock_set_lid_temp.assert_called_once_with(celsius=lid_temperature, module_id="test_id") # Dummy module_id

    expected_profile = [
        {"celsius": pre_denaturation_temp, "holdSeconds": pre_denaturation_time},
        {"celsius": denaturation_temp, "holdSeconds": denaturation_time},
        {"celsius": annealing_temp, "holdSeconds": annealing_time},
        {"celsius": extension_temp, "holdSeconds": extension_time},
        {"celsius": denaturation_temp, "holdSeconds": denaturation_time},
        {"celsius": annealing_temp, "holdSeconds": annealing_time},
        {"celsius": extension_temp, "holdSeconds": extension_time},
        {"celsius": final_extension_temp, "holdSeconds": final_extension_time},
        {"celsius": storage_temp, "holdSeconds": storage_time},
    ]

    mock_run_profile_no_wait.assert_called_once_with(
        profile=expected_profile,
        block_max_volume=block_max_volume,
        module_id="test_id", # Dummy module_id
    )

class OpentronsThermocyclerModuleTests(unittest.TestCase):
  def test_v1_serialization(self):
    tc = OpentronsThermocyclerModuleV1(
      name="test_v1_tc",
      opentrons_id="test_id", # Dummy opentrons_id
      child_location=Coordinate(0, 0, 0),
      child=ItemizedResource(name="plate", size_x=1, size_y=1, size_z=1, ordered_items={})
    )
    serialized = tc.serialize()
    self.assertIn("opentrons_id", serialized)
    self.assertEqual(serialized["opentrons_id"], "test_id")
    
