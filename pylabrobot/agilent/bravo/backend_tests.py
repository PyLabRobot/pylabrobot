"""Unit tests for :mod:`.backend`.

Drives :class:`AgilentBravoBackend` against
:class:`~.controllers.simulation.SimulationController` through a real
:class:`~.bravo.Bravo` and :class:`~.deck.resource.BravoDeck`. Two kinds of
assertion are used, per the two failure modes a golden-frame comparison
alone would miss: most tests inspect :class:`Bravo`'s own tracked state
(head mode, plate/tip selection, tip-on-head flag) directly, since that
state never reaches a controller call as a distinguishable value; a few
inspect the captured controller-call sequence directly, for values (flow
rate, distance from bottom) that only show up as a move target.
"""

from __future__ import annotations

import unittest
from typing import List, Tuple

from pylabrobot.legacy.liquid_handling.liquid_handler import LiquidHandler
from pylabrobot.legacy.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationPlate,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.resources import Trash, cor_96_wellplate_360uL_Fb, opentrons_96_tiprack_300ul
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack

from .backend import AgilentBravoBackend
from .block import HeadBlock, HeadBlockError
from .bravo import Bravo
from .config import BravoMachineConfig
from .deck.resource import BravoDeck
from .deck.teachpoints import Teachpoints
from .state_machine.golden_frame_support import RecordingSimulationController
from .types import HeadType


class _GripperlessSimulationController(RecordingSimulationController):
  """A simulated controller for the gripperless Bravo SRT.

  :class:`~.controllers.simulation.SimulationController` always reports
  ``has_gripper = True`` (the generic default from
  :class:`~.controllers.base.BravoController`); the real
  :class:`~.controllers.agile_srt.AgileSrtController` overrides both class
  attributes the same way this test double does.
  """

  has_gripper = False
  model_name = "Bravo SRT"


def _make_tip(maximal_volume: float = 30.0) -> Tip:
  """Build a disposable tip fixture."""
  return Tip(
    has_filter=False,
    total_tip_length=50.0,
    maximal_volume=maximal_volume,
    fitting_depth=8.0,
    name="test_tip",
  )


def _new_backend(
  *,
  head_type: HeadType = "96_d_70",
  gripper: bool = True,
  controller_cls=RecordingSimulationController,
) -> Tuple[AgilentBravoBackend, Bravo, BravoDeck, RecordingSimulationController]:
  """Build an AgilentBravoBackend wired up to a real Bravo and BravoDeck."""
  # "unknown" has no default teachpoints of its own (nothing has been
  # detected to build them from); build the deck/controller against a real
  # head's teachpoints and leave only config.head.head_type -- what
  # Bravo.head_type actually reflects -- set to "unknown".
  teachpoint_head_type = head_type if head_type != "unknown" else "96_d_70"
  ctrl = controller_cls(head_type=teachpoint_head_type)
  teachpoints = Teachpoints()
  teachpoints.set_default_teachpoints(teachpoint_head_type)
  config = BravoMachineConfig()
  config.head.head_type = head_type
  config.head.teach_tip_length_mm = 26.1
  if not gripper:
    config.axes = {k: v for k, v in config.axes.items() if k not in ("g", "zg")}
  deck = BravoDeck(head_type=teachpoint_head_type, teachpoints=teachpoints)
  bravo = Bravo(ctrl, config=config, deck=deck)
  backend = AgilentBravoBackend(bravo)
  backend.set_deck(deck)
  return backend, bravo, deck, ctrl


async def _mount_tips(backend: AgilentBravoBackend, deck: BravoDeck, rack_site: int = 4) -> TipRack:
  """Pick up the whole head from the rack at *rack_site*, assigning one if empty."""
  existing = deck.resource_at_site(rack_site)
  if existing is None:
    rack: TipRack = opentrons_96_tiprack_300ul(name=f"rack_{rack_site}")
    deck.assign_child_at_site(rack, rack_site)
  else:
    assert isinstance(existing, TipRack)
    rack = existing
  ops = [
    Pickup(resource=spot, offset=Coordinate.zero(), tip=_make_tip())
    for spot in rack.get_all_items()
  ]
  await backend.pick_up_tips(ops, use_channels=list(range(96)))
  return rack


