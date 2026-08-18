"""Tests for the fine-pipetting commands on the plain-class Flex heads.

Covers ``blow_out`` (in-place plunger blow-out, all heads), ``touch_tip``
(wall-touch, all heads) and ``liquid_probe``/``try_liquid_probe``
(pressure-based liquid-level detection, mount heads only), driven through the
recording ``ChatterboxTransport``. Two no-liquid signals are modeled: the
``liquid_probe_z`` kwarg omits the ``z_position`` result key entirely (a
succeeding transport's shape), and ``simulate_liquid_probe_not_found`` fails
the ``liquidProbe`` command with the engine's defined "liquidNotFound" error
(the real-hardware behavior). Also pins the shared well-position rule the
liquid ops build on -- a caller offset shifts the head from the default
position rather than replacing it -- and the one place that deliberately
does not follow it, ``touch_tip``; plus the two rules the single-nozzle
cherry-pick runs under: which nozzles an 8-channel Flex can anchor on, and
that no nozzle layout changes while a tip is mounted.

Also covers the base-class ops every head inherits: the in-place liquid ops
(``aspirate_in_place``/``dispense_in_place``/``air_gap_in_place``), which name
no well and move no tracker; the two tip-presence commands
(``get_tip_presence`` reports, ``verify_tip_presence`` makes the robot fail on
a mismatch); ``configure_for_volume``; and the ``unsafe_*`` recovery pair.
"""

import asyncio
import unittest
from typing import Any, Dict, Optional

from pylabrobot.opentrons.flex import OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead1
from pylabrobot.opentrons.flex_tests import _flex_head1, _flex_head8, _flex_head96
from pylabrobot.opentrons.robot import OpentronsCommandError, OpentronsError
from pylabrobot.opentrons.transport import ChatterboxTransport
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


