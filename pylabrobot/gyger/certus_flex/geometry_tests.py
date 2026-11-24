"""Plate transforms, physical envelope checks, and pre-motion validation."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.resources import Coordinate, cor_96_wellplate_360uL_Fb

from ._inco_udp import IncoUDP
from .device import CertusFlex, CertusFlexError, ValveCalibration
from .geometry import MotionBounds, PlateRegistration


def registration() -> PlateRegistration:
  """Build the registration observed on C196069 with a conservative test envelope."""
  return PlateRegistration(
    cor_96_wellplate_360uL_Fb("plate"),
    first_well=(14.38, -11.24),
    valve_offsets=tuple((34, 41.2 + 9 * n, 21) for n in range(8)),
    height=14.4,
    clearance=65.5,
    bounds=MotionBounds((-63, 20, 35), (80, 50, 66)),
    serial_number="C196069",
    head="flex8_angled",
  )


class GeometryTests(unittest.TestCase):
  """Anchor coordinates to observed hardware moves, not just inverse calculations."""

  def test_observed_wet_positions(self) -> None:
    reg = registration()
    for well, channel, expected in (
      ("B2", 1, (57.38, 29.96, 35.4)),
      ("C3", 2, (66.38, 29.96, 35.4)),
    ):
      for a, b in zip(reg.target(reg.plate.get_item(well), channel), expected):
        self.assertAlmostEqual(a, b)

  def test_foreign_well_channel_and_travel(self) -> None:
    reg = registration()
    for well, channel in (
      (reg.plate.get_item("A12"), 0),
      (reg.plate.get_item("A1"), True),
      (cor_96_wellplate_360uL_Fb("other").get_item("A1"), 0),
    ):
      with self.assertRaises(ValueError):
        reg.target(well, channel)

  def test_changed_geometry_is_rejected(self) -> None:
    reg = registration()
    well = reg.plate.get_item("B2")
    well.location = Coordinate(1, 2, 3)
    with self.assertRaisesRegex(ValueError, "geometry changed"):
      reg.target(well, 1)

  def test_invalid_bounds(self) -> None:
    with self.assertRaises(ValueError):
      MotionBounds((0, 0, 0), (1, float("nan"), 1))


class PlateOperationTests(unittest.IsolatedAsyncioTestCase):
  """Assert well validation happens before movement and trajectories use clearance."""

  def setUp(self) -> None:
    self.reg = registration()
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.device.serial_number = "C196069"
    self.inco = MagicMock(spec=IncoUDP)
    self.device._inco = self.inco
    self.inco.get_number.return_value = 4.0
    move_patch = patch.object(self.device, "_move", new_callable=AsyncMock)
    self.move: AsyncMock = move_patch.start()
    self.addCleanup(move_patch.stop)
    position_patch = patch.object(
      self.device,
      "get_position",
      new_callable=AsyncMock,
      side_effect=[(-62, 30, 65.5), (57.38, 29.96, 35.4)],
    )
    position_patch.start()
    self.addCleanup(position_patch.stop)

  async def test_clearance_path(self) -> None:
    await self.device.move_to_well(self.reg, self.reg.plate.get_item("B2"), channel=1)
    moves = self.move.call_args_list
    self.assertEqual(moves[0].args, ("MoveXYZ", -62, 30, 65.5, "Slow"))
    self.assertEqual(moves[1].args[0], "MoveXYZ")
    self.assertEqual(moves[1].args[3], 65.5)
    self.assertAlmostEqual(moves[2].args[3], 35.4)

  async def test_wrong_head_and_capacity_send_no_motion(self) -> None:
    self.inco.get_number.return_value = 1.0
    with self.assertRaises(CertusFlexError):
      await self.device.move_to_well(self.reg, self.reg.plate.get_item("B2"), channel=1)
    self.move.assert_not_awaited()
    self.device.valves[1].calibration = ValveCalibration(((1, 0.001), (1000, 0.1)), pressure=0.2)
    with self.assertRaisesRegex(ValueError, "capacity"):
      await self.device.dispense_to_well(self.reg, self.reg.plate.get_item("B2"), 900, channel=1)
    self.move.assert_not_awaited()
