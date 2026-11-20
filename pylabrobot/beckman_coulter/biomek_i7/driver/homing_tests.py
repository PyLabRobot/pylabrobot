"""Home All against the generic instrument simulated: the requests each board is sent are the ones
Biomek's Master Controller sent (the cold start the generic recording was made from; a forced Home
All of a homed instrument, as its logs show it), and where each axis ends."""

import asyncio
import struct
import unittest
from typing import Dict, List

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.recipes.homing import HOME_RECIPES, Homer
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import SimulatedAceUSB
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_driver,
  generic_recording,
  generic_simulated_boards,
)

SPAN8_Z = [f"Z{i}" for i in range(1, 9)]
# Reads the driver sends that Biomek's logs of a Home All leave out: the reads Biomek never
# sends (Get State, Get Interlock Status), and Read Analog/Digital Input (the drive supply and door
# checks), which Biomek polls in the background.
NOT_IN_RECORDINGS = set(p.FIRMWARE_READS) | {
  (p.Device.ANALOG_INPUT, 0x10),
  (p.Device.DIGITAL_INPUT, 0x10),
}
MASTER_LOGGER = "pylabrobot.beckman_coulter.biomek_i7.driver.master"
READ_ONLY_SETUP = dict(skip_device_initialization=True, skip_left_x_arm=True, skip_right_x_arm=True)
READS = ("Get ", "Read ", "List ")

# The Multichannel board's requests in a forced Home All of a homed instrument (`HM`), as
# Biomek's logs show it (2026-10-23): command, axis, and a move's target or a Move's timeout.
FORCED_HM_MULTICHANNEL = [
  ("Set State", "Z", None),
  ("Set Move Parameters", "Z", -1000),
  ("Move", "Z", 30000),
  ("Home", "Z", None),
  ("Set State", "GZ", None),
  ("Set Move Parameters", "GZ", 0),
  ("Move", "GZ", 30000),
  ("Set State", "GG", None),
  ("Set Move Parameters", "GG", 0),
  ("Move", "GG", 30000),
  ("Home", "GG", None),
  ("Set State", "GG", None),
  ("Set Move Parameters", "GG", 35753),
  ("Move", "GG", 30000),
  ("Set State", "GR", None),
  ("Set Move Parameters", "GR", 0),
  ("Move", "GR", 30000),
  ("Home", "GR", None),
  ("Set State", "GR", None),
  ("Set Move Parameters", "GR", -212581),
  ("Move", "GR", 30000),
  ("Set State", "GR", None),
  ("Set Move Parameters", "GR", -174956),
  ("Move", "GR", 30000),
  ("Set State", "GY", None),
  ("Set Move Parameters", "GY", 0),
  ("Move", "GY", 30000),
  ("Home", "GY", None),
  ("Set State", "GZ", None),
  ("Set Move Parameters", "GZ", 0),
  ("Move", "GZ", 30000),
  ("Home", "GZ", None),
  ("Set State", "GY", None),
  ("Set Move Parameters", "GY", 47574),
  ("Move", "GY", 30000),
  ("Set State", "Y", None),
  ("Set Move Parameters", "Y", 0),
  ("Move", "Y", 30000),
  ("Home", "Y", None),
  ("Set State", "Y", None),
  ("Set Move Parameters", "Y", 0),
  ("Move", "Y", 30000),
  ("Set State", "GR", None),
  ("Set Move Parameters", "GR", -141093),
  ("Move", "GR", 30000),
  ("Set State", "X", None),
  ("Set Move Parameters", "X", 0),
  ("Move", "X", 30000),
  ("Home", "X", None),
  ("Set State", "X", None),
  ("Set Move Parameters", "X", 35840),
  ("Move", "X", 30000),
]


def zero_sequence(message: bytes) -> str:
  return (message[:3] + b"\x00" + message[4:]).hex()


def opcode_names(messages: List[str]) -> List[str]:
  return [p.COMMANDS[(bytes.fromhex(h)[0], bytes.fromhex(h)[4])] for h in messages]


