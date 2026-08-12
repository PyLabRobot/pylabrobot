"""Tests for the fine-pipetting commands on the plain-class Flex heads.

Covers ``blow_out`` (in-place plunger blow-out, all heads), ``touch_tip``
(wall-touch, all heads) and ``liquid_probe``/``try_liquid_probe``
(pressure-based liquid-level detection, mount heads only), driven through the
recording ``ChatterboxTransport`` -- the transport's ``liquid_probe_z`` kwarg
models the robot-server's found-liquid ``z_position`` result key, which is
OMITTED entirely (not null) when no liquid is found.
"""

import asyncio
import unittest

from pylabrobot.opentrons.flex_head import _DEFAULT_DISPENSE_FLOW_RATE
from pylabrobot.opentrons.flex_tests import _flex_head1, _flex_head8, _flex_head96
from pylabrobot.opentrons.robot import OpentronsError
from pylabrobot.resources import (
  biorad_384_wellplate_50uL_Vb,
  cor_96_wellplate_360uL_Fb,
  set_tip_tracking,
  set_volume_tracking,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul


class TestBlowOut(unittest.TestCase):
  """blow_out sends one blowOutInPlace command at the current position and
  invalidates the plunger priming, so the NEXT aspirate re-sends
  prepareToAspirate first."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_head8_sends_blow_out_in_place_with_dispense_default_flow_rate(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.blow_out())

      blow_cmds = [c for c in transport.commands if c["commandType"] == "blowOutInPlace"]
      self.assertEqual(len(blow_cmds), 1)
      self.assertEqual(
        blow_cmds[0]["params"],
        {"pipetteId": head.pipette_id, "flowRate": _DEFAULT_DISPENSE_FLOW_RATE},
      )
    finally:
      asyncio.run(flex.stop())

  def test_head8_accepts_flow_rate_override(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.blow_out(flow_rate=12.5))

      blow_cmds = [c for c in transport.commands if c["commandType"] == "blowOutInPlace"]
      self.assertEqual(blow_cmds[0]["params"]["flowRate"], 12.5)
    finally:
      asyncio.run(flex.stop())

  def test_head8_next_aspirate_reprimes_after_blow_out(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate(plate, column=0, volume=10))
      asyncio.run(head.blow_out())
      asyncio.run(head.aspirate(plate, column=1, volume=10))

      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      aspirate_indices = [i for i, t in enumerate(cmd_types) if t == "aspirate"]
      self.assertEqual(len(prepare_indices), 2, "a blow-out must require a new prepare")
      self.assertEqual(len(aspirate_indices), 2)
      self.assertEqual(prepare_indices[1], aspirate_indices[1] - 1)
    finally:
      asyncio.run(flex.stop())

  def test_head1_blow_out_and_reprime(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      well = plate.get_item("B3")
      well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.aspirate(well, volume=10))
      asyncio.run(head.blow_out())
      asyncio.run(head.aspirate(well, volume=10))

      blow_cmds = [c for c in transport.commands if c["commandType"] == "blowOutInPlace"]
      self.assertEqual(len(blow_cmds), 1)
      self.assertEqual(
        blow_cmds[0]["params"],
        {"pipetteId": head.pipette_id, "flowRate": _DEFAULT_DISPENSE_FLOW_RATE},
      )
      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      self.assertEqual(len(prepare_indices), 2, "a blow-out must require a new prepare")
    finally:
      asyncio.run(flex.stop())

  def test_head96_blow_out_and_reprime(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      plate = cor_96_wellplate_360uL_Fb(name="plate96")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")
      for well in plate.get_all_items():
        well.tracker.set_volume(100.0)

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.aspirate(plate, volume=10))
      asyncio.run(head.blow_out())
      asyncio.run(head.aspirate(plate, volume=10))

      blow_cmds = [c for c in transport.commands if c["commandType"] == "blowOutInPlace"]
      self.assertEqual(len(blow_cmds), 1)
      self.assertEqual(blow_cmds[0]["params"]["pipetteId"], head.pipette_id)
      cmd_types = [c["commandType"] for c in transport.commands]
      prepare_indices = [i for i, t in enumerate(cmd_types) if t == "prepareToAspirate"]
      self.assertEqual(len(prepare_indices), 2, "a blow-out must require a new prepare")
    finally:
      asyncio.run(flex.stop())


class TestTouchTipHead1(unittest.TestCase):
  """FlexHead1.touch_tip sends one touchTip command naming the well, with the
  radius and a bottom-origin wellLocation offset; a missing tip rejects
  before any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_touch_tip_sends_one_command_with_radius_offset_and_well(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.touch_tip(plate.get_item("B3"), radius=0.75, offset=Coordinate(1, 2, 3)))

      touch_cmds = [c for c in transport.commands if c["commandType"] == "touchTip"]
      self.assertEqual(len(touch_cmds), 1)
      params = touch_cmds[0]["params"]
      self.assertEqual(params["pipetteId"], head.pipette_id)
      self.assertEqual(params["wellName"], "B3")
      self.assertEqual(params["radius"], 0.75)
      self.assertEqual(
        params["wellLocation"], {"origin": "bottom", "offset": {"x": 1, "y": 2, "z": 3}}
      )
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_defaults_radius_one_and_zero_offset(self):
    flex, transport, head = _flex_head1()
    try:
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      asyncio.run(head.touch_tip(plate.get_item("B3")))

      touch_cmds = [c for c in transport.commands if c["commandType"] == "touchTip"]
      params = touch_cmds[0]["params"]
      self.assertEqual(params["radius"], 1.0)
      self.assertEqual(
        params["wellLocation"], {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 0}}
      )
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_without_tip_raises_and_sends_nothing(self):
    flex, transport, head = _flex_head1()
    try:
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(plate, "C2")

      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.touch_tip(plate.get_item("B3")))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestTouchTipHead8(unittest.TestCase):
  """FlexHead8.touch_tip is column-addressed: one touchTip command anchored at
  the column's A-row well; a missing tip rejects before any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_touch_tip_anchors_at_column_a_row_well(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.touch_tip(plate, column=2))

      touch_cmds = [c for c in transport.commands if c["commandType"] == "touchTip"]
      self.assertEqual(len(touch_cmds), 1)
      self.assertEqual(touch_cmds[0]["params"]["wellName"], "A3")
      self.assertEqual(touch_cmds[0]["params"]["radius"], 1.0)
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_without_tips_raises_and_sends_nothing(self):
    flex, transport, head = _flex_head8()
    try:
      plate = cor_96_wellplate_360uL_Fb(name="plate")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(plate, "C2")

      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.touch_tip(plate, column=0))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestTouchTipHead96(unittest.TestCase):
  """FlexHead96.touch_tip fans one touchTip command anchored at "A1" out to
  all 96 channels; a non-96-position labware or a missing tip rejects before
  any wire command."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def test_touch_tip_anchors_at_a1(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      plate = cor_96_wellplate_360uL_Fb(name="plate96")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack))
      asyncio.run(head.touch_tip(plate))

      touch_cmds = [c for c in transport.commands if c["commandType"] == "touchTip"]
      self.assertEqual(len(touch_cmds), 1)
      self.assertEqual(touch_cmds[0]["params"]["wellName"], "A1")
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_rejects_non_96_labware(self):
    flex, transport, head = _flex_head96()
    try:
      rack = flex_96_tiprack_50ul(name="rack96")
      plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate_384, "C3")

      asyncio.run(head.pick_up_tips(rack))
      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.touch_tip(plate_384))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_without_tips_raises_and_sends_nothing(self):
    flex, transport, head = _flex_head96()
    try:
      plate = cor_96_wellplate_360uL_Fb(name="plate96")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(plate, "C2")

      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.touch_tip(plate))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestLiquidProbeHead1(unittest.TestCase):
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

  def _bench(self, **transport_kwargs):
    flex, transport, head = _flex_head1(**transport_kwargs)
    rack = flex_96_tiprack_50ul(name="rack1")
    plate = cor_96_wellplate_360uL_Fb(name="plate1")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    return flex, transport, head, rack, plate

  def test_liquid_probe_returns_configured_z_and_sends_probe_command(self):
    flex, transport, head, rack, plate = self._bench(liquid_probe_z=12.5)
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      z = asyncio.run(head.liquid_probe(plate.get_item("B3")))

      self.assertEqual(z, 12.5)
      probe_cmds = [c for c in transport.commands if c["commandType"] == "liquidProbe"]
      self.assertEqual(len(probe_cmds), 1)
      params = probe_cmds[0]["params"]
      self.assertEqual(params["pipetteId"], head.pipette_id)
      self.assertEqual(params["wellName"], "B3")
      self.assertEqual(
        params["wellLocation"], {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 0}}
      )
    finally:
      asyncio.run(flex.stop())

  def test_liquid_probe_raises_when_no_liquid_found(self):
    flex, transport, head, rack, plate = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(OpentronsError):
        asyncio.run(head.liquid_probe(plate.get_item("B3")))

      # The probe command WAS sent -- absence of z_position in its result is
      # what raised, not a pre-wire guard.
      probe_cmds = [c for c in transport.commands if c["commandType"] == "liquidProbe"]
      self.assertEqual(len(probe_cmds), 1)
    finally:
      asyncio.run(flex.stop())

  def test_try_liquid_probe_returns_none_when_no_liquid_found(self):
    flex, transport, head, rack, plate = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      z = asyncio.run(head.try_liquid_probe(plate.get_item("B3")))

      self.assertIsNone(z)
      probe_cmds = [c for c in transport.commands if c["commandType"] == "tryLiquidProbe"]
      self.assertEqual(len(probe_cmds), 1)
    finally:
      asyncio.run(flex.stop())

  def test_try_liquid_probe_returns_configured_z(self):
    flex, transport, head, rack, plate = self._bench(liquid_probe_z=4.75)
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      z = asyncio.run(head.try_liquid_probe(plate.get_item("B3")))
      self.assertEqual(z, 4.75)
    finally:
      asyncio.run(flex.stop())

  def test_liquid_probe_without_tip_raises_and_sends_nothing(self):
    flex, transport, head, _rack, plate = self._bench(liquid_probe_z=12.5)
    try:
      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.liquid_probe(plate.get_item("B3")))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestLiquidProbeHead8(unittest.TestCase):
  """FlexHead8 liquid probing is column-addressed: one probe command anchored
  at the column's A-row well."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def _bench(self, **transport_kwargs):
    flex, transport, head = _flex_head8(**transport_kwargs)
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    return flex, transport, head, rack, plate

  def test_liquid_probe_anchors_at_column_a_row_well_and_returns_z(self):
    flex, transport, head, rack, plate = self._bench(liquid_probe_z=7.25)
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      z = asyncio.run(head.liquid_probe(plate, column=3))

      self.assertEqual(z, 7.25)
      probe_cmds = [c for c in transport.commands if c["commandType"] == "liquidProbe"]
      self.assertEqual(len(probe_cmds), 1)
      self.assertEqual(probe_cmds[0]["params"]["wellName"], "A4")
    finally:
      asyncio.run(flex.stop())

  def test_try_liquid_probe_returns_none_when_no_liquid_found(self):
    flex, transport, head, rack, plate = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      z = asyncio.run(head.try_liquid_probe(plate, column=3))

      self.assertIsNone(z)
      probe_cmds = [c for c in transport.commands if c["commandType"] == "tryLiquidProbe"]
      self.assertEqual(len(probe_cmds), 1)
      self.assertEqual(probe_cmds[0]["params"]["wellName"], "A4")
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()
