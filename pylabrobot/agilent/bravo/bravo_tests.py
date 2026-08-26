"""Unit tests for :mod:`.bravo`.

Drives :class:`Bravo` against :class:`~.controllers.simulation.SimulationController`
(via the golden-frame package's recording wrapper, reused here rather than
building a second recorder) and asserts both the resulting controller-call
sequence and values that never reach a controller call directly, per the
two failure modes a golden-frame comparison alone would miss.

:class:`Bravo` builds a :class:`~.state_machine.engine.StateMachineEngine`
at construction time, which allocates an ``asyncio.Lock``; every test class
here is an :class:`unittest.IsolatedAsyncioTestCase` so that allocation
always happens against a live event loop, matching the golden-frame test
modules' own convention.
"""

from __future__ import annotations

import asyncio
import unittest

from .bravo import Bravo
from .config import BravoMachineConfig
from .controllers.base import AxisMoveInfo
from .deck.labware import Labware
from .head_mode import normalize_head_mode
from .state_machine.engine import ErrorAction
from .state_machine.golden_frame_support import (
  RecordingSimulationController,
  new_config,
  new_controller,
  new_teachpoints,
)
from .transport._bridge import AsyncTransportBase


def _plate(name: str = "test_plate") -> Labware:
  """Build a simple 96-well plate labware fixture."""
  return Labware(
    id=name,
    definition_id=name,
    name=name,
    height=14.0,
    width=85.5,
    length=127.5,
    wells=96,
    metadata={
      "kind": "plate",
      "base_class": "plate",
      "rows": 8,
      "cols": 12,
      "spacing_x_mm": 9.0,
      "spacing_y_mm": 9.0,
    },
  )


def _tip_box(name: str = "test_tipbox") -> Labware:
  """Build a simple 96-position tip box labware fixture."""
  return Labware(
    id=name,
    definition_id=name,
    name=name,
    height=60.0,
    width=85.5,
    length=127.5,
    wells=96,
    metadata={
      "kind": "tip_box",
      "base_class": "tip_box",
      "rows": 8,
      "cols": 12,
      "spacing_x_mm": 9.0,
      "spacing_y_mm": 9.0,
      "tip_definition_id": "st_30ul",
      "disposable_tip_capacity_ul": 30.0,
    },
  )


class _RecordingTransport(AsyncTransportBase):
  """A transport that records when setup/stop run, for ordering tests."""

  def __init__(self, order: "list[str]") -> None:
    super().__init__("fake", "test")
    self._order = order

  async def _open_io(self) -> None:
    self._order.append("transport.setup")

  async def _close_io(self) -> None:
    self._order.append("transport.stop")

  def send(self, data: bytes) -> None:
    raise NotImplementedError

  def receive(self, timeout: float = 2.0) -> bytes:
    raise NotImplementedError

  def receive_exact(self, num_bytes: int, timeout: float = 2.0) -> bytes:
    raise NotImplementedError


def _new_bravo(*, gripper: bool = True) -> "tuple[Bravo, RecordingSimulationController]":
  """Build a Bravo facade against a fresh recording simulation controller."""
  ctrl = RecordingSimulationController()
  config = new_config(gripper=gripper)
  config.head.teach_tip_length_mm = 26.1
  bravo = Bravo(ctrl, config=config, deck=None)
  # Bravo() builds its own default teachpoints for the configured head type,
  # which matches new_teachpoints()'s "96_d_70" default -- asserted directly
  # rather than assumed, since the two are built independently.
  assert bravo._teachpoints.as_dict() == new_teachpoints().as_dict()
  return bravo, ctrl


