import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.agilent.vspin import _nmc
from pylabrobot.agilent.vspin.access2 import Access2
from pylabrobot.agilent.vspin.errors import CentrifugeDoorError
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.resources import Coordinate, Resource


class TestVSpinEvents(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.addCleanup(self.vspin_ftdi.stop)

  async def test_spin_emits_loaded_bucket_resources_and_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[100000, 0]
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_home_position = AsyncMock(side_effect=[0, 1])  # type: ignore[method-assign]
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(g=500, duration=1, acceleration=0.5, deceleration=0.6)

    self.assertEqual(
      [event.name for event in events],
      [
        "centrifuge.spin.started",
        "centrifuge.spin.completed",
      ],
    )
    started, completed = events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["device"]["name"], "centrifuge")
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["bucket_resources"][0]["holder"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)
    self.assertNotIn("relative_centrifugal_force_g", started.data)
    self.assertNotIn("duration_seconds", started.data)

    rpm = VSpin.g_to_rpm(500)
    spin_target = _nmc.spin_target_distance(rpm, duration=1, acceleration=0.5)
    expected_spin_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0x97,
      position=spin_target,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(0.5),
    )
    expected_deceleration_command = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      0xB6,
      velocity=0,
      acceleration=_nmc.acceleration_to_nmc(0.6),
    )
    commands = [call.args[0] for call in vspin._send_nmc.await_args_list]
    self.assertIn(expected_spin_command, commands)
    self.assertIn(expected_deceleration_command, commands)

  async def test_spin_failure_emits_requested_parameters(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(ValueError, "G-force"):
        await vspin.spin(g=0)

    self.assertEqual(
      [event.name for event in events],
      [
        "centrifuge.spin.started",
        "centrifuge.spin.failed",
      ],
    )
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])
    self.assertEqual(events[1].data["error_type"], "ValueError")

  async def test_spin_accepts_positional_parameters_with_event_bus(self):
    vspin = VSpin(name="centrifuge", device_id="test")
    vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_door_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]
    vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[100000, 0]
    )
    vspin.request_position = AsyncMock(  # type: ignore[method-assign]
      side_effect=[0, 10000000, 20000000]
    )
    vspin.request_home_position = AsyncMock(side_effect=[0, 1])  # type: ignore[method-assign]
    vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await vspin.spin(500, 1, 0.5, 0.6)

    started = events[0]
    self.assertEqual(started.data["relative_centrifugal_force"], 500)
    self.assertEqual(started.data["duration"], 1)
    self.assertEqual(started.data["acceleration_fraction"], 0.5)
    self.assertEqual(started.data["deceleration_fraction"], 0.6)