class NumChannelsTests(unittest.IsolatedAsyncioTestCase):
  """num_channels is rows x columns of the installed head."""

  async def test_96_d_70_reports_96_channels(self):
    backend, _, _, _ = _new_backend(head_type="96_d_70")
    self.assertEqual(backend.num_channels, 96)

  async def test_8_d_lt_reports_8_channels(self):
    backend, _, _, _ = _new_backend(head_type="8_d_lt")
    self.assertEqual(backend.num_channels, 8)

  async def test_384_d_70_reports_384_channels(self):
    backend, _, _, _ = _new_backend(head_type="384_d_70")
    self.assertEqual(backend.num_channels, 384)


class NumArmsTests(unittest.IsolatedAsyncioTestCase):
  """num_arms/head96_installed derive from the installed model, not the base class default.

  LiquidHandlerBackend's own defaults (num_arms=0, head96_installed=False)
  are exactly wrong for a gripper-equipped, 96-channel Bravo: left
  unoverridden, LiquidHandler.setup() builds an empty _resource_pickups
  map and an empty head96 tracker dict, so every gripper call and every
  96-head call fails at the LiquidHandler layer before this backend is
  ever reached -- regardless of how complete pick_up_resource or
  aspirate96 are. The tests below drive a real LiquidHandler, not just
  the backend directly, since that is the only way this class of gap
  shows up at all.
  """

  async def test_gripper_model_reports_one_arm(self):
    backend, _, _, _ = _new_backend(gripper=True)
    self.assertEqual(backend.num_arms, 1)

  async def test_srt_reports_zero_arms(self):
    backend, _, _, _ = _new_backend(gripper=True, controller_cls=_GripperlessSimulationController)
    self.assertEqual(backend.num_arms, 0)

  async def test_96_channel_head_reports_head96_installed(self):
    backend, _, _, _ = _new_backend(head_type="96_d_70")
    self.assertTrue(backend.head96_installed)

  async def test_8_channel_head_reports_head96_not_installed(self):
    backend, _, _, _ = _new_backend(head_type="8_d_lt")
    self.assertFalse(backend.head96_installed)

  async def test_move_plate_round_trips_through_a_real_liquid_handler(self):
    # The end-to-end path a PyLabRobot user actually drives: LiquidHandler
    # -> AgilentBravoBackend -> Bravo. A test that only calls
    # backend.pick_up_resource/move_picked_up_resource/drop_resource
    # directly cannot see a num_arms gap, because LiquidHandler is the
    # layer that reads it.
    backend, bravo, deck, _ = _new_backend(gripper=True)
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    await lh.move_plate(plate, deck._site_holders[2])
    self.assertEqual(deck.site_for_resource(plate), 2)
    self.assertIsNone(bravo.get_labware(1))
    self.assertIsNotNone(bravo.get_labware(2))

  async def test_move_plate_on_the_srt_raises_pylabrobots_own_no_arm_error(self):
    backend, bravo, deck, _ = _new_backend(
      gripper=True, controller_cls=_GripperlessSimulationController
    )
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    with self.assertRaises(RuntimeError) as ctx:
      await lh.move_plate(plate, deck._site_holders[2])
    # This is PyLabRobot's own LiquidHandler-level error (raised before
    # the backend's pick_up_resource -- and its own SRT rejection -- is
    # ever reached), not AgilentBravoBackend._require_gripper's.
    self.assertIn("No robotic arm is installed", str(ctx.exception))


class SetupStopTests(unittest.IsolatedAsyncioTestCase):
  """setup/stop delegate to Bravo and log the unverified-hardware warning."""

  async def test_setup_logs_the_unverified_hardware_warning(self):
    backend, _, _, _ = _new_backend()
    with self.assertLogs("pylabrobot.agilent.bravo.backend", level="WARNING") as ctx:
      await backend.setup()
    self.assertTrue(any("unverified" in message for message in ctx.output))
    self.assertTrue(any("discuss.pylabrobot.org" in message for message in ctx.output))
    self.assertTrue(backend.setup_finished)

  async def test_stop_clears_setup_finished(self):
    backend, _, _, _ = _new_backend()
    await backend.setup()
    await backend.stop()
    self.assertFalse(backend.setup_finished)

  async def test_setup_without_a_deck_raises(self):
    ctrl = RecordingSimulationController()
    bravo = Bravo(ctrl, config=BravoMachineConfig(), deck=None)
    backend = AgilentBravoBackend(bravo)
    with self.assertRaises(AssertionError):
      await backend.setup()


