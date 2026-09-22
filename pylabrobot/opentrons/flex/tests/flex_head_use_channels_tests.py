"""Tests for the unified ``FlexHead8.aspirate``/``dispense`` (per-call nozzle config).

Collapses the three per-config methods -- ``aspirate`` (column), ``aspirate_single``
(one well), ``aspirate_container`` (trough) -- into ONE
``aspirate(target, volume, *, use_channels=...)`` and the same for ``dispense``.
The target *type* drives addressing; ``use_channels`` names/validates the active
nozzles for that call. The layout itself is fixed at pickup (the engine refuses a
nozzle reconfiguration while tips are attached), so a liquid op emits NO
``configureNozzleLayout`` -- only the ``aspirate``/``dispense`` command.

Wire payloads are inspected through the injected ``AsyncMock``.
"""

import unittest
from typing import List, Tuple
from unittest.mock import AsyncMock, _Call

from pylabrobot.opentrons.flex.errors import OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_head import FlexHead8
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.resources import (
  Container,
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


def _make_trough(name: str = "trough") -> Container:
  """A single-cavity reservoir (PLR ``Container``) mapped to a real Opentrons load name."""
  trough = Container(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=31.4,
    material_z_thickness=1.0,
    max_volume=195000.0,
  )
  return trough


async def _flex_head8(
  test_case: unittest.IsolatedAsyncioTestCase,
) -> Tuple[Flex, AsyncMock, FlexHead8]:
  api = make_api(pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")])
  flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
  test_case.addAsyncCleanup(flex.disconnect)
  await flex.setup()
  head = flex.left
  assert isinstance(head, FlexHead8)
  return flex, api, head


def _plate_on(flex: Flex, slot: str = "C2"):
  plate = cor_96_wellplate_360uL_Fb(name="plate")
  flex.deck.assign_child_at_slot(plate, slot)
  return plate


def _rack_on(flex: Flex, slot: str = "C1"):
  rack = flex_96_tiprack_50ul(name="rack")
  flex.deck.assign_child_at_slot(rack, slot)
  return rack


def _commands_of(api: AsyncMock, command_type: str) -> List[_Call]:
  return [c for c in api.submit_command.await_args_list if c.args[1] == command_type]


class TestUnifiedAspirateColumn(unittest.IsolatedAsyncioTestCase):
  """A ``Sequence[Well]`` target (a ``plate.column(c)``) aspirates that column
  with ONE ``aspirate`` anchored at its rearmost well, per-well trackers, and no
  ``configureNozzleLayout`` emitted at aspirate time."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_column_target_emits_one_anchored_aspirate_and_tracks_only_that_column(self):
    flex, api, head = await _flex_head8(self)
    rack = _rack_on(flex)
    plate = _plate_on(flex)
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)

    await head.pick_up_tips(rack, column=0)
    commands_before = api.submit_command.await_count

    await head.aspirate(plate.column(2), volume=50)

    aspirate_cmds = _commands_of(api, "aspirateInPlace")
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])

    # No configureNozzleLayout may be emitted by the aspirate itself: the
    # engine refuses a reconfiguration while tips are attached.
    new_cmds = api.submit_command.await_args_list[commands_before:]
    self.assertNotIn(
      "configureNozzleLayout",
      [c.args[1] for c in new_cmds],
    )

    wells = plate.get_all_items()
    column_2 = set(wells[16:24])
    for well in wells:
      expected = 50.0 if well in column_2 else 100.0
      self.assertAlmostEqual(well.tracker.volume, expected, msg=well.name)


class TestUnifiedAspirateSingleWell(unittest.IsolatedAsyncioTestCase):
  """A bare ``Well`` target aspirates it with the mounted single nozzle -- one
  ``aspirate`` at that well, its one tracker, no ``configureNozzleLayout``."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_bare_well_target_aspirates_with_single_nozzle(self):
    flex, api, head = await _flex_head8(self)
    rack = _rack_on(flex)
    plate = _plate_on(flex)
    target = plate.get_item("B3")
    target.tracker.set_volume(100.0)

    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")
    commands_before = api.submit_command.await_count

    await head.aspirate(target, volume=20)

    aspirate_cmds = _commands_of(api, "aspirateInPlace")
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])

    new_cmds = [c.args[1] for c in api.submit_command.await_args_list[commands_before:]]
    self.assertNotIn("configureNozzleLayout", new_cmds)

    self.assertAlmostEqual(target.tracker.volume, 80.0)
    for well in plate.get_all_items():
      if well is target:
        continue
      self.assertAlmostEqual(well.tracker.volume, 0.0, msg=well.name)


