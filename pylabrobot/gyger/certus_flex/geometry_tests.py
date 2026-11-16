"""Plate transforms, physical envelope checks, and pre-motion validation."""

from typing import Any
from unittest.mock import patch

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.resources import Coordinate, cor_96_wellplate_360uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase

from ._inco import Argument
from .device import CertusFlex, CertusFlexError, ValveCalibration
from .geometry import MotionBounds, PlateRegistration
from .testing_utils import FakeInco


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


class TestGeometry:
  """Anchor coordinates to observed hardware moves, not just inverse calculations."""

  def test_observed_wet_positions(self) -> None:
    reg = registration()
    for well, channel, expected in (
      ("B2", 1, (57.38, 29.96, 35.4)),
      ("C3", 2, (66.38, 29.96, 35.4)),
    ):
      for a, b in zip(reg.target(reg.plate.get_item(well), channel), expected):
        assert a == pytest.approx(b)

  def test_foreign_well_channel_and_travel(self) -> None:
    reg = registration()
    for well, channel in (
      (reg.plate.get_item("A12"), 0),
      (reg.plate.get_item("A1"), True),
      (cor_96_wellplate_360uL_Fb("other").get_item("A1"), 0),
    ):
      with pytest.raises(ValueError):
        reg.target(well, channel)

  def test_changed_geometry_is_rejected(self) -> None:
    reg = registration()
    well = reg.plate.get_item("B2")
    well.location = Coordinate(1, 2, 3)
    with pytest.raises(ValueError, match="geometry changed"):
      reg.target(well, 1)

  def test_invalid_bounds(self) -> None:
    with pytest.raises(ValueError):
      MotionBounds((0, 0, 0), (1, float("nan"), 1))


class TestPlateOperations(AnyioTestBase):
  """Assert well validation happens before movement and trajectories use clearance."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.reg = registration()
    self.device = CertusFlex("example.invalid", head="flex8_angled")
    self.inco = FakeInco(strings=["C196069", "1.0.7.378-trunk"], default_number=4.0)
    self.device._inco = self.inco  # type: ignore[assignment]
    self.moves: list[tuple[str, tuple[Argument, ...]]] = []
    positions = iter([(-62.0, 30.0, 65.5), (57.38, 29.96, 35.4)])

    async def fake_move(command: str, *arguments: Argument) -> None:
      await anyio.lowlevel.checkpoint()
      self.moves.append((command, arguments))

    async def fake_position() -> tuple[float, float, float]:
      await anyio.lowlevel.checkpoint()
      return next(positions)

    stack.enter_context(patch.object(self.device, "_move", new=fake_move))
    stack.enter_context(patch.object(self.device, "get_position", new=fake_position))
    await stack.enter_async_context(self.device.lifespan())

  async def test_clearance_path(self) -> None:
    await self.device.move_to_well(self.reg, self.reg.plate.get_item("B2"), channel=1)
    assert self.moves[0] == ("MoveXYZ", (-62.0, 30.0, 65.5, "Slow"))
    assert self.moves[1][0] == "MoveXYZ"
    assert self.moves[1][1][2] == 65.5
    assert self.moves[2][1][2] == pytest.approx(35.4)

  async def test_wrong_head_and_capacity_send_no_motion(self) -> None:
    self.inco.default_number = 1.0
    with pytest.raises(CertusFlexError):
      await self.device.move_to_well(self.reg, self.reg.plate.get_item("B2"), channel=1)
    assert self.moves == []
    self.device.valves[1].calibration = ValveCalibration(((1, 0.001), (1000, 0.1)), pressure=0.2)
    with pytest.raises(ValueError, match="capacity"):
      await self.device.dispense_to_well(self.reg, self.reg.plate.get_item("B2"), 900, channel=1)
    assert self.moves == []