class DeckRequirementTests(unittest.IsolatedAsyncioTestCase):
  """Operations require a BravoDeck, not a generic PyLabRobot Deck."""

  async def test_non_bravo_deck_is_rejected(self):
    from pylabrobot.resources.deck import Deck

    ctrl = RecordingSimulationController()
    bravo = Bravo(ctrl, config=BravoMachineConfig(), deck=None)
    backend = AgilentBravoBackend(bravo)
    backend.set_deck(Deck(size_x=1, size_y=1, size_z=1, name="generic"))
    plate = cor_96_wellplate_360uL_Fb(name="p1")
    op = SingleChannelAspiration(
      resource=plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=_make_tip(),
      volume=10.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.aspirate([op], use_channels=[0])
    self.assertIn("BravoDeck", str(ctx.exception))


class PickUpDropTipsHeadModeTests(unittest.IsolatedAsyncioTestCase):
  """pick_up_tips/drop_tips derive the head block and set the matching mode."""

  async def test_single_op_produces_single_barrel_mode(self):
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    op = Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip())
    await backend.pick_up_tips([op], use_channels=[0])
    self.assertEqual(bravo.head_mode.subset_type, "single_barrel")
    self.assertTrue(bravo._tips_on_head)

  async def test_full_column_produces_column_mode(self):
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    spots = [rack.get_item(f"{row}1") for row in "ABCDEFGH"]
    ops = [Pickup(resource=spot, offset=Coordinate.zero(), tip=_make_tip()) for spot in spots]
    await backend.pick_up_tips(ops, use_channels=list(range(8)))
    self.assertEqual(bravo.head_mode.subset_type, "column")
    self.assertEqual(bravo.head_mode.column_count, 1)

  async def test_full_row_produces_row_mode(self):
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    spots = [rack.get_item(f"A{col}") for col in range(1, 13)]
    ops = [Pickup(resource=spot, offset=Coordinate.zero(), tip=_make_tip()) for spot in spots]
    await backend.pick_up_tips(ops, use_channels=list(range(12)))
    self.assertEqual(bravo.head_mode.subset_type, "row")
    self.assertEqual(bravo.head_mode.row_count, 1)

  async def test_whole_rack_produces_all_barrels_mode(self):
    backend, bravo, deck, _ = await self._backend_with_rack()
    await _mount_tips(backend, deck)
    self.assertEqual(bravo.head_mode.subset_type, "all_barrels")

  async def test_drop_tips_returns_the_same_block_to_the_rack(self):
    # A full column, not a single barrel: Bravo's own return-legality
    # tracking (see bravo.py's _is_legal_tipbox_anchor "return" branch)
    # walks a full row/column band back in from the edge it was consumed
    # from, so only a return that spans a full row or column round-trips
    # cleanly against a box that started completely full.
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    spots = [rack.get_item(f"{row}1") for row in "ABCDEFGH"]
    ops = [Pickup(resource=spot, offset=Coordinate.zero(), tip=_make_tip()) for spot in spots]
    await backend.pick_up_tips(ops, use_channels=list(range(8)))
    drops = [Drop(resource=spot, offset=Coordinate.zero(), tip=_make_tip()) for spot in spots]
    await backend.drop_tips(drops, use_channels=list(range(8)))
    self.assertFalse(bravo._tips_on_head)

  async def test_single_barrel_return_to_a_still_full_box_is_rejected_clearly(self):
    # A genuine instrument/tracking constraint, not a backend defect (see
    # _apply_tip_return_anchor's docstring): a single-barrel return is
    # only legal once the rest of its row has also been vacated. Picking
    # then immediately returning one tip out of an otherwise-full box
    # hits exactly that constraint, and should say so rather than
    # surfacing Bravo's own lower-level "not accessible for return".
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    op = Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip())
    await backend.pick_up_tips([op], use_channels=[0])
    drop = Drop(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip())
    with self.assertRaises(RuntimeError) as ctx:
      await backend.drop_tips([drop], use_channels=[0])
    message = str(ctx.exception)
    self.assertIn("does not span the tip box's full width or height", message)
    self.assertIn("Bravo's own error", message)
    # Tips are still tracked as mounted: the rejected return must not
    # have silently cleared Bravo's own tip-on-head state.
    self.assertTrue(bravo._tips_on_head)

  async def test_drop_tips_to_a_shared_trash_ejects_whatever_is_mounted(self):
    # End-to-end through the public path only: BravoDeck.assign_child_at_site
    # plus a real pylabrobot.resources.trash.Trash, with no pre-seeded
    # Labware. This is the path that caught deck/resource.py's Trash ->
    # "plate" mapping gap (fixed by mapping Trash to "tip_trash" there); a
    # white-box test that pre-seeds the tip_trash Labware cannot see that
    # class of bug, because it never asks BravoDeck to do the translation.
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    op = Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip())
    await backend.pick_up_tips([op], use_channels=[0])
    # Use a site far from the rack (site 9, the opposite corner of the 3x3
    # grid from site 4) so the head's neighbor-clearance check has nothing
    # tall nearby to trip on -- unrelated to what this test is pinning.
    trash = Trash(name="trash1", size_x=127.0, size_y=85.0, size_z=10.0)
    deck.assign_child_at_site(trash, 9)
    drop = Drop(resource=trash, offset=Coordinate.zero(), tip=_make_tip())
    await backend.drop_tips([drop], use_channels=[0])
    self.assertFalse(bravo._tips_on_head)

  async def test_mixed_tip_spot_and_trash_drop_is_rejected(self):
    backend, bravo, deck, _ = await self._backend_with_rack()
    rack = deck.resource_at_site(4)
    trash = Trash(name="trash2", size_x=127.0, size_y=85.0, size_z=10.0)
    deck.assign_child_at_site(trash, 5)
    drops = [
      Drop(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip()),
      Drop(resource=trash, offset=Coordinate.zero(), tip=_make_tip()),
    ]
    with self.assertRaises(RuntimeError) as ctx:
      await backend.drop_tips(drops, use_channels=[0, 1])
    self.assertIn("same kind of resource", str(ctx.exception))

  async def _backend_with_rack(self):
    backend, bravo, deck, ctrl = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    return backend, bravo, deck, ctrl