class SimulatedRunTest(unittest.IsolatedAsyncioTestCase):
  """What each simulated board was sent since a mark."""

  marks: Dict[str, int]

  async def asyncSetUp(self) -> None:
    # IsolatedAsyncioTestCase runs its loop in debug mode, which records a stack for every
    # callback and future; a simulated Home All makes thousands, and takes 3 s instead of 0.03 s.
    # Nothing here is timed. The board-level tests keep debug mode's checks.
    asyncio.get_running_loop().set_debug(False)

  async def start(self, homed, configured=None, configuration=None) -> BiomekI7Driver:
    """A driver on the generic instrument simulated, read but not brought up; `configuration`, a
    declared configuration to use instead of the generic one (sealed)."""
    driver = generic_driver(
      generic_simulated_boards(homed=homed, configured=configured), configuration=configuration
    )
    await driver.setup(**READ_ONLY_SETUP)
    self.addAsyncCleanup(driver.stop)
    self.marks = {}
    self.mark(driver)
    return driver

  def usb(self, driver: BiomekI7Driver, module: str) -> SimulatedAceUSB:
    for board in driver.boards:
      if board.name == f"simulated {module}":
        assert isinstance(board.io, SimulatedAceUSB)
        return board.io
    raise KeyError(module)

  def mark(self, driver: BiomekI7Driver) -> None:
    for module in ("chassis", "multichannel", "span8"):
      self.marks[module] = len(self.usb(driver, module).requests)

  def since_mark(self, driver: BiomekI7Driver, module: str) -> List[str]:
    """What a board was sent since the mark, without the reads Biomek's recordings leave out."""
    requests = self.usb(driver, module).requests[self.marks[module] :]
    return [zero_sequence(m) for m in requests if (m[0], m[4]) not in NOT_IN_RECORDINGS]


class HomeAllTests(SimulatedRunTest):
  async def test_a_cold_home_all_sends_what_the_recorded_cold_start_sent(self) -> None:
    """From power-up (nothing configured or homed), setup configures and homes; every request each
    pod's board is sent that is not a read is one the Master Controller sent at the cold start the
    generic recording was made from, first sent in the same order."""
    driver = generic_driver(generic_simulated_boards(homed=False, configured=False))
    self.addAsyncCleanup(driver.stop)
    await driver.setup()
    recording = generic_recording()["boards"]
    for module in ("multichannel", "span8"):
      sent: List[str] = []
      for m in self.usb(driver, module).requests:
        key = (m[:3] + m[4:]).hex()
        if not p.COMMANDS[(m[0], m[4])].startswith(READS) and key not in sent:
          sent.append(key)
      recorded = [
        key
        for key, _ in recording[module]
        if not p.COMMANDS[(int(key[0:2], 16), int(key[6:8], 16))].startswith(READS)
      ]
      self.assertGreater(len(sent), 40, module)
      self.assertEqual(sent, recorded[: len(sent)], module)

  async def test_axes_end_where_biomek_leaves_them(self) -> None:
    driver = await self.start(False)
    await driver.configure()
    await driver.home()
    assert driver.left_x_arm and driver.right_x_arm
    left = await driver.left_x_arm.request_positions()
    self.assertEqual(
      (left["X"], left["GY"], left["GG"], left["GR"]), (35840, 47574, 35753, -141093)
    )
    right = await driver.right_x_arm.request_positions()
    self.assertEqual((right["X"], right["Z1"], right["Z8"]), (-2560, 9647, 9647))
    for pod in driver.arms:
      for axis in pod.axes:
        if not axis.is_pump and axis.name != "D":  # the plunger and pumps are homed apart (`HD`)
          self.assertEqual(await pod.request_homed_state(axis.name), "homed", (pod.side, axis.name))


