"""Tests for the CoRe grippers' resource and coordinate pick and drop helpers."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Callable, List, Optional, Tuple
from unittest.mock import AsyncMock

import pytest

from pylabrobot.hamilton.prep import PrepDriver, PrepSimulationDriver
from pylabrobot.hamilton.prep.driver import prep_commands as PrepCmd
from pylabrobot.hamilton.prep.driver.features.core_grippers import (
  FIRMWARE_Z_LEG,
  JAW_OPEN_EXTRA,
  CoreGrippers,
)
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.azenta import azenta_96_wellplate_200uL_Vb_4titudeframestar
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


def _make_grippers(deck: PrepDeck, stub_pick_and_drop: bool = True) -> Tuple[CoreGrippers, Any]:
  """Grippers that resolve real geometry against `deck` and stop before the device.

  By default the two calls the geometry ends in are stood in for on the object, so a test of what
  `pick_up_resource` and `drop_resource` work out reads the location they arrived at. A test of
  those two calls themselves asks for them whole, and reads the command that was sent instead.
  """
  pipettes = SimpleNamespace(
    configuration=SimpleNamespace(channels=[]),  # no reported windows
    num_channels=2,
    default_z_speed=125.0,
    move_to_safe_z=AsyncMock(),
    move_tool_bottom_to_z_positions=AsyncMock(),
    move_to_y_positions=AsyncMock(),
    move_to_xy_positions=AsyncMock(),
    _record_where_they_stopped=AsyncMock(),
    shaft=lambda channel: None,
    # Where PRPAA1087 had the jaws once a plate was gripped and raised: centred on it, at Z safety.
    request_locations=AsyncMock(
      return_value=[Coordinate(62.55, 84.47, 144.6), Coordinate(62.55, 4.0, 144.6)]
    ),
  )
  order: List[str] = []

  pipettes.move_to_xy_positions = AsyncMock(
    side_effect=lambda *args, **kwargs: order.append("move_to_xy_positions")
  )
  driver = SimpleNamespace(
    deck=deck,
    send_command=AsyncMock(
      side_effect=lambda command, **kwargs: order.append(type(command).__name__)
    ),
    pipettes=pipettes,
    x_arm=SimpleNamespace(move_to_x_position=AsyncMock()),
  )
  grippers = CoreGrippers(driver, grip_axis="y")  # type: ignore[arg-type]
  grippers._tools_mounted = True
  grippers._tools_on_channels = True

  async def set_z_acceleration(z_acceleration: float) -> None:
    order.append(f"z_acceleration={z_acceleration}")

  async def restore_z_acceleration() -> None:
    order.append("z_acceleration restored")

  grippers._set_z_acceleration = set_z_acceleration  # type: ignore[method-assign]
  grippers._restore_z_acceleration = restore_z_acceleration  # type: ignore[method-assign]

  def let_go(*args: Any, on_released: Optional[Callable[[], None]] = None, **kwargs: Any) -> None:
    if on_released is not None:
      on_released()
    grippers._clear_held_state()

  commands = SimpleNamespace(
    # The grip and the let-go stand in for the device and still call back, as the real ones do.
    pick_up_at=AsyncMock(
      side_effect=lambda *args, on_gripped=None, **kwargs: on_gripped and on_gripped()
    ),
    # A release is what leaves the jaws empty, so the stand-in keeps that and skips only the device.
    drop_at=AsyncMock(side_effect=let_go),
    send_command=driver.send_command,
    move_tool_bottom_to_z_positions=pipettes.move_tool_bottom_to_z_positions,
    move_to_safe_z=pipettes.move_to_safe_z,
    request_locations=pipettes.request_locations,
    record_where_they_stopped=pipettes._record_where_they_stopped,
    move_to_xy_positions=pipettes.move_to_xy_positions,
    order=order,
  )
  if stub_pick_and_drop:
    grippers._pick_up_at = commands.pick_up_at  # type: ignore[method-assign]
    grippers._drop_at = commands.drop_at  # type: ignore[method-assign]
  return grippers, commands


def test_the_tools_on_by_location_alone_let_the_grippers_act():
  """`pick_up_tools_at_location` puts them on the channels; `drop_tools` takes them off."""
  deck = PrepDeck(with_core_grippers=True)
  grippers, commands = _make_grippers(deck)
  grippers._tools_mounted = grippers._tools_on_channels = False
  pipettes: Any = grippers._driver.pipettes
  pipettes._resolve_traverse_height = lambda: 167.5
  pipettes._record_channel_bounds = AsyncMock()

  async def send(command, **kwargs):
    if isinstance(command, PrepCmd.PrepGetPlateHeld):
      return PrepCmd.PrepGetPlateHeld.Response(value=False)

  commands.send_command.side_effect = send

  async def _run() -> None:
    with pytest.raises(RuntimeError, match="not on the channels"):
      grippers._require_mounted()
    await grippers.pick_up_tools_at_location(100.0, 50.0, 60.0, 90.0)
    assert grippers._require_mounted() == (0, 1)
    assert not grippers.tools_mounted
    await grippers.drop_tools(move_to_safe_z_first=False)
    with pytest.raises(RuntimeError, match="not on the channels"):
      grippers._require_mounted()

  asyncio.run(_run())


def test_gripped_more_than_20_mm_below_its_top_is_refused():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    with pytest.raises(ValueError, match="pipetting head"):
      await grippers.pick_up_resource(plate, pickup_distance_from_top=20.5)
    commands.pick_up_at.assert_not_awaited()
    await grippers.pick_up_resource(plate, pickup_distance_from_top=12.0)
    commands.pick_up_at.assert_awaited_once()

  asyncio.run(_run())


def test_drop_resource_releases_over_the_destination_at_the_grip_height():
  """Released over the new holder's centre plus the offset, at the height it was gripped."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  grippers, commands = _make_grippers(deck)
  offset = Coordinate(0.5, -0.25, 1.0)

  async def _run() -> None:
    source = plate.get_absolute_location("c", "c", "b")
    await grippers.pick_up_resource(plate)
    await grippers.drop_resource(dest, offset=offset)
    placed = plate.get_absolute_location("c", "c", "b")
    picked: List[Coordinate] = [commands.pick_up_at.await_args.args[0]]
    dropped: List[Coordinate] = [commands.drop_at.await_args.args[0]]
    assert (picked[0].x, picked[0].y) == pytest.approx((source.x, source.y))
    assert (dropped[0].x, dropped[0].y) == pytest.approx((placed.x + offset.x, placed.y + offset.y))
    assert dropped[0].z - placed.z == pytest.approx(picked[0].z - source.z + offset.z)

  asyncio.run(_run())


