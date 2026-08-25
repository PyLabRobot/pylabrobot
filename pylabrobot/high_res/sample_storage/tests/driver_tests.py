import asyncio
import inspect
import json
import unittest
from typing import Dict, List
from unittest.mock import AsyncMock

from pylabrobot.events import EventBus, PLREvent, use_event_bus
from pylabrobot.high_res.sample_storage import AmbiStore, SteriStore, TundraStore
from pylabrobot.high_res.sample_storage.driver import HighResSampleStorage
from pylabrobot.high_res.sample_storage.driver.errors import (
  HighResSampleStorageAbortedError,
  HighResSampleStorageError,
  HighResSampleStorageFault,
  HighResSampleStorageProtocolError,
  PlateNotFoundError,
)
from pylabrobot.high_res.sample_storage.driver.models import get_model_info
from pylabrobot.high_res.sample_storage.driver.settings import HighResSampleStorageSettings
from pylabrobot.resources import Coordinate, Lid, Plate, PlateCarrier, PlateHolder, Resource, Well

# Real responses captured from a SteriStore (firmware 3.0.0.119, serial
# HRB-2209-35148) over the port-1000 remote-control server.
CAPTURES: Dict[str, List[str]] = {
  "version": [
    "ACK! version 1",
    "Product Name: SteriStore",
    "Serial Number: HRB-2209-35148",
    "libcommon Version:    1.1.0.119",
    "libts7600 Version:    1.0.0.119",
    "Firmware Version:     3.0.0.119",
    "Firmware Build: D9BE232A",
    "OK! version 1",
  ],
  "homedstatus": ["ACK! homedstatus 5", "not homed", "OK! homedstatus 5"],
  "doorstatus": [
    "ACK! doorstatus 17",
    "User Door: CLOSED",
    "RI: CLOSED",
    "SEAL: CLOSING",
    "RO1: CLOSING",
    "RO2: CLOSED",
    "RO3: CLOSED",
    "RO4: CLOSED",
    "OK! doorstatus 17",
  ],
  "platestatus": ["ACK! platestatus 9", "NO_PLATE", "OK! platestatus 9"],
  "neststatus": ["ACK! neststatus 11", "1: CLEAR", "2: CLEAR", "OK! neststatus 11"],
  "environmentstatus": [
    "ACK! environmentstatus 31",
    "TEMP:21.9/22.0/100.0",
    "RH:54.7/0.0/-100.0",
    "CO2:0.0/5.0/100.0",
    "O2:20.5/5.0/100.0",
    "TANK1:135.0:",
    "TANK2:135.0:",
    "OK! environmentstatus 31",
  ],
  "getstackerdimensions": [
    "ACK! getstackerdimensions 35",
    "1: 0.000 28.940 0",
    "2: 0.000 22.867 24",
    "13: 0.000 22.867 0",
    "OK! getstackerdimensions 35",
  ],
  # The home command failed because the pneumatic doors could not close (no air).
  "home": [
    "ACK! home 13",
    "Error 1: (00:32:44) 13: Unable to close all doors",
    "ERROR! home 13",
  ],
}


class FakeSocket:
  """Replays scripted line responses keyed by the command written to it."""

  def __init__(self, captures: Dict[str, List[str]]):
    self.captures = {command: list(lines) for command, lines in captures.items()}
    self.written: List[str] = []
    self._queue: List[str] = []
    self.setup_calls = 0
    self.stop_calls = 0

  async def setup(self):
    self.setup_calls += 1

  async def stop(self):
    self.stop_calls += 1

  async def write(self, data: bytes, timeout=None):
    command = data.decode("ascii").rstrip("\r\n")
    self.written.append(command)
    self._queue = list(self.captures[command])

  async def readuntil(self, separator: bytes = b"\n", timeout=None) -> bytes:
    return self._queue.pop(0).encode("ascii") + b"\r\n"


class TimeoutAfterAckSocket(FakeSocket):
  """Return one acknowledgement and then simulate a stalled device response."""

  def __init__(self, captures: Dict[str, List[str]], timeout_command: str):
    super().__init__(captures)
    self.timeout_command = timeout_command

  async def readuntil(self, separator: bytes = b"\n", timeout=None) -> bytes:
    if self.written[-1] == self.timeout_command and len(self._queue) == 1:
      raise TimeoutError("simulated response timeout")
    return await super().readuntil(separator=separator, timeout=timeout)


class HighResSampleStorageTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.driver = SteriStore(host="10.253.253.253", name="sample_store", racks={})
    self.socket = FakeSocket(CAPTURES)
    self.driver.io = self.socket  # type: ignore[assignment]
    self.retrieval = self.driver

  async def test_setup_loads_device_nests_without_locations(self):
    await self.driver.setup()

    self.assertEqual(self.socket.written, ["version", "environmentstatus", "neststatus"])
    self.assertEqual(
      [nest.name for nest in self.driver.nests], ["sample_store_nest_1", "sample_store_nest_2"]
    )
    self.assertTrue(all(nest.parent is self.driver for nest in self.driver.nests))
    self.assertTrue(all(nest.location is None for nest in self.driver.nests))
    self.assertEqual(
      set(self.driver.environment.parameters), {"TEMP", "RH", "CO2", "O2", "TANK1", "TANK2"}
    )

  async def test_setup_discovers_stackers_when_racks_are_omitted(self):
    driver = SteriStore(host="10.253.253.253", name="discovered_store")
    socket = FakeSocket(CAPTURES)
    driver.io = socket  # type: ignore[assignment]

    await driver.setup()

    self.assertEqual(
      socket.written,
      ["version", "getstackerdimensions", "environmentstatus", "neststatus"],
    )
    self.assertEqual(len(driver.racks), 1)
    rack = driver.racks[0]
    self.assertEqual(rack.name, "discovered_store_stacker_2")
    self.assertEqual(rack.capacity, 24)
    self.assertNotIn("stacker", rack.metadata)
    self.assertEqual(driver._racks_by_number, {2: rack})
    self.assertEqual(driver._locate(rack.sites[0]), (2, 1))
    self.assertEqual(driver._locate(rack.sites[23]), (2, 24))

  def test_init_rejects_invalid_physical_stacker_number(self):
    rack = PlateCarrier(name="rack", size_x=130, size_y=90, size_z=100)

    with self.assertRaisesRegex(ValueError, "positive integer"):
      HighResSampleStorage(host="10.253.253.253", name="sample_store", racks={0: rack})

  async def test_repeated_setup_reuses_discovered_stackers(self):
    driver = SteriStore(host="10.253.253.253", name="discovered_store")
    socket = FakeSocket(CAPTURES)
    driver.io = socket  # type: ignore[assignment]

    await driver.setup()
    original_racks = list(driver.racks)
    await driver.setup()

    self.assertEqual(driver.racks, original_racks)
    self.assertTrue(
      all(actual is original for actual, original in zip(driver.racks, original_racks))
    )
    self.assertEqual(socket.written.count("getstackerdimensions"), 1)

  async def test_setup_reuses_nests_when_called_again(self):
    await self.driver.setup()
    original_nests = list(self.driver.nests)

    await self.driver.setup()

    self.assertEqual(self.driver.nests, original_nests)
    self.assertTrue(
      all(actual is original for actual, original in zip(self.driver.nests, original_nests))
    )

  async def test_setup_loads_nests_when_device_nest_is_occupied(self):
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 12",
      "1: PLATE_AVAILABLE",
      "2: CLEAR",
      "OK! neststatus 12",
    ]

    await self.driver.setup()

    self.assertEqual(
      [nest.name for nest in self.driver.nests], ["sample_store_nest_1", "sample_store_nest_2"]
    )
    self.assertEqual(len(self.driver.nests), 2)
    self.assertTrue(all(nest.resource is None for nest in self.driver.nests))

  async def test_repeated_setup_preserves_nest_bookkeeping(self):
    await self.driver.setup()
    plate = Plate(
      name="plate_on_nest",
      size_x=127.76,
      size_y=85.48,
      size_z=14,
      ordered_items={},
    )
    self.driver.nests[0].assign_child_resource(plate, location=Coordinate.zero())
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 12",
      "2: CLEAR",
      "3: CLEAR",
      "OK! neststatus 12",
    ]

    await self.driver.setup()

    self.assertEqual(
      [nest.name for nest in self.driver.nests], ["sample_store_nest_1", "sample_store_nest_2"]
    )
    self.assertIs(self.driver.nests[0].resource, plate)

  async def test_setup_rejects_noncontiguous_initial_nests(self):
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 12",
      "1: CLEAR",
      "3: CLEAR",
      "OK! neststatus 12",
    ]

    with self.assertRaisesRegex(RuntimeError, r"contiguous nest numbers.*\[1, 3\]"):
      await self.driver.setup()

  async def test_setup_keeps_user_configured_model(self):
    driver = HighResSampleStorage(
      host="10.253.253.253", name="generic_store", racks={}, model="TundraStore"
    )
    driver.io = FakeSocket(CAPTURES)  # type: ignore[assignment]

    await driver.setup()

    self.assertEqual(driver.model, "TundraStore")
    self.assertEqual(driver.model_info, get_model_info("TundraStore"))
    self.assertEqual(driver.environment.temperature_range, (-20.0, 4.0))

  async def test_device_report_does_not_add_environment_control(self):
    driver = HighResSampleStorage(
      host="10.253.253.253", name="generic_store", racks={}, model="AmbiStore"
    )
    socket = FakeSocket(CAPTURES)
    driver.io = socket  # type: ignore[assignment]

    await driver.setup()

    self.assertEqual(socket.written, ["version", "neststatus"])
    self.assertFalse(hasattr(driver, "environment"))

  async def test_unverified_models_warn_during_setup_without_hiding_signature(self):
    for model_class in (AmbiStore, TundraStore):
      driver = model_class(host="10.253.253.253", name="unverified", racks={})
      driver.io = FakeSocket(CAPTURES)  # type: ignore[assignment]
      with self.assertLogs(
        "pylabrobot.high_res.sample_storage.driver.driver", level="WARNING"
      ) as logs:
        await driver.setup()
      self.assertIn("not been verified against hardware", " ".join(logs.output))
      self.assertIn("host: str", str(inspect.signature(model_class)))

  async def test_send_command_strips_ack_and_completion(self):
    data = await self.driver._send_command("neststatus")
    self.assertEqual(data, ["1: CLEAR", "2: CLEAR"])
    self.assertEqual(self.socket.written, ["neststatus"])

  async def test_send_command_requires_acknowledgement_first(self):
    self.socket.captures["bad"] = ["OK! bad 1"]

    with self.assertRaisesRegex(HighResSampleStorageProtocolError, "expected ACK! envelope"):
      await self.driver._send_command("bad")

  async def test_send_command_validates_acknowledged_command(self):
    self.socket.captures["bad"] = ["ACK! other 1", "OK! other 1"]

    with self.assertRaisesRegex(HighResSampleStorageProtocolError, "ACK echoed command 'other'"):
      await self.driver._send_command("bad")

  async def test_send_command_validates_completion_command(self):
    self.socket.captures["bad"] = ["ACK! bad 1", "OK! other 1"]

    with self.assertRaisesRegex(
      HighResSampleStorageProtocolError, "completion echoed command 'other'"
    ):
      await self.driver._send_command("bad")

  async def test_send_command_validates_completion_command_id(self):
    self.socket.captures["bad"] = ["ACK! bad 1", "OK! bad 2"]

    with self.assertRaisesRegex(HighResSampleStorageProtocolError, "does not match ACK ID '1'"):
      await self.driver._send_command("bad")

  async def test_send_command_rejects_duplicate_acknowledgement(self):
    self.socket.captures["bad"] = ["ACK! bad 1", "ACK! bad 1", "OK! bad 1"]

    with self.assertRaisesRegex(HighResSampleStorageProtocolError, "received a second ACK"):
      await self.driver._send_command("bad")

  async def test_send_command_closes_transport_after_response_timeout(self):
    socket = TimeoutAfterAckSocket({"slow": ["ACK! slow 1", "OK! slow 1"]}, timeout_command="slow")
    self.driver.io = socket  # type: ignore[assignment]

    with self.assertRaisesRegex(TimeoutError, "simulated response timeout"):
      await self.driver._send_command("slow")

    self.assertEqual(socket.stop_calls, 1)

  async def test_version(self):
    v = await self.driver.request_version()
    self.assertEqual(v.product_name, "SteriStore")
    self.assertEqual(v.serial_number, "HRB-2209-35148")
    self.assertEqual(v.firmware_version, "3.0.0.119")
    self.assertEqual(v.firmware_build, "D9BE232A")

  async def test_request_is_homed(self):
    self.assertFalse(await self.retrieval.request_is_homed())

  async def test_door_status(self):
    doors = await self.retrieval.request_door_status()
    self.assertEqual(doors["User Door"], "closed")
    self.assertEqual(doors["SEAL"], "closing")
    self.assertEqual(doors["RO1"], "closing")
    self.assertFalse(all(state == "closed" for state in doors.values()))

  async def test_nest_status(self):
    nests = await self.retrieval.request_nest_status()
    self.assertEqual(nests, {1: "clear", 2: "clear"})

  async def test_plate_available_nest_is_occupied(self):
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 12",
      "1: PLATE_AVAILABLE",
      "2: CLEAR",
      "OK! neststatus 12",
    ]

    self.assertEqual(await self.retrieval.request_nest_status(), {1: "occupied", 2: "clear"})
    self.assertTrue(await self.retrieval.request_nest_is_holding(1))
    self.assertFalse(await self.retrieval.request_nest_is_holding(2))

  async def test_plate_on_spatula(self):
    self.assertFalse(await self.retrieval.request_spatula_is_holding())

  async def test_environment_parsing(self):
    env = await self.driver.request_environment()
    self.assertAlmostEqual(env["TEMP"].current, 21.9)
    self.assertIsNotNone(env["TEMP"].setpoint)
    assert env["TEMP"].setpoint is not None  # narrow for type checker
    self.assertAlmostEqual(env["TEMP"].setpoint, 22.0)
    self.assertAlmostEqual(env["O2"].current, 20.5)
    # Sensor-only channel: current value, no setpoint.
    self.assertAlmostEqual(env["TANK1"].current, 135.0)
    self.assertIsNone(env["TANK1"].setpoint)

  async def test_temperature_reads_temp_channel(self):
    self.assertAlmostEqual(await self.driver.environment.request_current_temperature(), 21.9)
    self.assertAlmostEqual(await self.driver.environment.request_target_temperature(), 22.0)
    self.assertTrue(self.driver.environment.supports_active_cooling)
    self.assertTrue(self.driver.environment.supports_heating)
    self.assertEqual(self.driver.environment.temperature_range, (4.0, 100.0))

  def test_model_info(self):
    self.assertEqual(get_model_info("TundraStore").temperature_range, (-20.0, 4.0))
    self.assertTrue(get_model_info("TundraStore").supports_active_cooling)
    self.assertFalse(get_model_info("TundraStore").supports_heating)
    self.assertTrue(get_model_info("TundraStore").supports_humidity_control)
    self.assertIsNone(get_model_info("TundraStore").humidity_range)
    self.assertEqual(get_model_info("SteriStore").humidity_range, (0.0, 0.98))
    self.assertTrue(get_model_info("SteriStore").supports_co2_control)
    self.assertFalse(get_model_info("AmbiStore").has_environment_control)

  def test_partial_settings_preserve_missing_and_extra_firmware_keys(self):
    settings = HighResSampleStorageSettings.from_lines(
      [
        "MACHINE_TYPE = FutureStore3",
        "REST_SERVER_PORT = 1000",
        "CAROUSEL_VELOCITY = 12.5",
        "FUTURE_OPTION = enabled",
      ]
    )

    self.assertEqual(settings.machine_type, "FutureStore3")
    self.assertEqual(settings.rest_server_port, 1000)
    self.assertEqual(settings.carousel_velocity, 12.5)
    self.assertIsNone(settings.serial_number)
    self.assertEqual(settings.extra, {"FUTURE_OPTION": "enabled"})
    self.assertEqual(settings.raw["FUTURE_OPTION"], "enabled")

  async def test_temperature_range_is_validated(self):
    with self.assertRaises(ValueError):
      await self.driver.environment.set_temperature(101)
    self.assertEqual(self.socket.written, [])

  async def test_installed_temperature_limit_is_validated(self):
    self.socket.captures["environmentstatus"] = [
      "ACK! environmentstatus 32",
      "TEMP:21.9/22.0/80.0",
      "OK! environmentstatus 32",
    ]

    with self.assertRaisesRegex(ValueError, "installed limit of 80 C"):
      await self.driver.environment.set_temperature(90)

    self.assertEqual(self.socket.written, ["environmentstatus"])

  async def test_humidity_reads_rh_as_fraction(self):
    self.assertAlmostEqual(await self.driver.environment.request_current_humidity(), 0.547)
    self.assertAlmostEqual(await self.driver.environment.request_target_humidity(), 0.0)
    self.assertTrue(self.driver.environment.supports_humidity_control)

  async def test_gas_levels_are_fractions(self):
    self.assertAlmostEqual(await self.driver.environment.request_current_co2(), 0.0)
    self.assertAlmostEqual(await self.driver.environment.request_target_co2(), 0.05)
    self.assertAlmostEqual(await self.driver.environment.request_current_o2(), 0.205)
    self.assertAlmostEqual(await self.driver.environment.request_target_o2(), 0.05)
    self.assertTrue(self.driver.environment.supports_co2_control)
    self.assertTrue(self.driver.environment.supports_o2_control)

  async def test_tank_pressures(self):
    self.assertEqual(
      await self.driver.environment.request_tank_pressures(), {"TANK1": 135.0, "TANK2": 135.0}
    )

  async def test_environment_setters_convert_fractions_to_percent(self):
    commands = [
      "environmentset TEMP 37",
      "environmentset RH 90",
      "environmentset CO2 5",
      "environmentset O2 10",
    ]
    for command_id, command in enumerate(commands, start=50):
      self.socket.captures[command] = [
        f"ACK! {command} {command_id}",
        f"OK! {command} {command_id}",
      ]

    await self.driver.environment.set_temperature(37)
    await self.driver.environment.set_humidity(0.90)
    await self.driver.environment.set_co2(0.05)
    await self.driver.environment.set_o2(0.10)

    self.assertEqual(
      self.socket.written,
      [
        "environmentstatus",
        "environmentset TEMP 37",
        "environmentstatus",
        "environmentset RH 90",
        "environmentstatus",
        "environmentset CO2 5",
        "environmentstatus",
        "environmentset O2 10",
      ],
    )

  async def test_environment_control_can_be_enabled_and_disabled(self):
    commands = ["environment enable co2", "environment disable co2"]
    for command_id, command in enumerate(commands, start=60):
      self.socket.captures[command] = [
        f"ACK! {command} {command_id}",
        f"OK! {command} {command_id}",
      ]

    await self.driver.environment.start_co2_control()
    await self.driver.environment.stop_co2_control()

    self.assertEqual(
      self.socket.written,
      [
        "environmentstatus",
        "environment enable co2",
        "environmentstatus",
        "environment disable co2",
      ],
    )

  async def test_environment_control_emits_operation_events(self):
    self.socket.captures["environmentset TEMP 37"] = [
      "ACK! environmentset TEMP 37 65",
      "OK! environmentset TEMP 37 65",
    ]
    events: List[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.driver.environment.set_temperature(37)

    self.assertEqual(
      [event.name for event in events],
      [
        "temperature_controller.set_temperature.started",
        "temperature_controller.set_temperature.completed",
      ],
    )
    self.assertEqual(events[0].data["device"]["name"], "sample_store")
    self.assertEqual(events[0].data["resources"], [])
    self.assertEqual(events[0].data["target_temperature"], 37.0)
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])

  async def test_environment_validation_emits_failed_operation_event(self):
    events: List[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus), self.assertRaises(ValueError):
      await self.driver.environment.set_temperature(101)

    self.assertEqual(
      [event.name for event in events],
      [
        "temperature_controller.set_temperature.started",
        "temperature_controller.set_temperature.failed",
      ],
    )
    self.assertEqual(events[1].data["error_type"], "ValueError")
    self.assertEqual(events[0].context["operation_id"], events[1].context["operation_id"])

  async def test_environment_fraction_ranges_are_validated(self):
    with self.assertRaises(ValueError):
      await self.driver.environment.set_humidity(0.99)
    with self.assertRaises(ValueError):
      await self.driver.environment.set_co2(1.01)
    with self.assertRaises(ValueError):
      await self.driver.environment.set_o2(-0.01)
    self.assertEqual(self.socket.written, [])

  async def test_missing_optional_channel_cannot_be_controlled(self):
    self.socket.captures["environmentstatus"] = [
      "ACK! environmentstatus 31",
      "TEMP:21.9/22.0/100.0",
      "RH:54.7/0.0/-100.0",
      "CO2:0.0/5.0/100.0",
      "OK! environmentstatus 31",
    ]

    with self.assertRaisesRegex(NotImplementedError, "does not control O2"):
      await self.driver.environment.set_o2(0.10)

    self.assertFalse(self.driver.environment.supports_o2_control)

  async def test_stacker_dimensions(self):
    dims = await self.retrieval.request_stacker_dimensions()
    self.assertEqual(dims[0].stacker, 1)
    self.assertEqual(dims[0].slot_count, 0)
    self.assertEqual(dims[1].stacker, 2)
    self.assertAlmostEqual(dims[1].slot_height, 22.867)
    self.assertEqual(dims[1].slot_count, 24)

  async def test_barcode_scan_requires_clear_nests(self):
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 70",
      "1: PLATE_AVAILABLE",
      "2: CLEAR",
      "OK! neststatus 70",
    ]

    with self.assertRaisesRegex(RuntimeError, "plates are present on nests 1"):
      await self.driver.request_stacker_barcodes(2, 1)

    self.assertEqual(self.socket.written, ["neststatus"])

  async def test_barcode_scan_with_clear_nests(self):
    self.socket.captures["barcode 2 1"] = [
      "ACK! barcode 2 1 71",
      "2 1: EMPTY",
      "OK! barcode 2 1 71",
    ]

    self.assertEqual(await self.driver.request_stacker_barcodes(2, 1), ["2 1: EMPTY"])
    self.assertEqual(self.socket.written, ["neststatus", "barcode 2 1"])

  async def test_barcode_arguments_are_validated_before_querying_device(self):
    with self.assertRaises(ValueError):
      await self.driver.request_stacker_barcodes(0)
    with self.assertRaises(ValueError):
      await self.driver.request_stacker_barcodes("all", 1)
    with self.assertRaises(ValueError):
      await self.driver.request_stacker_barcodes(2, 0)
    self.assertEqual(self.socket.written, [])

  async def test_open_all_doors_accepts_confirmed_firmware_error(self):
    self.socket.captures["openalldoors"] = [
      "ACK! openalldoors 72",
      "Entering calibration mode.",
      "ERROR! openalldoors 72",
    ]
    self.socket.captures["doorstatus"] = [
      "ACK! doorstatus 73",
      "User Door: CLOSED",
      "RI: OPEN",
      "SEAL: OPEN",
      "RO1: OPEN",
      "RO2: OPEN",
      "OK! doorstatus 73",
    ]

    await self.driver.open_all_doors()

    self.assertEqual(self.socket.written, ["openalldoors", "doorstatus"])

  async def test_open_all_doors_waits_until_transitional_doors_reach_open(self):
    self.socket.captures["openalldoors"] = [
      "ACK! openalldoors 72",
      "Entering calibration mode.",
      "ERROR! openalldoors 72",
    ]
    door_status = AsyncMock(
      side_effect=[
        {"RI": "open", "SEAL": "opening"},
        {"RI": "open", "SEAL": "open"},
      ]
    )
    self.driver.request_door_status = door_status  # type: ignore[method-assign]

    await self.driver.open_all_doors()

    self.assertEqual(door_status.await_count, 2)

  async def test_open_all_doors_does_not_accept_stuck_opening_state(self):
    self.socket.captures["openalldoors"] = [
      "ACK! openalldoors 72",
      "Error 1: failed to finish opening doors",
      "ERROR! openalldoors 72",
    ]
    self.driver._motion_timeout = 0
    self.driver.request_door_status = AsyncMock(  # type: ignore[method-assign]
      return_value={"RI": "open", "SEAL": "opening"}
    )

    with self.assertRaises(HighResSampleStorageError):
      await self.driver.open_all_doors()

  async def test_open_all_doors_preserves_error_if_a_robot_door_remains_closed(self):
    self.socket.captures["openalldoors"] = [
      "ACK! openalldoors 74",
      "Error 1: failed to open doors",
      "ERROR! openalldoors 74",
    ]
    self.socket.captures["doorstatus"] = [
      "ACK! doorstatus 75",
      "User Door: CLOSED",
      "RI: OPEN",
      "SEAL: CLOSED",
      "OK! doorstatus 75",
    ]

    with self.assertRaises(HighResSampleStorageError):
      await self.driver.open_all_doors()

  async def test_home_error_raises_with_stack_detail(self):
    with self.assertRaises(HighResSampleStorageError) as ctx:
      await self.retrieval.home()
    self.assertIn("Unable to close all doors", str(ctx.exception))
    self.assertEqual(ctx.exception.command, "home")

  async def test_pick_formats_command(self):
    self.socket.captures["pick 3 12 1"] = ["ACK! pick 3 12 1 99", "OK! pick 3 12 1 99"]
    await self.retrieval._pick(3, 12, 1)
    self.assertEqual(self.socket.written, ["pick 3 12 1"])

  async def test_tray_maps_to_nest(self):
    await self.driver.setup()
    # 0-based tray -> device-reported nest number.
    self.assertEqual(self.retrieval._nest_for_tray(0), 1)
    self.assertEqual(self.retrieval._nest_for_tray(1), 2)
    with self.assertRaises(ValueError):
      self.retrieval._nest_for_tray(2)

  async def test_serialization_round_trip_preserves_configuration_and_nests(self):
    await self.driver.setup()
    serialized = self.driver.serialize()
    restored = Resource.deserialize(serialized)

    self.assertIsInstance(restored, SteriStore)
    assert isinstance(restored, SteriStore)
    self.assertEqual(restored.serialize(), serialized)
    self.assertEqual(restored.read_timeout, self.driver.read_timeout)
    self.assertEqual(restored.motion_timeout, self.driver.motion_timeout)

  def test_serialization_preserves_pending_rack_discovery(self):
    driver = SteriStore(host="192.0.2.1", name="discovery_store")

    restored = Resource.deserialize(json.loads(json.dumps(driver.serialize())))

    self.assertIsInstance(restored, SteriStore)
    assert isinstance(restored, SteriStore)
    self.assertFalse(restored._racks_loaded)
    self.assertEqual(restored.racks, [])


class HighResSampleStorageBookkeepingTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.site = PlateHolder(
      name="site_1", size_x=127.76, size_y=85.48, size_z=20, pedestal_size_z=0
    )
    rack = PlateCarrier(name="rack_1", size_x=130, size_y=90, size_z=100)
    rack.assign_child_resource(self.site, location=Coordinate.zero(), spot=0)
    well = Well(name="A1", size_x=8, size_y=8, size_z=10)
    well.location = Coordinate(10, 10, 2)
    self.plate = Plate(
      name="plate", size_x=127.76, size_y=85.48, size_z=14, ordered_items={"A1": well}
    )
    self.site.assign_child_resource(self.plate)

    self.driver = HighResSampleStorage(host="10.253.253.253", name="sample_store", racks={1: rack})
    self.socket = FakeSocket(CAPTURES)
    self.driver.io = self.socket  # type: ignore[assignment]

  async def asyncSetUp(self):
    await self.driver.setup()
    self.socket.written.clear()

  def _set_nest_status(self, nest_1: str, nest_2: str = "CLEAR") -> None:
    """Configure the fake live nest-sensor response."""
    self.socket.captures["neststatus"] = [
      "ACK! neststatus 12",
      f"1: {nest_1}",
      f"2: {nest_2}",
      "OK! neststatus 12",
    ]

  async def test_fetch_moves_plate_resource_to_nest(self):
    self.socket.captures["pick 1 1 1"] = ["ACK! pick 1 1 1 40", "OK! pick 1 1 1 40"]

    result = await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    self.assertIs(result, self.plate)
    self.assertIsNone(self.site.resource)
    self.assertIs(self.driver.nests[0].resource, self.plate)
    self.assertIs(self.plate.parent, self.driver.nests[0])
    self.assertIsNone(self.driver.unresolved_transfer)

  async def test_fetch_refuses_physically_occupied_destination_nest(self):
    self._set_nest_status("PLATE_AVAILABLE")

    with self.assertRaisesRegex(RuntimeError, "nest 1 must be clear"):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    self.assertEqual(self.socket.written, ["neststatus"])
    self.assertIs(self.site.resource, self.plate)

  async def test_fetch_emits_correlated_operation_and_bookkeeping_events(self):
    self.socket.captures["pick 1 1 1"] = ["ACK! pick 1 1 1 40", "OK! pick 1 1 1 40"]
    events: List[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    self.assertEqual(
      [event.name for event in events],
      [
        "incubator.fetch_plate.started",
        "resource.unassigned",
        "resource.assigned",
        "incubator.fetch_plate.completed",
      ],
    )
    operation_id = events[0].context["operation_id"]
    self.assertTrue(all(event.context["operation_id"] == operation_id for event in events))
    self.assertEqual(events[0].data["source"]["name"], self.site.name)
    self.assertEqual(events[0].data["destination"]["name"], self.driver.nests[0].name)

  def test_explicit_carrier_spots_define_physical_slots(self):
    first_inserted = PlateHolder(
      name="spot_7", size_x=127.76, size_y=85.48, size_z=20, pedestal_size_z=0
    )
    second_inserted = PlateHolder(
      name="spot_2", size_x=127.76, size_y=85.48, size_z=20, pedestal_size_z=0
    )
    rack = PlateCarrier(name="out_of_order", size_x=130, size_y=90, size_z=100)
    rack.assign_child_resource(first_inserted, location=Coordinate.zero(), spot=7)
    rack.assign_child_resource(second_inserted, location=Coordinate.zero(), spot=2)

    driver = HighResSampleStorage(
      host="10.253.253.253", name="explicit_slots", racks={7: rack}, model="SteriStore"
    )

    self.assertEqual(driver._locate(first_inserted), (7, 8))
    self.assertEqual(driver._locate(second_inserted), (7, 3))

    restored = Resource.deserialize(json.loads(json.dumps(driver.serialize())))
    self.assertIsInstance(restored, HighResSampleStorage)
    assert isinstance(restored, HighResSampleStorage)
    restored_rack = restored._racks_by_number[7]
    self.assertEqual(restored._locate(restored_rack.sites[7]), (7, 8))
    self.assertEqual(restored._locate(restored_rack.sites[2]), (7, 3))

  def test_site_selection_rejects_slots_that_are_too_short_for_lidded_plate(self):
    short = PlateHolder(name="short", size_x=127.76, size_y=85.48, size_z=16, pedestal_size_z=0)
    tall = PlateHolder(name="tall", size_x=127.76, size_y=85.48, size_z=18, pedestal_size_z=0)
    rack = PlateCarrier(name="height_rack", size_x=130, size_y=90, size_z=100)
    rack.assign_child_resource(short, location=Coordinate.zero(), spot=0)
    rack.assign_child_resource(tall, location=Coordinate.zero(), spot=1)
    plate = Plate(name="lidded", size_x=127.76, size_y=85.48, size_z=14, ordered_items={})
    plate.assign_child_resource(
      Lid(name="lid", size_x=127.76, size_y=85.48, size_z=5, nesting_z_height=2)
    )
    driver = HighResSampleStorage(
      host="10.253.253.253", name="height_store", racks={1: rack}, model="SteriStore"
    )

    self.assertIs(driver.find_smallest_site_for_plate(plate), tall)

  async def test_fetch_by_name_moves_plate_resource_to_nest(self):
    self.socket.captures["pick 1 1 1"] = ["ACK! pick 1 1 1 40", "OK! pick 1 1 1 40"]

    result = await self.driver.fetch_plate_to_loading_tray("plate", tray_index=0)

    self.assertIs(result, self.plate)
    self.assertIsNone(self.site.resource)
    self.assertIs(self.driver.nests[0].resource, self.plate)

  async def test_transfer_lock_covers_validation_motion_and_bookkeeping(self):
    transfer_started = asyncio.Event()
    release_transfer = asyncio.Event()
    pick_calls = []

    async def blocked_pick(stacker: int, slot: int, nest: int, close_door: bool = True):
      pick_calls.append((stacker, slot, nest, close_door))
      transfer_started.set()
      await release_transfer.wait()

    self.driver._pick = blocked_pick  # type: ignore[method-assign]
    first = asyncio.create_task(self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0))
    await asyncio.wait_for(transfer_started.wait(), timeout=1)

    second = asyncio.create_task(self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=1))
    await asyncio.sleep(0)

    # The second transfer must not even validate against the stale resource
    # tree while the first hardware move is in progress.
    self.assertEqual(pick_calls, [(1, 1, 1, True)])
    self.assertFalse(second.done())

    release_transfer.set()
    self.assertIs(await first, self.plate)
    with self.assertRaisesRegex(ValueError, "not attached to a plate carrier"):
      await second
    self.assertEqual(pick_calls, [(1, 1, 1, True)])

  def test_inventory_queries_use_resource_tree(self):
    self.assertEqual(self.driver.get_num_free_sites(), 0)
    self.assertIs(self.driver.get_site_by_plate_name("plate"), self.site)

  def test_serialization_round_trip_preserves_rack_mapping_and_plate_inventory(self):
    serialized = json.loads(json.dumps(self.driver.serialize()))
    restored = Resource.deserialize(serialized)

    self.assertIsInstance(restored, HighResSampleStorage)
    assert isinstance(restored, HighResSampleStorage)
    self.assertEqual(list(restored._racks_by_number), [1])
    restored_site = restored.get_site_by_plate_name("plate")
    self.assertEqual(restored._locate(restored_site), (1, 1))
    self.assertEqual(
      [nest.name for nest in restored.nests],
      [
        "sample_store_nest_1",
        "sample_store_nest_2",
      ],
    )

  async def test_take_in_plate_moves_nest_resource_to_selected_site(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)
    self._set_nest_status("PLATE_AVAILABLE")
    self.socket.captures["place 1 1 1"] = ["ACK! place 1 1 1 41", "OK! place 1 1 1 41"]

    result = await self.driver.take_in_plate(tray_index=0)

    self.assertIs(result, self.plate)
    self.assertIsNone(self.driver.nests[0].resource)
    self.assertIs(self.site.resource, self.plate)

  async def test_take_in_plate_emits_correlated_operation_and_bookkeeping_events(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)
    self._set_nest_status("PLATE_AVAILABLE")
    self.socket.captures["place 1 1 1"] = ["ACK! place 1 1 1 41", "OK! place 1 1 1 41"]
    events: List[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      await self.driver.take_in_plate(tray_index=0)

    self.assertEqual(
      [event.name for event in events],
      [
        "incubator.take_in_plate.started",
        "resource.unassigned",
        "resource.assigned",
        "incubator.take_in_plate.completed",
      ],
    )
    operation_id = events[0].context["operation_id"]
    self.assertTrue(all(event.context["operation_id"] == operation_id for event in events))
    self.assertEqual(events[0].data["source"]["name"], self.driver.nests[0].name)
    self.assertEqual(events[0].data["destination"]["name"], self.site.name)

  async def test_store_moves_plate_resource_to_site(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)
    self._set_nest_status("PLATE_AVAILABLE")
    self.socket.captures["place 1 1 1"] = ["ACK! place 1 1 1 41", "OK! place 1 1 1 41"]

    await self.driver.store_plate(self.plate, self.site, tray_index=0)

    self.assertIsNone(self.driver.nests[0].resource)
    self.assertIs(self.site.resource, self.plate)
    self.assertIs(self.plate.parent, self.site)
    self.assertIsNone(self.driver.unresolved_transfer)

  async def test_store_refuses_logical_plate_when_physical_nest_is_clear(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)

    with self.assertRaisesRegex(RuntimeError, "nest 1 must be occupied"):
      await self.driver.store_plate(self.plate, self.site, tray_index=0)

    self.assertEqual(self.socket.written, ["neststatus"])
    self.assertIs(self.driver.nests[0].resource, self.plate)

  async def test_transfer_plate_between_nests_updates_hardware_and_resource_tree(self):
    self.plate.unassign()
    self.driver.nests[1].assign_child_resource(self.plate)
    self._set_nest_status("CLEAR", "PLATE_AVAILABLE")
    self.socket.captures["nesttransfer 2 1"] = [
      "ACK! nesttransfer 2 1 50",
      "OK! nesttransfer 2 1 50",
    ]
    events: List[PLREvent] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)

    with use_event_bus(event_bus):
      result = await self.driver.transfer_plate_between_nests(1, 0)

    self.assertIs(result, self.plate)
    self.assertIsNone(self.driver.nests[1].resource)
    self.assertIs(self.driver.nests[0].resource, self.plate)
    self.assertEqual(self.socket.written, ["neststatus", "nesttransfer 2 1"])
    self.assertEqual(
      [event.name for event in events],
      [
        "incubator.transfer_plate.started",
        "resource.unassigned",
        "resource.assigned",
        "incubator.transfer_plate.completed",
      ],
    )
    self.assertIsNone(self.driver.unresolved_transfer)

  async def test_transfer_plate_between_nests_requires_live_sensor_agreement(self):
    self.plate.unassign()
    self.driver.nests[1].assign_child_resource(self.plate)

    with self.assertRaisesRegex(RuntimeError, "nest 2 must be occupied"):
      await self.driver.transfer_plate_between_nests(1, 0)

    self.assertEqual(self.socket.written, ["neststatus"])
    self.assertIs(self.driver.nests[1].resource, self.plate)

  async def test_aborted_nest_transfer_records_both_nest_endpoints(self):
    self.plate.unassign()
    self.driver.nests[1].assign_child_resource(self.plate)
    self._set_nest_status("CLEAR", "PLATE_AVAILABLE")
    self.socket.captures["nesttransfer 2 1"] = [
      "ACK! nesttransfer 2 1 50",
      "ABORTED! nesttransfer 2 1 50",
    ]

    with self.assertRaises(HighResSampleStorageAbortedError):
      await self.driver.transfer_plate_between_nests(1, 0)

    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertIs(transfer.source, self.driver.nests[1])
    self.assertIs(transfer.destination, self.driver.nests[0])
    self.assertIs(self.plate.parent, self.driver.nests[1])

  async def test_timeout_after_pick_ack_records_unresolved_transfer(self):
    command = "pick 1 1 1"
    captures = dict(CAPTURES)
    captures[command] = [f"ACK! {command} 42", f"OK! {command} 42"]
    socket = TimeoutAfterAckSocket(captures, timeout_command=command)
    self.driver.io = socket  # type: ignore[assignment]

    with self.assertRaisesRegex(TimeoutError, "simulated response timeout"):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertEqual(transfer.command, command)
    self.assertEqual(transfer.error_type, "TimeoutError")
    self.assertIs(self.plate.parent, self.site)
    self.assertEqual(socket.stop_calls, 1)

    result = await self.driver.resolve_unresolved_transfer("source")

    self.assertIs(result, self.plate)
    self.assertEqual(socket.setup_calls, 1)
    self.assertIsNone(self.driver.unresolved_transfer)
    self.assertEqual(socket.written[-2:], ["platestatus", "neststatus"])

  async def test_cancelled_pick_records_unresolved_transfer(self):
    started = asyncio.Event()

    async def blocked_pick(stacker: int, slot: int, nest: int, close_door: bool = True):
      started.set()
      await asyncio.Event().wait()

    self.driver._pick = blocked_pick  # type: ignore[method-assign]
    task = asyncio.create_task(self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with self.assertRaises(asyncio.CancelledError):
      await task

    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertEqual(transfer.command, "pick 1 1 1")
    self.assertEqual(transfer.error_type, "CancelledError")
    self.assertIs(self.plate.parent, self.site)

  async def test_aborted_pick_records_unresolved_transfer(self):
    self.socket.captures["pick 1 1 1"] = [
      "ACK! pick 1 1 1 42",
      "ABORTED! pick 1 1 1 42",
    ]

    with self.assertRaises(HighResSampleStorageAbortedError):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertEqual(transfer.error_type, "HighResSampleStorageAbortedError")
    self.assertIs(self.plate.parent, self.site)

  async def test_unsafe_pick_records_unresolved_transfer_and_recovery_does_not_clear_it(self):
    self.socket.captures["pick 1 1 1"] = [
      "ACK! pick 1 1 1 42",
      "Error 1: 42: Z height is unsafe for rotation, check machine",
      "ERROR! pick 1 1 1 42",
    ]

    with self.assertRaises(HighResSampleStorageFault):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertEqual(transfer.error_type, "HighResSampleStorageFault")

    self.socket.captures["enable"] = ["ACK! enable 43", "OK! enable 43"]
    self.socket.captures["spatulaout"] = ["ACK! spatulaout 44", "OK! spatulaout 44"]
    self.socket.captures["home"] = ["ACK! home 45", "OK! home 45"]
    self.socket.captures["homedstatus"] = [
      "ACK! homedstatus 46",
      "homed",
      "OK! homedstatus 46",
    ]
    self.socket.captures["status"] = [
      "ACK! status 47",
      "Carousel: 0.0",
      "Y axis: 0.0",
      "Z axis: 0.0",
      "OK! status 47",
    ]

    self.assertTrue(await self.driver.recover())
    self.assertIs(self.driver.unresolved_transfer, transfer)

  async def test_resolve_unresolved_fetch_to_destination_uses_live_sensors(self):
    async def ambiguous_pick(stacker: int, slot: int, nest: int, close_door: bool = True):
      raise TimeoutError("completion was lost")

    self.driver._pick = ambiguous_pick  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    self._set_nest_status("PLATE_AVAILABLE")
    result = await self.driver.resolve_unresolved_transfer("destination")

    self.assertIs(result, self.plate)
    self.assertIs(self.plate.parent, self.driver.nests[0])
    self.assertIsNone(self.site.resource)
    self.assertIsNone(self.driver.unresolved_transfer)
    self.assertEqual(self.socket.written[-2:], ["platestatus", "neststatus"])

  async def test_reconcile_rejects_spatula_plate_and_can_mark_plate_unassigned(self):
    async def ambiguous_pick(stacker: int, slot: int, nest: int, close_door: bool = True):
      raise TimeoutError("completion was lost")

    self.driver._pick = ambiguous_pick  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    transfer = self.driver.unresolved_transfer
    self.socket.captures["platestatus"] = [
      "ACK! platestatus 9",
      "PLATE_AVAILABLE",
      "OK! platestatus 9",
    ]
    with self.assertRaisesRegex(RuntimeError, "spatula reports that it is holding a plate"):
      await self.driver.resolve_unresolved_transfer("unassigned")
    self.assertIs(self.driver.unresolved_transfer, transfer)

    self.socket.captures["platestatus"] = [
      "ACK! platestatus 10",
      "NO_PLATE",
      "OK! platestatus 10",
    ]
    result = await self.driver.resolve_unresolved_transfer("unassigned")

    self.assertIs(result, self.plate)
    self.assertIsNone(self.plate.parent)
    self.assertIsNone(self.driver.unresolved_transfer)

  async def test_failed_fetch_leaves_resource_in_site(self):
    self.socket.captures["pick 1 1 1"] = [
      "ACK! pick 1 1 1 42",
      "Error 1: 42: No plate detected",
      "ERROR! pick 1 1 1 42",
    ]
    self.socket.captures["homedstatus"] = [
      "ACK! homedstatus 43",
      "homed",
      "OK! homedstatus 43",
    ]

    with self.assertRaises(PlateNotFoundError):
      await self.driver.fetch_plate_to_loading_tray(self.plate, tray_index=0)

    self.assertIs(self.site.resource, self.plate)
    self.assertIsNone(self.driver.nests[0].resource)
    self.assertIsNone(self.driver.unresolved_transfer)

  async def test_failed_store_records_unresolved_transfer_and_blocks_another_move(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)
    self._set_nest_status("PLATE_AVAILABLE")
    self.socket.captures["place 1 1 1"] = [
      "ACK! place 1 1 1 44",
      "Error 1: 44: Place failed",
      "ERROR! place 1 1 1 44",
    ]
    self.socket.captures["homedstatus"] = [
      "ACK! homedstatus 45",
      "homed",
      "OK! homedstatus 45",
    ]

    with self.assertRaises(HighResSampleStorageError):
      await self.driver.store_plate(self.plate, self.site, tray_index=0)

    self.assertIs(self.driver.nests[0].resource, self.plate)
    self.assertIsNone(self.site.resource)
    transfer = self.driver.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertIs(transfer.plate, self.plate)
    self.assertIs(transfer.source, self.driver.nests[0])
    self.assertIs(transfer.destination, self.site)
    self.assertEqual(transfer.command, "place 1 1 1")
    self.assertEqual(transfer.error_type, "HighResSampleStorageError")

    restored = Resource.deserialize(json.loads(json.dumps(self.driver.serialize())))
    self.assertIsInstance(restored, HighResSampleStorage)
    assert isinstance(restored, HighResSampleStorage)
    restored_transfer = restored.unresolved_transfer
    self.assertIsNotNone(restored_transfer)
    assert restored_transfer is not None
    self.assertEqual(restored_transfer.plate.name, "plate")
    self.assertEqual(restored_transfer.source.name, "sample_store_nest_1")
    self.assertEqual(restored_transfer.destination.name, "site_1")
    self.assertEqual(restored_transfer.command, "place 1 1 1")

    written = list(self.socket.written)
    with self.assertRaisesRegex(RuntimeError, "Plate location is unresolved"):
      await self.driver.store_plate(self.plate, self.site, tray_index=0)
    with self.assertRaisesRegex(RuntimeError, "Plate location is unresolved"):
      await self.driver._send_command("nesttransfer 1 2")
    self.assertEqual(self.socket.written, written)

  async def test_resolve_unresolved_store_to_source_uses_live_nest_sensor(self):
    self.plate.unassign()
    self.driver.nests[0].assign_child_resource(self.plate)
    self._set_nest_status("PLATE_AVAILABLE")

    async def ambiguous_place(stacker: int, slot: int, nest: int, close_door: bool = True):
      raise TimeoutError("completion was lost")

    self.driver._place = ambiguous_place  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await self.driver.store_plate(self.plate, self.site, tray_index=0)

    result = await self.driver.resolve_unresolved_transfer("source")

    self.assertIs(result, self.plate)
    self.assertIs(self.plate.parent, self.driver.nests[0])
    self.assertIsNone(self.driver.unresolved_transfer)
    self.assertEqual(self.socket.written[-2:], ["platestatus", "neststatus"])


if __name__ == "__main__":
  unittest.main()
