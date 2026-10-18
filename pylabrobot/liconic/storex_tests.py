"""Offline protocol and recovery tests using unittest mocks."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, call, create_autospec, patch

from pylabrobot.io.serial import Serial
from pylabrobot.liconic import (
  StoreX,
  StoreXCassette,
  StoreXError,
  StoreXProtocolError,
  UnresolvedPlateTransfer,
)
from pylabrobot.resources import Coordinate, Lid, Plate
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


class StoreXTestCase(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.rack = StoreXCassette("rack", num_sites=3, site_height=17, pitch=23)
    self.store = StoreX(
      "store",
      "offline",
      "STX44_IC",
      {2: self.rack},
      read_timeout=0.03,
      has_shaker=True,
      has_humidity_control=True,
      has_co2_control=True,
      has_n2_control=True,
      swap_home_sensor=100,
    )
    self.io = create_autospec(Serial, instance=True)
    self.store.io = self.io
    sleep_patch = patch("pylabrobot.liconic.storex.sleep", new_callable=AsyncMock)
    self.sleep = sleep_patch.start()
    self.addCleanup(sleep_patch.stop)
    self.io.readline.return_value = b"CC\r\n"
    await self.store.setup()
    self.io.reset_mock()
    self.sleep.reset_mock()
    self.io.readline.return_value = b"OK\r\n"
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.rack.sites[0].assign_child_resource(self.plate)


class StoreXProtocolTests(StoreXTestCase):
  async def test_fetch_command_sequence(self) -> None:
    self.io.readline.side_effect = [
      b"0\r\n",
      b"1\r\n",
      b"00004\r\n",  # fault, ready, initialized
      b"0\r\n",
      b"0\r\n",
      b"0\r\n",
      b"0\r\n",
      b"0\r\n",  # handling modes
      b"0\r\n",
      b"1,00005,00005\r\n",
      b"0,00000,00005\r\n",  # primary route, home
      b"OK\r\n",
      b"0\r\n",
      b"0\r\n",  # shovel and tray empty
      b"OK\r\n",
      b"OK\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
      b"OK\r\n",  # select slot
      b"OK\r\n",
      b"0\r\n",  # shovel empty before pick
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"1\r\n",  # pick complete, shovel occupied
      b"OK\r\n",
      b"1\r\n",
      b"0\r\n",  # shovel occupied, tray empty before put
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",  # put complete
    ]
    await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"RD DM202\r"),
        call(b"RD 1601\r"),
        call(b"RD 1610\r"),
        call(b"RD 1613\r"),
        call(b"RD 1714\r"),
        call(b"RD 1910\r"),
        call(b"RD 1607\r"),
        call(b"RD T17\r"),
        call(b"RD T16\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"RD 1813\r"),
        call(b"WR DM23 788\r"),
        call(b"WR DM25 3\r"),
        call(b"WR DM0 2\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"WR DM5 1\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"ST 1908\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"RD 1813\r"),
        call(b"ST 1906\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"RD 1813\r"),
      ],
    )
    self.assertIs(self.plate.parent, self.store.loading_tray)
    self.assertIsNone(self.store.shovel.resource)

  async def test_framing_accepts_fragmented_reply(self) -> None:
    self.io.readline.side_effect = [b"00", b"370\r", b"\n"]
    self.assertEqual(await self.store.get_temperature(), 37)
    self.io.write.assert_awaited_once_with(b"RD DM982\r")
    self.assertEqual(self.io.readline.await_count, 3)
    self.io.read.assert_not_called()

  async def test_malformed_and_unknown_replies_invalidate_transport(self) -> None:
    for reply in (b"1\n", b"\r\n", b"OK\r\n0\r\n", b"E9\r\n", b"\xff\r\n"):
      with self.subTest(reply=reply):
        self.io.readline.return_value = b"CC\r\n"
        await self.store.setup()
        self.io.reset_mock()
        self.io.readline.return_value = reply
        with self.assertRaises((StoreXProtocolError, UnicodeDecodeError)):
          await self.store.request_ready()
        self.io.stop.assert_awaited_once()
        self.assertIs(self.plate.parent, self.rack.sites[0])

  async def test_partial_reply_times_out_and_never_retries(self) -> None:
    self.io.readline.return_value = b"1\r"
    with patch("pylabrobot.liconic.storex.time") as clock:
      clock.monotonic.side_effect = [0, 0, 1]
      with self.assertRaises(TimeoutError):
        await self.store.request_ready()
    self.io.write.assert_awaited_once_with(b"RD 1915\r")
    self.io.stop.assert_awaited_once()

  async def test_reconnect_waits_for_failed_exchange_to_finish_closing(self) -> None:
    closing = asyncio.Event()
    release_close = asyncio.Event()

    async def slow_close() -> None:
      closing.set()
      await release_close.wait()

    self.io.stop.side_effect = slow_close
    self.io.readline.side_effect = [OSError("Lost reply"), b"CC\r\n"]
    query = asyncio.create_task(self.store.request_ready())
    await closing.wait()
    reconnect = asyncio.create_task(self.store.setup())
    await asyncio.sleep(0)
    self.io.setup.assert_not_awaited()
    release_close.set()
    with self.assertRaises(OSError):
      await query
    await reconnect
    self.io.setup.assert_awaited_once()
    self.assertEqual(self.io.write.await_args_list, [call(b"RD 1915\r"), call(b"CR\r")])

  async def test_controller_fault_preserves_code_command_and_description(self) -> None:
    self.io.readline.return_value = b"E1\r\n"
    with self.assertRaises(StoreXError) as caught:
      await self.store.request_ready()
    self.assertEqual(caught.exception.code, "E1")
    self.assertEqual(caught.exception.command, "RD 1915")
    self.assertIn("Invalid command", caught.exception.message)
    self.io.stop.assert_not_awaited()

  async def test_busy_fault_is_reported_before_ready_timeout_without_reset(self) -> None:
    self.io.readline.side_effect = [b"1\r\n", b"00013\r\n"]
    with self.assertRaises(StoreXError) as caught:
      await self.store._wait_ready(0.01)
    self.assertEqual(caught.exception.code, "00013")
    self.assertEqual(self.io.write.await_args_list, [call(b"RD 1814\r"), call(b"RD DM200\r")])

  async def test_plate_ready_uses_1815(self) -> None:
    self.io.readline.return_value = b"1\r\n"
    self.assertTrue(await self.store.request_plate_ready())
    self.io.write.assert_awaited_once_with(b"RD 1815\r")

  async def test_setup_is_repeatable_and_does_not_home(self) -> None:
    await self.store.setup()
    self.io.setup.assert_not_awaited()
    self.io.write.assert_not_awaited()
    await self.store.stop()
    await self.store.stop()
    self.io.reset_mock()
    self.io.readline.return_value = b"CC\r\n"
    await self.store.setup()
    self.assertEqual(
      self.io.mock_calls,
      [
        call.setup(),
        call.send_break(duration=0.2),
        call.reset_input_buffer(),
        call.reset_output_buffer(),
        call.write(b"CR\r"),
        call.readline(),
      ],
    )
    self.sleep.assert_awaited_once_with(0.15)
    self.assertIs(self.plate.parent, self.rack.sites[0])

  async def test_failed_and_cancelled_setup_close_connection(self) -> None:
    await self.store.stop()
    for reply in (b"OK\r\n", asyncio.CancelledError()):
      with self.subTest(reply=reply):
        self.io.reset_mock()
        self.io.readline.side_effect = [reply]
        with self.assertRaises((StoreXProtocolError, asyncio.CancelledError)):
          await self.store.setup()
        self.io.stop.assert_awaited()
        self.assertFalse(self.store._connected)

  async def test_initialize_confirms_state_and_is_repeatable(self) -> None:
    self.io.readline.side_effect = [
      b"0\r\n",
      b"00000\r\n",  # no fault, not initialized
      b"OK\r\n",
      b"0\r\n",  # enable shovel sensor, empty
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
      b"00004\r\n",  # initialize, ready, status
      b"0\r\n",
      b"00004\r\n",
      b"0\r\n",
      b"1\r\n",  # repeated initialize
    ]
    await self.store.initialize()
    await self.store.initialize()
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD 1814\r"),
        call(b"RD DM202\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"ST 1801\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"RD DM202\r"),
        call(b"RD 1814\r"),
        call(b"RD DM202\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
      ],
    )

  async def test_initialization_refuses_occupied_shovel(self) -> None:
    self.io.readline.side_effect = [b"0\r\n", b"00000\r\n", b"OK\r\n", b"1\r\n"]
    with self.assertRaisesRegex(RuntimeError, "plate on the shovel"):
      await self.store.initialize()
    self.assertEqual(
      self.io.write.await_args_list,
      [call(b"RD 1814\r"), call(b"RD DM202\r"), call(b"ST 1911\r"), call(b"RD 1812\r")],
    )

  async def test_motion_waits_before_first_ready_poll(self) -> None:
    with patch.object(self.store, "_wait_ready", new_callable=AsyncMock) as wait_ready:
      sequence = Mock()
      sequence.attach_mock(self.io.write, "write")
      sequence.attach_mock(self.sleep, "sleep")
      sequence.attach_mock(wait_ready, "wait_ready")
      await self.store._send_command_and_wait_until_ready("ST 1908", 60)
    self.assertEqual(
      sequence.mock_calls, [call.write(b"ST 1908\r"), call.sleep(0.25), call.wait_ready(60)]
    )

  async def test_select_site_programs_calibration_before_carousel_and_level(self) -> None:
    with patch.object(self.store, "_wait_ready", new_callable=AsyncMock) as wait_ready:
      sequence = Mock()
      sequence.attach_mock(self.io.write, "write")
      sequence.attach_mock(wait_ready, "wait_ready")
      await self.store._select_site(self.rack.sites[1], 60)
    self.assertEqual(
      sequence.mock_calls,
      [
        call.write(b"WR DM23 788\r"),
        call.write(b"WR DM25 3\r"),
        call.write(b"WR DM0 2\r"),
        call.wait_ready(60),
        call.write(b"WR DM5 2\r"),
      ],
    )


class StoreXTransferTests(StoreXTestCase):
  async def asyncSetUp(self) -> None:
    await super().asyncSetUp()
    self.operable_patch = patch.object(self.store, "_require_operable", new_callable=AsyncMock)
    self.operable = self.operable_patch.start()
    self.addCleanup(self.operable_patch.stop)
    configuration_patch = patch.object(
      self.store, "_require_transfer_configuration", new_callable=AsyncMock
    )
    self.configuration = configuration_patch.start()
    self.addCleanup(configuration_patch.stop)
    occupancy_patch = patch.object(self.store, "_require_occupancy", new_callable=AsyncMock)
    self.occupancy = occupancy_patch.start()
    self.addCleanup(occupancy_patch.stop)
    select_patch = patch.object(self.store, "_select_site", new_callable=AsyncMock)
    self.select = select_patch.start()
    self.addCleanup(select_patch.stop)
    wait_patch = patch.object(self.store, "_wait_ready", new_callable=AsyncMock)
    self.wait_ready = wait_patch.start()
    self.addCleanup(wait_patch.stop)

  async def test_fetch_uses_staged_commands_and_commits_inventory(self) -> None:
    self.assertIs(await self.store.fetch_plate_to_loading_tray("plate"), self.plate)
    self.assertIs(self.plate.parent, self.store.loading_tray)
    self.assertIsNone(self.store.shovel.resource)
    self.assertEqual(self.io.write.await_args_list, [call(b"ST 1908\r"), call(b"ST 1906\r")])
    self.select.assert_awaited_once_with(self.rack.sites[0], 120)
    self.assertEqual(
      self.occupancy.await_args_list,
      [
        call(self.store.shovel, False),
        call(self.rack.sites[0], True),
        call(self.store.loading_tray, False),
        call(self.rack.sites[0], True),
        call(self.store.shovel, False),
        call(self.rack.sites[0], False),
        call(self.store.shovel, True),
        call(self.store.shovel, True),
        call(self.store.loading_tray, False),
        call(self.store.shovel, False),
        call(self.store.loading_tray, True),
      ],
    )
    self.io.reset_mock()
    self.select.reset_mock()
    await self.store.fetch_plate_to_loading_tray(self.plate)
    self.io.write.assert_not_awaited()
    self.select.assert_not_awaited()

  async def test_store_then_repeat_does_not_move(self) -> None:
    self.plate.unassign()
    self.store.loading_tray.assign_child_resource(self.plate)
    await self.store.store_plate(self.plate, self.rack.sites[1])
    self.assertIs(self.plate.parent, self.rack.sites[1])
    self.assertEqual(self.io.write.await_args_list, [call(b"ST 1907\r"), call(b"ST 1909\r")])
    self.select.assert_has_awaits([call(self.rack.sites[1], 120), call(self.rack.sites[1], 120)])
    self.io.reset_mock()
    await self.store.store_plate(self.plate, self.rack.sites[1])
    self.io.write.assert_not_awaited()

  async def test_between_cassettes_selects_both_calibrations(self) -> None:
    other = StoreXCassette("deepwell", num_sites=2, site_height=44, pitch=50, pitch_steps=1700)
    self.store._racks[5] = other
    self.store.assign_child_resource(other, location=None)
    await self.store.store_plate(self.plate, other.sites[1])
    self.assertEqual(
      self.select.await_args_list, [call(self.rack.sites[0], 120), call(other.sites[1], 120)]
    )
    self.assertEqual(self.io.write.await_args_list, [call(b"ST 1908\r"), call(b"ST 1909\r")])
    self.assertIs(self.plate.parent, other.sites[1])

  async def test_sensor_preflight_happens_before_motion(self) -> None:
    self.occupancy.side_effect = [None, None, RuntimeError("sensor reports occupied")]
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.io.write.assert_not_awaited()
    self.select.assert_not_awaited()
    self.assertIsNone(self.store.unresolved_transfer)
    self.assertIs(self.plate.parent, self.rack.sites[0])

  async def test_lost_pick_reply_records_uncertainty_and_survives_reconnect(self) -> None:
    self.io.readline.side_effect = [OSError("Lost pick reply"), b"CC\r\n"]
    with self.assertRaises(OSError):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    transfer = self.store.unresolved_transfer
    assert transfer is not None
    self.assertIs(transfer.source, self.rack.sites[0])
    self.assertIs(transfer.destination, self.store.shovel)
    self.assertIs(self.plate.parent, self.rack.sites[0])
    self.io.stop.assert_awaited_once()
    await self.store.setup()
    self.assertIs(self.store.unresolved_transfer, transfer)
    self.operable_patch.stop()
    self.io.reset_mock()
    for action in (
      self.store.initialize,
      self.store.open_door,
      self.store.recover,
      lambda: self.store.fetch_plate_to_loading_tray(self.plate),
      lambda: self.store.scan_barcode(self.rack.sites[0], AsyncMock()),
    ):
      with self.assertRaisesRegex(RuntimeError, "unresolved"):
        await action()
    self.io.write.assert_not_awaited()

  async def test_lost_put_reply_retains_confirmed_shovel_assignment(self) -> None:
    self.io.readline.side_effect = [b"OK\r\n", OSError("Lost put reply")]
    with self.assertRaises(OSError):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIs(self.plate.parent, self.store.shovel)
    transfer = self.store.unresolved_transfer
    assert transfer is not None
    self.assertIs(transfer.source, self.store.shovel)
    self.assertIs(transfer.destination, self.store.loading_tray)
    self.io.stop.assert_awaited_once()
    self.assertEqual(self.io.write.await_args_list, [call(b"ST 1908\r"), call(b"ST 1906\r")])

  async def test_cancellation_preserves_uncertainty_and_closes_serial(self) -> None:
    self.io.readline.side_effect = asyncio.CancelledError()
    with self.assertRaises(asyncio.CancelledError):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.io.stop.assert_awaited_once()
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.assertIs(self.plate.parent, self.rack.sites[0])
    self.io.write.assert_awaited_once_with(b"ST 1908\r")

  async def test_resource_commit_failure_remains_reconcilable(self) -> None:
    with patch.object(
      self.store.loading_tray, "assign_child_resource", side_effect=RuntimeError("callback")
    ):
      with self.assertRaisesRegex(RuntimeError, "callback"):
        await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.io.readline.side_effect = [b"0\r\n", b"1\r\n", b"OK\r\n", b"0\r\n", b"1\r\n"]
    await self.store.resolve_unresolved_transfer("destination")
    self.assertIs(self.plate.parent, self.store.loading_tray)

  async def test_concurrent_fetch_validates_under_operation_lock(self) -> None:
    other = cor_96_wellplate_360uL_Fb("other")
    self.rack.sites[1].assign_child_resource(other)
    moving = asyncio.Event()
    finish = asyncio.Event()

    async def wait_for_motion(timeout: float) -> None:
      moving.set()
      await finish.wait()

    self.wait_ready.side_effect = wait_for_motion
    first = asyncio.create_task(self.store.fetch_plate_to_loading_tray(self.plate))
    await moving.wait()
    second = asyncio.create_task(self.store.fetch_plate_to_loading_tray(other))
    await asyncio.sleep(0)
    self.operable.assert_awaited_once()
    finish.set()
    await first
    with self.assertRaises((RuntimeError, ValueError)):
      await second
    self.assertEqual(self.io.write.await_args_list, [call(b"ST 1908\r"), call(b"ST 1906\r")])
    self.assertIs(other.parent, self.rack.sites[1])

  async def test_completed_command_with_missing_plate_sensor_stays_unresolved(self) -> None:
    self.occupancy.side_effect = [None] * 6 + [RuntimeError("sensor reports empty shovel")]
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIs(self.plate.parent, self.rack.sites[0])
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.io.write.assert_awaited_once_with(b"ST 1908\r")

  async def test_resume_confirmed_shovel_plate_only_places(self) -> None:
    self.plate.unassign()
    self.store.shovel.assign_child_resource(self.plate)
    await self.store.fetch_plate_to_loading_tray(self.plate)
    self.io.write.assert_awaited_once_with(b"ST 1906\r")
    self.select.assert_awaited_once_with(self.rack.sites[0], 120)
    self.assertIs(self.plate.parent, self.store.loading_tray)


class StoreXHandlingTests(StoreXTestCase):
  async def test_external_handshake_modes_are_rejected(self) -> None:
    for index, relay in enumerate((1601, 1610, 1613, 1714, 1910)):
      with self.subTest(relay=relay):
        self.io.reset_mock()
        self.io.readline.side_effect = (
          [b"0\r\n", b"1\r\n", b"00004\r\n"] + [b"0\r\n"] * index + [b"1\r\n"]
        )
        with self.assertRaisesRegex(RuntimeError, f"mode {relay}"):
          await self.store.fetch_plate_to_loading_tray(self.plate)
        self.assertEqual(
          self.io.write.await_args_list,
          [call(b"RD 1814\r"), call(b"RD 1915\r"), call(b"RD DM202\r")]
          + [call(f"RD {n}\r".encode()) for n in (1601, 1610, 1613, 1714, 1910)[: index + 1]],
        )

  async def test_reversed_transfer_route_is_rejected(self) -> None:
    self.io.readline.return_value = b"1\r\n"
    with patch.object(self.store, "_require_operable", new_callable=AsyncMock):
      with self.assertRaisesRegex(RuntimeError, "reversed"):
        await self.store.fetch_plate_to_loading_tray(self.plate)
    self.io.write.assert_awaited_once_with(b"RD 1607\r")

  async def test_swap_home_is_idempotent_and_uses_debounced_sensor(self) -> None:
    self.io.readline.side_effect = [b"1,00005,00005\r\n", b"0,00000,00005\r\n", b"0\r\n"]
    with patch.object(self.store, "_require_operable", new_callable=AsyncMock):
      await self.store.turn_swap_station(home=True)
    self.assertEqual(
      self.io.write.await_args_list, [call(b"RD T17\r"), call(b"RD T16\r"), call(b"RD 1912\r")]
    )

  async def test_swap_waits_for_physical_arrival(self) -> None:
    self.io.readline.side_effect = [
      b"1,00005,00005\r\n",
      b"0,00000,00005\r\n",
      b"0\r\n",
      b"OK\r\n",
      b"0,00000,00005\r\n",
      b"0,00000,00005\r\n",
      b"0\r\n",
      b"0,00000,00005\r\n",
      b"1,00005,00005\r\n",
    ]
    with patch.object(self.store, "_require_operable", new_callable=AsyncMock):
      await self.store.turn_swap_station(home=False)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD T17\r"),
        call(b"RD T16\r"),
        call(b"RD 1912\r"),
        call(b"ST 1912\r"),
        call(b"RD T17\r"),
        call(b"RD T16\r"),
        call(b"RD 1814\r"),
        call(b"RD T17\r"),
        call(b"RD T16\r"),
      ],
    )

  async def test_swap_requires_calibration_and_rejects_contradictory_sensors(self) -> None:
    self.store.swap_home_sensor = None
    with self.assertRaisesRegex(RuntimeError, "measured"):
      await self.store.request_swap_station_position()
    self.io.write.assert_not_awaited()
    self.store.swap_home_sensor = 100
    self.io.readline.return_value = b"1,00005,00005\r\n"
    with self.assertRaises(StoreXProtocolError):
      await self.store.request_swap_station_position()

  async def test_swap_home_overrides_pending_departure_from_home_switch(self) -> None:
    self.io.readline.side_effect = [
      b"1,00005,00005\r\n",
      b"0,00000,00005\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"1,00005,00005\r\n",
      b"0,00000,00005\r\n",
    ]
    with patch.object(self.store, "_require_operable", new_callable=AsyncMock):
      await self.store.turn_swap_station(home=True)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD T17\r"),
        call(b"RD T16\r"),
        call(b"RD 1912\r"),
        call(b"RS 1912\r"),
        call(b"RD T17\r"),
        call(b"RD T16\r"),
      ],
    )

  async def test_gate_operations_confirm_end_switch_and_repeat(self) -> None:
    self.io.readline.side_effect = [b"0\r\n", b"OK\r\n", b"1\r\n", b"1\r\n"] * 2
    with (
      patch.object(self.store, "_require_operable", new_callable=AsyncMock),
      patch.object(self.store, "_wait_ready", new_callable=AsyncMock),
    ):
      await self.store.open_door()
      await self.store.open_door()
      await self.store.close_door()
      await self.store.close_door()
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD 3\r"),
        call(b"ST 1901\r"),
        call(b"RD 3\r"),
        call(b"RD 3\r"),
        call(b"RD 2\r"),
        call(b"ST 1902\r"),
        call(b"RD 2\r"),
        call(b"RD 2\r"),
      ],
    )

  async def test_gate_missing_end_switch_requires_recovery(self) -> None:
    self.io.readline.side_effect = [b"0\r\n", b"OK\r\n", b"0\r\n"]
    with (
      patch.object(self.store, "_require_operable", new_callable=AsyncMock),
      patch.object(self.store, "_wait_ready", new_callable=AsyncMock),
    ):
      with self.assertRaisesRegex(RuntimeError, "end switch"):
        await self.store.open_door()
    self.assertTrue(self.store.needs_recovery)

  async def test_barcode_success_failure_and_cancellation_reset_positioning_mode(self) -> None:
    barcode = Barcode("ABC", "Code 128", "front")
    for result in (barcode, ValueError("reader"), asyncio.CancelledError()):
      with self.subTest(result=result):
        self.io.reset_mock()
        reader = AsyncMock(side_effect=[result])
        with (
          patch.object(self.store, "_require_operable", new_callable=AsyncMock),
          patch.object(
            self.store, "_read_shovel_sensor", new_callable=AsyncMock, return_value=False
          ),
          patch.object(self.store, "_select_site", new_callable=AsyncMock),
          patch.object(self.store, "_wait_ready", new_callable=AsyncMock),
        ):
          if isinstance(result, BaseException):
            with self.assertRaises(type(result)):
              await self.store.scan_barcode(self.rack.sites[0], reader)
          else:
            self.assertEqual(await self.store.scan_barcode(self.rack.sites[0], reader), barcode)
            self.assertEqual(self.plate.barcode, barcode)
        self.assertEqual(self.io.write.await_args_list, [call(b"ST 1910\r"), call(b"RS 1910\r")])
        reader.assert_awaited_once()
        self.assertIsNone(self.store.unresolved_transfer)

  async def test_recover_clears_barcode_mode_after_lost_positioning_reply(self) -> None:
    self.io.readline.side_effect = [OSError("Lost positioning reply")]
    reader = AsyncMock()
    with (
      patch.object(self.store, "_require_operable", new_callable=AsyncMock),
      patch.object(self.store, "_read_shovel_sensor", new_callable=AsyncMock, return_value=False),
      patch.object(self.store, "_select_site", new_callable=AsyncMock),
    ):
      with self.assertRaises(OSError):
        await self.store.scan_barcode(self.rack.sites[0], reader)
    reader.assert_not_awaited()
    self.assertTrue(self.store.needs_recovery)
    self.io.readline.side_effect = [
      b"CC\r\n",
      b"0\r\n",
      b"1\r\n",
      b"00004\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
    ]
    self.io.reset_mock()
    await self.store.setup()
    await self.store.recover()
    self.assertFalse(self.store.needs_recovery)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"CR\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"RD DM202\r"),
        call(b"RD 1910\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"RS 1910\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
      ],
    )

  async def test_reconciliation_checks_physical_sensors_without_moving(self) -> None:
    self.plate.unassign()
    self.store.shovel.assign_child_resource(self.plate)
    self.store._unresolved_transfer = UnresolvedPlateTransfer(
      self.plate, self.store.shovel, self.store.loading_tray, "ST 1906", "Lost reply"
    )
    self.io.readline.side_effect = [b"0\r\n", b"1\r\n", b"OK\r\n", b"0\r\n"]
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.resolve_unresolved_transfer("source")
    self.assertIs(self.plate.parent, self.store.shovel)
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.io.reset_mock()
    self.io.readline.side_effect = [b"0\r\n", b"1\r\n", b"OK\r\n", b"0\r\n", b"1\r\n"]
    await self.store.resolve_unresolved_transfer("destination")
    self.assertIs(self.plate.parent, self.store.loading_tray)
    self.assertIsNone(self.store.unresolved_transfer)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"RD 1813\r"),
      ],
    )

  async def test_transfer_station_wait_is_bounded(self) -> None:
    self.io.readline.side_effect = [b"0\r\n", b"0\r\n"]
    with patch("pylabrobot.liconic.storex.time") as clock:
      clock.monotonic.side_effect = [0, 0, 0, 0, 0, 2]
      with self.assertRaises(TimeoutError):
        await self.store.wait_for_transfer_station(True, timeout=1)
    self.assertEqual(self.io.write.await_args_list, [call(b"RD 1813\r"), call(b"RD 1814\r")])

  async def test_recovery_does_not_home_a_plate_on_shovel(self) -> None:
    self.io.readline.side_effect = [b"1\r\n", b"OK\r\n", b"1\r\n"]
    with self.assertRaisesRegex(RuntimeError, "shovel"):
      await self.store.recover()
    self.assertEqual(
      self.io.write.await_args_list, [call(b"RD 1814\r"), call(b"ST 1911\r"), call(b"RD 1812\r")]
    )
    self.io.reset_mock()
    self.io.readline.side_effect = [
      b"1\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"OK\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"1\r\n",
      b"00004\r\n",
    ]
    await self.store.recover()
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"RD 1814\r"),
        call(b"ST 1911\r"),
        call(b"RD 1812\r"),
        call(b"ST 1900\r"),
        call(b"ST 1801\r"),
        call(b"RD 1814\r"),
        call(b"RD 1915\r"),
        call(b"RD DM202\r"),
      ],
    )
    self.assertFalse(self.store.needs_recovery)


class StoreXClimateTests(StoreXTestCase):
  async def test_shaker_scale_and_idempotent_activation(self) -> None:
    self.io.readline.side_effect = [
      b"OK\r\n",
      b"00025\r\n",
      b"0\r\n",
      b"OK\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"00025\r\n",
      b"1\r\n",
      b"1\r\n",
      b"00025\r\n",
      b"1\r\n",
      b"OK\r\n",
      b"0\r\n",
      b"0\r\n",
      b"0\r\n",
    ]
    await self.store.start_shaking(25)
    await self.store.start_shaking(25)
    self.assertEqual(await self.store.get_shaker_speed(), 25)
    await self.store.stop_shaking()
    await self.store.stop_shaking()
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"WR DM39 00025\r"),
        call(b"RD DM39\r"),
        call(b"RD 1913\r"),
        call(b"ST 1913\r"),
        call(b"RD 1913\r"),
        call(b"WR DM39 00025\r"),
        call(b"RD DM39\r"),
        call(b"RD 1913\r"),
        call(b"RD 1913\r"),
        call(b"RD DM39\r"),
        call(b"RD 1913\r"),
        call(b"RS 1913\r"),
        call(b"RD 1913\r"),
        call(b"RD 1913\r"),
        call(b"RD 1913\r"),
      ],
    )

  async def test_climate_units_negative_temperature_and_readback(self) -> None:
    self.io.readline.side_effect = [
      b"OK\r\n",
      b"65336\r\n",
      b"OK\r\n",
      b"00900\r\n",
      b"OK\r\n",
      b"00500\r\n",
      b"OK\r\n",
      b"09000\r\n",
      b"65336\r\n",
      b"00900\r\n",
      b"00500\r\n",
      b"09000\r\n",
      b"65436\r\n",
      b"00850\r\n",
      b"00400\r\n",
      b"08000\r\n",
    ]
    await self.store.set_target_temperature(-20)
    await self.store.set_target_humidity(0.9)
    await self.store.set_target_co2_level(0.05)
    await self.store.set_target_n2_level(0.9)
    self.assertEqual(await self.store.get_target_temperature(), -20)
    self.assertEqual(await self.store.get_target_humidity(), 0.9)
    self.assertEqual(await self.store.get_target_co2_level(), 0.05)
    self.assertEqual(await self.store.get_target_n2_level(), 0.9)
    self.assertEqual(await self.store.get_temperature(), -10)
    self.assertEqual(await self.store.get_humidity(), 0.85)
    self.assertEqual(await self.store.get_co2_level(), 0.04)
    self.assertEqual(await self.store.get_n2_level(), 0.8)
    self.assertEqual(
      self.io.write.await_args_list,
      [
        call(b"WR DM890 65336\r"),
        call(b"RD DM890\r"),
        call(b"WR DM893 00900\r"),
        call(b"RD DM893\r"),
        call(b"WR DM894 00500\r"),
        call(b"RD DM894\r"),
        call(b"WR DM895 09000\r"),
        call(b"RD DM895\r"),
        call(b"RD DM890\r"),
        call(b"RD DM893\r"),
        call(b"RD DM894\r"),
        call(b"RD DM895\r"),
        call(b"RD DM982\r"),
        call(b"RD DM983\r"),
        call(b"RD DM984\r"),
        call(b"RD DM985\r"),
      ],
    )

  async def test_rejected_climate_setpoint_readback_raises(self) -> None:
    self.io.readline.side_effect = [b"OK\r\n", b"00800\r\n"]
    with self.assertRaisesRegex(RuntimeError, "DM893 did not confirm"):
      await self.store.set_target_humidity(0.9)
    self.assertEqual(
      self.io.write.await_args_list, [call(b"WR DM893 00900\r"), call(b"RD DM893\r")]
    )

  async def test_invalid_setpoints_timeouts_and_absent_options_do_not_write(self) -> None:
    for action in (
      lambda: self.store.start_shaking(0),
      lambda: self.store.start_shaking(51),
      lambda: self.store.set_target_humidity(2),
      lambda: self.store.set_target_temperature(float("nan")),
      lambda: self.store.fetch_plate_to_loading_tray(self.plate, timeout=float("inf")),
    ):
      with self.assertRaises(ValueError):
        await action()
    self.store.has_co2_control = False
    with self.assertRaises(NotImplementedError):
      await self.store.set_target_co2_level(0.05)
    self.io.write.assert_not_awaited()


class CassetteTests(unittest.TestCase):
  def test_documented_tall_pitch_and_calibrated_override(self) -> None:
    rack = StoreXCassette("tall", num_sites=4, site_height=104, pitch=110)
    self.assertEqual(rack.pitch_steps, 3769)
    self.assertEqual(rack.sites[3].location, Coordinate(z=330))
    self.assertEqual(rack.sites[3].get_size_z(), 104)
    calibrated = StoreXCassette(
      "calibrated", num_sites=4, site_height=104, pitch=110, pitch_steps=942
    )
    self.assertEqual(calibrated.pitch_steps, 942)

  def test_plate_height_includes_lid_and_last_slot_has_real_clearance(self) -> None:
    rack = StoreXCassette("rack", num_sites=2, site_height=17, pitch=23)
    store = StoreX("store", "offline", "STX44_IC", {1: rack})
    plate = Plate(
      "plate",
      size_x=127.76,
      size_y=85.48,
      size_z=15,
      ordered_items={},
      lid=Lid("lid", size_x=127.76, size_y=85.48, size_z=5, nesting_z_height=1),
    )
    with self.assertRaisesRegex(RuntimeError, "No free slot"):
      store.find_smallest_site_for_plate(plate)

  def test_invalid_geometry_and_physical_mapping(self) -> None:
    for kwargs in (
      {"num_sites": 0},
      {"pitch": float("nan")},
      {"pitch_steps": 0},
      {"site_height": 24},
    ):
      parameters: dict = dict(num_sites=3, pitch=23, site_height=17)
      parameters.update(kwargs)
      with self.assertRaises(ValueError):
        StoreXCassette("invalid", **parameters)
    rack = StoreXCassette("rack", num_sites=2, site_height=17, pitch=23)
    with self.assertRaises(ValueError):
      StoreX("store", "offline", "STX44_IC", {1: rack, 2: rack})


if __name__ == "__main__":
  unittest.main()