def test_drop_resource_not_holding_raises():
  deck = PrepDeck(with_core_grippers=True)
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await grippers.drop_resource(deck[2])

  asyncio.run(_run())


def test_drop_resource_after_coordinate_pick_raises():
  deck = PrepDeck(with_core_grippers=True)
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)

  async def _run() -> None:
    await grippers._pick_up_at(
      Coordinate(100, 200, 50),
      resource_size_x=127.0,
      resource_size_y=85.0,
      resource_size_z=14.0,
      plate_top_z_offset=5.0,
    )
    with pytest.raises(RuntimeError, match="pick_up_resource"):
      await grippers.drop_resource(deck[2])

  asyncio.run(_run())


def test_drop_resource_reassigns_holder():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  dest = deck[2]
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    await grippers.pick_up_resource(plate)
    await grippers.drop_resource(dest)
    assert plate.parent is dest
    assert dest.resource is plate
    assert deck[4].resource is None
    commands.drop_at.assert_awaited_once()
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await grippers.drop_resource(deck[4])

  asyncio.run(_run())


def test_pick_up_resource_width_override():
  """A width given at pick up is the one gripped with and released with."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    await grippers.pick_up_resource(plate, resource_size_y=80.5)
    gripped = grippers._holding_resource_width
    await grippers.drop_resource(deck[2])
    pick = commands.pick_up_at.await_args
    commands.drop_at.assert_awaited_once()
    assert pick.kwargs["resource_size_y"] == 80.5 and gripped == 80.5

  asyncio.run(_run())


def test_private_pick_up_at_enables_drop_at():
  deck = PrepDeck(with_core_grippers=True)
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
  place = Coordinate(10, 20, 30)

  async def _run() -> None:
    await grippers._pick_up_at(
      Coordinate(1, 2, 3),
      resource_size_x=127.0,
      resource_size_y=85.0,
      resource_size_z=14.0,
      plate_top_z_offset=5.0,
    )
    held = grippers._holding_resource_width
    await grippers._drop_at(place)
    sent = commands.send_command.await_args.args[0]
    assert isinstance(sent, PrepCmd.PrepDropPlate)
    assert (sent.plate_top_center.x_position, sent.plate_top_center.y_position) == (
      place.x,
      place.y,
    )
    assert held == 85.0
    # Up to Z safety before each carry and after letting go; the jaws raise it after the grip and
    # lower it to the let-go at a known speed.
    assert commands.move_to_safe_z.await_count == 3
    assert commands.move_tool_bottom_to_z_positions.await_count == 2
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await grippers._drop_at(place)

  asyncio.run(_run())


@pytest.mark.parametrize("resource_height, warned", [(14.0, False), (100.0, True)])
def test_drop_at_warns_when_carried_below_safe_deck_height(caplog, resource_height, warned):
  """Jaws carried at 144.6 mm, gripped 5 mm below the top: a 100 mm resource hangs to 49.6 mm."""
  deck = PrepDeck(with_core_grippers=True)
  grippers, _ = _make_grippers(deck, stub_pick_and_drop=False)

  async def _run() -> None:
    await grippers._pick_up_at(
      Coordinate(1, 2, 3),
      resource_size_x=127.0,
      resource_size_y=85.0,
      resource_size_z=resource_height,
      plate_top_z_offset=5.0,
    )
    await grippers._drop_at(Coordinate(10, 20, 30))

  # The pylabrobot logger does not propagate to the root, where caplog listens.
  gripper_logger = logging.getLogger(CoreGrippers.__module__)
  gripper_logger.addHandler(caplog.handler)
  try:
    asyncio.run(_run())
  finally:
    gripper_logger.removeHandler(caplog.handler)
  below = [r for r in caplog.records if "safe deck height" in r.getMessage()]
  assert bool(below) == warned
  if warned:
    assert "49.6 mm" in below[0].getMessage()


@pytest.mark.parametrize("surface, found", [(15.2, True), (None, False)])
def test_probe_resource_exists_ztouches_the_front_tool_over_its_centre(surface, found):
  """The front channel goes over the centre and searches from `search_distance` above the top to
  its centre."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck)
  probe = AsyncMock(return_value=surface)
  pipettes: Any = grippers._driver.pipettes
  pipettes.probe_z_using_ztouch = probe

  async def _run() -> bool:
    return await grippers.probe_resource_exists_using_ztouch(plate, search_distance=20.0)

  assert asyncio.run(_run()) is found
  top = plate.get_location_wrt(deck, x="c", y="c", z="t")
  xy = commands.move_to_xy_positions.await_args
  assert xy is not None
  assert xy.args == (top.x, {1: top.y})
  assert xy.kwargs["make_space"] is True
  probe.assert_awaited_once()
  seek = probe.await_args
  assert seek is not None
  assert seek.args == (1,)
  assert seek.kwargs["search_start_position"] == pytest.approx(top.z + 20.0)
  center = plate.get_location_wrt(deck, x="c", y="c", z="c")
  assert seek.kwargs["search_end_position"] == pytest.approx(center.z)
  assert seek.kwargs["allow_without_tip"] is True
  # Travelled at Z safety, and the probe is the one to leave the channels there, not 2 mm up.
  assert seek.kwargs["post_detection_distance"] == 0.0
  assert seek.kwargs["move_channels_to_safe_pos_after"] is True
  assert commands.move_to_safe_z.await_count == 1


