"""Fine-pipetting command arguments, response handling, and resource bookkeeping."""

import unittest
from unittest.mock import AsyncMock, call, patch

from pylabrobot.io.http import HTTP
from pylabrobot.opentrons.flex.errors import OpentronsCommandError, OpentronsError
from pylabrobot.opentrons.flex.flex import Flex
from pylabrobot.opentrons.flex.flex_head import FlexHead1, FlexHead8
from pylabrobot.opentrons.flex.tests.flex_tests import _flex_head1, _flex_head8, _flex_head96
from pylabrobot.opentrons.flex.tests.liquid_test_utils import pipetting_location
from pylabrobot.opentrons.flex.tests.mock_utils import make_api, make_flex
from pylabrobot.opentrons.run import OpentronsRun
from pylabrobot.opentrons.types import CommandInfo
from pylabrobot.resources import (
  biorad_384_wellplate_50uL_Vb,
  cor_96_wellplate_360uL_Fb,
  cor_cos_24_wellplate_3470uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


class TestBlowOut(unittest.IsolatedAsyncioTestCase):
  """blow_out sends one blowOutInPlace command at the current position.

  It leaves the plunger past its dispense bottom, but the driver sends no
  prepareToAspirate to fix that: a following well-addressed aspirate names a
  well, so the driver primes in air before descent."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def test_head8_sends_blow_out_in_place_with_dispense_default_flow_rate(self):
    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, "C1")

    await head.pick_up_tips(rack, column=0)
    await head.blow_out()

    blow_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "blowOutInPlace"]
    self.assertEqual(len(blow_cmds), 1)
    # p50_multi_v3.5 on a 50uL tip, per Opentrons' shipped pipette data.
    self.assertEqual(
      blow_cmds[0].args[2],
      {"pipetteId": head.pipette_id, "flowRate": 57.0},
    )

  async def test_head8_accepts_flow_rate_override(self):
    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, "C1")

    await head.pick_up_tips(rack, column=0)
    await head.blow_out(flow_rate=12.5)

    blow_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "blowOutInPlace"]
    self.assertEqual(blow_cmds[0].args[2]["flowRate"], 12.5)

  async def test_head8_next_aspirate_after_blow_out_primes_before_descent(self):
    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)

    await head.pick_up_tips(rack, column=0)
    await head.aspirate(plate, column=0, volume=10)
    await head.blow_out()
    await head.aspirate(plate, column=1, volume=10)

    cmd_types = [c.args[1] for c in api.submit_command.await_args_list]
    self.assertEqual(cmd_types.count("aspirateInPlace"), 2)
    self.assertIn("prepareToAspirate", cmd_types)

  async def test_head1_blow_out_then_aspirate_primes_before_descent(self):
    flex, api, head = await _flex_head1(self)
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    well = plate.get_item("B3")
    well.tracker.set_volume(100.0)

    await head.pick_up_tips(rack.get_item("A1"))
    await head.aspirate(well, volume=10)
    await head.blow_out()
    await head.aspirate(well, volume=10)

    blow_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "blowOutInPlace"]
    self.assertEqual(len(blow_cmds), 1)
    # p1000_single_v3.5 on a 50uL tip: a different pipette blows out at its own
    # rate, not the p50's 57.
    self.assertEqual(
      blow_cmds[0].args[2],
      {"pipetteId": head.pipette_id, "flowRate": 478.0},
    )
    cmd_types = [c.args[1] for c in api.submit_command.await_args_list]
    self.assertEqual(cmd_types.count("aspirateInPlace"), 2)
    self.assertIn("prepareToAspirate", cmd_types)

  async def test_head96_blow_out_then_aspirate_primes_before_descent(self):
    flex, api, head = await _flex_head96(self)
    rack = flex_96_tiprack_50ul(name="rack96")
    plate = cor_96_wellplate_360uL_Fb(name="plate96")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)

    await head.pick_up_tips(rack)
    await head.aspirate(plate, volume=10)
    await head.blow_out()
    await head.aspirate(plate, volume=10)

    blow_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "blowOutInPlace"]
    self.assertEqual(len(blow_cmds), 1)
    self.assertEqual(blow_cmds[0].args[2]["pipetteId"], head.pipette_id)
    cmd_types = [c.args[1] for c in api.submit_command.await_args_list]
    self.assertEqual(cmd_types.count("aspirateInPlace"), 2)
    self.assertIn("prepareToAspirate", cmd_types)


class TestTouchTipHead1(unittest.IsolatedAsyncioTestCase):
  """FlexHead1.touch_tip sends one touchTip command naming the well, with the
  radius and a TOP-origin wellLocation (default 1 mm below the rim; a caller
  offset replaces it, also top-relative); a missing tip rejects before any
  wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head1(self)

  async def test_touch_tip_sends_one_command_with_radius_offset_and_well(self):
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    await self.head.pick_up_tips(rack.get_item("A1"))
    await self.head.touch_tip(plate.get_item("B3"), radius=0.75, offset=Coordinate(1, 2, 3))

    touch_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "touchTip"]
    self.assertEqual(len(touch_cmds), 1)
    params = touch_cmds[0].args[2]
    self.assertEqual(params["pipetteId"], self.head.pipette_id)
    self.assertEqual(params["wellName"], "B3")
    self.assertEqual(params["radius"], 0.75)
    self.assertEqual(params["wellLocation"], {"origin": "top", "offset": {"x": 1, "y": 2, "z": 3}})

  async def test_touch_tip_defaults_radius_one_and_1mm_below_rim(self):
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    await self.head.pick_up_tips(rack.get_item("A1"))
    await self.head.touch_tip(plate.get_item("B3"))

    touch_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "touchTip"]
    params = touch_cmds[0].args[2]
    self.assertEqual(params["radius"], 1.0)
    self.assertEqual(
      params["wellLocation"], {"origin": "top", "offset": {"x": 0, "y": 0, "z": -1.0}}
    )

  async def test_touch_tip_without_tip_raises_and_sends_nothing(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    n_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.touch_tip(plate.get_item("B3"))

    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )


