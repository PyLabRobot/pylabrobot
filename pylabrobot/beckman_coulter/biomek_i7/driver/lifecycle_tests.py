"""Configure, the interlock, faults, pause / resume and stop, against the generic instrument
simulated: what the driver sends, and how it handles what the boards answer. Nothing here moves an
axis."""

import asyncio
import unittest
from typing import Any, Dict, List

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import AceError
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import SimulatedAceUSB
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_driver,
  generic_recording,
  generic_simulated_boards,
)

MASTER_LOGGER = "pylabrobot.beckman_coulter.biomek_i7.driver.master"
MODULES = ("chassis", "multichannel", "span8")
READ_ONLY_SETUP = dict(skip_device_initialization=True, skip_left_x_arm=True, skip_right_x_arm=True)


def zero_sequence(message: bytes) -> str:
  return (message[:3] + b"\x00" + message[4:]).hex()


def name_of(message: bytes) -> str:
  return p.COMMANDS[(message[0], message[4])]


class SimulatedTest(unittest.IsolatedAsyncioTestCase):
  """A driver on the generic instrument simulated, and what each board was sent since a mark."""

  marks: Dict[str, int]

  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)  # the test loop's debug mode makes runs slow

  async def start(self, homed: Any = True, configured: Any = None) -> BiomekI7Driver:
    driver = generic_driver(generic_simulated_boards(homed=homed, configured=configured))
    await driver.setup(**READ_ONLY_SETUP)
    self.addAsyncCleanup(driver.stop)
    self.marks = {}
    self.mark(driver)
    return driver

  async def start_initialized(self, homed: Any = True, configured: Any = None, **flags: Any):  # type: ignore[no-untyped-def]
    driver = await self.start(homed, configured)
    await driver.initialize(**flags)
    return driver

  def usb(self, driver: BiomekI7Driver, module: str) -> SimulatedAceUSB:
    for board in driver.boards:
      if board.name == f"simulated {module}":
        assert isinstance(board.io, SimulatedAceUSB)
        return board.io
    raise KeyError(module)

  def mark(self, driver: BiomekI7Driver) -> None:
    for module in MODULES:
      self.marks[module] = len(self.usb(driver, module).requests)

  def sent(self, driver: BiomekI7Driver, module: str) -> List[bytes]:
    return self.usb(driver, module).requests[self.marks[module] :]

  def names(self, driver: BiomekI7Driver, module: str) -> List[str]:
    return [name_of(m) for m in self.sent(driver, module)]

  def written(self, driver: BiomekI7Driver) -> List[str]:
    """Every request since the mark that is not a read."""
    return [
      name
      for module in MODULES
      for name in self.names(driver, module)
      if not name.startswith(("Get ", "Read ", "List "))
    ]

  async def settle(self) -> None:
    """Let the boards' read loops and the driver's background writes run."""
    for _ in range(5):
      await asyncio.sleep(0.02)


class SimulatorRuleTests(SimulatedTest):
  """The simulated boards answer as the instrument's did."""

  async def test_an_axis_is_configured_before_it_takes_a_state(self) -> None:
    driver = await self.start(homed=False, configured=False)
    pod = driver.left_x_arm
    assert pod is not None
    with self.assertRaisesRegex(AceError, "not configured"):
      await pod._unchecked_fw_enable(["Y"])
    await pod._unchecked_fw_configure(pod.axis("Y"))
    self.assertEqual((await pod.request_axis_states())["Y"], "disabled")
    self.assertEqual(await pod.request_homed_state("Y"), "not_homed")

  async def test_at_rest_cia402_drives_are_in_fault_and_refuse_enabling(self) -> None:
    driver = await self.start()
    left, right = driver.left_x_arm, driver.right_x_arm
    assert left is not None and right is not None
    self.assertEqual(set((await left.request_axis_states()).values()), {"fault"})
    self.assertEqual((await right.request_axis_states())["Z1"], "enabled")  # a SAGIAN drive
    with self.assertRaisesRegex(AceError, "interlock tripped"):
      await left._unchecked_fw_enable(["X"])
    self.assertLess(await left.request_drive_supply(), 1.0)

  async def test_armed_the_supply_comes_up_and_enabling_resets_a_fault(self) -> None:
    driver = await self.start()
    left = driver.left_x_arm
    assert left is not None and driver.chassis is not None
    await driver.chassis.arm_interlock()
    self.assertGreater(await left.request_drive_supply(), 30.0)
    await left._unchecked_fw_enable(["X"])
    self.assertEqual((await left.request_axis_states())["X"], "enabled")
    self.usb(driver, "multichannel").axis_model("Y").stuck_fault = True
    with self.assertRaises(AceError) as caught:
      await left._unchecked_fw_enable(["Y"])
    self.assertEqual(caught.exception.code, 0x3D)


