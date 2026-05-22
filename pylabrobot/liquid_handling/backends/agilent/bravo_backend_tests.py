"""Tests for the Agilent Bravo backend.

These exercise the backend against the in-process ``simulation`` control
core — no hardware. Motion accuracy on real hardware (the deck calibration
and clearance values) is validated separately on the bench.
"""

import unittest

from pylabrobot.liquid_handling.backends.agilent.bravo.controllers.simulation import (
  SimulationController,
)
from pylabrobot.liquid_handling.backends.agilent.bravo_backend import (
  AgilentBravoBackend,
  BravoDeckCalibration,
)
from pylabrobot.resources import Coordinate, Deck


def _backend(**kwargs) -> AgilentBravoBackend:
  backend = AgilentBravoBackend(controller_type="simulation", **kwargs)
  backend.set_deck(Deck(size_x=500, size_y=350, size_z=200))
  return backend


class AgilentBravoBackendTests(unittest.IsolatedAsyncioTestCase):
  """Backend behaviour against the simulation control core."""

  async def test_setup_and_stop(self):
    backend = _backend()
    self.assertFalse(backend.setup_finished)
    await backend.setup()
    self.assertTrue(backend.setup_finished)
    self.assertIsInstance(backend.controller, SimulationController)
    await backend.stop()
    self.assertFalse(backend.setup_finished)

  def test_num_channels(self):
    self.assertEqual(_backend().num_channels, 96)
    self.assertEqual(_backend(num_channels=384).num_channels, 384)

  def test_unknown_controller_type(self):
    with self.assertRaises(ValueError):
      AgilentBravoBackend(controller_type="not_a_bravo")

  def test_serialize(self):
    data = _backend(num_channels=384).serialize()
    self.assertEqual(data["type"], "AgilentBravoBackend")
    self.assertEqual(data["controller_type"], "simulation")
    self.assertEqual(data["num_channels"], 384)

  async def test_single_channel_operations_unsupported(self):
    """The Bravo head is fixed; per-channel operations are not available."""
    backend = _backend()
    with self.assertRaises(NotImplementedError):
      await backend.pick_up_tips([], [])
    with self.assertRaises(NotImplementedError):
      await backend.drop_tips([], [])
    with self.assertRaises(NotImplementedError):
      await backend.aspirate([], [])
    with self.assertRaises(NotImplementedError):
      await backend.dispense([], [])

  def test_can_pick_up_tip(self):
    self.assertTrue(_backend().can_pick_up_tip(0, None))  # type: ignore[arg-type]


class BravoDeckCalibrationTests(unittest.TestCase):
  """The deck→axis affine transform."""

  def test_identity_inverts_z(self):
    # Default: identity in X/Y, inverted Z (larger Bravo Z is physically lower).
    cal = BravoDeckCalibration()
    self.assertEqual(cal.to_axes(Coordinate(10.0, 20.0, 30.0)), (10.0, 20.0, -30.0))

  def test_offsets_and_signs(self):
    cal = BravoDeckCalibration(x_origin=100.0, y_origin=50.0, z_origin=5.0, x_sign=-1.0, z_sign=1.0)
    self.assertEqual(cal.to_axes(Coordinate(10.0, 20.0, 30.0)), (90.0, 70.0, 35.0))


if __name__ == "__main__":
  unittest.main()