class TestVSpinProtocol(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.ftdi_patch = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    ftdi_class = self.ftdi_patch.start()
    self.addCleanup(self.ftdi_patch.stop)
    self.io = ftdi_class.return_value
    self.vspin = VSpin(name="centrifuge")

  async def test_position_status_uses_fixed_length_and_checksum(self):
    response = bytes.fromhex("11222500004f000018e0050000a4")
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(side_effect=[response[:5], response[5:]])

    status = await self.vspin.request_positions_and_tachometer()

    self.assertEqual(status.status, 0x11)
    self.assertEqual(status.position, 0x2522)
    self.assertEqual(status.velocity, 0)
    self.assertEqual(status.home_position, 0x05E0)
    self.io.write.assert_awaited_once_with(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))
    self.assertEqual([call.args[0] for call in self.io.read.await_args_list], [14, 9])

  async def test_position_status_rejects_bad_checksum(self):
    response = bytearray.fromhex("11222500004f000018e0050000a4")
    response[-1] ^= 0xFF
    self.io.write = AsyncMock(return_value=4)
    self.io.read = AsyncMock(return_value=bytes(response))

    with self.assertRaisesRegex(_nmc.NMCProtocolError, "checksum mismatch"):
      await self.vspin.request_positions_and_tachometer()

  async def test_exact_response_times_out_with_partial_bytes(self):
    self.io.read = AsyncMock(side_effect=[b"\x01", b""])

    with self.assertRaisesRegex(TimeoutError, "1 of 2 expected"):
      await self.vspin._read_exact_response(length=2, timeout=0)

  async def test_send_nmc_uses_active_status_mask_length(self):
    self.vspin._servo_status_mask = _nmc.SEND_POSITION | _nmc.SEND_VELOCITY
    response = bytes.fromhex("0101000000020004")
    self.vspin.send_command = AsyncMock(return_value=response)  # type: ignore[method-assign]

    parsed = await self.vspin._send_nmc(_nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS))

    self.assertEqual(parsed, _nmc.NMCResponse(status=1, data=bytes.fromhex("010000000200")))
    self.vspin.send_command.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
      expected_response_length=8,
      read_timeout=0.2,
    )

  async def test_io_sensor_polarities_match_vspin_wiring(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=(1 << _nmc.INPUT_DOOR_OPEN) | (1 << _nmc.INPUT_BUCKET_LOCKED)
    )

    self.assertTrue(await self.vspin.request_door_open())
    self.assertTrue(await self.vspin.request_door_locked())
    self.assertFalse(await self.vspin.request_bucket_locked())

  async def test_io_output_updates_preserve_other_output_bits(self):
    self.vspin._io_output_word = 1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER
    self.vspin._send_nmc = AsyncMock(  # type: ignore[method-assign]
      return_value=_nmc.NMCResponse(status=0, data=b"")
    )

    await self.vspin._set_io_output_bit(_nmc.OUTPUT_DOOR_LOCK_CYLINDER, True)

    expected_word = (1 << _nmc.OUTPUT_BUCKET_LOCK_CYLINDER) | (1 << _nmc.OUTPUT_DOOR_LOCK_CYLINDER)
    self.vspin._send_nmc.assert_awaited_once_with(  # type: ignore[attr-defined]
      _nmc.build_set_output(_nmc.PIC_IO_ADDRESS, expected_word)
    )
    self.assertEqual(self.vspin._io_output_word, expected_word)

  async def test_position_wait_reports_last_position(self):
    self.vspin.request_position = AsyncMock(return_value=25)  # type: ignore[method-assign]

    with self.assertRaisesRegex(TimeoutError, "last position was 25"):
      await self.vspin._wait_for_position(100, timeout=0, operation="test motion")

  async def test_spin_faults_decode_ground_truth_io_bits(self):
    self.vspin._request_input_flags = AsyncMock(  # type: ignore[method-assign]
      return_value=1 << _nmc.INPUT_IMBALANCE
    )

    with self.assertRaisesRegex(RuntimeError, "imbalance"):
      await self.vspin._raise_for_spin_faults()

  async def test_stop_spin_commands_deceleration_and_confirms_zero_speed(self):
    self.vspin._spin_active = True
    self.vspin.request_tachometer = AsyncMock(  # type: ignore[method-assign]
      side_effect=[1000, 100, 0]
    )
    self.vspin._raise_for_spin_faults = AsyncMock()  # type: ignore[method-assign]
    self.vspin._command_deceleration = AsyncMock()  # type: ignore[method-assign]

    await self.vspin.stop_spin(deceleration=0.5)

    self.assertTrue(self.vspin._spin_cancel_requested)
    self.vspin._command_deceleration.assert_awaited_once_with(0.5)  # type: ignore[attr-defined]

  async def test_bucket_calibration_is_normalized_and_saved_consistently(self):
    self.vspin.request_position = AsyncMock(return_value=12_345)  # type: ignore[method-assign]
    self.vspin.request_home_position = AsyncMock(return_value=400)  # type: ignore[method-assign]
    self.io.request_serial = AsyncMock(return_value="vspin-serial")

    with patch("pylabrobot.agilent.vspin.vspin._save_vspin_calibrations") as save:
      await self.vspin.set_bucket_1_position_to_current()

    self.assertEqual(self.vspin.bucket_1_remainder, 4055)
    save.assert_called_once_with("vspin-serial", 4055)

  async def test_bucket_targets_use_shortest_path_independently(self):
    self.vspin._bucket_1_remainder = 100
    self.vspin.request_home_position = AsyncMock(return_value=500)  # type: ignore[method-assign]
    self.vspin.request_position = AsyncMock(return_value=7900)  # type: ignore[method-assign]

    self.assertEqual(await self.vspin.request_bucket_1_position(), 8400)
    self.assertEqual(await self.vspin.request_bucket_2_position(), 4400)