class PickUpTipsRejectionTests(unittest.IsolatedAsyncioTestCase):
  """pick_up_tips: contiguity and unassigned-labware rejections."""

  async def test_non_contiguous_selection_is_rejected_with_a_clear_message(self):
    backend, _, deck, _ = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    ops = [
      Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip()),
      Pickup(resource=rack.get_item("B2"), offset=Coordinate.zero(), tip=_make_tip()),
    ]
    # HeadBlockError (raised by block.head_block_for_identifiers) is a
    # ValueError, not a RuntimeError: it is not wrapped, since it already
    # carries a clear, specific message.
    with self.assertRaises(HeadBlockError) as ctx:
      await backend.pick_up_tips(ops, use_channels=[0, 1])
    message = str(ctx.exception)
    self.assertIn("do not form a contiguous rectangular block", message)

  async def test_unassigned_rack_is_rejected_by_name(self):
    backend, _, _deck, _ = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="unassigned_rack")
    ops = [Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip())]
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_tips(ops, use_channels=[0])
    self.assertIn("unassigned_rack", str(ctx.exception))


class OversizedBlockRejectionTests(unittest.IsolatedAsyncioTestCase):
  """A block that does not fit the installed head is rejected before dispatch."""

  async def test_two_columns_on_a_single_column_head_is_rejected(self):
    backend, bravo, deck, _ = _new_backend(head_type="8_d_lt")
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    ops = [
      Pickup(resource=rack.get_item("A1"), offset=Coordinate.zero(), tip=_make_tip()),
      Pickup(resource=rack.get_item("A2"), offset=Coordinate.zero(), tip=_make_tip()),
    ]
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_tips(ops, use_channels=[0, 1])
    message = str(ctx.exception)
    self.assertIn("does not fit the installed", message)
    self.assertIn("8x1", message)

  async def test_require_block_fits_directly_rejects_an_oversized_block(self):
    # Calls _require_block_fits in isolation from every other operation
    # precondition (deck assignment, tip catalogue, teachpoints): the
    # mutation-testing report flagged that the end-to-end test above can
    # be "killed" by an unrelated downstream error (an unconfigured tip
    # length for the 8_d_lt head, reached only once the fits check is
    # disabled) rather than by this exact assertion. This test can only
    # fail here, on this message, since nothing else runs.
    backend, _, _, _ = _new_backend(head_type="8_d_lt")
    block = HeadBlock(row_start=0, row_stop=1, col_start=0, col_stop=2)
    with self.assertRaises(RuntimeError) as ctx:
      backend._require_block_fits(block)
    message = str(ctx.exception)
    self.assertIn("does not fit the installed", message)
    self.assertIn("8x1", message)

  async def test_require_block_fits_accepts_a_block_that_fits(self):
    backend, _, _, _ = _new_backend(head_type="8_d_lt")
    block = HeadBlock(row_start=0, row_stop=8, col_start=0, col_stop=1)
    backend._require_block_fits(block)  # does not raise


