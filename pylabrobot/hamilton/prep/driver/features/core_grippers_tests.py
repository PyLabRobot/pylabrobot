"""Tests for CoreGripperArm resource/coordinate pick and drop helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.core_grippers import CoreGripperArm, CoreGrippers
from pylabrobot.resources import Coordinate
from pylabrobot.resources.corning.axygen.plates import cor_axy_96_wellplate_500uL_Ub
from pylabrobot.resources.hamilton import HamiltonCoreGrippers, PrepDeck
from pylabrobot.resources.head_tool import HeadTool


def _record_send(prep: PrepDriver) -> list[Any]:
  captured: list[Any] = []
  orig_send = prep.send_command

  async def recording(command, **kw):
    captured.append(command)
    return await orig_send(command, **kw)

  prep.send_command = recording  # type: ignore[method-assign, assignment]
  return captured


def _make_arm(deck: PrepDeck) -> CoreGripperArm:
  grippers = CoreGrippers(AsyncMock())
  grippers.pick_up_at_location = AsyncMock()  # type: ignore[method-assign]
  grippers.drop_at_location = AsyncMock()  # type: ignore[method-assign]
  driver = SimpleNamespace(core_grippers=grippers, deck=deck)
  return CoreGripperArm(driver, grip_axis="y")  # type: ignore[arg-type]


def test_drop_resource_releases_over_the_destination_at_the_grip_height():
  """Released over the new holder's centre plus the offset, at the height it was gripped."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  arm = _make_arm(deck)
  picked: List[Coordinate] = []
  dropped: List[Coordinate] = []
  arm.grippers.pick_up_at_location = AsyncMock(  # type: ignore[method-assign]
    side_effect=lambda location, *args, **kwargs: picked.append(location)
  )
  arm.grippers.drop_at_location = AsyncMock(  # type: ignore[method-assign]
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
    arm.grippers.drop_at_location.assert_awaited_once()  # type: ignore[attr-defined]
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
    pick = arm.grippers.pick_up_at_location.await_args  # type: ignore[attr-defined]
    drop = arm.grippers.drop_at_location.await_args  # type: ignore[attr-defined]
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
    args = arm.grippers.drop_at_location.await_args  # type: ignore[attr-defined]
    assert (args.args[0], args.args[1]) == (place, 85.0)
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await arm.drop_at_location(place)

  asyncio.run(_run())


def test_pick_up_tool_moves_over_the_tools_then_picks():
  """The channels are taken over the tools before the one PrepPickUpTool."""

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


def test_picking_the_tools_up_sends_where_the_deck_holds_them():
  """The positions sent are the holder's x and channel centres, and the height of the tools in it."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    captured = _record_send(p)

    # Read where the tools stand before they are taken: once they are, they are on the channels.
    holder = p.core_gripper_holder
    assert isinstance(holder, HamiltonCoreGrippers)
    at = holder.get_location_wrt(deck, x="c")
    tools = [child for child in holder.children if isinstance(child, HeadTool)]
    engage = min(tool.get_location_wrt(deck, z="t").z - tool.fitting_depth for tool in tools)

    await p.pick_up_core_grippers()

    (pickup,) = [c for c in captured if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert pickup.tool_position_x == at.x
    assert pickup.tool_position_z == engage == 62.0  # as the deck placed the holder before it was
    assert pickup.rear_channel_position_y == at.y + holder.back_channel_y_center  # a box of its own
    assert pickup.front_channel_position_y == at.y + holder.front_channel_y_center
    assert pickup.tool_seek == engage + 10.0
    assert pickup.tool_x_radius == 2.0
    assert pickup.tool_y_radius == 2.0
    assert pickup.tip_definition == PrepCmd.CO_RE_GRIPPER_TIP_PICKUP_PARAMETERS

    # The move that precedes it puts the channels over the same x, a channel centre either side.
    before = captured[: captured.index(pickup)]
    (approach,) = [c for c in before if isinstance(c, PrepCmd.PrepMoveToPosition)]
    assert approach.move_parameters.gantry_x_position == at.x
    assert [axis.y_position for axis in approach.move_parameters.axis_parameters] == [
      at.y + holder.back_channel_y_center,
      at.y + holder.front_channel_y_center,
    ]

    await p.return_core_grippers()
    assert len([c for c in captured if isinstance(c, PrepCmd.PrepDropTool)]) == 1
    assert not p.core_grippers_mounted

    await p.stop()

  asyncio.run(_run())


def test_the_tools_are_picked_up_where_they_stand_not_where_the_holder_sits():
  """They stand proud of the holder, and a channel sent to its base would be driven through them."""

  async def _t():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    captured = _record_send(p)
    # Where they stand is read before they are taken: a taken tool is on the channel, not here.
    holder = p.core_gripper_holder
    assert holder is not None
    tools = [child for child in holder.children if isinstance(child, HeadTool)]
    engage = min(tool.get_location_wrt(deck, z="t").z - tool.fitting_depth for tool in tools)

    await p.pick_up_core_grippers()

    (pickup,) = [c for c in captured if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert pickup.tool_position_z == engage
    assert pickup.tool_seek == engage + 10.0
    assert engage > holder.get_location_wrt(deck).z  # the holder's base is not where they are
    await p.stop()

  asyncio.run(_t())


def test_a_tool_pick_up_that_fails_still_raises_the_channels():
  """Down at the tools is where it leaves them, and the next lateral move would drag them through."""

  async def _t():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.pipettes is not None
    send = p.send_command
    raised: list = []

    async def failing(command, *args, **kwargs):
      if isinstance(command, PrepCmd.PrepPickUpTool):
        raise RuntimeError("the device refused the tools")
      if isinstance(command, PrepCmd.PrepMoveZUpToSafe):
        raised.append(command)
      return await send(command, *args, **kwargs)

    p.send_command = failing  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="refused the tools"):
      await p.pick_up_core_grippers()
    assert raised  # to Z safety on the way out
    await p.stop()

  asyncio.run(_t())


def test_the_tool_commands_are_the_frames_the_device_answered():
  """Ground truth: what PRPAA1087 was sent on 2026-09-18, taking the tools and putting them back.

  Both cycles answered in about four seconds with no error, so these are the bytes that work. The
  pick-up's payload carries every position in it - the tools\' X, the height a channel reaches into
  them, the seek above that, a channel centre either side - so a deck model, a geometry or an
  encoding that moves shows up here rather than on the deck.
  """

  async def _run() -> None:
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    captured = _record_send(p)

    await p.pick_up_core_grippers()
    await p.return_core_grippers()

    sent = [type(c).__name__ for c in captured if not isinstance(c, PrepCmd.PrepGetPositions)]
    assert sent == [
      "PrepGetXSpeedScale",  # the travel's speed, set for the move and put back
      "PrepSetXSpeedScale",
      "PrepMoveToPosition",  # over the tools, at the traverse height
      "PrepSetXSpeedScale",
      "PrepPickUpTool",
      "PrepMoveZUpToSafe",  # picked up, and back to Z safety
      "PrepGetChannelBounds",  # the tools move the channels' Z windows, so they are read again
      "PrepMoveZUpToSafe",  # and again before letting go
      "PrepDropTool",
      "PrepGetChannelBounds",
    ]

    (pickup,) = [c for c in captured if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert pickup.build_parameters().build().hex() == (
      "1e003000170102000000280004000000803f280004003333b7412000040000000000170102000000170102"
      "000000170102000100280004000000914328000400000078422800040000c080432800040000c089432800"
      "04000000904228000400000000402800040000000040"
    )

    (drop,) = [c for c in captured if isinstance(c, PrepCmd.PrepDropTool)]
    assert (
      drop.build_parameters().build().hex() == ""
    )  # it takes none: the tools go back where they came from

  asyncio.run(_run())


def test_the_tools_ride_on_the_channels_that_took_them():
  """A tool the channels have taken is theirs while they hold it, as a tip is, and goes back to
  the holder when it is dropped. Otherwise the model - and the viewer reading it - has the tools
  sitting in the holder while the channels carry them across the deck."""

  async def _run():
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    holder = p.core_gripper_holder
    assert holder is not None
    front, back = (t for t in holder.children if isinstance(t, HeadTool))
    parked = {tool.name: tool.get_location_wrt(p.deck) for tool in (front, back)}

    await p.pick_up_core_grippers()

    # Channel 0 takes the rear tool and channel 1 the front, which is the order they are sent in.
    assert p.pipettes is not None
    assert back.parent is p.pipettes.shaft(0)
    assert front.parent is p.pipettes.shaft(1)
    assert [c for c in holder.children if isinstance(c, HeadTool)] == []
    # They ride where the channels ride, which is not where they were parked.
    for tool in (front, back):
      assert tool.get_location_wrt(p.deck).z > parked[tool.name].z

    await p.return_core_grippers()

    assert {t.name for t in holder.children} == {front.name, back.name}
    for tool in (front, back):
      assert tool.get_location_wrt(p.deck) == parked[tool.name]

  asyncio.run(_run())


def test_a_drop_the_device_refuses_leaves_the_tools_on_the_channels():
  """The model says what the device did: if it did not let go, the channels are still holding the
  tools, and saying otherwise sends the next move somewhere with two tools it does not expect."""

  async def _run():
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    holder = p.core_gripper_holder
    assert holder is not None
    tools = [child for child in holder.children if isinstance(child, HeadTool)]

    await p.pick_up_core_grippers()
    held = [tool.parent for tool in tools]

    async def refuse(move_to_safe_z_first: bool = True) -> None:
      raise RuntimeError("error 51: the tool did not let go")

    assert p._core_gripper_arm is not None
    p._core_gripper_arm.grippers.drop_tool = refuse  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
      await p.return_core_grippers()

    assert [tool.parent for tool in tools] == held
    assert p.core_grippers_mounted
    assert [c for c in holder.children if isinstance(c, HeadTool)] == []
    await p.stop()

  asyncio.run(_run())