class TestUnifiedAspirateContainer(unittest.IsolatedAsyncioTestCase):
  """A ``Container`` (trough) target fans every mounted nozzle into the one
  cavity -- one ``aspirate`` at the container well, its single tracker staged
  with ``volume x active-channels``."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_container_target_stages_total_and_emits_one_aspirate(self):
    flex, api, head = await _flex_head8(self)
    rack = _rack_on(flex)
    trough = _make_trough()
    flex.deck.assign_child_at_slot(trough, "C2")
    trough.tracker.set_volume(1000.0)

    await head.pick_up_tips(rack, column=0)  # 8 tips, ALL layout
    commands_before = api.submit_command.await_count

    await head.aspirate(trough, volume=10)

    aspirate_cmds = _commands_of(api, "aspirateInPlace")
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])

    new_cmds = [c.args[1] for c in api.submit_command.await_args_list[commands_before:]]
    self.assertNotIn("configureNozzleLayout", new_cmds)

    # 8 channels x 10 uL = 80 uL removed from the one cavity.
    self.assertAlmostEqual(trough.tracker.volume, 920.0)


class TestUnifiedUseChannelsValidation(unittest.IsolatedAsyncioTestCase):
  """``use_channels`` names the active nozzles for the call and, since the
  layout is fixed at pickup, must match exactly the channels holding tips --
  a mismatch is refused before any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_use_channels_not_matching_mounted_tips_refuses_before_wire(self):
    rack = _rack_on(self.flex)
    plate = _plate_on(self.flex)
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)

    await self.head.pick_up_tips(rack, column=0)  # all 8 channels hold tips
    commands_before = self.api.submit_command.await_count

    # Only 3 channels named, but 8 are mounted: the fanned command cannot
    # actuate a subset, so this is refused.
    with self.assertRaises(OpentronsError):
      await self.head.aspirate(plate.column(2), volume=20, use_channels=[0, 1, 2])

    self.assertEqual(
      self.api.submit_command.await_count, commands_before, "no wire command may be sent"
    )

  async def test_use_channels_matching_all_mounted_is_accepted(self):
    rack = _rack_on(self.flex)
    plate = _plate_on(self.flex)
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)

    await self.head.pick_up_tips(rack, column=0)
    await self.head.aspirate(plate.column(2), volume=20, use_channels=list(range(8)))

    aspirate_cmds = _commands_of(self.api, "aspirateInPlace")
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])