class TestTouchTipHead8(unittest.IsolatedAsyncioTestCase):
  """FlexHead8.touch_tip is column-addressed: one touchTip command anchored at
  the column's A-row well; a missing tip rejects before any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)

  async def test_touch_tip_anchors_at_column_a_row_well(self):
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    await self.head.pick_up_tips(rack, column=0)
    await self.head.touch_tip(plate, column=2)

    touch_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "touchTip"]
    self.assertEqual(len(touch_cmds), 1)
    self.assertEqual(touch_cmds[0].args[2]["wellName"], "A3")
    self.assertEqual(touch_cmds[0].args[2]["radius"], 1.0)

  async def test_touch_tip_without_tips_raises_and_sends_nothing(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    n_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.touch_tip(plate, column=0)

    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )


class TestTouchTipHead96(unittest.IsolatedAsyncioTestCase):
  """FlexHead96.touch_tip fans one touchTip command anchored at "A1" out to
  all 96 channels; a non-96-position labware or a missing tip rejects before
  any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head96(self)

  async def test_touch_tip_anchors_at_a1(self):
    rack = flex_96_tiprack_50ul(name="rack96")
    plate = cor_96_wellplate_360uL_Fb(name="plate96")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    await self.head.pick_up_tips(rack)
    await self.head.touch_tip(plate)

    touch_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "touchTip"]
    self.assertEqual(len(touch_cmds), 1)
    self.assertEqual(touch_cmds[0].args[2]["wellName"], "A1")

  async def test_touch_tip_rejects_non_96_labware(self):
    rack = flex_96_tiprack_50ul(name="rack96")
    plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(plate_384, "C3")

    await self.head.pick_up_tips(rack)
    n_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.touch_tip(plate_384)

    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )

  async def test_touch_tip_without_tips_raises_and_sends_nothing(self):
    plate = cor_96_wellplate_360uL_Fb(name="plate96")
    self.flex.deck.assign_child_at_slot(plate, "C2")

    n_before = self.api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await self.head.touch_tip(plate)

    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )


class TestLiquidProbeHead1(unittest.IsolatedAsyncioTestCase):
  """FlexHead1 liquid probing: the found liquid z rides the command result's
  ``z_position`` key, which the robot-server OMITS entirely (not null) when
  no liquid is found -- liquid_probe raises on absence, try_liquid_probe
  returns None."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def _bench(self, **api_kwargs):
    flex, api, head = await _flex_head1(self, **api_kwargs)
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    return flex, api, head, rack, plate

  async def test_liquid_probe_returns_configured_z_and_sends_probe_command(self):
    flex, api, head, rack, plate = await self._bench(liquid_probe_z=12.5)
    await head.pick_up_tips(rack.get_item("A1"))
    z = await head.liquid_probe(plate.get_item("B3"))

    self.assertEqual(z, 12.5)
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "liquidProbe"]
    self.assertEqual(len(probe_cmds), 1)
    params = probe_cmds[0].args[2]
    self.assertEqual(params["pipetteId"], head.pipette_id)
    self.assertEqual(params["wellName"], "B3")
    # The probe starts 2 mm above the well rim (the engine's own start
    # offset) and descends; a bottom-origin start would begin at the floor.
    self.assertEqual(
      params["wellLocation"], {"origin": "top", "offset": {"x": 0, "y": 0, "z": 2.0}}
    )

  async def test_liquid_probe_raises_when_no_liquid_found(self):
    flex, api, head, rack, plate = await self._bench()
    await head.pick_up_tips(rack.get_item("A1"))
    with self.assertRaises(OpentronsError):
      await head.liquid_probe(plate.get_item("B3"))

    # The probe command WAS sent -- absence of z_position in its result is
    # what raised, not a pre-wire guard.
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "liquidProbe"]
    self.assertEqual(len(probe_cmds), 1)

  async def test_try_liquid_probe_returns_none_when_no_liquid_found(self):
    flex, api, head, rack, plate = await self._bench()
    await head.pick_up_tips(rack.get_item("A1"))
    z = await head.try_liquid_probe(plate.get_item("B3"))

    self.assertIsNone(z)
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "tryLiquidProbe"]
    self.assertEqual(len(probe_cmds), 1)

  async def test_try_liquid_probe_returns_configured_z(self):
    flex, api, head, rack, plate = await self._bench(liquid_probe_z=4.75)
    await head.pick_up_tips(rack.get_item("A1"))
    z = await head.try_liquid_probe(plate.get_item("B3"))
    self.assertEqual(z, 4.75)
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "tryLiquidProbe"]
    self.assertEqual(
      probe_cmds[0].args[2]["wellLocation"],
      {"origin": "top", "offset": {"x": 0, "y": 0, "z": 2.0}},
    )

  async def test_liquid_probe_without_tip_raises_and_sends_nothing(self):
    flex, api, head, _rack, plate = await self._bench(liquid_probe_z=12.5)
    n_before = api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await head.liquid_probe(plate.get_item("B3"))

    self.assertEqual(api.submit_command.await_count, n_before, "rejection must not reach the wire")

  async def test_liquid_probe_wire_failure_translates_to_liquid_not_found(self):
    # Real hardware FAILS the liquidProbe command with the defined
    # "liquidNotFound" error when no liquid is detected.
    flex, api, head, rack, plate = await self._bench()
    await head.pick_up_tips(rack.get_item("A1"))
    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          side_effect=[
            api.get_command.return_value,
            CommandInfo(
              "failed", {}, {"errorType": "liquidNotFound", "detail": "No liquid detected"}
            ),
          ]
        ),
      ),
      self.assertRaises(OpentronsError) as ctx,
    ):
      await head.liquid_probe(plate.get_item("B3"))

    self.assertEqual(ctx.exception.title, "LiquidNotFoundError")
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "liquidProbe"]
    self.assertEqual(len(probe_cmds), 1, "the probe reached the wire and failed there")

  async def test_try_liquid_probe_unaffected_by_liquid_probe_failure_mode(self):
    # tryLiquidProbe genuinely succeeds with the z_position key absent.
    flex, api, head, rack, plate = await self._bench()
    await head.pick_up_tips(rack.get_item("A1"))
    self.assertIsNone(await head.try_liquid_probe(plate.get_item("B3")))
    self.assertEqual(api.submit_command.await_args.args[1], "tryLiquidProbe")

  async def test_liquid_probe_other_wire_failure_reraises_untranslated(self):

    api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    self.addAsyncCleanup(flex.disconnect)
    await flex.setup()
    head = flex.right
    assert isinstance(head, FlexHead1)
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")

    await head.pick_up_tips(rack.get_item("A1"))
    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          side_effect=[
            api.get_command.return_value,
            CommandInfo("failed", {}, {"errorType": "overpressure", "detail": "clogged tip"}),
          ]
        ),
      ),
      self.assertRaises(OpentronsCommandError) as ctx,
    ):
      await head.liquid_probe(plate.get_item("B3"))
    self.assertEqual(ctx.exception.error_type, "overpressure")

  async def test_a_probe_refused_for_an_unprimed_plunger_carries_the_priming_remedy(self):
    """A probe pushes the plunger, so the robot refuses it on an unprimed one
    even though a well IS named: getting there means the tip has held liquid,
    and a probe wants a dry tip. The driver translates it like an in-place
    draw rather than letting the raw wire error through."""

    api = make_api(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
    flex = make_flex(deck=FlexDeck(), host="localhost", api=api)
    self.addAsyncCleanup(flex.disconnect)
    await flex.setup()
    head = flex.right
    assert isinstance(head, FlexHead1)
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")

    await head.pick_up_tips(rack.get_item("A1"))
    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          side_effect=[
            api.get_command.return_value,
            CommandInfo(
              "failed",
              {},
              {"errorType": "PipetteNotReadyToAspirateError", "detail": "Plunger is not ready"},
            ),
          ]
        ),
      ),
      self.assertRaises(OpentronsError) as caught,
    ):
      await head.liquid_probe(plate.get_item("B3"))
    self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    self.assertIn("prepare_to_aspirate()", str(caught.exception))


class TestLiquidProbeHead8(unittest.IsolatedAsyncioTestCase):
  """FlexHead8 liquid probing is column-addressed: one probe command anchored
  at the column's A-row well."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def _bench(self, **api_kwargs):
    flex, api, head = await _flex_head8(self, **api_kwargs)
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    return flex, api, head, rack, plate

  async def test_liquid_probe_anchors_at_column_a_row_well_and_returns_z(self):
    flex, api, head, rack, plate = await self._bench(liquid_probe_z=7.25)
    await head.pick_up_tips(rack, column=0)
    z = await head.liquid_probe(plate, column=3)

    self.assertEqual(z, 7.25)
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "liquidProbe"]
    self.assertEqual(len(probe_cmds), 1)
    self.assertEqual(probe_cmds[0].args[2]["wellName"], "A4")

  async def test_try_liquid_probe_returns_none_when_no_liquid_found(self):
    flex, api, head, rack, plate = await self._bench()
    await head.pick_up_tips(rack, column=0)
    z = await head.try_liquid_probe(plate, column=3)

    self.assertIsNone(z)
    probe_cmds = [c for c in api.submit_command.await_args_list if c.args[1] == "tryLiquidProbe"]
    self.assertEqual(len(probe_cmds), 1)
    self.assertEqual(probe_cmds[0].args[2]["wellName"], "A4")

  async def test_liquid_probe_without_tips_raises_and_sends_nothing(self):
    flex, api, head, _rack, plate = await self._bench(liquid_probe_z=7.25)
    n_before = api.submit_command.await_count
    with self.assertRaises(OpentronsError):
      await head.liquid_probe(plate, column=3)

    self.assertEqual(api.submit_command.await_count, n_before, "rejection must not reach the wire")


