import contextlib
import dataclasses
import unittest
import unittest.mock
from typing import List, cast

import pylabrobot.hamilton.star.driver.simulator as simulator
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.simulator import (
  BARE_X_ARM,
  DEFAULT_STAR_CONFIGURATION,
  STARSimulationDriver,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton import STARDeck


class TestXArm(unittest.IsolatedAsyncioTestCase):
  """`STARDriver.x_arm` is the single-arm shorthand, and its job is which error it raises."""

  async def test_before_setup(self):
    with self.assertRaises(RuntimeError):
      STARSimulationDriver(deck=STARDeck()).x_arm

  async def test_one_arm(self):
    star = STARSimulationDriver(deck=STARDeck())
    await star.setup()
    self.assertIs(star.x_arm, star.left_x_arm)

  async def test_two_arms(self):
    both = dataclasses.replace(
      DEFAULT_STAR_CONFIGURATION,
      right_arm=BARE_X_ARM,
    )
    star = STARSimulationDriver(configuration=both, deck=STARDeck())
    await star.setup()
    with self.assertRaises(ValueError):
      star.x_arm

  async def test_no_arms(self):
    neither = dataclasses.replace(DEFAULT_STAR_CONFIGURATION, left_arm=None, right_arm=None)
    star = STARSimulationDriver(configuration=neither, deck=STARDeck())
    await star.setup()
    with self.assertRaises(ValueError):
      star.x_arm


class TestSimulation(unittest.IsolatedAsyncioTestCase):
  """A simulated machine has no firmware to ask, so the resource model is all it can answer from."""

  async def test_simulation_needs_a_deck(self):
    with self.assertRaises(ValueError):
      STARSimulationDriver()


# What each initialization step is called in the sequences below, and where it is defined. The simulated
# classes override some of them, so each is recorded where a simulated run would reach it.
MOVING_STEPS = [
  (simulator.STARSimulationDriver, "pre_initialize", "VI instrument"),
  (simulator.Pipettes, "move_to_safe_z", "ZA channels to safe Z"),
  (simulator.SimulatedPipettes, "initialize", "DI channels"),
  (simulator.SimulatedISWAP, "initialize", "FI iSWAP"),
  (simulator.iSWAP, "park", "iSWAP park"),
  (simulator._SimulatedHead, "initialize", "EI 96-head"),
  (simulator._SimulatedHead, "probe_z_max", "EV 96-head probe and retract"),
  (simulator.SimulatedAutoload, "initialize", "II autoload"),
  (simulator.SimulatedAutoload, "park", "autoload park"),
]


@contextlib.contextmanager
def recorded_moves():
  """Record every setup step that moves the machine, in the order setup runs them."""
  moves: List[str] = []
  with contextlib.ExitStack() as stack:
    for owner, name, label in MOVING_STEPS:
      real = owner.__dict__[name]

      def wrap(real=real, label=label):
        async def recorded(self, *args, **kwargs):
          moves.append(label)
          return await real(self, *args, **kwargs)

        return recorded

      stack.enter_context(unittest.mock.patch.object(owner, name, wrap()))
    yield moves


class TestSetupSequence(unittest.IsolatedAsyncioTestCase):
  """Setup moves the machine, and the order it moves it in is what keeps the arm's modules from
  driving into each other. It follows the legacy routine: the channels reach Z safety and the head
  retracts before the iSWAP moves on the shared left X-drive, and the head is only initialized once
  its own status has been asked. The 96-head retract runs on every setup, since that retract is
  what keeps it clear."""

  async def run_setup(self, instrument_up: bool, head_up: bool, eject_position: bool) -> List[str]:
    star = simulator.STARSimulationDriver(deck=STARDeck(), initialized=instrument_up)
    star.initialized["H0"] = head_up
    cast(Head96, star.head96).configuration.tip_discard_location = (
      Coordinate(-263.8, 108.3, 200.0) if eject_position else None
    )
    with recorded_moves() as moves:
      await star.setup()
    return list(moves)

  async def test_everything_already_up(self):
    self.assertEqual(
      await self.run_setup(instrument_up=True, head_up=True, eject_position=True),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "iSWAP park",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def test_head_down_on_an_instrument_that_is_up(self):
    self.assertEqual(
      await self.run_setup(instrument_up=True, head_up=False, eject_position=True),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "iSWAP park",
        "EI 96-head",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def test_head_down_with_nowhere_to_eject(self):
    """It is still retracted, because that is what keeps it clear of the iSWAP; it is just not
    initialized, since initializing throws off whatever is mounted and there is nowhere to drop it."""
    self.assertEqual(
      await self.run_setup(instrument_up=True, head_up=False, eject_position=False),
      [
        "ZA channels to safe Z",
        "EV 96-head probe and retract",
        "iSWAP park",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )

  async def test_instrument_not_up(self):
    """The instrument procedure homes every drive, so nothing is raised beforehand."""
    self.assertEqual(
      await self.run_setup(instrument_up=False, head_up=False, eject_position=True),
      [
        "VI instrument",
        "DI channels",
        "FI iSWAP",
        "iSWAP park",
        "EI 96-head",
        "EV 96-head probe and retract",
        "II autoload",
        "autoload park",
      ],
    )