class TestUnifiedPickUpTips(unittest.IsolatedAsyncioTestCase):
  """One ``pick_up_tips(target, *, use_channels)`` chooses the layout and emits the
  ``configureNozzleLayout`` -- pickup is where per-call nozzle configuration lives.
  A ``Sequence[TipSpot]`` (a ``rack.column(c)``) -> ALL; a single ``TipSpot`` -> SINGLE."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_column_of_spots_picks_up_eight_in_all_layout(self):
    rack = _rack_on(self.flex)

    await self.head.pick_up_tips(rack.column(0))

    pickup = _commands_of(self.api, "pickUpTip")
    self.assertEqual(len(pickup), 1)
    self.assertEqual(pickup[0].args[2]["wellName"], "A1")
    self.assertEqual(sum(1 for t in self.head.get_mounted_tips() if t is not None), 8)
    # A fresh head is already ALL, so no SINGLE reconfiguration is emitted.
    styles = [
      c.args[2]["configurationParams"]["style"]
      for c in _commands_of(self.api, "configureNozzleLayout")
    ]
    self.assertNotIn("SINGLE", styles)

  async def test_single_spot_with_use_channels_configures_single_primary_nozzle(self):
    rack = _rack_on(self.flex)

    # Channel 0 anchors the REAR nozzle, whose idle seven trail forward, so the
    # front row is the one it can take a single tip from on a full rack.
    await self.head.pick_up_tips(rack.get_item("H1"), use_channels=[0])

    configure = _commands_of(self.api, "configureNozzleLayout")
    cfg = configure[-1].args[2]["configurationParams"]
    self.assertEqual(cfg["style"], "SINGLE")
    self.assertEqual(cfg["primaryNozzle"], "A1")
    pickup = _commands_of(self.api, "pickUpTip")
    self.assertEqual(pickup[-1].args[2]["wellName"], "H1")
    tips = self.head.get_mounted_tips()
    self.assertIsNotNone(tips[0])
    self.assertEqual(sum(1 for t in tips if t is not None), 1)

  async def test_single_spot_use_channels_off_anchor_refuses(self):
    rack = _rack_on(self.flex)
    # An 8-channel Flex can single-anchor only on A1 (ch 0) or H1 (ch 7).
    with self.assertRaises((ValueError, OpentronsError)):
      await self.head.pick_up_tips(rack.get_item("A1"), use_channels=[3])


class TestUnifiedPartialPickUp(unittest.IsolatedAsyncioTestCase):
  """A contiguous partial ``use_channels`` from the front (H1) end emits the QUADRANT
  config Opentrons' own protocol_api produces (start=H1 -> primary=frontRight=H1,
  backLeft=rear-most active nozzle) and picks up that partial column."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_front_partial_emits_quadrant_and_picks_the_front_channels(self):
    rack = _rack_on(self.flex)

    # Front 4 nozzles E,F,G,H = channels 4..7; spots in channel order.
    await_spots = rack.column(0)[4:8]
    await self.head.pick_up_tips(await_spots, use_channels=[4, 5, 6, 7])

    cfg = _commands_of(self.api, "configureNozzleLayout")[-1].args[2]["configurationParams"]
    self.assertEqual(cfg["style"], "QUADRANT")
    self.assertEqual(cfg["primaryNozzle"], "H1")
    self.assertEqual(cfg["frontRightNozzle"], "H1")
    self.assertEqual(cfg["backLeftNozzle"], "E1")  # rear-most of the front-4

    pickup = _commands_of(self.api, "pickUpTip")
    self.assertEqual(pickup[-1].args[2]["wellName"], "H1")  # anchor = primary nozzle's well

    tips = self.head.get_mounted_tips()
    self.assertEqual([i for i, t in enumerate(tips) if t is not None], [4, 5, 6, 7])

  async def test_non_contiguous_partial_refuses(self):
    rack = _rack_on(self.flex)
    spots = [rack.get_item("A1"), rack.get_item("C1"), rack.get_item("E1")]
    with self.assertRaises((ValueError, OpentronsError)):
      await self.head.pick_up_tips(spots, use_channels=[0, 2, 4])


class TestSingleNozzleLiquidClearance(unittest.IsolatedAsyncioTestCase):
  """The single-nozzle aspirate/dispense refuses when the idle nozzles would
  overhang the adjacent slot's labware (the same guard pickup runs), and allows
  it when the trailing row is empty."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_single_aspirate_refuses_when_idle_nozzles_overhang_a_tip_rack(self):
    rack = flex_96_tiprack_50ul(name="rack")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    plate = _plate_on(self.flex, "D1")  # D1 front; C1 (a tip rack) is directly behind it
    plate.get_item("A1").tracker.set_volume(100.0)

    await self.head.pick_up_tips(rack.get_item("A1"), use_channels=[7])  # H1 single
    with self.assertRaises((ValueError, OpentronsError)):
      await self.head.aspirate(plate.get_item("A1"), volume=20)

    self.assertEqual(len(_commands_of(self.api, "aspirateInPlace")), 0, "no aspirate may be sent")

  async def test_single_aspirate_allowed_when_trailing_row_empty(self):
    rack = flex_96_tiprack_50ul(name="rack")
    self.flex.deck.assign_child_at_slot(rack, "D1")  # pick from D1 (behind it, C1, is empty)
    plate = _plate_on(self.flex, "B1")  # aspirate B1; behind it, A1, is empty
    plate.get_item("A1").tracker.set_volume(100.0)

    await self.head.pick_up_tips(rack.get_item("A1"), use_channels=[7])
    await self.head.aspirate(plate.get_item("A1"), volume=20)

    self.assertEqual(len(_commands_of(self.api, "aspirateInPlace")), 1)


if __name__ == "__main__":
  unittest.main()
