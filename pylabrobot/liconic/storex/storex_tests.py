# mypy: disable-error-code="assignment,attr-defined,method-assign"

import unittest
from typing import Any, List, Optional, cast
from unittest.mock import AsyncMock

from pylabrobot.liconic.storex import STOREX_SITE_HEIGHT_TO_STEPS, StoreX
from pylabrobot.liconic.storex.constants import StoreXModel
from pylabrobot.liconic.storex.errors import StoreXControllerCommandError
from pylabrobot.liconic.storex.racks import (
  storex_rack_5mm_42,
  storex_rack_17mm_22,
  storex_rack_44mm_10,
)
from pylabrobot.resources import Coordinate, Plate, PlateCarrier, PlateHolder, Rotation
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


def make_storex(
  model: StoreXModel = "STX44_IC",
  racks: Optional[List[PlateCarrier]] = None,
  has_shaker: bool = False,
  barcode_scanner: Any = None,
) -> StoreX:
  return StoreX(
    name="incubator",
    model=model,
    port="/dev/null",
    racks=racks if racks is not None else [storex_rack_17mm_22("rack_1")],
    loading_tray_location=Coordinate.zero(),
    has_shaker=has_shaker,
    barcode_scanner=barcode_scanner,
  )


def make_plate(name: str = "plate") -> Plate:
  return cor_96_wellplate_360uL_Fb(name=name)


class TestModelsAndRacks(unittest.TestCase):
  def test_invalid_model(self) -> None:
    with self.assertRaises(ValueError):
      make_storex(model=cast(StoreXModel, "STX42_IC"))

  def test_model_capabilities(self) -> None:
    self.assertFalse(make_storex("STX44_NC").supports_temperature_control)
    self.assertTrue(make_storex("STX44_HC").supports_active_cooling)
    self.assertTrue(make_storex("STX44_AR").supports_humidity_control)
    self.assertFalse(make_storex("STX44_IC").supports_humidity_control)

  def test_step_sizes_match_documented_formula(self) -> None:
    for site_height, claimed_steps in STOREX_SITE_HEIGHT_TO_STEPS.items():
      if site_height != 104:
        self.assertEqual(claimed_steps, round((site_height + 6) * 1713 / 50))
    self.assertEqual(STOREX_SITE_HEIGHT_TO_STEPS[104], 3563)

  def test_rack_construction(self) -> None:
    rack = storex_rack_17mm_22("rack")
    self.assertEqual(len(rack.sites), 22)
    self.assertEqual(rack.model, "storex_rack_17mm_22")
    self.assertEqual(rack.sites[0].get_size_z(), 17)
    self.assertEqual(rack.get_size_z(), 505)

  def test_rack_addressing(self) -> None:
    racks = [storex_rack_17mm_22("rack_1"), storex_rack_17mm_22("rack_2")]
    storex = make_storex(racks=racks)
    self.assertEqual(storex._site_to_address(racks[0].sites[0]), (1, 1))
    self.assertEqual(storex._site_to_address(racks[1].sites[21]), (2, 22))
    self.assertEqual(storex._carrier_to_steps_and_positions(racks[0].sites[0]), (788, 22))

  def test_other_rack_steps(self) -> None:
    racks = [storex_rack_5mm_42("short"), storex_rack_44mm_10("tall")]
    storex = make_storex(racks=racks)
    self.assertEqual(storex._carrier_to_steps_and_positions(racks[0].sites[0]), (377, 42))
    self.assertEqual(storex._carrier_to_steps_and_positions(racks[1].sites[0]), (1713, 10))

  def test_rejects_non_storex_rack(self) -> None:
    rack = PlateCarrier(
      name="rack",
      size_x=100,
      size_y=100,
      size_z=500,
      sites={0: PlateHolder("site", 127, 85, 20, pedestal_size_z=0).at(Coordinate.zero())},
      model="other_rack_17mm_22",
    )
    storex = make_storex(racks=[rack])
    with self.assertRaises(ValueError):
      storex._carrier_to_steps_and_positions(rack.sites[0])

  def test_serialization_round_trip(self) -> None:
    rack = storex_rack_17mm_22("rack")
    rack.sites[0].assign_child_resource(make_plate("stored_plate"))
    storex = StoreX(
      name="incubator",
      model="STX44_IC",
      port="/dev/ttyUSB0",
      racks=[rack],
      loading_tray_location=Coordinate(1, 2, 3),
      has_shaker=True,
      size_x=100,
      size_y=200,
      size_z=300,
      rotation=Rotation(x=0, y=0, z=90),
    )

    restored = StoreX.deserialize(storex.serialize())

    self.assertIsInstance(restored, StoreX)
    restored = cast(StoreX, restored)
    self.assertEqual(restored.name, storex.name)
    self.assertEqual(restored.storex_model, storex.storex_model)
    self.assertEqual(restored.io.port, storex.io.port)
    self.assertEqual(restored.racks, storex.racks)
    self.assertEqual(restored.loading_tray.location, storex.loading_tray.location)
    self.assertEqual(restored.has_shaker, storex.has_shaker)
    self.assertEqual(restored.get_size_x(), storex.get_size_x())
    self.assertEqual(restored.get_size_y(), storex.get_size_y())
    self.assertEqual(restored.get_size_z(), storex.get_size_z())
    self.assertEqual(restored.rotation.serialize(), storex.rotation.serialize())
    self.assertIs(restored.racks[0].parent, restored)
    self.assertIs(restored.loading_tray.parent, restored)
    restored_plate = restored.racks[0].sites[0].resource
    self.assertIsNotNone(restored_plate)
    self.assertEqual(cast(Plate, restored_plate).name, "stored_plate")


