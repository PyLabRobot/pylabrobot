from pylabrobot.liquid_handling.backends.opentrons_simulator import OpentronsOT2Simulator
from pylabrobot.resources import Coordinate
from pylabrobot.testing.concurrency import AnyioTestBase


class TestOpentronsSimulatorMoveChannel(AnyioTestBase):
  """The simulator overrides ``_current_channel_position``.

  Since the base backend's ``move_channel_{x,y,z}`` now ``await`` that helper (so the
  underlying ``save_position`` HTTP call can be offloaded off the event loop), the
  simulator's override must stay a coroutine too. These tests guard against a
  regression where the override is made synchronous again, which would raise
  ``TypeError: object tuple can't be used in 'await' expression``.
  """

  async def test_move_channel_x_updates_position(self):
    sim = OpentronsOT2Simulator()
    await sim.move_channel_x(channel=0, x=50.0)
    self.assertEqual(sim._positions["sim-left"], Coordinate(x=50.0, y=0.0, z=0.0))

  async def test_move_channel_y_updates_position(self):
    sim = OpentronsOT2Simulator()
    await sim.move_channel_y(channel=0, y=25.0)
    self.assertEqual(sim._positions["sim-left"], Coordinate(x=0.0, y=25.0, z=0.0))

  async def test_move_channel_z_updates_position(self):
    sim = OpentronsOT2Simulator()
    await sim.move_channel_z(channel=0, z=10.0)
    self.assertEqual(sim._positions["sim-left"], Coordinate(x=0.0, y=0.0, z=10.0))