class TestBlowOut(unittest.TestCase):
  """blow_out sends one blowOutInPlace command at the current position.

  It leaves the plunger past its dispense bottom, but the driver sends no
  prepareToAspirate to fix that: a following well-addressed aspirate names a
  well, so the robot primes at the well top and descends by itself."""

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
      # p50_multi_v3.5 on a 50uL tip, per Opentrons' shipped pipette data.
      self.assertEqual(
        blow_cmds[0]["params"],
        {"pipetteId": head.pipette_id, "flowRate": 57.0},
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

  def test_head8_next_aspirate_after_blow_out_sends_no_prepare(self):
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
      self.assertEqual(cmd_types.count("aspirate"), 2)
      self.assertNotIn("prepareToAspirate", cmd_types)
    finally:
      asyncio.run(flex.stop())

  def test_head1_blow_out_then_aspirate_sends_no_prepare(self):
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
      # p1000_single_v3.5 on a 50uL tip: a different pipette blows out at its own
      # rate, not the p50's 57.
      self.assertEqual(
        blow_cmds[0]["params"],
        {"pipetteId": head.pipette_id, "flowRate": 478.0},
      )
      cmd_types = [c["commandType"] for c in transport.commands]
      self.assertEqual(cmd_types.count("aspirate"), 2)
      self.assertNotIn("prepareToAspirate", cmd_types)
    finally:
      asyncio.run(flex.stop())

  def test_head96_blow_out_then_aspirate_sends_no_prepare(self):
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
      self.assertEqual(cmd_types.count("aspirate"), 2)
      self.assertNotIn("prepareToAspirate", cmd_types)
    finally:
      asyncio.run(flex.stop())


class TestTouchTipHead1(unittest.TestCase):
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
        params["wellLocation"], {"origin": "top", "offset": {"x": 1, "y": 2, "z": 3}}
      )
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_defaults_radius_one_and_1mm_below_rim(self):
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
        params["wellLocation"], {"origin": "top", "offset": {"x": 0, "y": 0, "z": -1.0}}
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
      # The probe starts 2 mm above the well rim (the engine's own start
      # offset) and descends; a bottom-origin start would begin at the floor.
      self.assertEqual(
        params["wellLocation"], {"origin": "top", "offset": {"x": 0, "y": 0, "z": 2.0}}
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
      probe_cmds = [c for c in transport.commands if c["commandType"] == "tryLiquidProbe"]
      self.assertEqual(
        probe_cmds[0]["params"]["wellLocation"],
        {"origin": "top", "offset": {"x": 0, "y": 0, "z": 2.0}},
      )
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

  def test_liquid_probe_wire_failure_translates_to_liquid_not_found(self):
    # Real hardware FAILS the liquidProbe command with the defined
    # "liquidNotFound" error when no liquid is detected.
    flex, transport, head, rack, plate = self._bench(simulate_liquid_probe_not_found=True)
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(OpentronsError) as ctx:
        asyncio.run(head.liquid_probe(plate.get_item("B3")))

      self.assertEqual(ctx.exception.title, "LiquidNotFoundError")
      probe_cmds = [c for c in transport.commands if c["commandType"] == "liquidProbe"]
      self.assertEqual(len(probe_cmds), 1, "the probe reached the wire and failed there")
    finally:
      asyncio.run(flex.stop())

  def test_try_liquid_probe_unaffected_by_liquid_probe_failure_mode(self):
    # tryLiquidProbe genuinely succeeds with the z_position key absent.
    flex, _transport, head, rack, plate = self._bench(simulate_liquid_probe_not_found=True)
    try:
      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      self.assertIsNone(asyncio.run(head.try_liquid_probe(plate.get_item("B3"))))
    finally:
      asyncio.run(flex.stop())

  def test_liquid_probe_other_wire_failure_reraises_untranslated(self):
    class _OverpressureProbeTransport(ChatterboxTransport):
      """Fails liquidProbe with a different defined error than liquidNotFound."""

      async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = await super().post(path, json)
        data = (json or {}).get("data", {})
        if path.endswith("/commands") and data.get("commandType") == "liquidProbe":
          cmd_data = result["data"]
          cmd_data["status"] = "failed"
          cmd_data["error"] = {"errorType": "overpressure", "detail": "clogged tip"}
        return result

    transport = _OverpressureProbeTransport(
      pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")]
    )
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    try:
      head = flex.right
      assert isinstance(head, FlexHead1)
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(OpentronsCommandError) as ctx:
        asyncio.run(head.liquid_probe(plate.get_item("B3")))
      self.assertEqual(ctx.exception.error_type, "overpressure")
    finally:
      asyncio.run(flex.stop())

  def test_a_probe_refused_for_an_unprimed_plunger_carries_the_priming_remedy(self):
    """A probe pushes the plunger, so the robot refuses it on an unprimed one
    even though a well IS named: getting there means the tip has held liquid,
    and a probe wants a dry tip. The driver translates it like an in-place
    draw rather than letting the raw wire error through."""

    class _UnprimedProbeTransport(ChatterboxTransport):
      async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = await super().post(path, json)
        data = (json or {}).get("data", {})
        if path.endswith("/commands") and data.get("commandType") == "liquidProbe":
          cmd_data = result["data"]
          cmd_data["status"] = "failed"
          cmd_data["error"] = {
            "errorType": "PipetteNotReadyToAspirateError",
            "detail": "The pipette cannot probe liquid because a previous dispense or "
            "blowout pushed the plunger beyond the bottom position.",
          }
        return result

    transport = _UnprimedProbeTransport(pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")])
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    try:
      head = flex.right
      assert isinstance(head, FlexHead1)
      rack = flex_96_tiprack_50ul(name="rack1")
      plate = cor_96_wellplate_360uL_Fb(name="plate1")
      plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
      flex.deck.assign_child_at_slot(rack, "C1")
      flex.deck.assign_child_at_slot(plate, "C2")

      asyncio.run(head.pick_up_tips(rack.get_item("A1")))
      with self.assertRaises(OpentronsError) as caught:
        asyncio.run(head.liquid_probe(plate.get_item("B3")))
      self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
      self.assertIn("prepare_to_aspirate()", str(caught.exception))
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

  def test_liquid_probe_without_tips_raises_and_sends_nothing(self):
    flex, transport, head, _rack, plate = self._bench(liquid_probe_z=7.25)
    try:
      n_before = len(transport.commands)
      with self.assertRaises(OpentronsError):
        asyncio.run(head.liquid_probe(plate, column=3))

      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestHead8ColumnValidation(unittest.TestCase):
  """Every FlexHead8 column op validates the column against the labware's
  real grid BEFORE any wire command (including configureNozzleLayout and
  loadLabware), so a rejected op ships nothing."""

  def setUp(self):
    set_tip_tracking(True)
    set_volume_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)
    set_volume_tracking(False)

  def _bench(self):
    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    return flex, transport, head, rack, plate

  def test_out_of_range_columns_reject_every_op_with_zero_wire_commands(self):
    flex, transport, head, rack, plate = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))  # the trio needs mounted tips

      for column in (-1, 12, 15):
        ops = [
          lambda c=column: head.pick_up_tips(rack, column=c),
          lambda c=column: head.drop_tips(rack, column=c),
          lambda c=column: head.aspirate(plate, column=c, volume=10),
          lambda c=column: head.dispense(plate, column=c, volume=10),
          lambda c=column: head.touch_tip(plate, column=c),
          lambda c=column: head.liquid_probe(plate, column=c),
          lambda c=column: head.try_liquid_probe(plate, column=c),
        ]
        for op in ops:
          n_before = len(transport.commands)
          with self.assertRaises(ValueError):
            asyncio.run(op())
          self.assertEqual(
            len(transport.commands), n_before, f"column {column} rejection must ship nothing"
          )
    finally:
      asyncio.run(flex.stop())

  def test_column_minus_one_does_not_alias_to_the_last_column(self):
    # Negative indexing must not silently address column 12's wells.
    flex, transport, head, rack, _plate = self._bench()
    try:
      n_before = len(transport.commands)
      with self.assertRaises(ValueError):
        asyncio.run(head.pick_up_tips(rack, column=-1))
      self.assertEqual(len(transport.commands), n_before)
      for spot in rack.get_all_items():
        self.assertTrue(spot.has_tip(), spot.name)
    finally:
      asyncio.run(flex.stop())

  def test_384_well_plate_addresses_two_interleaved_row_sets_per_physical_column(self):
    # The nozzles hold their 9 mm pitch, so on a 16-row plate they cover
    # every other row: two sets of 8 per physical column, indexed in turn.
    flex, transport, head, rack, _plate = self._bench()
    try:
      plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
      flex.deck.assign_child_at_slot(plate_384, "C3")
      asyncio.run(head.pick_up_tips(rack, column=0))

      for column, expected in ((0, "A1"), (1, "B1"), (2, "A2"), (47, "B24")):
        anchor, items = head._column_anchor_and_items(plate_384, column)
        self.assertEqual(anchor, expected, f"column {column}")
        self.assertEqual(len(items), 8)
      # Column 0 covers the rear-row set the engine's own coverage math
      # reports for a full 8-channel configuration anchored at A1.
      _anchor, items = head._column_anchor_and_items(plate_384, 0)
      self.assertEqual(
        [plate_384.get_child_identifier(item) for item in items],
        ["A1", "C1", "E1", "G1", "I1", "K1", "M1", "O1"],
      )
      _anchor, items = head._column_anchor_and_items(plate_384, 1)
      self.assertEqual(
        [plate_384.get_child_identifier(item) for item in items],
        ["B1", "D1", "F1", "H1", "J1", "L1", "N1", "P1"],
      )
    finally:
      asyncio.run(flex.stop())

  def test_384_well_plate_column_op_reaches_the_wire_at_its_anchor_well(self):
    flex, transport, head, rack, _plate = self._bench()
    try:
      plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
      flex.deck.assign_child_at_slot(plate_384, "C3")
      for well in plate_384.get_all_items():
        well.tracker.set_volume(40.0)
      asyncio.run(head.pick_up_tips(rack, column=0))

      asyncio.run(head.aspirate(plate_384, column=3, volume=10))

      aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
      self.assertEqual(len(aspirate_cmds), 1)
      self.assertEqual(aspirate_cmds[0]["params"]["wellName"], "B2")
      # Only the 8 wells the nozzles actually reach lose liquid.
      touched = ["B2", "D2", "F2", "H2", "J2", "L2", "N2", "P2"]
      for name in touched:
        self.assertAlmostEqual(plate_384.get_item(name).tracker.volume, 30.0, msg=name)
      self.assertAlmostEqual(plate_384.get_item("A2").tracker.volume, 40.0)
      self.assertAlmostEqual(plate_384.get_item("C2").tracker.volume, 40.0)
    finally:
      asyncio.run(flex.stop())

  def test_384_well_plate_rejects_out_of_range_column_pre_wire(self):
    flex, transport, head, rack, _plate = self._bench()
    try:
      plate_384 = biorad_384_wellplate_50uL_Vb(name="plate384")
      flex.deck.assign_child_at_slot(plate_384, "C3")
      asyncio.run(head.pick_up_tips(rack, column=0))

      n_before = len(transport.commands)
      for op in (
        lambda: head.aspirate(plate_384, column=48, volume=10),
        lambda: head.liquid_probe(plate_384, column=100),
        lambda: head.touch_tip(plate_384, column=-1),
      ):
        with self.assertRaises(ValueError):
          asyncio.run(op())
      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_row_count_that_is_not_a_multiple_of_eight_still_rejects_pre_wire(self):
    # A 4-row (24-well) plate has no set of 8 evenly spaced rows, so the
    # narrowing that protects hardware survives the 384 widening.
    flex, transport, head, rack, _plate = self._bench()
    try:
      plate_24 = cor_cos_24_wellplate_3470uL_Fb(name="plate24")
      flex.deck.assign_child_at_slot(plate_24, "C3")
      asyncio.run(head.pick_up_tips(rack, column=0))

      n_before = len(transport.commands)
      with self.assertRaises(ValueError):
        asyncio.run(head.touch_tip(plate_24, column=0))
      self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestWellPositionOffsets(unittest.TestCase):
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

  def _bench(self):
    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(rack, "C1")
    flex.deck.assign_child_at_slot(plate, "C2")
    for well in plate.get_all_items():
      well.tracker.set_volume(100.0)
    asyncio.run(head.pick_up_tips(rack, column=0))
    return flex, transport, head, plate

  def _aspirate_well_location(self, transport: ChatterboxTransport) -> dict:
    aspirate_cmds = [c for c in transport.commands if c["commandType"] == "aspirate"]
    self.assertEqual(len(aspirate_cmds), 1)
    well_location: dict = aspirate_cmds[0]["params"]["wellLocation"]
    return well_location

  def test_lateral_offset_on_a_plate_column_keeps_the_bottom_clearance(self):
    flex, transport, head, plate = self._bench()
    try:
      asyncio.run(head.aspirate(plate, column=0, volume=10, offset=Coordinate(x=1, y=2)))
      self.assertEqual(
        self._aspirate_well_location(transport),
        {"origin": "bottom", "offset": {"x": 1, "y": 2, "z": 1.0}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_zero_offset_keeps_the_bottom_clearance(self):
    flex, transport, head, plate = self._bench()
    try:
      asyncio.run(head.aspirate(plate, column=0, volume=10, offset=Coordinate.zero()))
      self.assertEqual(
        self._aspirate_well_location(transport),
        {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 1.0}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_liquid_height_is_the_base_the_offset_rides_on(self):
    # liquid_height is already measured from the bottom, so it replaces the
    # clearance as the base; the offset still adds on top of whichever base.
    flex, transport, head, plate = self._bench()
    try:
      asyncio.run(
        head.aspirate(plate, column=0, volume=10, offset=Coordinate(x=2, z=0.5), liquid_height=3)
      )
      self.assertEqual(
        self._aspirate_well_location(transport),
        {"origin": "bottom", "offset": {"x": 2, "y": 0, "z": 3.5}},
      )
    finally:
      asyncio.run(flex.stop())

  def test_touch_tip_offset_replaces_while_aspirate_offset_shifts(self):
    # The same Coordinate(x=1) means different things on the two calls, which
    # is why both public docstrings spell the rule out: touch_tip's z IS the
    # touch height (the Opentrons Python API's absolute v_offset).
    flex, transport, head, plate = self._bench()
    try:
      asyncio.run(head.aspirate(plate, column=0, volume=10, offset=Coordinate(x=1)))
      asyncio.run(head.touch_tip(plate, column=0, offset=Coordinate(x=1)))
      touch_cmds = [c for c in transport.commands if c["commandType"] == "touchTip"]
      self.assertEqual(
        self._aspirate_well_location(transport),
        {"origin": "bottom", "offset": {"x": 1, "y": 0, "z": 1.0}},
      )
      self.assertEqual(
        touch_cmds[0]["params"]["wellLocation"],
        {"origin": "top", "offset": {"x": 1, "y": 0, "z": 0}},
      )
    finally:
      asyncio.run(flex.stop())


class TestSingleNozzleLayout(unittest.TestCase):
  """An 8-channel Flex can anchor a SINGLE nozzle layout on its "A1" or "H1"
  nozzle and nothing else: the engine's primaryNozzle is a literal of those
  (plus the 12-column ends a 96-head uses), and the pipette's own
  validNozzleMaps carry only SingleA1/SingleH1. The engine also refuses ANY
  nozzle reconfiguration, "ALL" included, while a tip is attached."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  def _bench(self, slot="C1"):
    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, slot)
    return flex, transport, head, rack

  def _nozzle_params(self, transport: ChatterboxTransport) -> list:
    return [
      c["params"]["configurationParams"]
      for c in transport.commands
      if c["commandType"] == "configureNozzleLayout"
    ]

  def test_the_front_most_row_is_pickable_from_a_full_rack_on_the_rear_anchor(self):
    # The workable single-tip pattern on a ganged head: take the front-most row
    # anchored on the REAR nozzle, so the idle seven extend past the front edge
    # over nothing. Consuming a rack front-to-back keeps that true every time.
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="H3"))
      self.assertEqual(self._nozzle_params(transport)[-1]["primaryNozzle"], "A1")
      pickups = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(pickups[-1]["params"]["wellName"], "H3")
      self.assertIsNotNone(head.get_mounted_tips()[0])
    finally:
      asyncio.run(flex.stop())

  def test_the_anchor_nozzle_decides_the_channel_not_the_wells_row(self):
    # In a SINGLE layout the engine moves the chosen nozzle over whatever well
    # is named, so the tip lands on that nozzle's channel. Row A is the rear
    # nozzle's own row, and naming H1 still puts the tip on channel 7.
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="A3", primary_nozzle="H1"))

      self.assertEqual(self._nozzle_params(transport)[-1]["primaryNozzle"], "H1")
      pickups = [c for c in transport.commands if c["commandType"] == "pickUpTip"]
      self.assertEqual(pickups[-1]["params"]["wellName"], "A3")
      self.assertIsNotNone(head.get_mounted_tips()[7])
      self.assertTrue(all(tip is None for i, tip in enumerate(head.get_mounted_tips()) if i != 7))
    finally:
      asyncio.run(flex.stop())

  def test_a_rear_row_refuses_the_default_anchor_and_names_the_front_one(self):
    # Ganged nozzles descend together, so the REAR anchor on a rear row puts the
    # idle seven over rows B-H: eight tips. Refused, not silently swapped.
    flex, transport, head, rack = self._bench()
    try:
      with self.assertRaises(ValueError) as caught:
        asyncio.run(head.pick_up_single_tip(rack, well="A2"))
      self.assertIn("more than one tip", str(caught.exception))
      self.assertIn("Anchor on H1", str(caught.exception))

      asyncio.run(head.pick_up_single_tip(rack, well="A2", primary_nozzle="H1"))
      self.assertEqual(self._nozzle_params(transport)[-1]["primaryNozzle"], "H1")
      self.assertIsNotNone(head.get_mounted_tips()[7])
    finally:
      asyncio.run(flex.stop())

  def test_the_default_anchor_is_the_rear_nozzle_whatever_the_rack_holds(self):
    # The anchor fixes where the idle seven hang for the tip's whole life, so it
    # is declared, not derived: same call, same anchor, full rack or nearly bare.
    for empty_the_rest in (False, True):
      flex, transport, head, rack = self._bench()
      try:
        if empty_the_rest:
          for spot in rack.get_all_items():
            if spot.name != rack.get_item("H3").name and spot.has_tip():
              spot.tracker.remove_tip(commit=True)
        asyncio.run(head.pick_up_single_tip(rack, well="H3"))
        self.assertEqual(self._nozzle_params(transport)[-1]["primaryNozzle"], "A1")
        self.assertIsNotNone(head.get_mounted_tips()[0])
      finally:
        asyncio.run(flex.stop())

  def test_a_full_rack_middle_row_is_refused_rather_than_taking_a_handful_of_tips(self):
    # No anchor clears a middle row of a full rack: whichever end you pick,
    # some idle nozzles sit over tips. Refusing beats silently taking several.
    flex, _transport, head, rack = self._bench()
    try:
      with self.assertRaises(ValueError) as caught:
        asyncio.run(head.pick_up_single_tip(rack, well="D2"))
      self.assertIn("more than one tip", str(caught.exception))
    finally:
      asyncio.run(flex.stop())

  def test_a_middle_row_is_allowed_once_its_neighbours_are_already_empty(self):
    # Same well, but with the rest of the column consumed the idle nozzles have
    # nothing to engage, so the pick really is single.
    flex, transport, head, rack = self._bench()
    try:
      for spot in rack.get_all_items():
        if spot.name != rack.get_item("D2").name and spot.has_tip():
          spot.tracker.remove_tip(commit=True)
      asyncio.run(head.pick_up_single_tip(rack, well="D2"))
      self.assertEqual(self._nozzle_params(transport)[-1]["primaryNozzle"], "A1")
    finally:
      asyncio.run(flex.stop())

  def test_an_unanchorable_nozzle_is_rejected_pre_wire(self):
    flex, transport, head, rack = self._bench()
    try:
      commands_before = len(transport.commands)
      with self.assertRaises(ValueError):
        asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="B1"))
      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_reconfiguring_the_layout_while_a_tip_is_mounted_is_refused(self):
    flex, transport, head, rack = self._bench()
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    plate.ot_load_name = "corning_96_wellplate_360ul_flat"  # type: ignore[attr-defined]
    flex.deck.assign_child_at_slot(plate, "C2")
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1"))
      commands_before = len(transport.commands)

      # A column op resets the layout to ALL, which the robot refuses while
      # the single tip is still on.
      with self.assertRaises(OpentronsError) as caught:
        asyncio.run(head.aspirate(plate, column=1, volume=10))
      self.assertIn("nozzle layout", str(caught.exception))
      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_dropping_the_single_tip_restores_all_mode(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1"))
      asyncio.run(head.drop_single_tip(flex.deck.get_trash_area()))

      self.assertEqual(self._nozzle_params(transport)[-1], {"style": "ALL"})
      self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_discarding_a_cherry_picked_tip_drops_it_then_restores_all_mode(self):
    # A trash drop addresses no channel, so it must not wait on a layout
    # reset the robot refuses while the tip is still on.
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1"))
      asyncio.run(head.discard_tips(flex.deck.get_trash_area()))

      self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
      sent = [c["commandType"] for c in transport.commands]
      self.assertLess(sent.index("dropTipInPlace"), len(sent) - 1)
      self.assertEqual(sent[-1], "configureNozzleLayout")
      self.assertEqual(self._nozzle_params(transport)[-1], {"style": "ALL"})
    finally:
      asyncio.run(flex.stop())

  def test_returning_a_cherry_picked_tip_to_a_rack_column_names_the_op_that_works(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1"))
      commands_before = len(transport.commands)

      with self.assertRaises(OpentronsError) as caught:
        asyncio.run(head.drop_tips(rack, column=0))
      self.assertIn("drop_single_tip", str(caught.exception))
      self.assertEqual(len(transport.commands), commands_before)

      asyncio.run(head.drop_single_tip(flex.deck.get_trash_area()))
    finally:
      asyncio.run(flex.stop())

  def test_a_front_row_slot_puts_the_rear_anchor_out_of_reach(self):
    # Anchoring A1 over D1's front rows leaves the mount ahead of the robot's
    # own front limit: the pipette body reaches 95mm forward of it.
    flex, _, head, rack = self._bench(slot="D1")
    try:
      self.assertEqual(head.reachable_single_nozzles(rack, "A1"), ("A1", "H1"))
      self.assertEqual(head.reachable_single_nozzles(rack, "F1"), ("H1",))
      self.assertEqual(head.reachable_single_nozzles(rack, "H1"), ("H1",))
    finally:
      asyncio.run(flex.stop())

  def test_on_a_front_slot_one_anchor_cannot_reach_and_the_other_cannot_clear(self):
    # On a front slot the rear anchor overruns the robot's front limit and the
    # front anchor hangs the idle seven back over full rows. Each refusal says which.
    flex, transport, head, rack = self._bench(slot="D1")
    try:
      commands_before = len(transport.commands)
      with self.assertRaises(ValueError) as reach:
        asyncio.run(head.pick_up_single_tip(rack, well="F1", primary_nozzle="A1"))
      self.assertIn("outside the robot", str(reach.exception))

      with self.assertRaises(ValueError) as clearance:
        asyncio.run(head.pick_up_single_tip(rack, well="F1", primary_nozzle="H1"))
      self.assertIn("more than one tip", str(clearance.exception))

      self.assertEqual(len(transport.commands), commands_before, "must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_an_explicit_out_of_reach_anchor_is_refused_naming_the_one_that_works(self):
    # An explicit choice is never silently swapped.
    flex, transport, head, rack = self._bench(slot="D1")
    try:
      commands_before = len(transport.commands)
      with self.assertRaises(ValueError) as caught:
        asyncio.run(head.pick_up_single_tip(rack, well="F1", primary_nozzle="A1"))
      self.assertIn("H1", str(caught.exception))
      self.assertEqual(len(transport.commands), commands_before)
    finally:
      asyncio.run(flex.stop())

  def test_every_flex_slot_well_is_reachable_by_one_anchor_or_the_other(self):
    # No slot the deck accepts leaves a well unpickable; A3 holds the trash.
    for slot in [f"{row}{col}" for row in "ABCD" for col in "123" if f"{row}{col}" != "A3"]:
      flex, _, head, rack = self._bench(slot=slot)
      try:
        for well_row in "ABCDEFGH":
          reachable = head.reachable_single_nozzles(rack, f"{well_row}1")
          self.assertTrue(reachable, f"{slot} {well_row}1 unreachable both ways")
      finally:
        asyncio.run(flex.stop())

  def test_stop_leaves_no_cherry_picked_tip_on_the_pipette(self):
    flex, transport, head, rack = self._bench()
    asyncio.run(head.pick_up_single_tip(rack, well="A1", primary_nozzle="H1"))

    asyncio.run(flex.stop())

    self.assertIn("dropTipInPlace", [c["commandType"] for c in transport.commands])
    self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))


class TestInPlaceLiquidOps(unittest.TestCase):
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

  def _bench(self):
    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, "C1")
    return flex, transport, head, rack

  def _params(self, transport: ChatterboxTransport, command_type: str) -> dict:
    cmds = [c for c in transport.commands if c["commandType"] == command_type]
    self.assertEqual(len(cmds), 1)
    params: dict = cmds[0]["params"]
    return params

  def test_aspirate_in_place_sends_volume_and_the_aspirate_default_flow_rate(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))

      self.assertEqual(
        self._params(transport, "aspirateInPlace"),
        {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 35.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_aspirate_in_place_accepts_a_flow_rate_override(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15, flow_rate=3.5))

      self.assertEqual(self._params(transport, "aspirateInPlace")["flowRate"], 3.5)
    finally:
      asyncio.run(flex.stop())

  def test_a_pickup_leaves_the_plunger_ready_so_in_place_draws_need_no_prepare(self):
    """The robot primes as part of a tip pickup, so back-to-back in-place
    aspirates straight off a fresh tip send no prepareToAspirate at all."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))
      asyncio.run(head.aspirate_in_place(volume=15))

      sent = [c["commandType"] for c in transport.commands]
      self.assertEqual(sent.count("aspirateInPlace"), 2)
      self.assertNotIn("prepareToAspirate", sent)
    finally:
      asyncio.run(flex.stop())

  def _assert_in_place_draw_is_refused(self, head, transport):
    """The robot refuses, and the driver hands back a message naming both remedies."""
    n_before = len(transport.commands)
    with self.assertRaises(OpentronsError) as caught:
      asyncio.run(head.aspirate_in_place(volume=15))
    self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    self.assertIn("prepare_to_aspirate()", str(caught.exception))
    self.assertIn("aspirate from a well", str(caught.exception))
    self.assertEqual(len(transport.commands), n_before + 1, "the refusal comes from the robot")

  def test_a_dispense_that_empties_the_tip_makes_the_next_in_place_draw_refuse(self):
    """The ordinary transfer loop's second draw: emptying the tip leaves the
    plunger past its bottom, and with no well named the robot will not fix it."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))
      asyncio.run(head.dispense_in_place(volume=15))

      self._assert_in_place_draw_is_refused(head, transport)
    finally:
      asyncio.run(flex.stop())

  def test_a_partial_dispense_leaves_the_plunger_ready(self):
    """Only a dispense that EMPTIES the tip un-primes it, because only that one
    gets a push-out. Refusing after every dispense would refuse work the robot
    accepts."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))
      asyncio.run(head.dispense_in_place(volume=5))
      asyncio.run(head.aspirate_in_place(volume=5))

      sent = [c["commandType"] for c in transport.commands]
      self.assertEqual(sent.count("aspirateInPlace"), 2)
      self.assertNotIn("prepareToAspirate", sent)
    finally:
      asyncio.run(flex.stop())

  def test_lifting_clear_and_priming_by_hand_clears_the_refusal(self):
    """The documented remedy, end to end: the caller moves the tip out of the
    liquid, primes, and the in-place draw goes through."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))
      asyncio.run(head.dispense_in_place(volume=15))
      self._assert_in_place_draw_is_refused(head, transport)

      asyncio.run(head.prepare_to_aspirate())
      asyncio.run(head.aspirate_in_place(volume=15))

      sent = [c["commandType"] for c in transport.commands]
      self.assertEqual(sent[-1], "aspirateInPlace")
      self.assertEqual(sent.count("prepareToAspirate"), 1)
    finally:
      asyncio.run(flex.stop())

  def test_configure_for_volume_makes_the_next_in_place_draw_refuse(self):
    """Switching volume mode moves where the plunger bottom IS, so the robot
    drops its ready flag."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.configure_for_volume(10.0))

      self._assert_in_place_draw_is_refused(head, transport)
    finally:
      asyncio.run(flex.stop())

  def test_a_blow_out_makes_the_next_in_place_draw_refuse(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=15))
      asyncio.run(head.blow_out())

      self._assert_in_place_draw_is_refused(head, transport)
    finally:
      asyncio.run(flex.stop())

  def test_an_air_gap_in_place_refuses_the_same_way(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.blow_out())

      with self.assertRaises(OpentronsError) as caught:
        asyncio.run(head.air_gap_in_place(volume=10))
      self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    finally:
      asyncio.run(flex.stop())

  def test_dispense_in_place_sends_the_dispense_default_and_omits_push_out(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.dispense_in_place(volume=15))

      self.assertEqual(
        self._params(transport, "dispenseInPlace"),
        {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 57.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_dispense_in_place_carries_push_out_only_when_given(self):
    # Omitted rather than sent as null, so the robot picks its own push-out
    # for the mounted tip and volume.
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.dispense_in_place(volume=15, flow_rate=8.0, push_out=2.5))

      self.assertEqual(
        self._params(transport, "dispenseInPlace"),
        {"pipetteId": head.pipette_id, "volume": 15, "flowRate": 8.0, "pushOut": 2.5},
      )
    finally:
      asyncio.run(flex.stop())

  def test_air_gap_in_place_uses_the_aspirate_default_flow_rate(self):
    # The air gap IS an aspirate motion, so it takes the aspirate default,
    # not the dispense one.
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.air_gap_in_place(volume=5))

      self.assertEqual(
        self._params(transport, "airGapInPlace"),
        {"pipetteId": head.pipette_id, "volume": 5, "flowRate": 35.0},
      )
    finally:
      asyncio.run(flex.stop())

  def test_air_gap_in_place_accepts_a_flow_rate_override(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.air_gap_in_place(volume=5, flow_rate=1.25))

      self.assertEqual(self._params(transport, "airGapInPlace")["flowRate"], 1.25)
    finally:
      asyncio.run(flex.stop())

  def test_every_in_place_op_without_a_tip_raises_and_sends_nothing(self):
    flex, transport, head, _rack = self._bench()
    try:
      for op in (
        lambda: head.aspirate_in_place(volume=15),
        lambda: head.dispense_in_place(volume=15),
        lambda: head.air_gap_in_place(volume=5),
      ):
        n_before = len(transport.commands)
        with self.assertRaises(OpentronsError):
          asyncio.run(op())
        self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())

  def test_head1_and_head96_inherit_the_in_place_ops(self):
    # They live on the base class, so every head issues them identically.
    flex1, transport1, head1 = _flex_head1()
    try:
      rack1 = flex_96_tiprack_50ul(name="rack1")
      flex1.deck.assign_child_at_slot(rack1, "C1")
      asyncio.run(head1.pick_up_tips(rack1.get_item("A1")))
      asyncio.run(head1.aspirate_in_place(volume=15))
      asyncio.run(head1.dispense_in_place(volume=15))

      # Each head carries its OWN pipette's default, not one shared number:
      # p1000_single_v3.5 on a 50uL tip against the 96-head's 6.0 below.
      self.assertEqual(
        self._params(transport1, "aspirateInPlace"),
        {"pipetteId": head1.pipette_id, "volume": 15, "flowRate": 478.0},
      )
      self.assertEqual(self._params(transport1, "dispenseInPlace")["volume"], 15)
    finally:
      asyncio.run(flex1.stop())

    flex96, transport96, head96 = _flex_head96()
    try:
      rack96 = flex_96_tiprack_50ul(name="rack96")
      flex96.deck.assign_child_at_slot(rack96, "C1")
      asyncio.run(head96.pick_up_tips(rack96))
      asyncio.run(head96.air_gap_in_place(volume=5))

      self.assertEqual(
        self._params(transport96, "airGapInPlace"),
        {"pipetteId": head96.pipette_id, "volume": 5, "flowRate": 6.0},
      )
    finally:
      asyncio.run(flex96.stop())


class _TipPresenceTransport(ChatterboxTransport):
  """Answers getTipPresence with a fixed sensor reading."""

  def __init__(self, status: str, **kwargs):
    super().__init__(**kwargs)
    self.status = status

  async def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = await super().post(path, json)
    data = (json or {}).get("data", {})
    if path.endswith("/commands") and data.get("commandType") == "getTipPresence":
      result["data"]["result"] = {"status": self.status}
    return result


class TestTipPresenceCommands(unittest.TestCase):
  """get_tip_presence returns the sensor reading from the command result and
  leaves the judgement to the caller; verify_tip_presence hands the robot the
  state to check against, and refuses a state that is neither "present" nor
  "absent" before anything reaches the wire."""

  def setUp(self):
    set_tip_tracking(True)

  def tearDown(self):
    set_tip_tracking(False)

  def _head1_with_status(self, status: str):
    transport = _TipPresenceTransport(
      status, pipettes=[("p1000_single_flex", 1, 1.0, 1000.0, "right")]
    )
    flex = OpentronsFlex(deck=FlexDeck(), host="localhost", transport=transport)
    asyncio.run(flex.setup())
    head = flex.right
    assert isinstance(head, FlexHead1)
    return flex, transport, head

  def test_get_tip_presence_returns_the_reported_status(self):
    flex, transport, head = self._head1_with_status("present")
    try:
      self.assertEqual(asyncio.run(head.get_tip_presence()), "present")

      presence_cmds = [c for c in transport.commands if c["commandType"] == "getTipPresence"]
      self.assertEqual(len(presence_cmds), 1)
      self.assertEqual(presence_cmds[0]["params"], {"pipetteId": head.pipette_id})
    finally:
      asyncio.run(flex.stop())

  def test_get_tip_presence_reports_absent_without_raising(self):
    flex, _transport, head = self._head1_with_status("absent")
    try:
      self.assertEqual(asyncio.run(head.get_tip_presence()), "absent")
    finally:
      asyncio.run(flex.stop())

  def test_verify_tip_presence_sends_the_expected_state(self):
    flex, transport, head = _flex_head8()
    try:
      rack = flex_96_tiprack_50ul(name="rack")
      flex.deck.assign_child_at_slot(rack, "C1")

      # Each state is asserted while it actually holds: the robot fails the
      # command on a mismatch, so a bare pair would be checking a lie.
      asyncio.run(head.verify_tip_presence("absent"))
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.verify_tip_presence("present"))

      verify_cmds = [c for c in transport.commands if c["commandType"] == "verifyTipPresence"]
      self.assertEqual(
        [c["params"] for c in verify_cmds],
        [
          {"pipetteId": head.pipette_id, "expectedState": "absent"},
          {"pipetteId": head.pipette_id, "expectedState": "present"},
        ],
      )
    finally:
      asyncio.run(flex.stop())

  def test_verify_tip_presence_rejects_any_other_state_and_sends_nothing(self):
    # "unknown" is a reading the sensor can return, not a state worth
    # asserting, so it is refused with the rest.
    flex, transport, head = _flex_head8()
    try:
      for state in ("unknown", "Present", "", "yes"):
        n_before = len(transport.commands)
        with self.assertRaises(ValueError):
          asyncio.run(head.verify_tip_presence(state))
        self.assertEqual(len(transport.commands), n_before, "rejection must not reach the wire")
    finally:
      asyncio.run(flex.stop())