def test_pick_up_tools_moves_over_the_tools_then_picks():
  """The channels are taken over the tools before the one PrepPickUpTool."""

  async def _run() -> None:
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    captured = _record_send(p)

    assert p.core_grippers is not None
    await p.core_grippers.pick_up_tools()

    pickups = [i for i, c in enumerate(captured) if isinstance(c, PrepCmd.PrepPickUpTool)]
    assert len(pickups) == 1
    assert any(isinstance(c, PrepCmd.PrepMoveToPosition) for c in captured[: pickups[0]])

    await p.core_grippers.return_tools()
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

    await p.core_grippers.pick_up_tools()

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

    await p.core_grippers.return_tools()
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

    assert p.core_grippers is not None
    await p.core_grippers.pick_up_tools()

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
      assert p.core_grippers is not None
      assert p.core_grippers is not None
      await p.core_grippers.pick_up_tools()
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

    assert p.core_grippers is not None
    await p.core_grippers.pick_up_tools()
    await p.core_grippers.return_tools()

    sent = [type(c).__name__ for c in captured if not isinstance(c, PrepCmd.PrepGetPositions)]
    assert sent == [
      "PrepGetXSpeedScale",  # the travel's speed, set for the move and put back
      "PrepSetXSpeedScale",
      "PrepMoveToPosition",  # over the tools, at the traverse height
      "PrepSetXSpeedScale",
      "PrepPickUpTool",
      "PrepMoveZUpToSafe",  # picked up, and back to Z safety
      "PrepGetChannelBounds",  # the tools move the channels' Z windows, so they are read again
      # and then each channel is asked whether it is really carrying a tool, once per mount
      "PrepProbeRequest",
      "PrepProbeRequest",
      "PrepGetTipDefinitionHeld",
      "PrepProbeRequest",
      "PrepProbeRequest",
      "PrepGetTipDefinitionHeld",
      "PrepGetPlateHeld",  # nothing held, so the tools may go home
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

    assert p.core_grippers is not None
    await p.core_grippers.pick_up_tools()

    # Channel 0 takes the rear tool and channel 1 the front, which is the order they are sent in.
    assert p.pipettes is not None
    assert back.parent is p.pipettes.shaft(0)
    assert front.parent is p.pipettes.shaft(1)
    assert [c for c in holder.children if isinstance(c, HeadTool)] == []
    # They ride where the channels ride, which is not where they were parked.
    for tool in (front, back):
      assert tool.get_location_wrt(p.deck).z > parked[tool.name].z

    await p.core_grippers.return_tools()

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

    assert p.core_grippers is not None
    await p.core_grippers.pick_up_tools()
    held = [tool.parent for tool in tools]

    async def refuse(move_to_safe_z_first: bool = True) -> None:
      raise RuntimeError("error 51: the tool did not let go")

    p.core_grippers.drop_tools = refuse  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
      await p.core_grippers.return_tools()

    assert [tool.parent for tool in tools] == held
    assert p.core_grippers_mounted
    assert [c for c in holder.children if isinstance(c, HeadTool)] == []
    await p.stop()

  asyncio.run(_run())


def test_request_tool_attached_asks_the_device_not_the_flag():
  """What the machine holds, which is what survives a restart while the model does not."""

  async def _run():
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    assert p.core_grippers is not None
    grippers = p.core_grippers

    assert [await grippers.request_tool_attached(ch) for ch in (0, 1)] == [False, False]
    await grippers.pick_up_tools()
    assert [await grippers.request_tool_attached(ch) for ch in (0, 1)] == [True, True]

    # The flag is this session's; the query is the device's, and they are read apart.
    grippers._tools_mounted = False
    assert await grippers.request_tool_attached(0) is True

    grippers._tools_mounted = True
    await grippers.return_tools()
    assert [await grippers.request_tool_attached(ch) for ch in (0, 1)] == [False, False]

    with pytest.raises(ValueError, match="channel must be between"):
      await grippers.request_tool_attached(7)
    await p.stop()

  asyncio.run(_run())


def test_a_held_resource_moves_with_both_jaws_or_not_at_all():
  """A channel's own Y opens or skews the grip while holding; the arm they share does not."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    await grippers.move_to_y_positions([150.0, 60.0])  # nothing held: allowed
    await grippers.pick_up_resource(plate)

    for name, call in (
      ("move_to_y_positions", grippers.move_to_y_positions([150.0, 60.0])),
      ("move_to_xy_positions", grippers.move_to_xy_positions(200.0, {0: 150.0})),
    ):
      with pytest.raises(RuntimeError, match="move_resource_to_xy_position"):
        await call
      assert name  # the name is here to say which one failed

    # X is the arm both channels ride, so it carries what they hold without opening them.
    await grippers.move_to_x_position(200.0)

  asyncio.run(_run())


def test_move_resource_to_xy_position_carries_it_at_the_height_it_is_at_now():
  """Not the height it was gripped at: the pick-up raises it, and lowering it back to that height to
  carry it would take it through whatever stands between."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)

  async def _run() -> None:
    with pytest.raises(RuntimeError, match="Not holding anything"):
      await grippers.move_resource_to_xy_position(100.0, 200.0)

    await grippers.pick_up_resource(plate)
    gripped_top = grippers._plate_top_center
    assert gripped_top is not None
    await grippers.move_resource_to_xy_position(100.0, 200.0)

    sent = commands.send_command.await_args.args[0]
    assert isinstance(sent, PrepCmd.PrepMovePlate)
    assert (sent.plate_top_center.x_position, sent.plate_top_center.y_position) == (100.0, 200.0)
    # The plane alone, at the height the jaws are at now (144.6), its top 5 mm above them - far
    # above the top it had when it was gripped.
    assert sent.plate_top_center.z_position == pytest.approx(149.6)
    assert gripped_top.z < 30.0
    assert grippers._plate_top_center == Coordinate(100.0, 200.0, 149.6)

  asyncio.run(_run())


def test_request_plate_held_reads_the_pipettors_record():
  """Set by a finished pick-up whatever the jaws closed on, cleared by a drop or a release."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    grippers = p.core_grippers
    plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
    await grippers.pick_up_tools()

    assert await grippers.request_plate_held() is False
    await grippers.pick_up_resource(plate)
    assert await grippers.request_plate_held() is True
    await grippers.drop_resource(deck[2])
    assert await grippers.request_plate_held() is False

    await grippers.pick_up_resource(plate)
    await grippers.release_plate()
    assert await grippers.request_plate_held() is False

    await grippers.return_tools()
    await p.stop()

  asyncio.run(_run())


def test_get_plate_held_is_the_recorded_method():
  """GetPlateHeld is method 22 of the Pipettor, on both firmware versions recorded."""
  assert PrepCmd.PrepGetPlateHeld.command_id == 22
  assert PrepCmd.PrepGetPlateHeld.firmware_path == "MLPrepRoot.PipettorRoot.Pipettor"
  assert PrepCmd.PrepGetPlateHeld().build_parameters().build() == b""


def test_return_resource_puts_it_back_where_it_was_taken_from():
  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    grippers = p.core_grippers
    plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
    source, where = plate.parent, plate.get_absolute_location()
    await grippers.pick_up_tools()

    await grippers.pick_up_resource(plate)
    await grippers.move_resource_to_xy_position(150.0, 250.0)
    await grippers.return_resource()

    assert plate.parent is source
    assert plate.get_absolute_location() == where
    assert await grippers.request_plate_held() is False
    with pytest.raises(RuntimeError, match="nothing to return it to"):
      await grippers.return_resource()

    await grippers.return_tools()
    await p.stop()

  asyncio.run(_run())


def test_return_resource_off_a_plain_parent_puts_it_on_the_deck_where_it_stood():
  """A holder would say where; a plain parent does not, so it goes to the deck at the same point."""
  deck = PrepDeck(with_core_grippers=True)
  bench = Resource("bench", size_x=200, size_y=200, size_z=10)
  deck.assign_child_resource(bench, location=Coordinate(10, 10, 0))
  plate = cor_axy_96_wellplate_500uL_Ub("plate")
  bench.assign_child_resource(plate, location=Coordinate(30.0, 40.0, 10.0))
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    await grippers.pick_up_resource(plate)
    picked = commands.pick_up_at.await_args.args[0]
    await grippers.return_resource()
    dropped = commands.drop_at.await_args.args[0]
    assert plate.parent is deck and plate.location == Coordinate(40.0, 50.0, 10.0)
    assert (dropped.x, dropped.y, dropped.z) == pytest.approx((picked.x, picked.y, picked.z))

  asyncio.run(_run())


def test_release_plate_leaves_nothing_held_and_nothing_to_return():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck)

  async def _run() -> None:
    await grippers.pick_up_resource(plate)
    await grippers.release_plate()
    assert grippers._holding_resource_width is None and grippers._held_resource is None
    await grippers.move_to_y_positions([150.0, 60.0])  # the guard is off once it is released
    with pytest.raises(RuntimeError, match="nothing to return it to"):
      await grippers.return_resource()

  asyncio.run(_run())


def _plate_commands(sent: List[Any], kind: type) -> List[Any]:
  return [c for c in sent if isinstance(c, kind)]


def test_a_returned_plate_is_let_go_of_at_the_top_it_was_taken_at():
  """Move and drop are told the plate's top, and the jaws go the pick-up's offset below it.

  PRPAA1087, 2026-09-21: sent the grip height as the top, every drop went 5.00 mm below the grip,
  with the jaws still closed on the plate. The top a plate is let go of at is the top it was taken
  at, when it goes back where it came from; in between it is carried where it was raised to.
  """
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
  top = plate.get_location_wrt(deck, "c", "c", "t")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)

  async def _run() -> None:
    await grippers.pick_up_resource(plate)
    await grippers.move_resource_to_xy_position(top.x + 1.0, top.y)
    await grippers.move_resource_to_xy_position(top.x, top.y)
    await grippers.return_resource()
    sent = [c.args[0] for c in commands.send_command.await_args_list]
    (pick,) = _plate_commands(sent, PrepCmd.PrepPickUpPlate)
    moves = _plate_commands(sent, PrepCmd.PrepMovePlate)
    (drop,) = _plate_commands(sent, PrepCmd.PrepDropPlate)
    # What the pick-up sends is unchanged: its top, and the jaws 5 mm below it.
    assert pick.plate_top_center.z_position == pytest.approx(top.z)
    assert pick.grip_height == pytest.approx(top.z - 5.0)
    # Carried at the height it was raised to - the jaws at 144.6, its top the offset above - and
    # not lowered back to the height it was gripped at, which would carry it through the deck. The
    # last is the carry over where it is let go of.
    assert [m.plate_top_center.z_position for m in moves] == pytest.approx([149.6] * 3)
    assert (moves[-1].plate_top_center.x_position, moves[-1].plate_top_center.y_position) == (
      pytest.approx((top.x, top.y))
    )
    # Let go of at the top it was taken at, not at the height the jaws hold it.
    assert drop.plate_top_center.z_position == pytest.approx(top.z)

    # Straight down both ways: over the plate, jaws open around it, before the firmware grips; and
    # carried over the destination before it lets go. Left to itself it dives across the deck.
    approach = commands.move_to_xy_positions.await_args_list[0]
    half = (85.48 + 2 * 2.5 + JAW_OPEN_EXTRA) / 2
    assert approach.args[0] == pytest.approx(top.x)
    assert approach.args[1] == pytest.approx({0: top.y + half, 1: top.y - half})
    assert approach.kwargs["minimum_traverse_height_start"] == 0
    assert commands.order.index("move_to_xy_positions") < commands.order.index("PrepPickUpPlate")
    sent_in_order = [c for c in commands.order if c.startswith("Prep")]
    assert sent_in_order[sent_in_order.index("PrepDropPlate") - 1] == "PrepMovePlate"

  asyncio.run(_run())


def test_pickup_distance_from_top_is_how_far_below_the_top_the_jaws_close():
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
  top = plate.get_location_wrt(deck, "c", "c", "t")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)

  async def _run() -> None:
    await grippers.pick_up_resource(plate, pickup_distance_from_top=3.0)
    (pick,) = _plate_commands(
      [c.args[0] for c in commands.send_command.await_args_list], PrepCmd.PrepPickUpPlate
    )
    assert pick.grip_height == pytest.approx(top.z - 3.0)
    assert pick.plate_top_center.z_position == pytest.approx(top.z)

  asyncio.run(_run())


def test_move_resource_to_xy_position_keeps_the_dimension_it_is_not_given():
  """One of x and y moves it in that one; the other is where the jaws have it now."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
  locations = commands.request_locations

  async def _run() -> None:
    with pytest.raises(ValueError, match="nowhere to move it"):
      await grippers.move_resource_to_xy_position()
    await grippers.pick_up_resource(plate)

    def last_move() -> Tuple[float, float, float]:
      move = commands.send_command.await_args.args[0]
      assert isinstance(move, PrepCmd.PrepMovePlate)
      c = move.plate_top_center
      return (c.x_position, c.y_position, c.z_position)

    # At the height the jaws are at now, 144.6, with the plate's top 5 mm above them.
    await grippers.move_resource_to_xy_position(x=150.0)
    assert last_move() == pytest.approx((150.0, (84.47 + 4.0) / 2, 149.6))
    await grippers.move_resource_to_xy_position(y=200.0)
    assert last_move() == pytest.approx((62.55, 200.0, 149.6))
    await grippers.move_resource_to_xy_position(x=150.0, y=200.0)
    assert last_move() == pytest.approx((150.0, 200.0, 149.6))
    # Asked every time: the height is never taken from what was remembered. The pick-up asks once
    # too, for the height it rises back to.
    assert locations.await_count == 4

  asyncio.run(_run())


def test_a_z_acceleration_holds_from_the_grip_until_it_is_let_go():
  """The empty approach and the empty raise after letting go run at the pipettes' default."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
  commands.move_to_safe_z.side_effect = lambda *args, **kwargs: commands.order.append("safe_z")
  commands.move_tool_bottom_to_z_positions.side_effect = lambda *args, **kwargs: (
    commands.order.append("jaws_z")
  )

  async def _run() -> None:
    await grippers.pick_up_resource(plate, z_acceleration=100.0)
    await grippers.return_resource(z_acceleration=50.0)
    await grippers.pick_up_resource(plate)
    assert commands.order == [
      "safe_z",  # up, empty
      "move_to_xy_positions",  # over it, empty
      "z_acceleration=100.0",
      "PrepPickUpPlate",  # gripped
      "jaws_z",  # lifted
      "z_acceleration restored",
      "z_acceleration=50.0",
      "safe_z",
      "PrepMovePlate",
      "jaws_z",  # lowered
      "PrepDropPlate",  # let go
      "z_acceleration restored",
      "safe_z",  # up, empty
      "safe_z",
      "move_to_xy_positions",
      "z_acceleration=150.0",  # named none: default_z_acceleration_with_resource_held
      "PrepPickUpPlate",
      "jaws_z",
      "z_acceleration restored",
    ]

  asyncio.run(_run())


def test_a_z_speed_is_how_fast_a_held_plate_moves_in_z():
  """The raise after the grip is at it; the let-go is left the last millimetre. The way down, empty,
  is the firmware's."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    grippers = p.core_grippers
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    spot = plate.parent
    await grippers.pick_up_tools()
    travel_z = (await grippers._pipettes.request_locations())[0].z
    captured = _record_send(p)

    def sent() -> List[Tuple[str, Any]]:
      out: List[Tuple[str, Any]] = []
      for c in captured:
        if isinstance(c, PrepCmd.PrepMoveZAbsolute):
          out.append(("z", (round(c.channels[0].z_position, 2), c.velocity)))
        elif isinstance(c, (PrepCmd.PrepPickUpPlate, PrepCmd.PrepDropPlate)):
          out.append((type(c).__name__, None))
      return out

    grip = grippers._compute_pickup_location(plate, Coordinate.zero(), 5.0).z
    await grippers.pick_up_resource(plate, z_speed=20.0)
    assert sent() == [("PrepPickUpPlate", None), ("z", (round(travel_z, 2), 20.0))]
    captured.clear()
    await grippers.return_resource(z_speed=20.0)
    assert sent() == [("z", (round(grip + FIRMWARE_Z_LEG, 2), 20.0)), ("PrepDropPlate", None)]
    assert plate.parent is spot
    await p.stop()

  asyncio.run(_run())


def test_only_moves_with_a_resource_held_set_the_z_drives_and_they_go_back_to_what_setup_read():
  """The tools alone run at the pipettes' default; a held resource at its own, then back."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    assert p.core_grippers is not None
    grippers = p.core_grippers
    assert grippers._pipettes.default_z_acceleration == 800.0  # read at setup
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    captured = _record_send(p)

    def accelerations() -> List[float]:
      return [c.value for c in captured if isinstance(c, PrepCmd.PrepZDriveSetAcceleration)]

    held = grippers.default_z_acceleration_with_resource_held
    await grippers.pick_up_tools()
    assert accelerations() == []
    await grippers.pick_up_resource(plate)
    assert accelerations() == [held, held, 800.0, 800.0]
    captured.clear()
    await grippers.return_resource()
    assert accelerations() == [held, held, 800.0, 800.0]
    captured.clear()
    await grippers.return_tools()
    assert accelerations() == []
    await p.stop()

  asyncio.run(_run())


def test_a_coordinate_puts_it_down_exactly_as_the_spot_it_names_would():
  """Dropped at a spot's centre-bottom, the device is sent what dropping into the spot sends."""

  async def drop(by_coordinate: Optional[Coordinate]) -> Tuple[Any, Any, Any, Any]:
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
    await grippers.pick_up_resource(plate)
    if by_coordinate is None:
      await grippers.drop_resource(deck[4])
    else:
      await grippers.drop_resource(by_coordinate)
    sent = [c.args[0] for c in commands.send_command.await_args_list]
    (release,) = [c for c in sent if isinstance(c, PrepCmd.PrepDropPlate)]
    carry = [c for c in sent if isinstance(c, PrepCmd.PrepMovePlate)][-1]
    return deck, plate, release, carry

  async def _run() -> None:
    deck_a, into_spot, release_a, carry_a = await drop(None)
    ccb = into_spot.get_location_wrt(deck_a, "c", "c", "b")
    deck_b, at_point, release_b, carry_b = await drop(ccb)
    for a, b in ((release_a, release_b), (carry_a, carry_b)):
      top_a, top_b = a.plate_top_center, b.plate_top_center
      assert (top_b.x_position, top_b.y_position, top_b.z_position) == pytest.approx(
        (top_a.x_position, top_a.y_position, top_a.z_position)
      )
    # In the tree it joins the deck, with its centre-bottom where it was told.
    assert at_point.parent is deck_b
    assert at_point.get_location_wrt(deck_b, "c", "c", "b") == pytest.approx(ccb)

  asyncio.run(_run())


def test_the_grippers_ride_the_two_front_most_channels_and_move_the_rest_aside():
  """channels[-2] carries the back tool and channels[-1] the front one, however many there are."""
  deck = PrepDeck(with_core_grippers=True)
  plate = deck[4] = cor_axy_96_wellplate_500uL_Ub("plate")
  grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
  pipettes: Any = grippers._driver.pipettes
  pipettes.num_channels = 4
  pipettes.request_locations.return_value = [
    Coordinate(62.55, 300.0, 144.6),
    Coordinate(62.55, 200.0, 144.6),
    Coordinate(62.55, 84.47, 144.6),
    Coordinate(62.55, 4.0, 144.6),
  ]

  async def _run() -> None:
    assert (grippers._back_channel, grippers._front_channel) == (2, 3)
    await grippers.move_to_y_positions([150.0, 60.0])
    assert pipettes.move_to_y_positions.await_args.args[0] == {2: 150.0, 3: 60.0}
    await grippers.pick_up_resource(plate)
    approach = commands.move_to_xy_positions.await_args
    assert set(approach.args[1]) == {2, 3} and approach.kwargs["make_space"] is True
    await grippers.move_resource_to_xy_position(y=100.0)
    moved = [
      c
      for c in commands.send_command.await_args_list
      if type(c.args[0]).__name__ == "PrepMovePlate"
    ]
    top = moved[-1].args[0].plate_top_center
    assert grippers._plate_top_z_offset is not None
    assert (top.x_position, top.z_position) == pytest.approx(
      (62.55, 144.6 + grippers._plate_top_z_offset)
    )

  asyncio.run(_run())


def test_every_firmware_move_records_where_the_channels_stopped_even_when_refused():
  """The model follows the device: after each command that moves the channels, worked or not."""

  async def run(kind: type, refused: bool) -> List[str]:
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    grippers, commands = _make_grippers(deck, stub_pick_and_drop=False)
    pipettes: Any = grippers._driver.pipettes
    pipettes._resolve_traverse_height = lambda: 167.5
    pipettes._record_channel_bounds = AsyncMock()
    pipettes._record_where_they_stopped.side_effect = lambda: commands.order.append("record")
    sent = commands.send_command.side_effect

    async def send(command, **kwargs):
      sent(command, **kwargs)
      if refused and isinstance(command, kind):
        raise RuntimeError("refused")
      if isinstance(command, PrepCmd.PrepGetPlateHeld):
        return PrepCmd.PrepGetPlateHeld.Response(value=False)

    commands.send_command.side_effect = send
    steps = {
      PrepCmd.PrepPickUpTool: lambda: grippers.pick_up_tools_at_location(100.0, 50.0, 60.0, 90.0),
      PrepCmd.PrepDropTool: lambda: grippers.drop_tools(move_to_safe_z_first=False),
      PrepCmd.PrepPickUpPlate: lambda: grippers.pick_up_resource(plate),
      PrepCmd.PrepMovePlate: lambda: grippers.move_resource_to_xy_position(x=150.0),
      PrepCmd.PrepDropPlate: lambda: grippers.return_resource(),
      PrepCmd.PrepReleasePlate: lambda: grippers.release_plate(),
    }
    if kind in (PrepCmd.PrepMovePlate, PrepCmd.PrepDropPlate, PrepCmd.PrepReleasePlate):
      await grippers.pick_up_resource(plate)
      commands.order.clear()
    try:
      await steps[kind]()
    except RuntimeError as e:
      assert refused and str(e) == "refused"
    order: List[str] = commands.order
    return order

  for kind in (
    PrepCmd.PrepPickUpTool,
    PrepCmd.PrepDropTool,
    PrepCmd.PrepPickUpPlate,
    PrepCmd.PrepMovePlate,
    PrepCmd.PrepDropPlate,
    PrepCmd.PrepReleasePlate,
  ):
    for refused in (False, True):
      order = asyncio.run(run(kind, refused))
      at = order.index(kind.__name__)
      assert order[at + 1] == "record", (kind.__name__, refused, order)


def test_with_the_tools_on_z_is_at_their_jaws_and_the_shafts_stay_at_z_safety():
  """As the device reports it: Z safety reads 144.6 with the tools on, 167.5 without."""

  async def _run():
    p = PrepSimulationDriver(deck=PrepDeck(with_core_grippers=True))
    await p.setup()
    assert p.core_grippers is not None and p.pipettes is not None
    pipettes = p.pipettes

    def shaft_end_z(channel: int) -> float:
      resource = pipettes.resources[channel]
      assert pipettes.deck is not None
      return resource.get_location_wrt(pipettes.deck).z + pipettes._reference_anchor(resource).z

    await p.core_grippers.pick_up_tools()
    assert [c.z for c in await pipettes.request_locations()] == pytest.approx([144.6, 144.6])
    assert [shaft_end_z(0), shaft_end_z(1)] == pytest.approx([167.5, 167.5])
    await p.core_grippers.return_tools()
    await pipettes.move_to_safe_z()
    assert [c.z for c in await pipettes.request_locations()] == pytest.approx([167.5, 167.5])
    assert [shaft_end_z(0), shaft_end_z(1)] == pytest.approx([167.5, 167.5])
    await p.stop()

  asyncio.run(_run())


def test_the_simulator_moves_the_grippers_where_the_device_did():
  """Tools on, a grip, a carry, a return, tools home: each ends where PRPAA1087 reported it.

  To 0.01 mm, the precision positions are read to, as the device reports them.
  """

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    grippers, pipettes = p.core_grippers, p.pipettes
    assert grippers is not None and pipettes is not None

    async def jaws() -> Tuple[float, float, float, float]:
      back, front = (await pipettes.request_locations())[:2]
      assert back.x == pytest.approx(front.x) and back.z == pytest.approx(front.z)
      return back.x, back.y, front.y, back.z

    width = plate.get_absolute_size_y()
    centre = plate.get_absolute_location(x="c", y="c")
    closed = width + JAW_OPEN_EXTRA - 2 * 4.5  # grip_distance: clearance 2.5 + squeeze 2.0
    opened = closed + 2 * (2.5 + 4.5)  # a drop opens by its y_clearance and the grip distance

    await grippers.pick_up_tools()
    assert await jaws() == pytest.approx(
      (290.0, 275.5, 257.5, 144.6), abs=0.01
    )  # at the holder, Z safety
    await grippers.pick_up_resource(plate)
    x, back_y, front_y, z = await jaws()
    assert (x, back_y - front_y, (back_y + front_y) / 2, z) == pytest.approx(
      (centre.x, closed, centre.y, 144.6), abs=0.01
    )
    await grippers.move_resource_to_xy_position(x=80.0, y=230.0)
    assert await jaws() == pytest.approx(
      (80.0, 230.0 + closed / 2, 230.0 - closed / 2, 144.6), abs=0.01
    )
    await grippers.return_resource()
    x, back_y, front_y, z = await jaws()
    assert (x, back_y - front_y, (back_y + front_y) / 2, z) == pytest.approx(
      (centre.x, opened, centre.y, 144.6), abs=0.01
    )
    await grippers.return_tools()
    assert await jaws() == pytest.approx(
      (290.0, 275.5, 257.5, 167.5), abs=0.01
    )  # home, shafts at traverse
    await p.stop()

  asyncio.run(_run())


def test_a_held_plate_rides_on_the_front_tool_and_lands_where_it_is_put():
  """Gripped, it hangs from the front tool where the jaws hold it; put down, it is in its place."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    spot, taken_at = plate.parent, plate.location
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    grippers, pipettes = p.core_grippers, p.pipettes
    assert grippers is not None and pipettes is not None
    await grippers.pick_up_tools()
    front_tool = grippers._front_tool()

    async def jaws() -> Tuple[float, float, float]:
      back, front = (await pipettes.request_locations())[:2]
      return front.x, (back.y + front.y) / 2, front.z

    def plate_centre_and_top() -> Tuple[float, float, float]:
      c = plate.get_absolute_location(x="c", y="c", z="t")
      return c.x, c.y, c.z

    await grippers.pick_up_resource(plate)
    from_top = grippers._pickup_distance_from_top
    assert plate.parent is front_tool and from_top is not None
    x, y, z = await jaws()
    assert plate_centre_and_top() == pytest.approx((x, y, z + from_top), abs=0.01)

    await grippers.move_resource_to_xy_position(x=80.0, y=280.0)
    assert plate_centre_and_top() == pytest.approx((80.0, 280.0, z + from_top), abs=0.01)

    await grippers.return_resource()
    assert plate.parent is spot and plate.location == taken_at

    await grippers.pick_up_resource(plate)
    held_at = plate.get_location_wrt(deck)
    await grippers.release_plate()
    assert plate.parent is deck and plate.location == pytest.approx(held_at)
    await p.stop()

  asyncio.run(_run())


def test_while_a_plate_is_held_the_simulator_refuses_the_tools_home_and_initializing_as_the_device():
  """The plate-held latch: 0x0F04 for PrepDropTool, 0x0F0A (and 0x0F04) for PrepInitialize."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    grippers = p.core_grippers
    assert grippers is not None
    await grippers.pick_up_tools()
    await grippers.pick_up_resource(plate)
    front_tool = grippers._front_tool()

    with pytest.raises(RuntimeError, match="the grippers hold plate"):
      await grippers.return_tools()  # refused before anything is sent
    with pytest.raises(HoiError, match="0x0F04"):
      await p.send_command(PrepCmd.PrepDropTool())
    assert grippers.tools_mounted and plate.parent is front_tool  # nothing let go of in the model
    initialize = PrepCmd.PrepInitialize(
      smart=True,
      tip_drop_params=PrepCmd.InitTipDropParameters(
        default_values=True, x_position=287.0, rolloff_distance=3, channel_parameters=[]
      ),
    )
    with pytest.raises(HoiError, match="0x0F0A"):
      await p.send_command(initialize)

    await grippers.return_resource()
    await grippers.return_tools()  # accepted once the plate is down
    await p.send_command(initialize)
    await p.stop()

  asyncio.run(_run())


def test_the_plate_is_taken_at_the_grip_and_put_down_at_the_let_go_before_the_jaws_rise():
  """So the model lifts it with the jaws, and the jaws rise empty after letting go of it."""

  async def _run():
    deck = PrepDeck(with_core_grippers=True)
    plate = deck[0] = azenta_96_wellplate_200uL_Vb_4titudeframestar(name="plate")
    spot = plate.parent
    at = plate.get_absolute_location()
    stood_at = (at.x, at.y, at.z)
    p = PrepSimulationDriver(deck=deck)
    await p.setup()
    grippers = p.core_grippers
    assert grippers is not None
    await grippers.pick_up_tools()
    front_tool = grippers._front_tool()
    at_each_raise: List[Tuple[Optional[str], Tuple[float, float, float]]] = []
    raise_ = grippers._raise_to_traverse

    async def recording(minimum_traverse_height_end: Optional[float]) -> None:
      parent = None if plate.parent is None else plate.parent.name
      where = plate.get_absolute_location()
      at_each_raise.append((parent, (where.x, where.y, where.z)))
      await raise_(minimum_traverse_height_end)

    move_jaws = grippers._move_jaws_to_z

    async def recording_jaws(z: float, speed: float) -> None:
      parent = None if plate.parent is None else plate.parent.name
      where = plate.get_absolute_location()
      at_each_raise.append((parent, (where.x, where.y, where.z)))
      await move_jaws(z, speed)

    grippers._raise_to_traverse = recording  # type: ignore[method-assign]
    grippers._move_jaws_to_z = recording_jaws  # type: ignore[method-assign]
    assert spot is not None and front_tool is not None

    await grippers.pick_up_resource(plate)
    assert [parent for parent, _ in at_each_raise] == [spot.name, front_tool.name]
    assert at_each_raise[1][1] == pytest.approx(stood_at, abs=0.01)  # taken where it stood

    at_each_raise.clear()
    await grippers.return_resource()
    # Carried up, lowered to the let-go, and the jaws up empty.
    assert [parent for parent, _ in at_each_raise] == [front_tool.name, front_tool.name, spot.name]
    assert at_each_raise[2][1] == pytest.approx(stood_at, abs=0.01)  # put down where it stood
    await p.stop()

  asyncio.run(_run())
