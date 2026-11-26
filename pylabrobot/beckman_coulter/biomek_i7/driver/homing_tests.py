"""Home All against the generic instrument simulated: the requests each board is sent are the ones
Biomek's Master Controller sent (the cold start the generic recording was made from; a forced Home
All of a homed instrument, as its logs show it), and where each axis ends."""

import asyncio
import unittest
from typing import Dict, List

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import SimulatedAceUSB
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_driver,
  generic_simulated_boards,
)

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


if __name__ == "__main__":
  unittest.main()