class InitializeTests(SimulatedTest):
  async def test_only_a_board_that_lost_its_tables_is_configured(self) -> None:
    driver = await self.start_initialized(
      homed={"multichannel": ["X"]}, configured={"multichannel": True, "span8": False}
    )
    self.assertNotIn("Configure", self.names(driver, "multichannel"))
    assert driver.right_x_arm is not None
    self.assertEqual(self.names(driver, "span8").count("Configure"), len(driver.right_x_arm.axes))
    self.assertEqual(
      self.names(driver, "chassis")[:3],
      ["Trip Interlock", "Notify On Interlock Trip", "Set Status Display"],
    )
    states = await driver.request_axis_states()
    self.assertEqual({s for pod in states.values() for s in pod.values()}, {"enabled"})

  async def test_configure_sends_what_the_recorded_cold_start_sent(self) -> None:
    """Each pod's Configure requests, and the chassis's interlock requests before them and the
    I/O box write after, are the ones the Master Controller sent at the cold start the generic
    recording comes from, in its order."""
    driver = await self.start(homed=False, configured=False)
    await driver.configure()
    recording = generic_recording()["boards"]
    for module in ("multichannel", "span8"):
      recorded = [
        key for key, _ in recording[module] if key[6:8] == "10" and key[:2] in ("11", "12")
      ]
      sent = [(m[:3] + m[4:]).hex() for m in self.sent(driver, module) if name_of(m) == "Configure"]
      self.assertEqual(sent, recorded, module)
      self.assertEqual(self.names(driver, module)[-1], "Stop Module Execution")
    chassis = [(m[:3] + m[4:]).hex() for m in self.sent(driver, "chassis")]
    recorded_keys = {key for key, _ in recording["chassis"]}
    self.assertEqual(
      self.names(driver, "chassis"),
      ["Trip Interlock", "Notify On Interlock Trip", "Set Status Display", "Send Digital I/O Box"],
    )
    self.assertTrue(set(chassis) <= recorded_keys, set(chassis) - recorded_keys)

  async def test_drives_at_rest_are_in_fault_and_enabling_clears_it(self) -> None:
    driver = await self.start()
    with self.assertLogs(MASTER_LOGGER, "INFO") as logs:
      await driver.initialize()
    self.assertTrue(any("as at rest" in line for line in logs.output))
    self.assertFalse(any("collision" in line for line in logs.output))
    self.assertNotIn("Configure", self.written(driver))
    states = await driver.request_axis_states()
    self.assertEqual({s for pod in states.values() for s in pod.values()}, {"enabled"})
    self.assertFalse(driver.interlock_tripped.is_set())

  async def test_enabling_follows_the_master_controllers_order(self) -> None:
    driver = await self.start()
    await driver.arm_interlock()
    left = [m for m in self.sent(driver, "multichannel") if name_of(m) == "Set State"]
    assert driver.left_x_arm is not None
    names = {(a.device, a.index): a.name for a in driver.left_x_arm.axes}
    self.assertEqual(
      [names[(m[0], m[1])] for m in left], ["Z", "GZ", "GR", "GY", "GG", "Z", "D", "X", "Y"]
    )

  async def test_a_fault_under_power_is_reset_with_a_warning(self) -> None:
    driver = await self.start()
    usb = self.usb(driver, "multichannel")
    usb.interlock.armed = True  # left armed, by an earlier session
    for model in usb.axes.values():
      model.state = p.AxisState.ENABLED
    usb.axis_model("Z").state = p.AxisState.FAULT
    with self.assertLogs(MASTER_LOGGER, "WARNING") as logs:
      await driver.initialize()
    self.assertTrue(any(": Z in fault" in line and "collision" in line for line in logs.output))
    self.assertEqual(usb.axis_model("Z").state, p.AxisState.ENABLED)

  async def test_a_sagian_fault_is_warned_about_even_at_rest(self) -> None:
    driver = await self.start()
    self.usb(driver, "span8").axis_model("Z3").state = p.AxisState.FAULT
    with self.assertLogs(MASTER_LOGGER, "WARNING") as logs:
      await driver.initialize()
    self.assertTrue(any("Z3" in line and "collision" in line for line in logs.output))

  async def test_a_fault_that_does_not_clear_returns_the_instrument_to_rest(self) -> None:
    driver = await self.start()
    self.usb(driver, "multichannel").axis_model("Z").stuck_fault = True
    with self.assertRaises(AceError):
      await driver.initialize()
    chassis = self.sent(driver, "chassis")
    self.assertEqual([name_of(m) for m in chassis[-2:]], ["Trip Interlock", "Set Status Display"])
    self.assertEqual(
      zero_sequence(chassis[-1]), zero_sequence(p.set_status_display("error").encode(1))
    )
    self.assertFalse(self.usb(driver, "chassis").interlock.armed)
    self.assertTrue(driver.interlock_tripped.is_set())

  async def test_a_drive_supply_that_stays_off_stops_it_before_enabling(self) -> None:
    driver = await self.start()
    self.usb(driver, "chassis").interlock.supply_comes_up = False
    with self.assertRaisesRegex(RuntimeError, "drive supply"):
      await driver.initialize()
    self.assertNotIn("Set State", self.written(driver))
    self.assertFalse(self.usb(driver, "chassis").interlock.armed)

  async def test_an_open_door_does_not_stop_it(self) -> None:
    driver = await self.start()
    chassis_usb = self.usb(driver, "chassis")
    chassis_usb.interlock.door_open = True
    assert driver.chassis and driver.front_cover
    self.assertEqual(await driver.front_cover.request_state(), "open")
    await driver.initialize()  # Biomek arms with the door open
    self.assertEqual(await driver.chassis.request_interlock_state(), "armed")

  async def test_a_broken_light_curtain_stops_it(self) -> None:
    driver = await self.start()
    chassis_usb = self.usb(driver, "chassis")
    chassis_usb.interlock.light_curtain_broken = True
    with self.assertRaisesRegex(RuntimeError, "light curtain"):
      await driver.initialize()
    self.assertFalse(chassis_usb.interlock.armed)

  async def test_door_changes_are_reported_and_the_light_curtain_trips_the_interlock(self) -> None:
    driver = await self.start_initialized()
    assert driver.chassis and driver.front_cover
    await driver.front_cover.notify_changes()
    seen: List[str] = []
    driver.front_cover.subscribe(seen.append)
    chassis_usb = self.usb(driver, "chassis")
    chassis_usb.set_door(True)
    await self.settle()
    self.assertEqual(seen, ["open"])
    self.assertEqual(await driver.chassis.request_interlock_state(), "armed")
    chassis_usb.set_door(False)
    await self.settle()
    self.assertEqual(seen, ["open", "closed"])
    with self.assertLogs(MASTER_LOGGER, "ERROR") as logs:
      chassis_usb.set_light_curtain(True)
      await self.settle()
    self.assertIn("light curtain violation", "\n".join(logs.output))
    self.assertEqual(await driver.chassis.request_interlock_state(), "tripped")
    self.assertTrue(driver.interlock_tripped.is_set())
    self.assertEqual(
      zero_sequence(self.sent(driver, "chassis")[-2]),
      zero_sequence(p.set_status_display("error").encode(1)),
    )

  async def test_skipping_configuration_leaves_unconfigured_pods_alone(self) -> None:
    driver = await self.start(homed=False, configured=False)
    await driver.initialize(skip_device_initialization=True)
    self.assertEqual(self.written(driver), [])

  async def test_a_skipped_pod_is_left_alone(self) -> None:
    driver = await self.start_initialized(skip_right_x_arm=True)
    self.assertEqual([n for n in self.names(driver, "span8") if not n.startswith("Get ")], [])
    self.assertIn("Set State", self.names(driver, "multichannel"))

  async def test_setup_brings_the_instrument_up_and_is_repeatable(self) -> None:
    driver = generic_driver(generic_simulated_boards(homed=False))
    self.addAsyncCleanup(driver.stop)
    await driver.setup()
    states = await driver.request_axis_states()
    self.assertEqual({s for pod in states.values() for s in pod.values()}, {"enabled"})
    await driver.setup()  # configured already: not configured again
    states = await driver.request_axis_states()
    self.assertEqual({s for pod in states.values() for s in pod.values()}, {"enabled"})


