"""Tests for PrepGripperArm resource/coordinate pick and drop helpers."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import Prep
from pylabrobot.hamilton.prep import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.gripper import PrepGripper, PrepGripperArm
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import HamiltonCoreGrippers, PrepDeck


def _record_send(prep: Prep) -> list[Any]:
  captured: list[Any] = []
  orig_send = prep.client.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.client.send_command = recording  # type: ignore[method-assign, assignment]
  return captured


def _make_arm(deck: PrepDeck) -> PrepGripperArm:
  backend = PrepGripper(client=AsyncMock(), channels=AsyncMock())
  backend.pick_up_at_location = AsyncMock()  # type: ignore[method-assign]
  backend.drop_at_location = AsyncMock()  # type: ignore[method-assign]
  return PrepGripperArm(backend=backend, reference_resource=deck, grip_axis="y")


def test_drop_location_matches_holder_geometry_and_offset():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  arm = _make_arm(deck)

  pdfb = arm._resolve_pickup_distance(plate, None)
  arm._held_resource = plate
  arm._pickup_distance_from_bottom = pdfb
  arm._holding_resource_width = arm._resource_width(plate)

  offset = Coordinate(1.0, 2.0, 3.0)
  got = arm._drop_location(dest, offset)

  expected = (
    dest.get_absolute_location("l", "f", "b")
    + dest.get_default_child_location(plate)
    + plate.center()
    + offset
    + Coordinate(0, 0, pdfb)
  )
  assert got.x == pytest.approx(expected.x)
  assert got.y == pytest.approx(expected.y)
  assert got.z == pytest.approx(expected.z)


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
  dropped: List[Coordinate] = []

  async def _capture_drop(location: Coordinate, resource_width: float, **kwargs: Any) -> None:
    del resource_width, kwargs
    dropped.append(location)

  arm.backend.drop_at_location = _capture_drop  # type: ignore[method-assign]

  async def _run() -> None:
    await arm.pick_up_resource(plate)
    assert plate.parent is deck[4]
    await arm.drop_resource(dest)
    assert plate.parent is dest
    assert dest.resource is plate
    assert deck[4].resource is None
    assert arm._held_resource is None
    assert arm._holding_resource_width is None
    assert len(dropped) == 1

  asyncio.run(_run())


def test_pick_up_resource_width_override():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  arm = _make_arm(deck)
  captured: dict[str, Any] = {}

  async def _capture_pick(
    location: Coordinate,
    resource_width: float,
    *,
    resource_length: float,
    resource_height: float,
    plate_top_z_offset: float,
    clearance_y: float = 2.5,
    grip_speed_y: float = 5.0,
    squeeze_mm: float = 2.0,
  ) -> None:
    del location, resource_length, resource_height, plate_top_z_offset
    del clearance_y, grip_speed_y, squeeze_mm
    captured["resource_width"] = resource_width

  arm.backend.pick_up_at_location = _capture_pick  # type: ignore[method-assign]

  async def _run() -> None:
    await arm.pick_up_resource(plate, resource_width=80.5)
    assert captured["resource_width"] == 80.5
    assert arm._holding_resource_width == 80.5

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
    assert arm._holding_resource_width == 85.0
    assert arm._held_resource is None
    await arm.drop_at_location(place)
    arm.backend.drop_at_location.assert_awaited_once()  # type: ignore[attr-defined]
    args = arm.backend.drop_at_location.await_args  # type: ignore[attr-defined]
    assert args is not None
    assert args.args[0] == place
    assert args.args[1] == 85.0
    assert arm._holding_resource_width is None

  asyncio.run(_run())


def test_drop_resource_applies_offset_to_firmware_location():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  arm = _make_arm(deck)
  dropped_loc: Optional[Coordinate] = None

  async def _capture_drop(location: Coordinate, resource_width: float, **kwargs: Any) -> None:
    nonlocal dropped_loc
    del resource_width, kwargs
    dropped_loc = location

  arm.backend.drop_at_location = _capture_drop  # type: ignore[method-assign]
  offset = Coordinate(0.5, -0.25, 1.0)

  async def _run() -> None:
    await arm.pick_up_resource(plate)
    expected = arm._drop_location(dest, offset)
    await arm.drop_resource(dest, offset=offset)
    assert dropped_loc is not None
    assert dropped_loc.x == pytest.approx(expected.x)
    assert dropped_loc.y == pytest.approx(expected.y)
    assert dropped_loc.z == pytest.approx(expected.z)

  asyncio.run(_run())


def test_pick_up_tool_default_pre_position_moves_then_picks():
  """Default pre_position=True issues PrepMoveToPosition before PrepPickUpTool."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = Prep(deck=deck, chatterbox=True)
    await p.setup()
    assert p.gripper is not None
    captured = _record_send(p)

    await p.pick_up_core_grippers()

    seq = [
      c for c in captured if isinstance(c, (PrepCmd.PrepMoveToPosition, PrepCmd.PrepPickUpTool))
    ]
    assert len(seq) >= 2
    assert isinstance(seq[0], PrepCmd.PrepMoveToPosition)
    assert isinstance(seq[1], PrepCmd.PrepPickUpTool)

    await p.return_core_grippers()
    await p.stop()

  asyncio.run(_run())


def test_pick_up_tool_pre_position_false_skips_move():
  """Explicit pre_position=False sends PrepPickUpTool without a prior PrepMoveToPosition."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = Prep(deck=deck, chatterbox=True)
    await p.setup()
    assert p.gripper is not None
    captured = _record_send(p)

    mount = deck.get_resource("core_grippers")
    assert isinstance(mount, HamiltonCoreGrippers)
    loc = mount.get_location_wrt(deck)
    await p.gripper.pick_up_tool(
      tool_position_x=loc.x,
      tool_position_z=loc.z,
      front_channel_position_y=loc.y + mount.front_channel_y_center,
      rear_channel_position_y=loc.y + mount.back_channel_y_center,
      pre_position=False,
    )

    moves = [c for c in captured if isinstance(c, PrepCmd.PrepMoveToPosition)]
    pickups = [c for c in captured if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert moves == []
    assert len(pickups) >= 1

    await p.stop()

  asyncio.run(_run())