class TestLifecycle(unittest.IsolatedAsyncioTestCase):
  async def test_setup_handshake_and_scanner(self) -> None:
    scanner = AsyncMock()
    storex = make_storex(barcode_scanner=scanner)
    storex.io = AsyncMock()
    storex.io.port = "/dev/null"
    storex.io.readline = AsyncMock(side_effect=[b"CC\r\n", b"OK\r\n", b"1\r\n"])

    await storex.setup()

    self.assertEqual(
      [call.args[0] for call in storex.io.write.await_args_list],
      [b"CR\r", b"ST 1801\r", b"RD 1915\r"],
    )
    scanner.setup.assert_awaited_once()

  async def test_setup_closes_io_on_bad_activation_reply(self) -> None:
    storex = make_storex()
    storex.io = AsyncMock()
    storex.io.readline = AsyncMock(side_effect=[b"CC\r\n", b"NO\r\n"])

    with self.assertRaises(RuntimeError):
      await storex.setup()

    storex.io.stop.assert_awaited_once()

  async def test_stop_closes_scanner(self) -> None:
    scanner = AsyncMock()
    storex = make_storex(barcode_scanner=scanner)
    storex.io = AsyncMock()
    storex.io.port = "/dev/null"
    await storex.stop()
    storex.io.stop.assert_awaited_once()
    scanner.stop.assert_awaited_once()