class InitializeTests(SimulatedRunTest):
  """`initialize`, which `setup` runs after discovery: homing forced or not, and refusals that come
  before anything is written."""

  async def start_initialized(self, homed, configured=None, **flags) -> BiomekI7Driver:
    driver = await self.start(homed, configured)
    await driver.initialize(**flags)
    return driver

  def written(self, driver: BiomekI7Driver) -> List[str]:
    return [
      name
      for module in ("chassis", "multichannel", "span8")
      for name in opcode_names(self.since_mark(driver, module))
      if not name.startswith(("Get ", "Read ", "List "))
    ]

  async def test_forced_on_a_homed_instrument_is_biomeks_home_all(self) -> None:
    driver = await self.start_initialized(True, force_initialize=True)
    assert driver.left_x_arm is not None
    names = {(a.device, a.index): a.name for a in driver.left_x_arm.axes}
    sent = [bytes.fromhex(h) for h in self.since_mark(driver, "multichannel")]
    decoded = []
    for m in sent:
      name = p.COMMANDS[(m[0], m[4])]
      value = None
      if name == "Set Move Parameters":
        value = struct.unpack_from("<i", m, 5 + 16)[0]
      elif name in ("Move", "Move All"):
        value = struct.unpack_from("<I", m, 5)[0]
      decoded.append((name, names.get((m[0], m[1]), ""), value))
    # After the re-arm's enables and reads: the recipe's requests.
    start = len(decoded) - len(FORCED_HM_MULTICHANNEL)
    self.assertEqual(decoded[start:], FORCED_HM_MULTICHANNEL)
    self.assertNotIn("Home", [d[0] for d in decoded[:start]])

  async def test_a_homed_instrument_is_not_homed_again_by_default(self) -> None:
    forced = await self.start_initialized(True, force_initialize=True)
    forced_sent = {m: self.since_mark(forced, m) for m in ("multichannel", "span8")}
    driver = await self.start_initialized(True)
    names = self.written(driver)
    self.assertNotIn("Configure", names)
    self.assertNotIn("Home", names)
    for module in ("multichannel", "span8"):
      sent = self.since_mark(driver, module)
      # Every move is one Biomek makes, in Biomek's order: the default only leaves homes out.
      moves = [h for h in sent if bytes.fromhex(h)[4] == 0x40]
      forced_moves = [h for h in forced_sent[module] if bytes.fromhex(h)[4] == 0x40]
      it = iter(forced_moves)
      self.assertTrue(all(m in it for m in moves), module)
    assert driver.left_x_arm and driver.right_x_arm
    left = await driver.left_x_arm.request_positions()
    self.assertEqual(
      (left["X"], left["GY"], left["GG"], left["GR"], left["Z"]),
      (35840, 47574, 35753, -141093, -1000),
    )
    right = await driver.right_x_arm.request_positions()
    self.assertEqual((right["X"], right["Z1"], right["Z8"]), (-2560, 9647, 9647))

  def moves(self, driver: BiomekI7Driver) -> List[str]:
    """Every Set Move Parameters, Move, Move All and Home sent since the mark."""
    motion = {"Set Move Parameters", "Move", "Move All", "Home"}
    return [
      name
      for module in ("multichannel", "span8")
      for name in opcode_names(self.since_mark(driver, module))
      if name in motion
    ]

  async def test_resume_moves_an_axis_pushed_by_hand_back(self) -> None:
    driver = await self.start_initialized(True)
    left = self.usb(driver, "multichannel")
    await driver.pause()
    x = left.axis_model("X")
    paused_at = x.position
    x.position += 2000  # pushed by hand while the drives had no power
    await driver.resume()
    self.assertEqual(x.position, paused_at)

  async def test_no_pod_moves_beside_a_skipped_pod_whose_x_is_not_homed(self) -> None:
    for flags in (
      dict(skip_right_x_arm=True),
      dict(skip_device_initialization=True),  # the right pod is left out, unconfigured
    ):
      with self.subTest(**flags):
        driver = await self.start(
          {"multichannel": ["X"]}, configured={"multichannel": True, "span8": False}
        )
        with self.assertRaisesRegex(RuntimeError, "shared X rail"):
          await driver.initialize(**flags)
        self.assertEqual(self.written(driver), [])

  async def test_a_skipped_pod_with_x_homed_is_left_alone(self) -> None:
    driver = await self.start_initialized(True, skip_right_x_arm=True)
    self.assertEqual(
      [n for n in opcode_names(self.since_mark(driver, "span8")) if not n.startswith("Get ")], []
    )
    self.assertIn("Move", opcode_names(self.since_mark(driver, "multichannel")))

  async def test_setup_brings_the_instrument_up(self) -> None:
    driver = generic_driver(generic_simulated_boards())
    self.addAsyncCleanup(driver.stop)
    await driver.setup()
    assert driver.right_x_arm
    self.assertEqual(await driver.right_x_arm.request_homed_state("Y4"), "homed")
    await driver.setup()  # repeatable: an instrument that is up is left up
    self.assertEqual(await driver.right_x_arm.request_homed_state("Y4"), "homed")


class RecipeTests(unittest.TestCase):
  def setUp(self) -> None:
    async def discover() -> list:
      driver = generic_driver()
      await driver.discover()
      await driver.stop()
      return list(driver.arms)

    self.pods = {pod.module_type: pod for pod in asyncio.run(discover())}

  def test_every_target_is_within_the_axis_limits(self) -> None:
    for module, steps in HOME_RECIPES.items():
      pod = self.pods[module]
      homer = Homer(pod)
      for step in steps:
        for name, target in homer.targets(step):
          pod.axis(name).check_position(target)

  def test_post_home_targets_are_the_recorded_home_positions(self) -> None:
    """Where a recipe leaves X, the left Y and the gripper axes is the Master Controller's `phome`
    for them, as the motor recording carries it."""
    for module, steps in HOME_RECIPES.items():
      pod = self.pods[module]
      homer = Homer(pod)
      last: Dict[str, int] = {}
      for step in steps:
        for name, target in homer.targets(step):
          last[name] = target
      for name in ("X", "GY", "GG", "GR") + (("Y",) if module == "multichannel" else ()):
        self.assertEqual(last[name], pod.axis(name).home_position, (module, name))

  def test_the_gripper_turns_from_its_home_position(self) -> None:
    # Home, then 190 and 90 degrees from the home position, then the home position: the counts
    # Biomek's homes sent.
    for module, pod in self.pods.items():
      homer = Homer(pod)
      turns = [t for step in HOME_RECIPES[module] for n, t in homer.targets(step) if n == "GR"]
      self.assertEqual(turns, [0, -212581, -174956, -141093], module)


if __name__ == "__main__":
  unittest.main()