class TestConfigureForVolume(unittest.TestCase):
  """configure_for_volume names the volume the pipette should be set up for.
  It runs with no tip mounted on purpose: the robot refuses a mode change
  while a tip is attached, so it belongs before the pickup."""

  def test_configure_for_volume_sends_pipette_id_and_volume_before_any_pickup(self):
    flex, transport, head = _flex_head8()
    try:
      asyncio.run(head.configure_for_volume(5.0))

      configure_cmds = [c for c in transport.commands if c["commandType"] == "configureForVolume"]
      self.assertEqual(len(configure_cmds), 1)
      self.assertEqual(configure_cmds[0]["params"], {"pipetteId": head.pipette_id, "volume": 5.0})
    finally:
      asyncio.run(flex.stop())


class TestUnsafeRecoveryOps(unittest.TestCase):
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

  def _bench(self):
    flex, transport, head = _flex_head8()
    rack = flex_96_tiprack_50ul(name="rack")
    flex.deck.assign_child_at_slot(rack, "C1")
    return flex, transport, head, rack

  def test_unsafe_drop_tip_in_place_sends_pipette_id_and_clears_mounted_tips(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.unsafe_drop_tip_in_place())

      drop_cmds = [c for c in transport.commands if c["commandType"] == "unsafe/dropTipInPlace"]
      self.assertEqual(len(drop_cmds), 1)
      self.assertEqual(drop_cmds[0]["params"], {"pipetteId": head.pipette_id})
      self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
    finally:
      asyncio.run(flex.stop())

  def test_unsafe_drop_tip_in_place_returns_no_tip_to_the_rack(self):
    # The tip falls where the head is, so the spot it came from stays empty.
    flex, _transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.unsafe_drop_tip_in_place())

      self.assertFalse(rack.get_item("A1").has_tip())
    finally:
      asyncio.run(flex.stop())

  def test_unsafe_blow_out_in_place_sends_the_given_flow_rate(self):
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.unsafe_blow_out_in_place(flow_rate=20.0))

      blow_cmds = [c for c in transport.commands if c["commandType"] == "unsafe/blowOutInPlace"]
      self.assertEqual(len(blow_cmds), 1)
      self.assertEqual(blow_cmds[0]["params"], {"pipetteId": head.pipette_id, "flowRate": 20.0})
    finally:
      asyncio.run(flex.stop())

  def test_an_unsafe_blow_out_makes_the_next_in_place_draw_refuse(self):
    """The recovery blow-out leaves the plunger where the ordinary one does,
    so the next in-place draw needs the same manual prime."""
    flex, transport, head, rack = self._bench()
    try:
      asyncio.run(head.pick_up_tips(rack, column=0))
      asyncio.run(head.aspirate_in_place(volume=10))
      asyncio.run(head.unsafe_blow_out_in_place(flow_rate=20.0))

      with self.assertRaises(OpentronsError) as caught:
        asyncio.run(head.aspirate_in_place(volume=10))
      self.assertEqual(caught.exception.title, "NotReadyToAspirateError")
    finally:
      asyncio.run(flex.stop())

  def test_unsafe_ops_still_run_when_this_process_knows_of_no_tip(self):
    # The old contract refused these unless this head's own bookkeeping showed a
    # tip. That bookkeeping is per-process and empty after any restart, which is
    # exactly when a tip is stranded on the nozzle and these calls are the only
    # way off it. Guarding on it made the escape hatch refuse in the one
    # situation it exists for, so the guard is gone and the commands go out.
    flex, transport, head, _rack = self._bench()
    try:
      self.assertTrue(all(tip is None for tip in head.get_mounted_tips()))
      for op, expected in (
        (head.unsafe_drop_tip_in_place, "unsafe/dropTipInPlace"),
        (lambda: head.unsafe_blow_out_in_place(flow_rate=20.0), "unsafe/blowOutInPlace"),
      ):
        n_before = len(transport.commands)
        asyncio.run(op())
        sent = [c["commandType"] for c in transport.commands[n_before:]]
        self.assertIn(expected, sent)
    finally:
      asyncio.run(flex.stop())


if __name__ == "__main__":
  unittest.main()
