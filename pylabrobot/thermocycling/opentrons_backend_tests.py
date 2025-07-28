import sys
import unittest
from unittest.mock import patch

from pylabrobot.resources.itemized_resource import ItemizedResource
from pylabrobot.thermocycling.opentrons import OpentronsThermocyclerModuleV1
from pylabrobot.thermocycling.opentrons_backend import OpentronsThermocyclerBackend
from pylabrobot.thermocycling.standard import BlockStatus, LidStatus, Step


def _is_python_3_10():
  return sys.version_info[:2] == (3, 10)


@unittest.skipIf(not _is_python_3_10(), "requires Python 3.10")
class TestOpentronsThermocyclerBackend(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    await super().asyncSetUp()
    self.thermocycler_backend = OpentronsThermocyclerBackend(opentrons_id="test_id")

  def test_opentrons_v1_serialization(self):
    """Test that the Opentrons-specific resource model serializes correctly."""
    tc_model = OpentronsThermocyclerModuleV1(
      name="test_v1_tc",
      opentrons_id="test_id",
      child=ItemizedResource(name="plate", size_x=1, size_y=1, size_z=1, ordered_items={}),
    )
    serialized = tc_model.serialize()
    assert "opentrons_id" in serialized
    assert serialized["opentrons_id"] == "test_id"
    deserialized = OpentronsThermocyclerModuleV1.deserialize(serialized)
    assert tc_model == deserialized

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  async def test_find_module_raises_error_if_not_found(self, mock_list_connected_modules):
    """Test that an error is raised if the module is not found."""
    mock_list_connected_modules.return_value = [{"id": "some_other_id", "data": {}}]
    with self.assertRaises(RuntimeError) as e:
      await self.thermocycler_backend.get_lid_open()
    self.assertEqual(str(e.exception), "Module 'test_id' not found")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_open_lid")
  async def test_open_lid(self, mock_open_lid):
    await self.thermocycler_backend.open_lid()
    mock_open_lid.assert_called_once_with(module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_close_lid")
  async def test_close_lid(self, mock_close_lid):
    await self.thermocycler_backend.close_lid()
    mock_close_lid.assert_called_once_with(module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_block_temperature")
  async def test_set_block_temperature(self, mock_set_block_temp):
    await self.thermocycler_backend.set_block_temperature(95.0)
    mock_set_block_temp.assert_called_once_with(celsius=95.0, module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_set_lid_temperature")
  async def test_set_lid_temperature(self, mock_set_lid_temp):
    await self.thermocycler_backend.set_lid_temperature(105.0)
    mock_set_lid_temp.assert_called_once_with(celsius=105.0, module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_block")
  async def test_deactivate_block(self, mock_deactivate_block):
    await self.thermocycler_backend.deactivate_block()
    mock_deactivate_block.assert_called_once_with(module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_deactivate_lid")
  async def test_deactivate_lid(self, mock_deactivate_lid):
    await self.thermocycler_backend.deactivate_lid()
    mock_deactivate_lid.assert_called_once_with(module_id="test_id")

  @patch("pylabrobot.thermocycling.opentrons_backend.thermocycler_run_profile_no_wait")
  async def test_run_profile(self, mock_run_profile):
    profile = [Step(temperature=95, hold_seconds=10)]
    await self.thermocycler_backend.run_profile(profile, 50.0)
    # print all calls
    mock_run_profile.assert_called_once_with(
      profile=[{"celsius": 95, "holdSeconds": 10}], block_max_volume=50.0, module_id="test_id"
    )

  @patch("pylabrobot.thermocycling.opentrons_backend.list_connected_modules")
  async def test_getters_return_correct_data(self, mock_list_connected_modules):
    mock_data = {
      "id": "test_id",
      "data": {
        "currentTemperature": 25.5,
        "targetTemperature": 95.0,
        "lidTemperature": 37.1,
        "lidTargetTemperature": 105.0,
        "lidStatus": "open",
        "lidTemperatureStatus": "holding at target",
        "status": "holding at target",
        "holdTime": 12.0,
        "currentCycleIndex": 2,
        "totalCycleCount": 10,
        "currentStepIndex": 1,
        "totalStepCount": 3,
      },
    }
    mock_list_connected_modules.return_value = [mock_data]

    assert await self.thermocycler_backend.get_block_current_temperature() == 25.5
    assert await self.thermocycler_backend.get_block_target_temperature() == 95.0
    assert await self.thermocycler_backend.get_lid_current_temperature() == 37.1
    assert await self.thermocycler_backend.get_lid_target_temperature() == 105.0
    assert await self.thermocycler_backend.get_lid_open() is True
    assert await self.thermocycler_backend.get_lid_status() == LidStatus.HOLDING_AT_TARGET
    assert await self.thermocycler_backend.get_block_status() == BlockStatus.HOLDING_AT_TARGET
    assert await self.thermocycler_backend.get_hold_time() == 12.0
    assert await self.thermocycler_backend.get_current_cycle_index() == 1  # 2 - 1 = 1 (zero-based)
    assert await self.thermocycler_backend.get_total_cycle_count() == 10
    assert await self.thermocycler_backend.get_current_step_index() == 0  # 1 - 1 = 0 (zero-based)
    assert await self.thermocycler_backend.get_total_step_count() == 3