class TestValueConversions(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.storex = make_storex("STX44_AR", has_shaker=True)
    self.storex._send_command = AsyncMock(return_value="OK")
    self.storex._wait_ready = AsyncMock()

  async def test_temperature(self) -> None:
    await self.storex.set_temperature(37.5)
    self.storex._send_command.assert_any_call("WR DM890 00375")
    self.storex._send_command = AsyncMock(return_value="370")
    self.assertEqual(await self.storex.request_current_temperature(), 37.0)

  async def test_humidity(self) -> None:
    await self.storex.set_humidity(0.9)
    self.storex._send_command.assert_any_call("WR DM893 00900")
    self.storex._send_command = AsyncMock(return_value="900")
    self.assertEqual(await self.storex.request_current_humidity(), 0.9)

  async def test_gases(self) -> None:
    await self.storex.set_co2_level(0.05)
    self.storex._send_command.assert_any_call("WR DM894 00500")
    await self.storex.set_n2_level(0.9)
    self.storex._send_command.assert_any_call("WR DM895 09000")

  async def test_fraction_range(self) -> None:
    with self.assertRaises(ValueError):
      await self.storex.set_humidity(1.1)
    with self.assertRaises(ValueError):
      await self.storex.set_co2_level(-0.1)

  async def test_shaking(self) -> None:
    await self.storex.start_shaking(25.0)
    self.storex._send_command.assert_any_call("WR DM39 00250")
    self.storex._send_command.assert_any_call("ST 1913")
    await self.storex.stop_shaking()
    self.storex._send_command.assert_any_call("RS 1913")

  async def test_rejects_missing_features(self) -> None:
    no_climate = make_storex("STX44_NC")
    with self.assertRaises(NotImplementedError):
      await no_climate.set_temperature(37.0)
    with self.assertRaises(NotImplementedError):
      await no_climate.request_current_temperature()
    with self.assertRaises(NotImplementedError):
      await no_climate.set_humidity(0.5)
    with self.assertRaises(NotImplementedError):
      await make_storex().start_shaking(10.0)


class TestStorageOperations(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.rack = storex_rack_17mm_22("rack")
    self.storex = make_storex(racks=[self.rack])
    self.storex._send_command = AsyncMock(return_value="OK")
    self.storex._wait_ready = AsyncMock()

  async def test_fetch_moves_resource_to_tray(self) -> None:
    plate = make_plate()
    self.rack.sites[0].assign_child_resource(plate)

    result = await self.storex.fetch_plate_to_loading_tray("plate")

    self.assertIs(result, plate)
    self.assertIsNone(self.rack.sites[0].resource)
    self.assertIs(self.storex.loading_tray.resource, plate)
    self.storex._send_command.assert_any_call("ST 1905")
    self.storex._send_command.assert_any_call("ST 1903")

  async def test_take_in_moves_resource_to_selected_site(self) -> None:
    plate = make_plate()
    self.storex.loading_tray.assign_child_resource(plate)

    result = await self.storex.take_in_plate(self.rack.sites[2])

    self.assertIs(result, plate)
    self.assertIsNone(self.storex.loading_tray.resource)
    self.assertIs(self.rack.sites[2].resource, plate)
    self.storex._send_command.assert_any_call("ST 1904")

  async def test_move_plate_updates_internal_site(self) -> None:
    plate = make_plate()
    self.rack.sites[0].assign_child_resource(plate)

    await self.storex.move_plate("plate", self.rack.sites[1])

    self.assertIsNone(self.rack.sites[0].resource)
    self.assertIs(self.rack.sites[1].resource, plate)
    self.storex._send_command.assert_any_call("ST 1908")
    self.storex._send_command.assert_any_call("ST 1909")

  async def test_move_plate_validates_destination_before_pick(self) -> None:
    self.rack.sites[0].assign_child_resource(make_plate())
    other_rack = storex_rack_17mm_22("other")
    with self.assertRaises(ValueError):
      await self.storex.move_plate("plate", other_rack.sites[0])
    self.storex._send_command.assert_not_awaited()

  async def test_fetch_refuses_occupied_tray_before_commands(self) -> None:
    self.rack.sites[0].assign_child_resource(make_plate("stored"))
    self.storex.loading_tray.assign_child_resource(make_plate("tray"))
    with self.assertRaises(ValueError):
      await self.storex.fetch_plate_to_loading_tray("stored")
    self.storex._send_command.assert_not_awaited()

  async def test_barcode_scan_waits_and_resets_shovel(self) -> None:
    barcode = Barcode(data="ABC", symbology="unknown", position_on_resource="front")
    scanner = AsyncMock()
    scanner.scan_barcode = AsyncMock(return_value=barcode)
    self.storex.barcode_scanner = scanner
    plate = make_plate()
    self.rack.sites[0].assign_child_resource(plate)

    result = await self.storex.scan_barcode(self.rack.sites[0])

    self.assertIs(result, barcode)
    self.assertIs(plate.barcode, barcode)
    calls = [call.args[0] for call in self.storex._send_command.await_args_list]
    self.assertLess(calls.index("ST 1910"), calls.index("RS 1910"))
    self.assertIn("ST 1903", calls)


class TestSensorsAndSwapStation(unittest.IsolatedAsyncioTestCase):
  async def test_sensors(self) -> None:
    storex = make_storex()
    storex._send_command = AsyncMock(side_effect=["OK", "1", "0", "1"])
    self.assertTrue(await storex.request_shovel_sensor())
    self.assertFalse(await storex.request_transfer_sensor())
    self.assertTrue(await storex.request_second_transfer_sensor())

  async def test_swap_station_moves_only_when_needed(self) -> None:
    storex = make_storex()
    storex._send_command = AsyncMock(return_value="0")
    await storex.move_swap_station_home()
    self.assertEqual(storex._send_command.await_count, 1)

    storex._send_command.reset_mock()
    storex._send_command.return_value = "1"
    await storex.move_swap_station_home()
    self.assertEqual(
      [call.args[0] for call in storex._send_command.await_args_list],
      ["RD 1912", "RS 1912"],
    )

  async def test_swap_station_moves_to_swapped(self) -> None:
    storex = make_storex()
    storex._send_command = AsyncMock(return_value="0")
    await storex.move_swap_station_swapped()
    self.assertEqual(
      [call.args[0] for call in storex._send_command.await_args_list],
      ["RD 1912", "ST 1912"],
    )


class TestErrors(unittest.IsolatedAsyncioTestCase):
  async def test_empty_response(self) -> None:
    storex = make_storex()
    storex.io = AsyncMock()
    storex.io.port = "/dev/null"
    storex.io.read = AsyncMock(return_value=b"")
    with self.assertRaises(RuntimeError):
      await storex._send_command("RD 1915")

  async def test_controller_error(self) -> None:
    storex = make_storex()
    storex.io = AsyncMock()
    storex.io.port = "/dev/null"
    storex.io.read = AsyncMock(return_value=b"E1")
    with self.assertRaises(StoreXControllerCommandError):
      await storex._send_command("ST 1801")

  async def test_unknown_error(self) -> None:
    storex = make_storex()
    storex.io = AsyncMock()
    storex.io.port = "/dev/null"
    storex.io.read = AsyncMock(return_value=b"E9")
    with self.assertRaisesRegex(RuntimeError, "Unknown error"):
      await storex._send_command("ST 1801")


if __name__ == "__main__":
  unittest.main()