class PauseTests(SimulatedTest):
  async def test_pause_trips_and_shows_paused_and_resume_re_arms(self) -> None:
    driver = await self.start_initialized()
    chassis_usb = self.usb(driver, "chassis")
    self.mark(driver)
    await driver.pause()
    self.assertTrue(driver.paused)
    self.assertFalse(chassis_usb.interlock.armed)
    self.assertTrue(driver.interlock_tripped.is_set())
    self.assertEqual(
      [zero_sequence(m) for m in self.sent(driver, "chassis")],
      [
        zero_sequence(p.trip_interlock().encode(1)),
        zero_sequence(p.set_status_display("paused").encode(1)),
      ],
    )
    with self.assertRaisesRegex(RuntimeError, "already paused"):
      await driver.pause()
    await driver.resume()
    self.assertFalse(driver.paused)
    self.assertTrue(chassis_usb.interlock.armed)
    self.assertFalse(driver.interlock_tripped.is_set())
    states = await driver.request_axis_states()
    self.assertEqual({s for pod in states.values() for s in pod.values()}, {"enabled"})

  async def test_a_pause_is_not_an_error(self) -> None:
    driver = await self.start_initialized()
    with self.assertNoLogs(MASTER_LOGGER, "ERROR"):
      await driver.pause()
      await self.settle()

  async def test_a_broken_light_curtain_keeps_it_paused(self) -> None:
    driver = await self.start_initialized()
    chassis_usb = self.usb(driver, "chassis")
    await driver.pause()
    chassis_usb.set_light_curtain(True)
    with self.assertRaisesRegex(RuntimeError, "light curtain"):
      await driver.resume()
    self.assertTrue(driver.paused)
    self.assertFalse(chassis_usb.interlock.armed)
    chassis_usb.set_light_curtain(False)
    await driver.resume()
    self.assertFalse(driver.paused)
    self.assertTrue(chassis_usb.interlock.armed)

  async def test_pause_needs_an_armed_interlock(self) -> None:
    driver = await self.start()
    with self.assertRaisesRegex(RuntimeError, "not armed"):
      await driver.pause()
    with self.assertRaisesRegex(RuntimeError, "not paused"):
      await driver.resume()