class TestAccess2Events(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.vspin_ftdi = patch("pylabrobot.agilent.vspin.vspin.FTDI", autospec=True)
    self.access2_ftdi = patch("pylabrobot.agilent.vspin.access2.FTDI", autospec=True)
    self.vspin_ftdi.start()
    self.access2_ftdi.start()
    self.addCleanup(self.access2_ftdi.stop)
    self.addCleanup(self.vspin_ftdi.stop)

  async def asyncSetUp(self):
    self.vspin = VSpin(name="centrifuge", device_id="test")
    self.vspin._door_open = True
    self.vspin._at_bucket = self.vspin.bucket1
    self.vspin.request_door_open = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_bucket_locked = AsyncMock(return_value=True)  # type: ignore[method-assign]
    self.vspin.request_spinning = AsyncMock(return_value=False)  # type: ignore[method-assign]
    self.loader = Access2(name="loader", device_id="test", vspin=self.vspin)
    self.loader.driver.load = AsyncMock()  # type: ignore[method-assign]
    self.loader.driver.unload = AsyncMock()  # type: ignore[method-assign]

  async def test_load_emits_loader_to_bucket_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.loader.load()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.load.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.load.started",
        "centrifuge_loader.load.completed",
      ],
    )
    started, completed = lifecycle_events
    self.assertEqual(started.context["operation_id"], completed.context["operation_id"])
    self.assertEqual(started.data["resources"][0]["name"], "plate_1")
    self.assertEqual(started.data["source"]["name"], "loader")
    self.assertEqual(started.data["destination"]["name"], "centrifuge_bucket1")
    self.assertIs(self.vspin.bucket1.resource, plate)

  async def test_unload_failure_emits_bucket_to_loader_transfer(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.loader.driver.unload = AsyncMock(  # type: ignore[method-assign]
      side_effect=RuntimeError("loader fault")
    )
    events: list[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      with self.assertRaisesRegex(RuntimeError, "loader fault"):
        await self.loader.unload()

    lifecycle_events = [
      event for event in events if event.name.startswith("centrifuge_loader.unload.")
    ]
    self.assertEqual(
      [event.name for event in lifecycle_events],
      [
        "centrifuge_loader.unload.started",
        "centrifuge_loader.unload.failed",
      ],
    )
    started, failed = lifecycle_events
    self.assertEqual(started.context["operation_id"], failed.context["operation_id"])
    self.assertEqual(started.data["source"]["name"], "centrifuge_bucket1")
    self.assertEqual(started.data["destination"]["name"], "loader")
    self.assertEqual(failed.data["error_type"], "RuntimeError")

  async def test_load_requires_physical_bucket_lock_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_bucket_locked = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "physically locked"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.loader.resource, plate)
    self.assertIsNone(self.vspin.bucket1.resource)

  async def test_unload_requires_stopped_vspin_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.vspin.bucket1.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_spinning = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with self.assertRaisesRegex(RuntimeError, "must be stopped"):
      await self.loader.unload()

    self.loader.driver.unload.assert_not_awaited()  # type: ignore[attr-defined]
    self.assertIs(self.vspin.bucket1.resource, plate)
    self.assertIsNone(self.loader.resource)

  async def test_load_requires_physical_door_open_before_driver_motion(self):
    plate = Resource("plate_1", size_x=1, size_y=1, size_z=1)
    self.loader.assign_child_resource(plate, location=Coordinate.zero())
    self.vspin.request_door_open = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with self.assertRaisesRegex(CentrifugeDoorError, "door-open sensor"):
      await self.loader.load()

    self.loader.driver.load.assert_not_awaited()  # type: ignore[attr-defined]