class AspirateDispenseTests(unittest.IsolatedAsyncioTestCase):
  """aspirate/dispense: block/mode derivation and value uniformity."""

  async def _backend_ready_to_pipette(self):
    backend, bravo, deck, ctrl = _new_backend()
    plate = cor_96_wellplate_360uL_Fb(name="plate5")
    deck.assign_child_at_site(plate, 5)
    await backend.setup()
    await _mount_tips(backend, deck)
    ctrl.calls.clear()
    return backend, bravo, deck, ctrl, plate

  async def test_aspirate_on_one_well_sets_single_barrel_mode_and_its_anchor(self):
    backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
    op = SingleChannelAspiration(
      resource=plate.get_item("C4"),
      offset=Coordinate.zero(),
      tip=_make_tip(),
      volume=25.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    await backend.aspirate([op], use_channels=[0])
    self.assertEqual(bravo.head_mode.subset_type, "single_barrel")
    self.assertEqual((bravo._plate_selection[5].row, bravo._plate_selection[5].col), (2, 3))
    w_moves = [
      m for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"] if m["axis"] == "w"
    ]
    self.assertTrue(any(abs(m["position"] - 25.0) < 1e-6 for m in w_moves))

  async def test_aspirate_full_row_sets_row_mode(self):
    backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
    ops = [
      SingleChannelAspiration(
        resource=plate.get_item(f"A{col}"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
      for col in range(1, 13)
    ]
    await backend.aspirate(ops, use_channels=list(range(12)))
    self.assertEqual(bravo.head_mode.subset_type, "row")

  async def test_non_uniform_volume_is_rejected_naming_the_values(self):
    backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
    ops = [
      SingleChannelAspiration(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
      SingleChannelAspiration(
        resource=plate.get_item("B1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=20.0,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
    ]
    with self.assertRaises(RuntimeError) as ctx:
      await backend.aspirate(ops, use_channels=[0, 1])
    message = str(ctx.exception)
    self.assertIn("single aspirate volume", message)
    self.assertIn("10.0", message)
    self.assertIn("20.0", message)

  async def test_non_uniform_flow_rate_is_rejected_naming_the_values(self):
    backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
    ops = [
      SingleChannelAspiration(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=5.0,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
      SingleChannelAspiration(
        resource=plate.get_item("B1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=7.5,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      ),
    ]
    with self.assertRaises(RuntimeError) as ctx:
      await backend.aspirate(ops, use_channels=[0, 1])
    message = str(ctx.exception)
    self.assertIn("flow rate", message)
    self.assertIn("5.0", message)
    self.assertIn("7.5", message)

  async def test_flow_rate_reaches_the_w_axis_move_velocity(self):
    async def w_velocity_for(flow_rate):
      backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
      op = SingleChannelAspiration(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=flow_rate,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
      await backend.aspirate([op], use_channels=[0])
      w_moves = [
        m
        for c in ctrl.calls
        if c["method"] == "move"
        for m in c["args"]["moves"]
        if m["axis"] == "w"
      ]
      return {m["velocity"] for m in w_moves}

    default_velocities = await w_velocity_for(None)
    overridden_velocities = await w_velocity_for(12.5)
    self.assertNotIn(12.5, default_velocities)
    self.assertIn(12.5, overridden_velocities)

  async def test_liquid_height_reaches_the_z_target(self):
    async def z_targets_for(liquid_height):
      backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
      op = SingleChannelAspiration(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=None,
        liquid_height=liquid_height,
        blow_out_air_volume=None,
        mix=None,
      )
      await backend.aspirate([op], use_channels=[0])
      return tuple(
        m["position"]
        for c in ctrl.calls
        if c["method"] == "move"
        for m in c["args"]["moves"]
        if m["axis"] == "z"
      )

    near = await z_targets_for(1.0)
    far = await z_targets_for(8.0)
    self.assertNotEqual(near, far)

  async def test_mix_embedded_in_an_op_is_rejected(self):
    from pylabrobot.legacy.liquid_handling.standard import Mix

    backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
    op = SingleChannelAspiration(
      resource=plate.get_item("A1"),
      offset=Coordinate.zero(),
      tip=_make_tip(),
      volume=10.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=Mix(volume=5.0, repetitions=2, flow_rate=5.0),
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.aspirate([op], use_channels=[0])
    self.assertIn("mix", str(ctx.exception))

  async def test_dispense_maps_blow_out_to_blowout(self):
    # blow_out_air_volume is folded into the same W move as an additive
    # total (see DispenseTask: total = volume + blowout), not a separate
    # move -- so the signal to pin is the move's target position, not an
    # extra move appearing.
    async def w_targets_for(blow_out_air_volume):
      # Aspirate more than volume + blowout will ever consume, so the
      # dispense target position (aspirated - (volume + blowout)) stays
      # positive and distinguishable instead of clamping to 0 in both
      # cases (see DispenseTask._target_w_after_dispense).
      backend, bravo, deck, ctrl, plate = await self._backend_ready_to_pipette()
      aspirate_op = SingleChannelAspiration(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=50.0,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=None,
        mix=None,
      )
      await backend.aspirate([aspirate_op], use_channels=[0])
      ctrl.calls.clear()
      dispense_op = SingleChannelDispense(
        resource=plate.get_item("A1"),
        offset=Coordinate.zero(),
        tip=_make_tip(),
        volume=10.0,
        flow_rate=None,
        liquid_height=None,
        blow_out_air_volume=blow_out_air_volume,
        mix=None,
      )
      await backend.dispense([dispense_op], use_channels=[0])
      return tuple(
        m["position"]
        for c in ctrl.calls
        if c["method"] == "move"
        for m in c["args"]["moves"]
        if m["axis"] == "w"
      )

    without_blowout = await w_targets_for(None)
    with_blowout = await w_targets_for(5.0)
    self.assertNotEqual(without_blowout, with_blowout)


class Tips96Tests(unittest.IsolatedAsyncioTestCase):
  """pick_up_tips96/drop_tips96."""

  async def test_full_rack_pickup_is_all_barrels(self):
    backend, bravo, deck, ctrl = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    items = rack.get_all_items()
    tips = [_make_tip() for _ in items]
    pickup = PickupTipRack(resource=rack, offset=Coordinate.zero(), tips=tips)
    await backend.pick_up_tips96(pickup)
    self.assertEqual(bravo.head_mode.subset_type, "all_barrels")
    self.assertTrue(bravo._tips_on_head)

  async def test_partial_rack_pickup_derives_the_populated_block(self):
    backend, bravo, deck, ctrl = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    items = rack.get_all_items()
    tips: List = []
    for item in items:
      identifier = rack.get_child_identifier(item)
      tips.append(_make_tip() if identifier in ("A1", "B1") else None)
    pickup = PickupTipRack(resource=rack, offset=Coordinate.zero(), tips=tips)
    await backend.pick_up_tips96(pickup)
    # A1 and B1 only: a 2x1 block, not a full column (which would need all
    # 8 rows) -- so this reduces to "rectangle", not "column".
    self.assertEqual(bravo.head_mode.subset_type, "rectangle")
    self.assertEqual(bravo.head_mode.row_count, 2)
    self.assertEqual(bravo.head_mode.column_count, 1)

  async def test_fully_empty_rack_pickup_is_rejected(self):
    backend, bravo, deck, ctrl = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    pickup = PickupTipRack(
      resource=rack, offset=Coordinate.zero(), tips=[None for _ in rack.get_all_items()]
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_tips96(pickup)
    self.assertIn("at least one populated", str(ctx.exception))

  async def test_drop_tips96_ejects_whatever_is_mounted(self):
    backend, bravo, deck, ctrl = _new_backend()
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    await _mount_tips(backend, deck)
    drop = DropTipRack(resource=rack, offset=Coordinate.zero())
    await backend.drop_tips96(drop)
    self.assertFalse(bravo._tips_on_head)

  async def test_96_path_rejected_on_a_non_96_channel_head(self):
    backend, bravo, deck, ctrl = _new_backend(head_type="384_d_70")
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    pickup = PickupTipRack(
      resource=rack, offset=Coordinate.zero(), tips=[_make_tip() for _ in rack.get_all_items()]
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_tips96(pickup)
    message = str(ctx.exception)
    self.assertIn("requires a 96-channel head", message)
    self.assertIn("384_d_70", message)

  async def test_unidentified_head_is_rejected(self):
    backend, bravo, deck, ctrl = _new_backend(head_type="unknown")
    rack = opentrons_96_tiprack_300ul(name="rack4")
    deck.assign_child_at_site(rack, 4)
    pickup = PickupTipRack(
      resource=rack, offset=Coordinate.zero(), tips=[_make_tip() for _ in rack.get_all_items()]
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_tips96(pickup)
    self.assertIn("not been identified", str(ctx.exception))


class AspirateDispense96Tests(unittest.IsolatedAsyncioTestCase):
  """aspirate96/dispense96: single scalar volume, whole head."""

  async def test_aspirate96_sets_all_barrels_and_anchors_at_the_minimum_well(self):
    backend, bravo, deck, ctrl = _new_backend()
    plate = cor_96_wellplate_360uL_Fb(name="plate5")
    deck.assign_child_at_site(plate, 5)
    await _mount_tips(backend, deck)
    wells = plate.get_all_items()
    op = MultiHeadAspirationPlate(
      wells=wells,
      offset=Coordinate.zero(),
      tips=[_make_tip() for _ in wells],
      volume=15.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=None,
    )
    await backend.aspirate96(op)
    self.assertEqual(bravo.head_mode.subset_type, "all_barrels")
    self.assertEqual((bravo._plate_selection[5].row, bravo._plate_selection[5].col), (0, 0))

  async def test_dispense96_mix_is_rejected(self):
    from pylabrobot.legacy.liquid_handling.standard import Mix

    backend, bravo, deck, ctrl = _new_backend()
    plate = cor_96_wellplate_360uL_Fb(name="plate5")
    deck.assign_child_at_site(plate, 5)
    await _mount_tips(backend, deck)
    wells = plate.get_all_items()
    op = MultiHeadDispensePlate(
      wells=wells,
      offset=Coordinate.zero(),
      tips=[_make_tip() for _ in wells],
      volume=15.0,
      flow_rate=None,
      liquid_height=None,
      blow_out_air_volume=None,
      mix=Mix(volume=5.0, repetitions=1, flow_rate=5.0),
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.dispense96(op)
    self.assertIn("mix", str(ctx.exception))


class GripperTests(unittest.IsolatedAsyncioTestCase):
  """pick_up_resource/move_picked_up_resource/drop_resource."""

  async def test_full_cycle_moves_the_labware(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    await backend.pick_up_resource(pickup)
    self.assertIsNotNone(bravo._gripper_held_task)

    move = ResourceMove(
      resource=plate,
      location=Coordinate(
        x=deck.teachpoints.get_teachpoint(2, "x"), y=deck.teachpoints.get_teachpoint(2, "y"), z=0.0
      ),
      gripped_direction=GripDirection.FRONT,
      pickup_distance_from_top=5.0,
      offset=Coordinate.zero(),
    )
    await backend.move_picked_up_resource(move)

    drop = ResourceDrop(
      resource=plate,
      destination=Coordinate(
        x=deck.teachpoints.get_teachpoint(2, "x"), y=deck.teachpoints.get_teachpoint(2, "y"), z=0.0
      ),
      destination_absolute_rotation=Rotation(0, 0, 0),
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      pickup_direction=GripDirection.FRONT,
      direction=GripDirection.FRONT,
      rotation=0.0,
    )
    await backend.drop_resource(drop)
    self.assertIsNone(bravo._gripper_held_task)
    self.assertIsNone(bravo.get_labware(1))
    self.assertIsNotNone(bravo.get_labware(2))

  async def test_pick_up_resource_rejects_a_non_zero_offset(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate(1.0, 0.0, 0.0),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_resource(pickup)
    self.assertIn("offset", str(ctx.exception))

  async def test_pick_up_resource_rejects_a_non_front_direction(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.LEFT,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_resource(pickup)
    message = str(ctx.exception)
    self.assertIn("direction", message)
    self.assertIn("LEFT", message)

  async def test_move_picked_up_resource_rejects_a_non_zero_offset(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    await backend.pick_up_resource(pickup)
    move = ResourceMove(
      resource=plate,
      location=Coordinate(
        x=deck.teachpoints.get_teachpoint(2, "x"), y=deck.teachpoints.get_teachpoint(2, "y"), z=0.0
      ),
      gripped_direction=GripDirection.FRONT,
      pickup_distance_from_top=5.0,
      offset=Coordinate(0.0, 1.0, 0.0),
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.move_picked_up_resource(move)
    self.assertIn("offset", str(ctx.exception))

  async def test_drop_resource_rejects_a_non_zero_rotation(self):
    # A caller asking for a 90-degree rotated placement without checking
    # for an error would otherwise get the plate placed unrotated, in the
    # wrong orientation, with no indication anything went wrong.
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    await backend.pick_up_resource(pickup)
    drop = ResourceDrop(
      resource=plate,
      destination=Coordinate(
        x=deck.teachpoints.get_teachpoint(2, "x"), y=deck.teachpoints.get_teachpoint(2, "y"), z=0.0
      ),
      destination_absolute_rotation=Rotation(0, 0, 0),
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      pickup_direction=GripDirection.FRONT,
      direction=GripDirection.FRONT,
      rotation=90.0,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.drop_resource(drop)
    self.assertIn("rotation", str(ctx.exception))

  async def test_drop_resource_rejects_a_non_front_drop_direction(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    await backend.pick_up_resource(pickup)
    drop = ResourceDrop(
      resource=plate,
      destination=Coordinate(
        x=deck.teachpoints.get_teachpoint(2, "x"), y=deck.teachpoints.get_teachpoint(2, "y"), z=0.0
      ),
      destination_absolute_rotation=Rotation(0, 0, 0),
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      pickup_direction=GripDirection.FRONT,
      direction=GripDirection.BACK,
      rotation=0.0,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.drop_resource(drop)
    message = str(ctx.exception)
    self.assertIn("direction", message)
    self.assertIn("BACK", message)

  async def test_drop_destination_not_matching_a_site_is_rejected(self):
    backend, bravo, deck, ctrl = _new_backend(gripper=True)
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    await backend.pick_up_resource(pickup)
    drop = ResourceDrop(
      resource=plate,
      destination=Coordinate(x=99999.0, y=99999.0, z=0.0),
      destination_absolute_rotation=Rotation(0, 0, 0),
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      pickup_direction=GripDirection.FRONT,
      direction=GripDirection.FRONT,
      rotation=0.0,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.drop_resource(drop)
    self.assertIn("taught sites", str(ctx.exception))

  async def test_srt_gripper_operations_are_rejected_naming_the_model(self):
    backend, bravo, deck, ctrl = _new_backend(
      gripper=True, controller_cls=_GripperlessSimulationController
    )
    plate = cor_96_wellplate_360uL_Fb(name="source_plate")
    deck.assign_child_at_site(plate, 1)
    pickup = ResourcePickup(
      resource=plate,
      offset=Coordinate.zero(),
      pickup_distance_from_top=5.0,
      direction=GripDirection.FRONT,
    )
    with self.assertRaises(RuntimeError) as ctx:
      await backend.pick_up_resource(pickup)
    message = str(ctx.exception)
    self.assertIn("Bravo SRT", message)
    self.assertIn("no gripper", message)


class CanPickUpTipTests(unittest.IsolatedAsyncioTestCase):
  """can_pick_up_tip: tip capacity against the installed head."""

  async def test_compatible_tip_is_accepted(self):
    backend, _, _, _ = _new_backend(head_type="96_d_70")
    self.assertTrue(backend.can_pick_up_tip(0, _make_tip(30.0)))

  async def test_channel_idx_does_not_change_the_answer(self):
    backend, _, _, _ = _new_backend(head_type="96_d_70")
    tip = _make_tip(30.0)
    results = {backend.can_pick_up_tip(ch, tip) for ch in range(96)}
    self.assertEqual(results, {True})

  async def test_incompatible_capacity_is_rejected(self):
    backend, _, _, _ = _new_backend(head_type="96_d_70")
    self.assertFalse(backend.can_pick_up_tip(0, _make_tip(999.0)))

  async def test_unidentified_head_rejects_every_tip(self):
    backend, _, _, _ = _new_backend(head_type="unknown")
    self.assertFalse(backend.can_pick_up_tip(0, _make_tip(30.0)))


if __name__ == "__main__":
  unittest.main()
