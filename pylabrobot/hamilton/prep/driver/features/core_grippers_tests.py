"""Tests for CoreGripperArm resource/coordinate pick and drop helpers."""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.core_grippers import CoreGripperArm, CoreGrippers
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import HamiltonCoreGrippers, PrepDeck


def _record_send(prep: PrepDriver) -> list[Any]:
  captured: list[Any] = []
  orig_send = prep.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.send_command = recording  # type: ignore[method-assign, assignment]
  return captured


def _make_arm(deck: PrepDeck) -> CoreGripperArm:
  backend = CoreGrippers(AsyncMock())
  backend.pick_up_at_location = AsyncMock()  # type: ignore[method-assign]
  backend.drop_at_location = AsyncMock()  # type: ignore[method-assign]
  return CoreGripperArm(backend=backend, reference_resource=deck, grip_axis="y")


def test_drop_resource_releases_over_the_destination_at_the_grip_height():
  """Released over the new holder's centre plus the offset, at the height it was gripped."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  arm = _make_arm(deck)
  picked: List[Coordinate] = []
  dropped: List[Coordinate] = []
  arm.backend.pick_up_at_location = AsyncMock(  # type: ignore[method-assign]
    side_effect=lambda location, *args, **kwargs: picked.append(location)
  )
  arm.backend.drop_at_location = AsyncMock(  # type: ignore[method-assign]
    side_effect=lambda location, *args, **kwargs: dropped.append(location)
  )
  offset = Coordinate(0.5, -0.25, 1.0)

  async def _run() -> None:
    source = plate.get_absolute_location("c", "c", "b")
    await arm.pick_up_resource(plate)
    await arm.drop_resource(dest, offset=offset)
    placed = plate.get_absolute_location("c", "c", "b")
    assert (picked[0].x, picked[0].y) == pytest.approx((source.x, source.y))
    assert (dropped[0].x, dropped[0].y) == pytest.approx((placed.x + offset.x, placed.y + offset.y))
    assert dropped[0].z - placed.z == pytest.approx(picked[0].z - source.z + offset.z)

  asyncio.run(_run())


def test_drop_resource_not_holding_raises():
  deck = PrepDeck(with_core_grippers=True)
  arm = _make_arm(deck)

  async def _run() -> None:
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await arm.drop_resource(deck[2])

  asyncio.run(_run())


def test_drop_resource_after_coordinate_pick_raises():
  deck = PrepDeck(with_core_grippers=True)
  arm = _make_arm(deck)

  async def _run() -> None:
    await arm.pick_up_at_location(
      Coordinate(100, 200, 50),
      resource_width=85.0,
      resource_length=127.0,
      resource_height=14.0,
      plate_top_z_offset=5.0,
    )
    with pytest.raises(RuntimeError, match="pick_up_resource"):
      await arm.drop_resource(deck[2])

  asyncio.run(_run())


def test_drop_resource_reassigns_holder():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  arm = _make_arm(deck)

  async def _run() -> None:
    await arm.pick_up_resource(plate)
    await arm.drop_resource(dest)
    assert plate.parent is dest
    assert dest.resource is plate
    assert deck[4].resource is None
    arm.backend.drop_at_location.assert_awaited_once()  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await arm.drop_resource(deck[4])

  asyncio.run(_run())


def test_pick_up_resource_width_override():
  """A width given at pick up is the one gripped with and released with."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  arm = _make_arm(deck)

  async def _run() -> None:
    await arm.pick_up_resource(plate, resource_width=80.5)
    await arm.drop_resource(deck[2])
    pick = arm.backend.pick_up_at_location.await_args  # type: ignore[attr-defined]
    drop = arm.backend.drop_at_location.await_args  # type: ignore[attr-defined]
    assert pick.args[1] == 80.5 and drop.args[1] == 80.5

  asyncio.run(_run())


def test_pick_up_at_location_enables_drop_at_location():
  deck = PrepDeck(with_core_grippers=True)
  arm = _make_arm(deck)
  place = Coordinate(10, 20, 30)

  async def _run() -> None:
    await arm.pick_up_at_location(
      Coordinate(1, 2, 3),
      resource_width=85.0,
      resource_length=127.0,
      resource_height=14.0,
      plate_top_z_offset=5.0,
    )
    await arm.drop_at_location(place)
    args = arm.backend.drop_at_location.await_args  # type: ignore[attr-defined]
    assert (args.args[0], args.args[1]) == (place, 85.0)
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await arm.drop_at_location(place)

  asyncio.run(_run())


def test_pick_up_tool_default_pre_position_moves_then_picks():
  """Default pre_position=True moves to the tools before the one PrepPickUpTool."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    captured = _record_send(p)

    await p.pick_up_core_grippers()

    pickups = [i for i, c in enumerate(captured) if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert len(pickups) == 1
    assert any(isinstance(c, PrepCmd.PrepMoveToPosition) for c in captured[: pickups[0]])

    await p.return_core_grippers()
    await p.stop()

  asyncio.run(_run())


def test_pick_up_tool_pre_position_false_skips_move():
  """Explicit pre_position=False sends PrepPickUpTool without a prior PrepMoveToPosition."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    captured = _record_send(p)

    mount = deck.get_resource("core_grippers")
    assert isinstance(mount, HamiltonCoreGrippers)
    loc = mount.get_location_wrt(deck)
    await p.core_grippers.pick_up_tool(
      tool_position_x=loc.x,
      tool_position_z=loc.z,
      front_channel_position_y=loc.y + mount.front_channel_y_center,
      rear_channel_position_y=loc.y + mount.back_channel_y_center,
      pre_position=False,
    )

    moves = [c for c in captured if isinstance(c, PrepCmd.PrepMoveToPosition)]
    pickups = [c for c in captured if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert moves == []
    assert len(pickups) == 1

    await p.stop()

  asyncio.run(_run())