class TestHead8ColumnValidation(unittest.IsolatedAsyncioTestCase):
  """Every FlexHead8 column op validates the column against the labware's
  real grid BEFORE any wire command (including configureNozzleLayout and
  loadLabware), so a rejected op ships nothing."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)
    self.rack = flex_96_tiprack_50ul(name="rack")
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.flex.deck.assign_child_at_slot(self.rack, "C1")
    self.flex.deck.assign_child_at_slot(self.plate, "C2")

  async def test_out_of_range_columns_reject_every_op_with_zero_wire_commands(self):
    await self.head.pick_up_tips(self.rack, column=0)  # the trio needs mounted tips

    for column in (-1, 12, 15):
      ops = [
        lambda c=column: self.head.pick_up_tips(self.rack, column=c),
        lambda c=column: self.head.drop_tips(self.rack, column=c),
        lambda c=column: self.head.aspirate(self.plate, column=c, volume=10),
        lambda c=column: self.head.dispense(self.plate, column=c, volume=10),
        lambda c=column: self.head.touch_tip(self.plate, column=c),
        lambda c=column: self.head.liquid_probe(self.plate, column=c),
        lambda c=column: self.head.try_liquid_probe(self.plate, column=c),
      ]
      for op in ops:
        n_before = self.api.submit_command.await_count
        with self.assertRaises(ValueError):
          await op()
        self.assertEqual(
          self.api.submit_command.await_count,
          n_before,
          f"column {column} rejection must ship nothing",
        )

  async def test_column_minus_one_does_not_alias_to_the_last_column(self):
    # Negative indexing must not silently address column 12's wells.
    n_before = self.api.submit_command.await_count
    with self.assertRaises(ValueError):
      await self.head.pick_up_tips(self.rack, column=-1)
    self.assertEqual(self.api.submit_command.await_count, n_before)
    for spot in self.rack.get_all_items():
      self.assertTrue(spot.has_tip(), spot.name)

  async def test_384_well_plate_addresses_two_interleaved_row_sets_per_physical_column(self):
    # The nozzles hold their 9 mm pitch, so on a 16-row plate they cover
    # every other row: two sets of 8 per physical column, indexed in turn.
    plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
    self.flex.deck.assign_child_at_slot(plate_384, "C3")
    await self.head.pick_up_tips(self.rack, column=0)

    for column, expected in ((0, "A1"), (1, "B1"), (2, "A2"), (47, "B24")):
      anchor, items = self.head._column_anchor_and_items(plate_384, column)
      self.assertEqual(anchor, expected, f"column {column}")
      self.assertEqual(len(items), 8)
    # Column 0 covers the rear-row set the engine's own coverage math
    # reports for a full 8-channel configuration anchored at A1.
    _anchor, items = self.head._column_anchor_and_items(plate_384, 0)
    self.assertEqual(
      [plate_384.get_child_identifier(item) for item in items],
      ["A1", "C1", "E1", "G1", "I1", "K1", "M1", "O1"],
    )
    _anchor, items = self.head._column_anchor_and_items(plate_384, 1)
    self.assertEqual(
      [plate_384.get_child_identifier(item) for item in items],
      ["B1", "D1", "F1", "H1", "J1", "L1", "N1", "P1"],
    )

  async def test_384_well_plate_column_op_reaches_the_wire_at_its_anchor_well(self):
    plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
    self.flex.deck.assign_child_at_slot(plate_384, "C3")
    for well in plate_384.get_all_items():
      well.tracker.set_volume(40.0)
    await self.head.pick_up_tips(self.rack, column=0)

    await self.head.aspirate(plate_384, column=3, volume=10)

    aspirate_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    self.assertNotIn("wellName", aspirate_cmds[0].args[2])
    # Only the 8 wells the nozzles actually reach lose liquid.
    touched = ["B2", "D2", "F2", "H2", "J2", "L2", "N2", "P2"]
    for name in touched:
      self.assertAlmostEqual(plate_384.get_item(name).tracker.volume, 30.0, msg=name)
    self.assertAlmostEqual(plate_384.get_item("A2").tracker.volume, 40.0)
    self.assertAlmostEqual(plate_384.get_item("C2").tracker.volume, 40.0)

  async def test_384_well_plate_rejects_out_of_range_column_pre_wire(self):
    plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
    self.flex.deck.assign_child_at_slot(plate_384, "C3")
    await self.head.pick_up_tips(self.rack, column=0)

    n_before = self.api.submit_command.await_count
    for op in (
      lambda: self.head.aspirate(plate_384, column=48, volume=10),
      lambda: self.head.liquid_probe(plate_384, column=100),
      lambda: self.head.touch_tip(plate_384, column=-1),
    ):
      with self.assertRaises(ValueError):
        await op()
    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )

  async def test_row_count_that_is_not_a_multiple_of_eight_still_rejects_pre_wire(self):
    # A 4-row (24-well) plate has no set of 8 evenly spaced rows, so the
    # narrowing that protects hardware survives the 384 widening.
    plate_24 = cor_cos_24_wellplate_3470uL_Fb(name="plate24")
    self.flex.deck.assign_child_at_slot(plate_24, "C3")
    await self.head.pick_up_tips(self.rack, column=0)

    n_before = self.api.submit_command.await_count
    with self.assertRaises(ValueError):
      await self.head.touch_tip(plate_24, column=0)
    self.assertEqual(
      self.api.submit_command.await_count, n_before, "rejection must not reach the wire"
    )


class TestWellPositionOffsets(unittest.IsolatedAsyncioTestCase):
  """A caller offset SHIFTS the head from the default liquid position (1 mm
  above the well bottom, or ``liquid_height`` above it), never replaces it:
  a Coordinate carries z=0 when the caller only meant to nudge x/y, so a
  replacing offset would put the tip on the well floor."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.flex.deck.assign_child_at_slot(rack, "C1")
    self.flex.deck.assign_child_at_slot(self.plate, "C2")
    for well in self.plate.get_all_items():
      well.tracker.set_volume(100.0)
    await self.head.pick_up_tips(rack, column=0)

  def _aspirate_well_location(self, api: AsyncMock, plate) -> dict:
    aspirate_cmds = [
      c for c in api.submit_command.await_args_list if c.args[1] == "aspirateInPlace"
    ]
    self.assertEqual(len(aspirate_cmds), 1)
    well_location = pipetting_location(api, plate.get_item("A1"))
    return well_location

  async def test_lateral_offset_on_a_plate_column_keeps_the_bottom_clearance(self):
    await self.head.aspirate(self.plate, column=0, volume=10, offset=Coordinate(x=1, y=2))
    self.assertEqual(
      self._aspirate_well_location(self.api, self.plate),
      {"origin": "bottom", "offset": {"x": 1, "y": 2, "z": 1.0}},
    )

  async def test_zero_offset_keeps_the_bottom_clearance(self):
    await self.head.aspirate(self.plate, column=0, volume=10, offset=Coordinate.zero())
    self.assertEqual(
      self._aspirate_well_location(self.api, self.plate),
      {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
    )

  async def test_liquid_height_is_the_base_the_offset_rides_on(self):
    # liquid_height is already measured from the bottom, so it replaces the
    # clearance as the base; the offset still adds on top of whichever base.
    await self.head.aspirate(
      self.plate, column=0, volume=10, offset=Coordinate(x=2, z=0.5), liquid_height=3
    )
    self.assertEqual(
      self._aspirate_well_location(self.api, self.plate),
      {"origin": "bottom", "offset": {"x": 2, "y": 0, "z": 3.5}},
    )

  async def test_touch_tip_offset_replaces_while_aspirate_offset_shifts(self):
    # The same Coordinate(x=1) means different things on the two calls, which
    # is why both public docstrings spell the rule out: touch_tip's z IS the
    # touch height (the Opentrons Python API's absolute v_offset).
    await self.head.aspirate(self.plate, column=0, volume=10, offset=Coordinate(x=1))
    await self.head.touch_tip(self.plate, column=0, offset=Coordinate(x=1))
    touch_cmds = [c for c in self.api.submit_command.await_args_list if c.args[1] == "touchTip"]
    self.assertEqual(
      self._aspirate_well_location(self.api, self.plate),
      {"origin": "bottom", "offset": {"x": 1, "y": 0, "z": 1.0}},
    )
    self.assertEqual(
      touch_cmds[0].args[2]["wellLocation"],
      {"origin": "top", "offset": {"x": 1, "y": 0, "z": 0}},
    )


class TestSingleNozzleLayout(unittest.IsolatedAsyncioTestCase):
  """An 8-channel Flex can anchor a SINGLE nozzle layout on its "A1" or "H1"
  nozzle and nothing else: the engine's primaryNozzle is a literal of those
  (plus the 12-column ends a 96-head uses), and the pipette's own
  validNozzleMaps carry only SingleA1/SingleH1. The engine also refuses ANY
  nozzle reconfiguration, "ALL" included, while a tip is attached."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  async def _bench(self, slot="C1"):
    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, slot)
    return flex, api, head, rack

  def _nozzle_params(self, api: AsyncMock) -> list:
    return [
      c.args[2]["configurationParams"]
      for c in api.submit_command.await_args_list
      if c.args[1] == "configureNozzleLayout"
    ]

  async def test_the_front_most_row_is_pickable_from_a_full_rack_on_the_rear_anchor(self):
    # The workable single-tip pattern on a ganged head: take the front-most row
    # anchored on the REAR nozzle, so the idle seven extend past the front edge
    # over nothing. Consuming a rack front-to-back keeps that true every time.
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="H3", primary_nozzle="A1")
    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "A1")
    pickups = [c for c in api.submit_command.await_args_list if c.args[1] == "pickUpTip"]
    self.assertEqual(pickups[-1].args[2]["wellName"], "H3")
    self.assertIsNotNone(head.get_mounted_tips()[0])

  async def test_the_anchor_nozzle_decides_the_channel_not_the_wells_row(self):
    # In a SINGLE layout the engine moves the chosen nozzle over whatever well
    # is named, so the tip lands on that nozzle's channel. Row A is the rear
    # nozzle's own row, and naming H1 still puts the tip on channel 7.
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="A3", primary_nozzle="H1")

    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "H1")
    pickups = [c for c in api.submit_command.await_args_list if c.args[1] == "pickUpTip"]
    self.assertEqual(pickups[-1].args[2]["wellName"], "A3")
    self.assertIsNotNone(head.get_mounted_tips()[7])
    self.assertTrue(all(tip is None for i, tip in enumerate(head.get_mounted_tips()) if i != 7))

  async def test_the_rear_anchor_on_a_rear_row_refuses_and_names_the_front_one(self):
    # Ganged nozzles descend together, so the REAR anchor on a rear row puts the
    # idle seven over rows B-H: eight tips. Refused, not silently swapped.
    flex, api, head, rack = await self._bench()
    with self.assertRaises(ValueError) as caught:
      await head.pick_up_single_tip(rack, well="A2", primary_nozzle="A1")
    self.assertIn("more than one tip", str(caught.exception))
    self.assertIn("Anchor on H1", str(caught.exception))

    await head.pick_up_single_tip(rack, well="A2", primary_nozzle="H1")
    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "H1")
    self.assertIsNotNone(head.get_mounted_tips()[7])

  async def test_the_default_anchor_is_the_front_nozzle_whatever_the_rack_holds(self):
    # The default anchor is H1, the FRONT nozzle: its idle seven trail off the
    # REAR edge, so a rear-row well is clean whatever the rack holds -- same call,
    # same anchor, full rack or nearly bare.
    for empty_the_rest in (False, True):
      flex, api, head, rack = await self._bench()
      if empty_the_rest:
        for spot in rack.get_all_items():
          if spot.name != rack.get_item("A3").name and spot.has_tip():
            spot.tracker.remove_tip(commit=True)
      await head.pick_up_single_tip(rack, well="A3")
      self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "H1")
      self.assertIsNotNone(head.get_mounted_tips()[7])

  async def test_a_full_rack_middle_row_is_refused_rather_than_taking_a_handful_of_tips(self):
    # No anchor clears a middle row of a full rack: whichever end you pick,
    # some idle nozzles sit over tips. Refusing beats silently taking several.
    flex, api, head, rack = await self._bench()
    with self.assertRaises(ValueError) as caught:
      await head.pick_up_single_tip(rack, well="D2")
    self.assertIn("more than one tip", str(caught.exception))

  async def test_a_middle_row_is_allowed_once_its_neighbours_are_already_empty(self):
    # Same well, but with the rest of the column consumed the idle nozzles have
    # nothing to engage, so the pick really is single.
    flex, api, head, rack = await self._bench()
    for spot in rack.get_all_items():
      if spot.name != rack.get_item("D2").name and spot.has_tip():
        spot.tracker.remove_tip(commit=True)
    await head.pick_up_single_tip(rack, well="D2")
    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "H1")

  async def test_an_omitted_well_takes_the_next_tracked_tip_in_the_anchors_order(self):
    # ot3-style: with no well, the driver walks tip tracking in the anchor's own
    # consumption order and takes the first clear tip. Default H1 consumes a rack
    # back-to-front, so a full rack yields the rear-most tip, well A1.
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack)
    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "H1")
    pickups = [c for c in api.submit_command.await_args_list if c.args[1] == "pickUpTip"]
    self.assertEqual(pickups[-1].args[2]["wellName"], "A1")
    self.assertIsNotNone(head.get_mounted_tips()[7])

  async def test_an_omitted_well_on_the_rear_anchor_takes_the_front_most_tip(self):
    # The A1 anchor consumes front-to-back, so with no well it takes the
    # front-most tip, well H1 -- the mirror of the H1 default.
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, primary_nozzle="A1")
    self.assertEqual(self._nozzle_params(api)[-1]["primaryNozzle"], "A1")
    pickups = [c for c in api.submit_command.await_args_list if c.args[1] == "pickUpTip"]
    self.assertEqual(pickups[-1].args[2]["wellName"], "H1")
    self.assertIsNotNone(head.get_mounted_tips()[0])

  async def test_an_unanchorable_nozzle_is_rejected_pre_wire(self):
    flex, api, head, rack = await self._bench()
    commands_before = api.submit_command.await_count
    with self.assertRaises(ValueError):
      await head.pick_up_single_tip(rack, well="A1", primary_nozzle="B1")
    self.assertEqual(api.submit_command.await_count, commands_before)

  async def test_reconfiguring_the_layout_while_a_tip_is_mounted_is_refused(self):
    flex, api, head, rack = await self._bench()
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    flex.deck.assign_child_at_slot(plate, "C2")
    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")
    commands_before = api.submit_command.await_count

    # A column op resets the layout to ALL, which the robot refuses while
    # the single tip is still on.
    with self.assertRaises(OpentronsError) as caught:
      await head.aspirate(plate, column=1, volume=10)
    self.assertIn("nozzle layout", str(caught.exception))
    self.assertEqual(api.submit_command.await_count, commands_before)

  async def test_dropping_the_single_tip_restores_all_mode(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")
    await head.drop_single_tip(flex.deck.get_trash_area())

    self.assertEqual(self._nozzle_params(api)[-1], {"style": "ALL"})
    self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))

  async def test_discarding_a_cherry_picked_tip_drops_it_then_restores_all_mode(self):
    # A trash drop addresses no channel, so it must not wait on a layout
    # reset the robot refuses while the tip is still on.
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")
    await head.discard_tips(flex.deck.get_trash_area())

    self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
    sent = [c.args[1] for c in api.submit_command.await_args_list]
    self.assertLess(sent.index("dropTipInPlace"), len(sent) - 1)
    self.assertEqual(sent[-1], "configureNozzleLayout")
    self.assertEqual(self._nozzle_params(api)[-1], {"style": "ALL"})

  async def test_returning_a_cherry_picked_tip_to_a_rack_column_names_the_op_that_works(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")
    commands_before = api.submit_command.await_count

    with self.assertRaises(OpentronsError) as caught:
      await head.drop_tips(rack, column=0)
    self.assertIn("drop_single_tip", str(caught.exception))
    self.assertEqual(api.submit_command.await_count, commands_before)

    await head.drop_single_tip(flex.deck.get_trash_area())

  async def test_a_front_row_slot_puts_the_rear_anchor_out_of_reach(self):
    # Anchoring A1 over D1's front rows leaves the mount ahead of the robot's
    # own front limit: the pipette body reaches 95mm forward of it.
    flex, _, head, rack = await self._bench(slot="D1")
    self.assertEqual(head.reachable_single_nozzles(rack, "A1"), ("A1", "H1"))
    self.assertEqual(head.reachable_single_nozzles(rack, "F1"), ("H1",))
    self.assertEqual(head.reachable_single_nozzles(rack, "H1"), ("H1",))

  async def test_on_a_front_slot_one_anchor_cannot_reach_and_the_other_cannot_clear(self):
    # On a front slot the rear anchor overruns the robot's front limit and the
    # front anchor hangs the idle seven back over full rows. Each refusal says which.
    flex, api, head, rack = await self._bench(slot="D1")
    commands_before = api.submit_command.await_count
    with self.assertRaises(ValueError) as reach:
      await head.pick_up_single_tip(rack, well="F1", primary_nozzle="A1")
    self.assertIn("outside the robot", str(reach.exception))

    with self.assertRaises(ValueError) as clearance:
      await head.pick_up_single_tip(rack, well="F1", primary_nozzle="H1")
    self.assertIn("more than one tip", str(clearance.exception))

    self.assertEqual(api.submit_command.await_count, commands_before, "must not reach the wire")

  async def test_an_explicit_out_of_reach_anchor_is_refused_naming_the_one_that_works(self):
    # An explicit choice is never silently swapped.
    flex, api, head, rack = await self._bench(slot="D1")
    commands_before = api.submit_command.await_count
    with self.assertRaises(ValueError) as caught:
      await head.pick_up_single_tip(rack, well="F1", primary_nozzle="A1")
    self.assertIn("H1", str(caught.exception))
    self.assertEqual(api.submit_command.await_count, commands_before)

  async def test_every_flex_slot_well_is_reachable_by_one_anchor_or_the_other(self):
    # No slot the deck accepts leaves a well unpickable; A3 holds the trash.
    for slot in [f"{row}{col}" for row in "ABCD" for col in "123" if f"{row}{col}" != "A3"]:
      flex, _, head, rack = await self._bench(slot=slot)
      for well_row in "ABCDEFGH":
        reachable = head.reachable_single_nozzles(rack, f"{well_row}1")
        self.assertTrue(reachable, f"{slot} {well_row}1 unreachable both ways")

  async def test_stop_leaves_no_cherry_picked_tip_on_the_pipette(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1")

    await flex.stop()

    self.assertIn("dropTipInPlace", [c.args[1] for c in api.submit_command.await_args_list])
    self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))


class TestInPlaceLiquidOps(unittest.IsolatedAsyncioTestCase):
  """The in-place ops act where the head already is: one command each, naming
  no labware and no well, carrying the flow-rate default of the motion they
  are (aspirate for the air gap too). Each requires a mounted tip, refused
  before the wire.

  Naming no well is also why the in-place DRAWS are the only ops that make the
  caller deal with plunger priming. The robot primes a well-addressed aspirate
  itself, at the well top where the tip is in open air; with no well it has no
  safe height to do that at, so it refuses instead of guessing, and the driver
  passes the refusal on rather than priming blind."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def _bench(self):
    flex, api, head = await _flex_head8(self)
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, "C1")
    return flex, api, head, rack

  def _params(self, api: AsyncMock, command_type: str) -> dict:
    cmds = [c for c in api.submit_command.await_args_list if c.args[1] == command_type]
    self.assertEqual(len(cmds), 1)
    params: dict = cmds[0].args[2]
    return params

  async def test_aspirate_in_place_sends_volume_and_the_aspirate_default_flow_rate(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.aspirate_in_place(volume=15)

    self.assertEqual(
      self._params(api, "aspirateInPlace"),
      {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0},
    )

  async def test_aspirate_in_place_accepts_a_flow_rate_override(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.aspirate_in_place(volume=15, flow_rate=3.5)

    self.assertEqual(self._params(api, "aspirateInPlace")["flowRate"], 3.5)

  async def test_a_pickup_leaves_the_plunger_ready_so_in_place_draws_need_no_prepare(self):
    """The robot primes as part of a tip pickup, so back-to-back in-place
    aspirates straight off a fresh tip send no prepareToAspirate at all."""
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.aspirate_in_place(volume=15)
    await head.aspirate_in_place(volume=15)

    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        )
      ]
      * 2,
    )

  async def _assert_in_place_draw_is_refused(self, head, api):
    """The robot refuses, and the driver hands back a message naming both remedies."""
    n_before = api.submit_command.await_count
    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          return_value=CommandInfo(
            "failed",
            {},
            {"errorType": "PipetteNotReadyToAspirateError", "detail": "Plunger is not ready"},
          )
        ),
      ),
      self.assertRaises(OpentronsError) as caught,
    ):
      await head.aspirate_in_place(volume=15)
    self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    self.assertIn("prepare_to_aspirate()", str(caught.exception))
    self.assertIn("aspirate from a well", str(caught.exception))
    self.assertEqual(
      api.submit_command.await_count, n_before + 1, "the refusal comes from the robot"
    )
    api.submit_command.assert_awaited_with(
      "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
    )

  async def test_a_dispense_that_empties_the_tip_makes_the_next_in_place_draw_refuse(self):
    """The ordinary transfer loop's second draw: emptying the tip leaves the
    plunger past its bottom, and with no well named the robot will not fix it."""
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.aspirate_in_place(volume=15)
    await head.dispense_in_place(volume=15)

    await self._assert_in_place_draw_is_refused(head, api)
    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
        call(
          "run", "dispenseInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 57.0}
        ),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
      ],
    )

  async def test_a_partial_dispense_leaves_the_plunger_ready(self):
    """Only a dispense that EMPTIES the tip un-primes it, because only that one
    gets a push-out. Refusing after every dispense would refuse work the robot
    accepts."""
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.aspirate_in_place(volume=15)
    await head.dispense_in_place(volume=5)
    await head.aspirate_in_place(volume=5)

    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
        call(
          "run", "dispenseInPlace", {"pipetteId": head.pipette_id, "volume": 5, "flowRate": 57.0}
        ),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 5, "flowRate": 35.0}
        ),
      ],
    )

  async def test_lifting_clear_and_priming_by_hand_clears_the_refusal(self):
    """The documented remedy, end to end: the caller moves the tip out of the
    liquid, primes, and the in-place draw goes through."""
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.aspirate_in_place(volume=15)
    await head.dispense_in_place(volume=15)
    await self._assert_in_place_draw_is_refused(head, api)

    await head.prepare_to_aspirate()
    await head.aspirate_in_place(volume=15)

    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
        call(
          "run", "dispenseInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 57.0}
        ),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
        call("run", "prepareToAspirate", {"pipetteId": head.pipette_id}),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
      ],
    )

  async def test_configure_for_volume_makes_the_next_in_place_draw_refuse(self):
    """Switching volume mode moves where the plunger bottom IS, so the robot
    drops its ready flag."""
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.configure_for_volume(10.0)

    await self._assert_in_place_draw_is_refused(head, api)
    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call("run", "configureForVolume", {"pipetteId": head.pipette_id, "volume": 10.0}),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
      ],
    )

  async def test_a_blow_out_makes_the_next_in_place_draw_refuse(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.aspirate_in_place(volume=15)
    await head.blow_out()

    await self._assert_in_place_draw_is_refused(head, api)
    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
        call("run", "blowOutInPlace", {"pipetteId": head.pipette_id, "flowRate": 57.0}),
        call(
          "run", "aspirateInPlace", {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0}
        ),
      ],
    )

  async def test_an_air_gap_in_place_refuses_the_same_way(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    api.submit_command.reset_mock()
    await head.blow_out()

    with (
      patch.object(
        api,
        "get_command",
        AsyncMock(
          return_value=CommandInfo(
            "failed",
            {},
            {"errorType": "PipetteNotReadyToAspirateError", "detail": "Plunger is not ready"},
          )
        ),
      ),
      self.assertRaises(OpentronsError) as caught,
    ):
      await head.air_gap_in_place(volume=10)
    self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    self.assertEqual(
      api.submit_command.await_args_list,
      [
        call("run", "blowOutInPlace", {"pipetteId": head.pipette_id, "flowRate": 57.0}),
        call(
          "run", "airGapInPlace", {"pipetteId": head.pipette_id, "volume": 10, "flowRate": 35.0}
        ),
      ],
    )

  async def test_dispense_in_place_sends_the_dispense_default_and_omits_push_out(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.dispense_in_place(volume=15)

    self.assertEqual(
      self._params(api, "dispenseInPlace"),
      {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 57.0},
    )

  async def test_dispense_in_place_carries_push_out_only_when_given(self):
    # Omitted rather than sent as null, so the robot picks its own push-out
    # for the mounted tip and volume.
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.dispense_in_place(volume=15, flow_rate=8.0, push_out=2.5)

    self.assertEqual(
      self._params(api, "dispenseInPlace"),
      {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 8.0, "pushOut": 2.5},
    )

  async def test_air_gap_in_place_uses_the_aspirate_default_flow_rate(self):
    # The air gap IS an aspirate motion, so it takes the aspirate default,
    # not the dispense one.
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.air_gap_in_place(volume=5)

    self.assertEqual(
      self._params(api, "airGapInPlace"),
      {"pipetteId": head.pipette_id, "volume": 5, "flowRate": 35.0},
    )

  async def test_air_gap_in_place_accepts_a_flow_rate_override(self):
    flex, api, head, rack = await self._bench()
    await head.pick_up_tips(rack, column=0)
    await head.air_gap_in_place(volume=5, flow_rate=1.25)

    self.assertEqual(self._params(api, "airGapInPlace")["flowRate"], 1.25)

  async def test_every_in_place_op_without_a_tip_raises_and_sends_nothing(self):
    flex, api, head, _rack = await self._bench()
    for op in (
      lambda: head.aspirate_in_place(volume=15),
      lambda: head.dispense_in_place(volume=15),
      lambda: head.air_gap_in_place(volume=5),
    ):
      n_before = api.submit_command.await_count
      with self.assertRaises(OpentronsError):
        await op()
      self.assertEqual(
        api.submit_command.await_count, n_before, "rejection must not reach the wire"
      )

  async def test_head1_and_head96_inherit_the_in_place_ops(self):
    # They live on the base class, so every head issues them identically.
    flex1, transport1, head1 = await _flex_head1(self)
    rack1 = flex_96_tiprack_50ul(name="rack1")
    flex1.deck.assign_child_at_slot(rack1, "C1")
    await head1.pick_up_tips(rack1.get_item("A1"))
    await head1.aspirate_in_place(volume=15)
    await head1.dispense_in_place(volume=15)

    # Each head carries its OWN pipette's default, not one shared number:
    # p1000_single_v3.5 on a 50uL tip against the 96-head's 6.0 below.
    self.assertEqual(
      self._params(transport1, "aspirateInPlace"),
      {"pipetteId": head1.pipette_id, "volume": 15, "flowRate": 478.0},
    )
    self.assertEqual(self._params(transport1, "dispenseInPlace")["volume"], 15)

    flex96, transport96, head96 = await _flex_head96(self)
    rack96 = flex_96_tiprack_50ul(name="rack96")
    flex96.deck.assign_child_at_slot(rack96, "C1")
    await head96.pick_up_tips(rack96)
    await head96.air_gap_in_place(volume=5)

    self.assertEqual(
      self._params(transport96, "airGapInPlace"),
      {"pipetteId": head96.pipette_id, "volume": 5, "flowRate": 6.0},
    )


class TestTipPresenceCommands(unittest.IsolatedAsyncioTestCase):
  """Tip queries and volume configuration preserve their HTTP command payloads."""

  def setUp(self):
    self.io = AsyncMock(spec=HTTP)
    self.flex = Flex("robot.test", io=self.io, command_poll_interval=0)
    self.flex._run = OpentronsRun(self.flex._api, "run", "9.1.2", command_poll_interval=0)
    self.head = FlexHead8(self.flex, "left", "pipette", 8, "p1000_multi_v3.5", 1000)

  async def test_get_tip_presence_returns_the_reported_status(self):
    self.io.request.side_effect = [
      {"data": {"id": "command"}},
      {"data": {"status": "succeeded", "result": {"status": "present"}}},
    ]

    self.assertEqual(await self.head.get_tip_presence(), "present")

    self.io.request.assert_has_awaits(
      [
        call(
          "POST",
          "/runs/run/commands",
          {
            "data": {
              "commandType": "getTipPresence",
              "params": {"pipetteId": "pipette"},
              "intent": "setup",
            }
          },
        ),
        call("GET", "/runs/run/commands/command"),
      ]
    )
    self.assertEqual(self.io.request.await_count, 2)

  async def test_get_tip_presence_reports_absent_without_raising(self):
    self.io.request.side_effect = [
      {"data": {"id": "command"}},
      {"data": {"status": "succeeded", "result": {"status": "absent"}}},
    ]

    self.assertEqual(await self.head.get_tip_presence(), "absent")
    self.io.request.assert_awaited_with("GET", "/runs/run/commands/command")
    self.assertEqual(self.io.request.await_count, 2)

  async def test_verify_tip_presence_sends_the_expected_state(self):
    for expected_state, wire_state in ((False, "absent"), (True, "present")):
      with self.subTest(expected_state=expected_state):
        self.io.request.reset_mock()
        self.io.request.side_effect = [
          {"data": {"id": "command"}},
          {"data": {"status": "succeeded", "result": {}}},
        ]

        await self.head.verify_tip_presence(expected_state)

        self.io.request.assert_has_awaits(
          [
            call(
              "POST",
              "/runs/run/commands",
              {
                "data": {
                  "commandType": "verifyTipPresence",
                  "params": {"pipetteId": "pipette", "expectedState": wire_state},
                  "intent": "setup",
                }
              },
            ),
            call("GET", "/runs/run/commands/command"),
          ]
        )
        self.assertEqual(self.io.request.await_count, 2)

  async def test_verify_tip_presence_rejects_any_other_state_and_sends_nothing(self):
    for state in ("present", "absent", "unknown", "", 0, 1, None):
      with self.subTest(state=state), self.assertRaises(TypeError):
        await self.head.verify_tip_presence(state)  # type: ignore[arg-type]
    self.io.request.assert_not_awaited()

  async def test_configure_for_volume_sends_pipette_id_and_volume_before_any_pickup(self):
    self.io.request.side_effect = [
      {"data": {"id": "command"}},
      {"data": {"status": "succeeded", "result": {}}},
    ]

    await self.head.configure_for_volume(42)

    self.io.request.assert_has_awaits(
      [
        call(
          "POST",
          "/runs/run/commands",
          {
            "data": {
              "commandType": "configureForVolume",
              "params": {"pipetteId": "pipette", "volume": 42},
              "intent": "setup",
            }
          },
        ),
        call("GET", "/runs/run/commands/command"),
      ]
    )
    self.assertEqual(self.io.request.await_count, 2)


class TestUnsafeRecoveryOps(unittest.IsolatedAsyncioTestCase):
  """The unsafe/ ops are the recovery path: one command each, no labware, no
  trackers. The drop clears this head's per-channel tip bookkeeping (the tip
  goes back to no rack), and the blow-out leaves the plunger unprimed the way
  the ordinary blow_out does. Both require a mounted tip."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  async def asyncSetUp(self):
    self.flex, self.api, self.head = await _flex_head8(self)
    self.rack = flex_96_tiprack_50ul(name="rack")
    self.flex.deck.assign_child_at_slot(self.rack, "C1")

  async def test_unsafe_drop_tip_in_place_sends_pipette_id_and_clears_mounted_tips(self):
    await self.head.pick_up_tips(self.rack, column=0)
    await self.head.unsafe_drop_tip_in_place()

    drop_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "unsafe/dropTipInPlace"
    ]
    self.assertEqual(len(drop_cmds), 1)
    self.assertEqual(drop_cmds[0].args[2], {"pipetteId": self.head.pipette_id})
    self.assertTrue(all(tip is None for tip in self.head.get_mounted_tips()))

  async def test_unsafe_drop_tip_in_place_returns_no_tip_to_the_rack(self):
    # The tip falls where the head is, so the spot it came from stays empty.
    await self.head.pick_up_tips(self.rack, column=0)
    await self.head.unsafe_drop_tip_in_place()

    self.assertFalse(self.rack.get_item("A1").has_tip())

  async def test_unsafe_blow_out_in_place_sends_the_given_flow_rate(self):
    await self.head.pick_up_tips(self.rack, column=0)
    await self.head.unsafe_blow_out_in_place(flow_rate=20.0)

    blow_cmds = [
      c for c in self.api.submit_command.await_args_list if c.args[1] == "unsafe/blowOutInPlace"
    ]
    self.assertEqual(len(blow_cmds), 1)
    self.assertEqual(blow_cmds[0].args[2], {"pipetteId": self.head.pipette_id, "flowRate": 20.0})

  async def test_an_unsafe_blow_out_makes_the_next_in_place_draw_refuse(self):
    """The recovery blow-out leaves the plunger where the ordinary one does,
    so the next in-place draw needs the same manual prime."""
    await self.head.pick_up_tips(self.rack, column=0)
    self.api.submit_command.reset_mock()
    await self.head.aspirate_in_place(volume=10)
    await self.head.unsafe_blow_out_in_place(flow_rate=20.0)

    with (
      patch.object(
        self.api,
        "get_command",
        AsyncMock(
          return_value=CommandInfo(
            "failed",
            {},
            {"errorType": "PipetteNotReadyToAspirateError", "detail": "Plunger is not ready"},
          )
        ),
      ),
      self.assertRaises(OpentronsError) as caught,
    ):
      await self.head.aspirate_in_place(volume=10)
    self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    self.assertEqual(
      self.api.submit_command.await_args_list,
      [
        call(
          "run",
          "aspirateInPlace",
          {"pipetteId": self.head.pipette_id, "volume": 10, "flowRate": 35.0},
        ),
        call("run", "unsafe/blowOutInPlace", {"pipetteId": self.head.pipette_id, "flowRate": 20.0}),
        call(
          "run",
          "aspirateInPlace",
          {"pipetteId": self.head.pipette_id, "volume": 10, "flowRate": 35.0},
        ),
      ],
    )

  async def test_unsafe_ops_still_run_when_this_process_knows_of_no_tip(self):
    # The old contract refused these unless this head's own bookkeeping showed a
    # tip. That bookkeeping is per-process and empty after any restart, which is
    # exactly when a tip is stranded on the nozzle and these calls are the only
    # way off it. Guarding on it made the escape hatch refuse in the one
    # situation it exists for, so the guard is gone and the commands go out.
    self.assertTrue(all(tip is None for tip in self.head.get_mounted_tips()))
    for op, expected in (
      (self.head.unsafe_drop_tip_in_place, "unsafe/dropTipInPlace"),
      (lambda: self.head.unsafe_blow_out_in_place(flow_rate=20.0), "unsafe/blowOutInPlace"),
    ):
      n_before = self.api.submit_command.await_count
      await op()
      sent = [c.args[1] for c in self.api.submit_command.await_args_list[n_before:]]
      self.assertIn(expected, sent)


if __name__ == "__main__":
  unittest.main()