class SetupStopOrderingTests(unittest.IsolatedAsyncioTestCase):
  """setup() must bring the transport up before the controller; stop() the reverse."""

  async def test_setup_calls_transport_before_controller(self):
    order: "list[str]" = []
    ctrl = RecordingSimulationController()
    original_initialize = ctrl.initialize

    def recording_initialize():
      order.append("controller.initialize")
      return original_initialize()

    ctrl.initialize = recording_initialize  # type: ignore[method-assign]
    transport = _RecordingTransport(order)
    bravo = Bravo(ctrl, transport=transport, config=BravoMachineConfig())
    await bravo.setup()
    self.assertEqual(order, ["transport.setup", "controller.initialize"])

  async def test_stop_calls_controller_before_transport(self):
    order: "list[str]" = []
    ctrl = RecordingSimulationController()
    original_deinitialize = ctrl.deinitialize

    def recording_deinitialize():
      order.append("controller.deinitialize")
      return original_deinitialize()

    ctrl.deinitialize = recording_deinitialize  # type: ignore[method-assign]
    transport = _RecordingTransport(order)
    bravo = Bravo(ctrl, transport=transport, config=BravoMachineConfig())
    await bravo.stop()
    self.assertEqual(order, ["controller.deinitialize", "transport.stop"])

  async def test_setup_with_no_transport_only_initializes_the_controller(self):
    ctrl = RecordingSimulationController()
    bravo = Bravo(ctrl, config=BravoMachineConfig())
    await bravo.setup()  # must not raise despite transport=None

  async def test_setup_syncs_the_homed_axes_cache_from_the_controller(self):
    bravo, ctrl = _new_bravo()
    self.assertFalse(bravo.is_axis_homed("x"))
    await bravo.setup()
    # SimulationController starts every axis homed at its offset.
    self.assertTrue(bravo.is_axis_homed("x"))
    self.assertTrue(bravo.is_axis_homed("g"))

  async def test_stop_clears_the_homed_axes_cache(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    await bravo.stop()
    self.assertFalse(bravo.is_axis_homed("x"))


class HeadModeDefaultTests(unittest.IsolatedAsyncioTestCase):
  """The facade's head mode starts at the normalized all-barrels default."""

  async def test_default_head_mode_is_all_barrels_back_left(self):
    bravo, _ = _new_bravo()
    expected = normalize_head_mode("96_d_70", "all_barrels", "back_left")
    self.assertEqual(bravo.head_mode, expected)
    self.assertEqual(bravo.head_mode.subset_type, "all_barrels")
    self.assertEqual(bravo.head_mode.subset_config, "back_left")
    self.assertEqual(bravo.head_mode.row_count, 8)
    self.assertEqual(bravo.head_mode.column_count, 12)

  async def test_constructor_passes_all_barrels_back_left_literally(self):
    """normalize_head_mode collapses subset_config to "back_left" for
    "all_barrels" regardless of what was passed in, so the resulting
    HeadMode alone can't tell "back_left" apart from some other literal
    the constructor might have passed instead -- this pins the literal
    arguments themselves, via the module attribute bravo.py calls through.
    """
    import pylabrobot.agilent.bravo.bravo as bravo_module

    calls: list = []
    original = bravo_module.normalize_head_mode

    def recording(head_type, subset_type, subset_config, *args, **kwargs):
      calls.append((subset_type, subset_config))
      return original(head_type, subset_type, subset_config, *args, **kwargs)

    bravo_module.normalize_head_mode = recording
    try:
      _new_bravo()
    finally:
      bravo_module.normalize_head_mode = original
    self.assertEqual(calls[0], ("all_barrels", "back_left"))

  async def test_set_head_mode_updates_and_returns_the_new_mode(self):
    bravo, _ = _new_bravo()
    mode = bravo.set_head_mode("row", "back_left", row_count=1)
    self.assertEqual(bravo.head_mode, mode)
    self.assertEqual(mode.subset_type, "row")
    self.assertEqual(mode.row_count, 1)


class HeadIdentityTests(unittest.IsolatedAsyncioTestCase):
  """head_type/head_geometry/head_capacity/has_gripper/model_name never reach a controller call."""

  async def test_head_type_reflects_the_configured_head(self):
    ctrl = RecordingSimulationController()
    config = new_config()
    config.head.head_type = "384_d_70"
    bravo = Bravo(ctrl, config=config, deck=None)
    self.assertEqual(bravo.head_type, "384_d_70")

  async def test_head_geometry_for_96_d_70(self):
    bravo, _ = _new_bravo()
    geometry = bravo.head_geometry
    self.assertEqual((geometry.rows, geometry.columns), (8, 12))

  async def test_head_capacity_is_rows_times_columns(self):
    bravo, _ = _new_bravo()
    self.assertEqual(bravo.head_capacity, 96)

  async def test_has_gripper_reflects_the_controller(self):
    bravo, _ = _new_bravo(gripper=True)
    self.assertTrue(bravo.has_gripper)

  async def test_model_name_reflects_the_controller(self):
    bravo, ctrl = _new_bravo()
    self.assertEqual(bravo.model_name, ctrl.model_name)


class EngineErrorHandlerEscapeHatchTests(unittest.IsolatedAsyncioTestCase):
  """No error handler is registered by default (see the module docstring),
  but the engine itself is reachable via bravo.engine for a caller that
  wants the interactive abort/retry/ignore loop instead.
  """

  async def test_no_handler_by_default_a_step_failure_raises(self):
    ctrl = new_controller(all_homed=False)
    config = new_config(gripper=True)
    config.head.teach_tip_length_mm = 26.1
    bravo = Bravo(ctrl, config=config)
    with self.assertRaises(RuntimeError):
      await bravo.initialize()

  async def test_a_caller_registered_handler_can_intercept_the_same_failure(self):
    ctrl = new_controller(all_homed=False)
    config = new_config(gripper=True)
    config.head.teach_tip_length_mm = 26.1
    bravo = Bravo(ctrl, config=config)
    errors: list = []
    bravo.engine.set_error_handler(errors.append)

    async def auto_ignore():
      while True:
        if bravo.engine.awaiting_error_action:
          bravo.engine.resolve_error(ErrorAction.IGNORE)
          return
        await asyncio.sleep(0)

    task = asyncio.ensure_future(bravo.initialize())
    await auto_ignore()
    await task  # must not raise: the registered handler intercepted it
    self.assertEqual(len(errors), 1)


class InitializeTests(unittest.IsolatedAsyncioTestCase):
  """initialize() runs the full InitializeTask cold-start sequence."""

  async def test_initialize_homes_every_axis_when_the_w_prompt_is_disabled(self):
    ctrl = new_controller(all_homed=False)
    config = new_config(gripper=True)
    config.head.teach_tip_length_mm = 26.1
    config.safety.prompt_home_w = False
    bravo = Bravo(ctrl, config=config)
    await bravo.initialize()
    for axis in ("x", "y", "z", "w", "g", "zg"):
      self.assertTrue(bravo.is_axis_homed(axis))

  async def test_initialize_raises_instead_of_blocking_on_the_w_axis_prompt(self):
    """With prompt_home_w at its default (True) and no engine error handler
    registered, the W-axis confirmation step must raise a RuntimeError
    carrying the prompt's own message rather than hang waiting for an
    operator response that will never come.
    """
    ctrl = new_controller(all_homed=False)
    config = new_config(gripper=True)
    config.head.teach_tip_length_mm = 26.1
    self.assertTrue(config.safety.prompt_home_w)
    bravo = Bravo(ctrl, config=config)
    with self.assertRaises(RuntimeError) as ctx:
      await bravo.initialize()
    self.assertIn("W-axis", str(ctx.exception))
    # The exception propagated before home_w ran: W is still unhomed.
    self.assertFalse(bravo.is_axis_homed("w"))


class HomingTests(unittest.IsolatedAsyncioTestCase):
  """home() reaches the engine and produces the expected controller calls."""

  async def test_home_with_default_axes_homes_xyzwg_zg_in_safe_order(self):
    bravo, ctrl = _new_bravo()
    homed = await bravo.home()
    self.assertEqual(homed, ["z", "zg", "g", "x", "y", "w"])
    home_calls = [c for c in ctrl.calls if c["method"] == "home_axes"]
    self.assertEqual(len(home_calls), 1)
    self.assertEqual(home_calls[0]["args"]["axes"], ["z", "zg", "g", "x", "y", "w"])
    for axis in homed:
      self.assertTrue(bravo.is_axis_homed(axis))

  async def test_home_without_gripper_axes_when_controller_has_no_gripper(self):
    bravo, ctrl = _new_bravo(gripper=False)
    homed = await bravo.home()
    self.assertNotIn("g", homed)
    self.assertNotIn("zg", homed)

  async def test_home_single_axis_forces_and_marks_homed(self):
    bravo, ctrl = _new_bravo()
    await bravo.home_single_axis("x")
    home_calls = [c for c in ctrl.calls if c["method"] == "home_axes"]
    self.assertEqual(home_calls[-1]["args"], {"axes": ["x"], "force": True})
    self.assertTrue(bravo.is_axis_homed("x"))

  async def test_home_single_axis_w_parks_at_zero(self):
    bravo, ctrl = _new_bravo()
    ctrl.move([AxisMoveInfo(axis="w", position=25.0)])
    await bravo.home_single_axis("w")
    self.assertAlmostEqual(ctrl.get_position("w"), 0.0, places=3)

  async def test_is_axis_homed_defaults_false_before_any_home(self):
    bravo, _ = _new_bravo()
    self.assertFalse(bravo.is_axis_homed("x"))


class MotionTests(unittest.IsolatedAsyncioTestCase):
  """move_axis/jog_axis/move_to_location/move_to_safe_z/get_position(s)."""

  async def test_move_axis_reaches_the_controller(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    await bravo.move_axis("x", 50.0)
    self.assertAlmostEqual(bravo.get_position("x"), 50.0, places=3)

  async def test_jog_axis_moves_relative_and_returns_new_position(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    await bravo.move_axis("x", 50.0)
    new_pos = await bravo.jog_axis("x", 5.0)
    self.assertAlmostEqual(new_pos, 55.0, places=3)

  async def test_move_to_location_reaches_the_engine_and_moves_xyz(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    await bravo.move_to_location(1)
    moved_axes = {
      m["axis"] for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"]
    }
    self.assertIn("x", moved_axes)
    self.assertIn("y", moved_axes)
    self.assertIn("z", moved_axes)
    self.assertAlmostEqual(
      bravo.get_position("x"), bravo._teachpoints.get_teachpoint(1, "x"), places=3
    )

  async def test_move_to_safe_z_moves_z_to_the_configured_safe_position(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    bravo._config.safety.z_safe_position = 5.0
    await bravo.move_to_safe_z()
    self.assertAlmostEqual(bravo.get_position("z"), 5.0, places=3)

  async def test_get_all_positions_covers_every_axis(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    positions = bravo.get_all_positions()
    self.assertEqual(set(positions.keys()), {"x", "y", "z", "w", "g", "zg"})

  async def test_enable_disable_motor_reach_the_controller(self):
    bravo, ctrl = _new_bravo()
    bravo.enable_motor("x")
    bravo.disable_motor("x")
    methods = [c["method"] for c in ctrl.calls]
    self.assertIn("enable_motor", methods)
    self.assertIn("disable_motor", methods)


class DeckLabwareTests(unittest.IsolatedAsyncioTestCase):
  """set_labware/clear_labware/get_labware."""

  async def test_set_labware_then_get_labware_round_trips(self):
    bravo, _ = _new_bravo()
    plate = _plate()
    bravo.set_labware(1, plate)
    self.assertIs(bravo.get_labware(1), plate)

  async def test_clear_labware_empties_the_location(self):
    bravo, _ = _new_bravo()
    bravo.set_labware(1, _plate())
    bravo.clear_labware(1)
    self.assertIsNone(bravo.get_labware(1))

  async def test_get_labware_at_empty_location_is_none(self):
    bravo, _ = _new_bravo()
    self.assertIsNone(bravo.get_labware(2))


class LiquidHandlingReachesTheEngineTests(unittest.IsolatedAsyncioTestCase):
  """aspirate/dispense/mix build a task and run it through the engine."""

  async def _bravo_with_plate(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    bravo.set_labware(3, _plate())
    # 96_d_70 is a disposable-tip head: liquid handling requires tips on
    # the head, so pick a set up before the test's own assertions.
    bravo.set_labware(4, _tip_box())
    await bravo.tips_on(4)
    ctrl.calls.clear()
    return bravo, ctrl

  async def test_aspirate_moves_to_the_target_location(self):
    bravo, ctrl = await self._bravo_with_plate()
    await bravo.aspirate(3, 50.0)
    moved_axes = {
      m["axis"] for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"]
    }
    self.assertIn("z", moved_axes)
    self.assertIn("w", moved_axes)

  async def test_aspirate_distance_from_bottom_reaches_the_task(self):
    """distance_from_bottom is a caller-supplied argument threaded through
    to AspirateTask; pinned directly by checking two different values
    produce two different Z targets, since a hardcoded pass-through would
    still move Z (caught above) but wouldn't vary with the argument.
    """

    async def z_targets_for(distance_from_bottom):
      bravo, ctrl = await self._bravo_with_plate()
      await bravo.aspirate(3, 50.0, distance_from_bottom=distance_from_bottom)
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

  async def test_dispense_moves_to_the_target_location(self):
    bravo, ctrl = await self._bravo_with_plate()
    await bravo.aspirate(3, 50.0)
    ctrl.calls.clear()
    await bravo.dispense(3, 50.0)
    moved_axes = {
      m["axis"] for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"]
    }
    self.assertIn("w", moved_axes)

  async def test_mix_performs_the_configured_number_of_cycles(self):
    bravo, ctrl = await self._bravo_with_plate()
    await bravo.mix(3, 20.0, mix_cycles=2)
    w_moves = [
      m for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"] if m["axis"] == "w"
    ]
    # Each cycle aspirates then dispenses at the well, so at least 2 W moves per cycle.
    self.assertGreaterEqual(len(w_moves), 4)

  async def test_aspirate_on_a_lidded_plate_raises_before_touching_the_controller(self):
    bravo, ctrl = await self._bravo_with_plate()
    lidded = bravo.get_labware(3)
    lidded.is_lidded = True
    with self.assertRaises(RuntimeError):
      await bravo.aspirate(3, 50.0)
    self.assertEqual(ctrl.calls, [])


class TipsTests(unittest.IsolatedAsyncioTestCase):
  """tips_on/tips_off reach the engine and update tip state."""

  async def _bravo_with_tipbox(self):
    bravo, ctrl = _new_bravo()
    await bravo.setup()
    bravo.set_labware(4, _tip_box())
    return bravo, ctrl

  async def test_tips_on_marks_tips_on_head_and_moves(self):
    bravo, ctrl = await self._bravo_with_tipbox()
    await bravo.tips_on(4)
    self.assertTrue(bravo._tips_on_head)
    self.assertIsNotNone(bravo._attached_tip_length_mm)
    moved_axes = {
      m["axis"] for c in ctrl.calls if c["method"] == "move" for m in c["args"]["moves"]
    }
    self.assertIn("z", moved_axes)

  async def test_tips_on_twice_raises(self):
    bravo, _ = await self._bravo_with_tipbox()
    await bravo.tips_on(4)
    with self.assertRaises(RuntimeError):
      await bravo.tips_on(4)

  async def test_tips_off_clears_tip_state(self):
    bravo, ctrl = await self._bravo_with_tipbox()
    await bravo.tips_on(4)
    await bravo.tips_off(4)
    self.assertFalse(bravo._tips_on_head)
    self.assertIsNone(bravo._attached_tip_length_mm)

  async def test_tips_on_consumes_tipbox_occupancy(self):
    bravo, _ = await self._bravo_with_tipbox()
    await bravo.tips_on(4)
    self.assertEqual(bravo._occupied_tip_wells(4), set())

  async def test_set_tip_selection_rejects_out_of_range_cell(self):
    bravo, _ = await self._bravo_with_tipbox()
    with self.assertRaises(RuntimeError):
      bravo.set_tip_selection(4, 99, 0)


class PlateSelectionTests(unittest.IsolatedAsyncioTestCase):
  """set_plate_selection."""

  async def test_set_plate_selection_within_range_succeeds(self):
    bravo, _ = _new_bravo()
    bravo.set_labware(3, _plate())
    selection = bravo.set_plate_selection(3, 0, 0)
    self.assertEqual((selection.row, selection.col), (0, 0))

  async def test_set_plate_selection_out_of_range_raises(self):
    bravo, _ = _new_bravo()
    bravo.set_labware(3, _plate())
    with self.assertRaises(RuntimeError):
      bravo.set_plate_selection(3, 99, 0)


class GripperTests(unittest.IsolatedAsyncioTestCase):
  """gripper_pick/gripper_move/gripper_place hold state across three calls."""

  async def _bravo_with_source_plate(self):
    bravo, ctrl = _new_bravo(gripper=True)
    await bravo.setup()
    bravo.set_labware(1, _plate("source_plate"))
    return bravo, ctrl

  async def test_full_cycle_moves_the_labware_between_locations(self):
    bravo, ctrl = await self._bravo_with_source_plate()
    await bravo.gripper_pick(1)
    self.assertIsNotNone(bravo._gripper_held_task)
    await bravo.gripper_move(2)
    await bravo.gripper_place(2)
    self.assertIsNone(bravo._gripper_held_task)
    self.assertIsNone(bravo._gripper_pick_location)
    self.assertIsNone(bravo.get_labware(1))
    self.assertIsNotNone(bravo.get_labware(2))
    self.assertEqual(bravo.get_labware(2).name, "source_plate")

  async def test_pick_grips_and_place_releases(self):
    bravo, ctrl = await self._bravo_with_source_plate()
    await bravo.gripper_pick(1)
    self.assertIn("grip", [c["method"] for c in ctrl.calls])
    ctrl.calls.clear()
    await bravo.gripper_place(2)
    self.assertIn("open_gripper", [c["method"] for c in ctrl.calls])

  async def test_pick_while_already_holding_raises(self):
    bravo, _ = await self._bravo_with_source_plate()
    await bravo.gripper_pick(1)
    with self.assertRaises(RuntimeError):
      await bravo.gripper_pick(1)

  async def test_move_without_holding_raises(self):
    bravo, _ = await self._bravo_with_source_plate()
    with self.assertRaises(RuntimeError):
      await bravo.gripper_move(2)

  async def test_place_without_holding_raises(self):
    bravo, _ = await self._bravo_with_source_plate()
    with self.assertRaises(RuntimeError):
      await bravo.gripper_place(2)

  async def test_pick_from_empty_location_raises(self):
    bravo, _ = await self._bravo_with_source_plate()
    with self.assertRaises(RuntimeError):
      await bravo.gripper_pick(5)

  async def test_gripper_move_target_uses_the_documented_offset_formula(self):
    """gripper_move's XY target never reaches a golden-frame comparison by
    itself (it's one leg of a coordinated move) -- pinned directly against
    the formula the docstring names: teachpoint Y + gripper.y_offset +
    the head's Y offset constant.
    """
    bravo, ctrl = await self._bravo_with_source_plate()
    bravo._config.gripper.y_offset = 3.5
    await bravo.gripper_pick(1)
    ctrl.calls.clear()
    await bravo.gripper_move(2)
    move_call = next(c for c in ctrl.calls if c["method"] == "move")
    x_move = next(m for m in move_call["args"]["moves"] if m["axis"] == "x")
    y_move = next(m for m in move_call["args"]["moves"] if m["axis"] == "y")
    expected_x = bravo._teachpoints.get_teachpoint(2, "x")
    expected_y = bravo._teachpoints.get_teachpoint(2, "y") + 3.5 + 0.0  # 96_d_70 head offset is 0
    self.assertAlmostEqual(x_move["position"], expected_x, places=6)
    self.assertAlmostEqual(y_move["position"], expected_y, places=6)


if __name__ == "__main__":
  unittest.main()
