import unittest
from unittest.mock import AsyncMock, create_autospec

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons import (
  OT2,
  MagneticModuleStatus,
  OT2MagneticModule,
  OT2ModuleMismatchError,
  OT2TemperatureModule,
  TemperatureModuleStatus,
)
from pylabrobot.opentrons.api import OpentronsAPI
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import CommandInfo, ModuleInfo
from pylabrobot.resources import Coordinate
from pylabrobot.resources.celltreat import celltreat_96_wellplate_350uL_Fb
from pylabrobot.resources.opentrons import OTDeck


def _loaded(module_id: str, serial_number: str) -> CommandInfo:
  """The robot's answer to loadModule."""
  return CommandInfo(
    "succeeded", {"moduleId": module_id, "serialNumber": serial_number, "model": "m"}, {}
  )


class OT2ModuleTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    """Use a real deck with a mocked run and API."""
    self.protocol_run = create_autospec(OpentronsRun, instance=True, spec_set=True)
    self.protocol_run.active = True
    self.api = create_autospec(OpentronsAPI, instance=True, spec_set=True)
    self.deck = OTDeck()
    self.robot = OT2("ot2.local", deck=self.deck, io=AsyncMock(spec_set=HTTP))
    self.robot._api = self.api
    self.robot._connected = True
    self.robot._run = self.protocol_run
    self.cooler = OT2TemperatureModule("cooler")
    self.deck.assign_child_at_slot(self.cooler, 3)
    self.magnet = OT2MagneticModule("magnet", serial_number="MDV-1")
    self.deck.assign_child_at_slot(self.magnet, 1)

  def test_labware_stands_where_the_module_definition_puts_it(self) -> None:
    plate = celltreat_96_wellplate_350uL_Fb("plate")
    self.cooler.assign_child_resource(plate)
    slot_corner = self.deck.slot_locations[2]
    self.assertEqual(
      plate.get_location_wrt(self.deck), slot_corner + Coordinate(-1.45, -0.15, 80.09)
    )
    self.assertEqual(
      (self.cooler.model, self.magnet.model), ("temperatureModuleV2", "magneticModuleV2")
    )

  async def test_a_module_loads_once_into_a_run_at_its_slot(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-1", "TDV-7")

    self.assertEqual(await self.robot.load_module(self.cooler), "module-1")
    self.assertEqual(await self.robot.load_module(self.cooler), "module-1")

    self.protocol_run.execute.assert_awaited_once_with(
      "loadModule", {"model": "temperatureModuleV2", "location": {"slotName": "3"}}
    )
    self.assertTrue(self.cooler.is_loaded)
    self.assertEqual(self.cooler.serial_number, "TDV-7")

  async def test_a_later_run_loads_the_module_again(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-1", "TDV-7")
    await self.robot.load_module(self.cooler)
    later = create_autospec(OpentronsRun, instance=True, spec_set=True)
    later.active = True
    later.execute.return_value = _loaded("module-9", "TDV-7")
    self.robot._run = later

    self.assertFalse(self.cooler.is_loaded)
    with self.assertRaisesRegex(RuntimeError, "not loaded"):
      await self.cooler.set_temperature(37)
    self.assertEqual(await self.robot.load_module(self.cooler), "module-9")

  async def test_the_robot_taking_another_module_than_the_one_named_is_refused(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-2", "MDV-2")

    with self.assertRaisesRegex(
      OT2ModuleMismatchError, "loaded magneticModuleV2 MDV-2 in slot 1, not MDV-1"
    ) as caught:
      await self.robot.load_module(self.magnet)

    self.assertEqual((caught.exception.slot, caught.exception.serial_number), (1, "MDV-2"))
    self.assertFalse(self.magnet.is_loaded)

  async def test_a_module_off_the_deck_is_refused_before_anything_is_sent(self) -> None:
    with self.assertRaisesRegex(ValueError, "deck slot"):
      await self.robot.load_module(OT2TemperatureModule("loose"))
    self.protocol_run.execute.assert_not_awaited()

  async def test_temperature_commands(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-1", "TDV-7")
    await self.robot.load_module(self.cooler)
    self.protocol_run.execute.reset_mock()

    await self.cooler.set_temperature(4)
    await self.cooler.wait_for_temperature(timeout=600)
    await self.cooler.deactivate()

    self.assertEqual(
      [c.args for c in self.protocol_run.execute.await_args_list],
      [
        ("temperatureModule/setTargetTemperature", {"moduleId": "module-1", "celsius": 4.0}),
        ("temperatureModule/waitForTemperature", {"moduleId": "module-1"}),
        ("temperatureModule/deactivate", {"moduleId": "module-1"}),
      ],
    )
    self.assertEqual(self.protocol_run.execute.await_args_list[1].kwargs, {"timeout": 600})

  async def test_temperature_outside_what_the_module_holds_is_refused(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-1", "TDV-7")
    await self.robot.load_module(self.cooler)
    self.protocol_run.execute.reset_mock()
    for temperature in (3.9, 95.1, float("nan")):
      with self.assertRaises(ValueError):
        await self.cooler.set_temperature(temperature)
    self.protocol_run.execute.assert_not_awaited()

  async def test_magnet_commands(self) -> None:
    self.protocol_run.execute.return_value = _loaded("module-2", "MDV-1")
    await self.robot.load_module(self.magnet)
    self.protocol_run.execute.reset_mock()

    await self.magnet.engage(8)
    await self.magnet.disengage()
    with self.assertRaises(ValueError):
      await self.magnet.engage(25.5)

    self.assertEqual(
      [c.args for c in self.protocol_run.execute.await_args_list],
      [
        ("magneticModule/engage", {"moduleId": "module-2", "height": 8.0}),
        ("magneticModule/disengage", {"moduleId": "module-2"}),
      ],
    )

  def test_gen1_modules_stand_their_labware_where_their_definitions_put_it(self) -> None:
    cooler = OT2TemperatureModule("cooler_gen1", model="temperatureModuleV1")
    magnet = OT2MagneticModule("magnet_gen1", model="magneticModuleV1")
    self.assertEqual(cooler.labware_offset, Coordinate(-0.15, -0.15, 80.09))
    self.assertEqual(magnet.labware_offset, Coordinate(0.125, -0.125, 82.25))
    self.assertEqual((cooler.model, magnet.model), ("temperatureModuleV1", "magneticModuleV1"))
    self.assertEqual((magnet.maximum_engage_height, self.magnet.maximum_engage_height), (20, 25))
    with self.assertRaisesRegex(ValueError, "not a Temperature Module"):
      OT2TemperatureModule("wrong", model="magneticModuleV1")  # type: ignore[arg-type]

  async def test_a_gen1_module_loads_as_its_model_and_its_magnets_stop_at_20_mm(self) -> None:
    magnet = OT2MagneticModule("magnet_gen1", model="magneticModuleV1")
    self.deck.assign_child_at_slot(magnet, 4)
    self.protocol_run.execute.return_value = _loaded("module-3", "MDV1-1")
    await self.robot.load_module(magnet)
    self.protocol_run.execute.assert_awaited_once_with(
      "loadModule", {"model": "magneticModuleV1", "location": {"slotName": "4"}}
    )
    self.protocol_run.execute.reset_mock()
    with self.assertRaisesRegex(ValueError, "between 0 and 20 mm"):
      await magnet.engage(21)
    await magnet.engage(20)
    self.protocol_run.execute.assert_awaited_once_with(
      "magneticModule/engage", {"moduleId": "module-3", "height": 20.0}, timeout=None
    )

  async def test_status_is_read_for_the_module_by_its_serial_number(self) -> None:
    self.protocol_run.execute.side_effect = [_loaded("module-1", "TDV-7"), _loaded("m2", "MDV-1")]
    await self.robot.load_module(self.cooler)
    await self.robot.load_module(self.magnet)
    self.api.get_connected_modules.return_value = (
      ModuleInfo(
        "a",
        "temperatureModuleType",
        "temperatureModuleV2",
        "TDV-0",
        {"currentTemperature": 23.0, "targetTemperature": None, "status": "idle"},
      ),
      ModuleInfo(
        "b",
        "temperatureModuleType",
        "temperatureModuleV2",
        "TDV-7",
        {"currentTemperature": 4.1, "targetTemperature": 4, "status": "holding at target"},
      ),
      ModuleInfo(
        "c",
        "magneticModuleType",
        "magneticModuleV2",
        "MDV-1",
        {"engaged": True, "height": 8.0, "status": "engaged"},
      ),
    )

    self.assertEqual(
      await self.cooler.request_status(), TemperatureModuleStatus(4.1, 4.0, "holding at target")
    )
    self.assertEqual(await self.magnet.request_status(), MagneticModuleStatus(True, 8.0, "engaged"))


if __name__ == "__main__":
  unittest.main()