class FaultAndStopTests(SimulatedTest):
  async def test_a_motor_failing_on_its_own_trips_the_interlock(self) -> None:
    driver = await self.start_initialized()
    assert driver.left_x_arm is not None
    x = driver.left_x_arm.axis("X")
    self.mark(driver)
    with self.assertLogs(MASTER_LOGGER, "ERROR") as logs:
      # A Move All's NOTICE for X with a following error, sequence 0: nobody asked.
      self.usb(driver, "multichannel")._queue(bytes([x.device, x.index, 0, 0, 0x50, 0xFE, 0x3C]))
      await self.settle()
    self.assertIn("The left X motor failed", "\n".join(logs.output))
    self.assertEqual(self.names(driver, "chassis"), ["Trip Interlock", "Set Status Display"])
    self.assertEqual(self.sent(driver, "chassis")[1][5:], p.set_status_display("error").params)
    self.assertTrue(driver.interlock_tripped.is_set())

  async def test_a_trip_the_driver_made_is_not_an_error(self) -> None:
    driver = await self.start_initialized()
    self.mark(driver)
    with self.assertNoLogs(MASTER_LOGGER, "ERROR"):
      await driver.stop()
      # The chassis's own trip event, after the driver's trip.
      self.usb(driver, "chassis")._queue(bytes([p.Device.INTERLOCK, 1, 0, 0, 0x10, 0x03, 0, 0xFF]))
      await self.settle()

  async def test_stop_leaves_the_instrument_at_rest(self) -> None:
    driver = await self.start()
    await driver.stop()  # never armed: nothing is sent
    self.assertEqual(self.written(driver), [])
    driver = await self.start_initialized()
    self.mark(driver)
    await driver.stop()
    for module in ("multichannel", "span8"):
      self.assertEqual(self.names(driver, module), ["Stop Module Execution"])
    self.assertEqual(
      [zero_sequence(m) for m in self.sent(driver, "chassis")],
      [
        zero_sequence(p.trip_interlock().encode(1)),
        zero_sequence(p.set_status_display("ready").encode(1)),
      ],
    )
    self.assertFalse(self.usb(driver, "chassis").interlock.armed)


if __name__ == "__main__":
  unittest.main()
