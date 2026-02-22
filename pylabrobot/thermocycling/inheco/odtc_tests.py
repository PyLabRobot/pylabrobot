"""Tests for ODTC: backend, thermocycler resource, SiLA interface, and model utilities."""

import asyncio
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

from pylabrobot.resources import Coordinate
from pylabrobot.thermocycling.inheco.odtc_backend import ODTCBackend, ODTCExecution
from pylabrobot.thermocycling.inheco.odtc_model import (
  ODTC_DIMENSIONS,
  PREMETHOD_ESTIMATED_DURATION_SECONDS,
  ODTCMethodSet,
  ODTCProgress,
  ODTCProtocol,
  ODTCStage,
  ODTCStep,
  estimate_method_duration_seconds,
  method_set_to_xml,
  normalize_variant,
  odtc_protocol_to_protocol,
  parse_method_set,
)
from pylabrobot.thermocycling.inheco.odtc_sila_interface import (
  FirstEventTimeout,
  FirstEventType,
  ODTCSiLAInterface,
  SiLAState,
  SiLATimeoutError,
)
from pylabrobot.thermocycling.inheco.odtc_thermocycler import ODTCThermocycler


def _minimal_data_event_payload(remaining_s: float = 300.0) -> Dict[str, Any]:
  """Minimal DataEvent payload (valid XML); ODTCProgress.from_data_event parses elapsed_s=0 when no Elapsed time."""
  inner = (
    f'<d><dataSeries nameId="Remaining duration" unit="s">'
    f"<integerValue>{int(remaining_s)}</integerValue></dataSeries></d>"
  )
  escaped = inner.replace("<", "&lt;").replace(">", "&gt;")
  return {
    "requestId": 12345,
    "dataValue": f"<r><AnyData>{escaped}</AnyData></r>",
  }


def _data_event_payload_with_elapsed(elapsed_s: float, request_id: int = 12345) -> Dict[str, Any]:
  """DataEvent payload with Elapsed time (ms) for progress/lookup tests."""
  ms = int(elapsed_s * 1000)
  inner = (
    f'<d><dataSeries nameId="Elapsed time" unit="ms">'
    f"<integerValue>{ms}</integerValue></dataSeries></d>"
  )
  escaped = inner.replace("<", "&lt;").replace(">", "&gt;")
  return {
    "requestId": request_id,
    "dataValue": f"<r><AnyData>{escaped}</AnyData></r>",
  }


def _data_event_payload_with_elapsed_and_temps(
  elapsed_s: float,
  current_temp_c: Optional[float] = None,
  lid_temp_c: Optional[float] = None,
  target_temp_c: Optional[float] = None,
  request_id: int = 12345,
) -> Dict[str, Any]:
  """DataEvent payload with Elapsed time and optional temperatures (1/100°C in XML)."""
  parts = [
    f'<dataSeries nameId="Elapsed time" unit="ms">'
    f"<integerValue>{int(elapsed_s * 1000)}</integerValue></dataSeries>",
  ]
  if current_temp_c is not None:
    parts.append(
      f'<dataSeries nameId="Current temperature" unit="1/100°C">'
      f"<integerValue>{int(current_temp_c * 100)}</integerValue></dataSeries>"
    )
  if lid_temp_c is not None:
    parts.append(
      f'<dataSeries nameId="LID temperature" unit="1/100°C">'
      f"<integerValue>{int(lid_temp_c * 100)}</integerValue></dataSeries>"
    )
  if target_temp_c is not None:
    parts.append(
      f'<dataSeries nameId="Target temperature" unit="1/100°C">'
      f"<integerValue>{int(target_temp_c * 100)}</integerValue></dataSeries>"
    )
  inner = "<d>" + "".join(parts) + "</d>"
  escaped = inner.replace("<", "&lt;").replace(">", "&gt;")
  return {
    "requestId": request_id,
    "dataValue": f"<r><AnyData>{escaped}</AnyData></r>",
  }


class TestNormalizeVariant(unittest.TestCase):
  """Tests for normalize_variant (96/384 -> 960000/384000)."""

  def test_96_maps_to_960000(self):
    self.assertEqual(normalize_variant(96), 960000)

  def test_384_maps_to_384000(self):
    self.assertEqual(normalize_variant(384), 384000)

  def test_3840000_normalizes_to_384000(self):
    self.assertEqual(normalize_variant(3840000), 384000)

  def test_invalid_raises(self):
    with self.assertRaises(ValueError) as cm:
      normalize_variant(123)
    self.assertIn("123", str(cm.exception))
    self.assertIn("Valid", str(cm.exception))


class TestODTCProgressFromDataEventPayload(unittest.TestCase):
  """Tests for ODTCProgress.from_data_event with raw payload (parsing covered indirectly)."""

  def test_from_data_event_experiment_step_sequence_fallback(self):
    """When no 'Step' dataSeries, current_step_index is taken from experimentStep @sequence (1-based)."""
    inner = (
      '<containerData><experimentStep name="Temperatures" sequence="5" type="Measurement">'
      '<dataSeries nameId="Elapsed time" unit="ms"><integerValue>10000</integerValue></dataSeries>'
      "</experimentStep></containerData>"
    )
    escaped = inner.replace("<", "&lt;").replace(">", "&gt;")
    payload = {"requestId": 1, "dataValue": f"<r><AnyData>{escaped}</AnyData></r>"}
    progress = ODTCProgress.from_data_event(payload, None)
    self.assertEqual(progress.current_step_index, 4)  # 1-based sequence 5 -> 0-based index 4
    self.assertEqual(progress.elapsed_s, 10.0)


class TestEstimateMethodDurationSeconds(unittest.TestCase):
  """Tests for estimate_method_duration_seconds (ODTC method duration from steps)."""

  def test_premethod_constant(self):
    """PREMETHOD_ESTIMATED_DURATION_SECONDS is 10 minutes."""
    self.assertEqual(PREMETHOD_ESTIMATED_DURATION_SECONDS, 600.0)

  def test_empty_method_returns_zero(self):
    """Method with no steps has zero duration."""
    odtc = ODTCProtocol(
      kind="method", name="empty", start_block_temperature=20.0, steps=[], stages=[]
    )
    self.assertEqual(estimate_method_duration_seconds(odtc), 0.0)

  def test_single_step_no_loop(self):
    """Single step: ramp + plateau + overshoot. Ramp = |95 - 20| / 4.4 ≈ 17.045 s."""
    odtc = ODTCProtocol(
      kind="method",
      name="single",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=30.0,
          overshoot_time=5.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
      stages=[],
    )
    # Ramp: 75 / 4.4 ≈ 17.045; plateau: 30; overshoot: 5
    got = estimate_method_duration_seconds(odtc)
    self.assertAlmostEqual(got, 17.045 + 30 + 5, places=1)

  def test_single_step_zero_slope_clamped(self):
    """Zero slope is clamped to avoid division by zero; duration is finite."""
    odtc = ODTCProtocol(
      kind="method",
      name="zero_slope",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=0.0,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=0.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
      stages=[],
    )
    # Ramp: 75 / 0.1 = 750 s (clamped); plateau: 10
    got = estimate_method_duration_seconds(odtc)
    self.assertAlmostEqual(got, 750 + 10, places=1)

  def test_two_steps_with_loop(self):
    """Two steps with loop: step 1 -> step 2 (goto 1, loop 2) = run 1,2,1,2."""
    odtc = ODTCProtocol(
      kind="method",
      name="loop",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=0.0,
          goto_number=0,
          loop_number=0,
        ),
        ODTCStep(
          number=2,
          slope=2.2,
          plateau_temperature=60.0,
          plateau_time=5.0,
          overshoot_time=0.0,
          goto_number=1,
          loop_number=1,  # repeat_count = 2
        ),
      ],
      stages=[],
    )
    # Execution: step1, step2, step1, step2
    got = estimate_method_duration_seconds(odtc)
    self.assertGreater(got, 0)
    self.assertLess(got, 1000)


class TestODTCProgressPositionFromElapsed(unittest.TestCase):
  """Tests for ODTCProgress.from_data_event(payload, odtc) position (timeline lookup from elapsed)."""

  def test_premethod_elapsed_zero(self):
    """Premethod at elapsed 0: step 0, cycle 0, setpoint = target_block_temperature."""
    odtc = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    payload = _data_event_payload_with_elapsed(0.0)
    progress = ODTCProgress.from_data_event(payload, odtc)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 37.0)
    self.assertEqual(progress.total_step_count, 1)
    self.assertEqual(progress.total_cycle_count, 1)
    self.assertGreater(progress.remaining_hold_s, 0)
    self.assertEqual(progress.estimated_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)
    self.assertEqual(progress.remaining_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)

  def test_premethod_elapsed_mid_run(self):
    """Premethod mid-run: same step/cycle/setpoint, remaining_hold decreases."""
    odtc = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    payload = _data_event_payload_with_elapsed(300.0)
    progress = ODTCProgress.from_data_event(payload, odtc)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 37.0)
    self.assertGreater(progress.remaining_hold_s, 0)
    self.assertLess(progress.remaining_hold_s, PREMETHOD_ESTIMATED_DURATION_SECONDS - 300.0 + 1)
    rem = progress.remaining_duration_s
    self.assertIsNotNone(rem)
    self.assertAlmostEqual(cast(float, rem), 300.0, delta=1.0)

  def test_premethod_elapsed_beyond_duration(self):
    """Premethod beyond estimated duration: remaining_hold_s = 0."""
    odtc = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    beyond = PREMETHOD_ESTIMATED_DURATION_SECONDS + 60.0
    payload = _data_event_payload_with_elapsed(beyond)
    progress = ODTCProgress.from_data_event(payload, odtc)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 37.0)
    self.assertEqual(progress.remaining_hold_s, 0.0)
    self.assertEqual(progress.remaining_duration_s, 0.0)

  def test_method_no_steps(self):
    """Method with no steps: step 0, cycle 0."""
    odtc = ODTCProtocol(
      kind="method",
      name="empty",
      start_block_temperature=20.0,
      steps=[],
      stages=[],
    )
    payload = _data_event_payload_with_elapsed(0.0)
    progress = ODTCProgress.from_data_event(payload, odtc)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.total_step_count, 0)
    self.assertEqual(progress.total_cycle_count, 1)

  def test_method_single_step(self):
    """Method with single step: step 0, cycle 0, setpoint from step."""
    odtc = ODTCProtocol(
      kind="method",
      name="single",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=30.0,
          overshoot_time=5.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
      stages=[],
    )
    payload = _data_event_payload_with_elapsed(0.0)
    progress = ODTCProgress.from_data_event(payload, odtc)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 95.0)
    self.assertGreater(progress.remaining_hold_s, 0)
    total_dur = estimate_method_duration_seconds(odtc)
    payload_end = _data_event_payload_with_elapsed(total_dur + 10.0)
    progress_end = ODTCProgress.from_data_event(payload_end, odtc)
    self.assertEqual(progress_end.remaining_hold_s, 0.0)
    self.assertEqual(progress_end.target_temp_c, 95.0)

  def test_method_multi_step_with_loops(self):
    """Method with 3 steps x 2 cycles: step_index and cycle_index advance with elapsed."""
    odtc = ODTCProtocol(
      kind="method",
      name="pcr",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=2.0,
          goto_number=0,
          loop_number=0,
        ),
        ODTCStep(
          number=2,
          slope=4.4,
          plateau_temperature=55.0,
          plateau_time=10.0,
          overshoot_time=2.0,
          goto_number=0,
          loop_number=0,
        ),
        ODTCStep(
          number=3,
          slope=4.4,
          plateau_temperature=72.0,
          plateau_time=15.0,
          overshoot_time=2.0,
          goto_number=1,
          loop_number=2,
        ),
      ],
      stages=[],
    )
    payload0 = _data_event_payload_with_elapsed(0.0)
    progress0 = ODTCProgress.from_data_event(payload0, odtc)
    self.assertEqual(progress0.current_step_index, 0)
    self.assertEqual(progress0.current_cycle_index, 0)
    self.assertEqual(progress0.target_temp_c, 95.0)
    self.assertEqual(progress0.total_step_count, 3)
    self.assertEqual(progress0.total_cycle_count, 2)
    total_dur = estimate_method_duration_seconds(odtc)
    payload_end = _data_event_payload_with_elapsed(total_dur + 100.0)
    progress_end = ODTCProgress.from_data_event(payload_end, odtc)
    self.assertEqual(progress_end.current_step_index, 2)
    self.assertEqual(progress_end.current_cycle_index, 1)
    self.assertEqual(progress_end.target_temp_c, 72.0)
    self.assertEqual(progress_end.remaining_hold_s, 0.0)

  def test_elapsed_negative_treated_as_zero(self):
    """Negative elapsed_s in payload is not possible (XML ms); from_data_event(None, odtc) gives elapsed_s=0."""
    odtc = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    progress = ODTCProgress.from_data_event(None, odtc)
    self.assertEqual(progress.elapsed_s, 0.0)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 37.0)
    self.assertEqual(progress.remaining_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)


class TestODTCProgress(unittest.TestCase):
  """Tests for ODTCProgress.from_data_event(payload, odtc) and format_progress_log_message."""

  def test_from_data_event_none_none(self):
    """from_data_event(None, None): elapsed_s=0, temps None, estimated_duration_s=None, remaining_duration_s=0."""
    progress = ODTCProgress.from_data_event(None, None)
    self.assertIsInstance(progress, ODTCProgress)
    self.assertEqual(progress.elapsed_s, 0.0)
    self.assertIsNone(progress.current_temp_c)
    self.assertIsNone(progress.estimated_duration_s)
    self.assertEqual(progress.remaining_duration_s, 0.0)

  def test_from_data_event_payload_none(self):
    """from_data_event(payload, None): elapsed_s and temps from payload; estimated/remaining duration 0."""
    payload = _data_event_payload_with_elapsed_and_temps(50.0, current_temp_c=25.0, lid_temp_c=24.0)
    progress = ODTCProgress.from_data_event(payload, None)
    self.assertIsInstance(progress, ODTCProgress)
    self.assertEqual(progress.elapsed_s, 50.0)
    self.assertEqual(progress.current_temp_c, 25.0)
    self.assertEqual(progress.lid_temp_c, 24.0)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.remaining_hold_s, 0.0)
    self.assertIsNone(progress.estimated_duration_s)
    self.assertEqual(progress.remaining_duration_s, 0.0)
    msg = progress.format_progress_log_message()
    self.assertIn("ODTC progress", msg)
    self.assertIn("50", msg)
    self.assertIn("25.0", msg)

  def test_from_data_event_none_odtc(self):
    """from_data_event(None, odtc): elapsed_s=0; estimated_duration_s set; remaining_duration_s = estimated."""
    premethod = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    progress = ODTCProgress.from_data_event(None, premethod)
    self.assertEqual(progress.elapsed_s, 0.0)
    self.assertEqual(progress.estimated_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)
    self.assertEqual(progress.remaining_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)

  def test_from_data_event_premethod(self):
    """from_data_event(payload, premethod): step 0, cycle 0, setpoint; estimated/remaining duration."""
    payload = _data_event_payload_with_elapsed_and_temps(100.0, current_temp_c=35.0)
    premethod = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    progress = ODTCProgress.from_data_event(payload, odtc=premethod)
    self.assertIsInstance(progress, ODTCProgress)
    self.assertEqual(progress.elapsed_s, 100.0)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 37.0)
    self.assertGreater(progress.remaining_hold_s, 0)
    self.assertEqual(progress.estimated_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)
    rem = progress.remaining_duration_s
    self.assertIsNotNone(rem)
    self.assertAlmostEqual(cast(float, rem), 500.0, delta=1.0)  # 600 - 100
    msg = progress.format_progress_log_message()
    self.assertIn("ODTC progress", msg)
    self.assertIn("step", msg)
    self.assertIn("cycle", msg)
    self.assertIn("37.0", msg)

  def test_from_data_event_method(self):
    """from_data_event(payload, method): step_index, cycle_index, remaining_hold_s from position."""
    payload = _data_event_payload_with_elapsed(0.0)
    odtc = ODTCProtocol(
      kind="method",
      name="pcr",
      start_block_temperature=20.0,
      steps=[
        ODTCStep(
          number=1,
          slope=4.4,
          plateau_temperature=95.0,
          plateau_time=10.0,
          overshoot_time=2.0,
          goto_number=0,
          loop_number=0,
        ),
      ],
      stages=[],
    )
    progress = ODTCProgress.from_data_event(payload, odtc=odtc)
    self.assertIsInstance(progress, ODTCProgress)
    self.assertEqual(progress.current_step_index, 0)
    self.assertEqual(progress.current_cycle_index, 0)
    self.assertEqual(progress.target_temp_c, 95.0)
    self.assertGreater(progress.remaining_hold_s, 0)
    self.assertIsNotNone(progress.estimated_duration_s)
    rem = progress.remaining_duration_s
    self.assertIsNotNone(rem)
    self.assertGreater(cast(float, rem), 0)
    msg = progress.format_progress_log_message()
    self.assertIn("ODTC progress", msg)
    self.assertIn("elapsed", msg)

  def test_format_progress_log_message_includes_elapsed_and_temps(self):
    """format_progress_log_message returns string with ODTC progress, elapsed_s, and temps."""
    progress = ODTCProgress(
      elapsed_s=120.0,
      current_temp_c=72.0,
      lid_temp_c=105.0,
      target_temp_c=72.0,
    )
    msg = progress.format_progress_log_message()
    self.assertIn("ODTC progress", msg)
    self.assertIn("120", msg)
    self.assertIn("72.0", msg)


class TestODTCSiLAInterface(unittest.IsolatedAsyncioTestCase):
  """Tests for ODTCSiLAInterface."""

  def setUp(self):
    """Set up test fixtures."""
    self.interface = ODTCSiLAInterface(machine_ip="192.168.1.100", client_ip="127.0.0.1")

  def test_normalize_command_name(self):
    """Test command name normalization for aliases."""
    self.assertEqual(self.interface._normalize_command_name("OpenDoor"), "OpenDoor")
    self.assertEqual(self.interface._normalize_command_name("PrepareForOutput"), "OpenDoor")
    self.assertEqual(self.interface._normalize_command_name("CloseDoor"), "CloseDoor")
    self.assertEqual(self.interface._normalize_command_name("PrepareForInput"), "CloseDoor")
    self.assertEqual(self.interface._normalize_command_name("ExecuteMethod"), "ExecuteMethod")

  def test_check_state_allowability(self):
    """Test state allowability checking."""
    # GetStatus allowed in all states
    self.interface._current_state = SiLAState.STARTUP
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))

    self.interface._current_state = SiLAState.STANDBY
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))
    self.assertTrue(self.interface._check_state_allowability("Initialize"))
    self.assertFalse(self.interface._check_state_allowability("ExecuteMethod"))

    # GetStatus allowed during initializing (needed for polling and post-Initialize verification)
    self.interface._current_state = SiLAState.INITIALIZING
    self.assertTrue(self.interface._check_state_allowability("GetStatus"))
    self.assertTrue(self.interface._check_state_allowability("GetDeviceIdentification"))
    self.assertFalse(self.interface._check_state_allowability("ExecuteMethod"))

    self.interface._current_state = SiLAState.IDLE
    self.assertTrue(self.interface._check_state_allowability("ExecuteMethod"))
    self.assertFalse(self.interface._check_state_allowability("Initialize"))

  def test_check_parallelism(self):
    """Test parallelism checking."""
    # No commands executing - should allow
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

    # SetParameters executing - ReadActualTemperature can run in parallel
    self.interface._executing_commands.add("SetParameters")
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

    # ExecuteMethod executing - SetParameters cannot run in parallel (S)
    self.interface._executing_commands.clear()
    self.interface._executing_commands.add("ExecuteMethod")
    self.assertFalse(self.interface._check_parallelism("SetParameters"))

    # ExecuteMethod executing - ReadActualTemperature can run in parallel (P)
    self.assertTrue(self.interface._check_parallelism("ReadActualTemperature"))

  def test_validate_lock_id(self):
    """Test lockId validation."""
    # Device not locked - any lockId (including None) is fine
    self.interface._lock_id = None
    self.interface._validate_lock_id(None)  # Should not raise
    self.interface._validate_lock_id("some_id")  # Should not raise

    # Device locked - must provide matching lockId
    self.interface._lock_id = "locked_id"
    self.interface._validate_lock_id("locked_id")  # Should not raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._validate_lock_id("wrong_id")
    # Check that error mentions lockId mismatch
    error_msg = str(cm.exception)
    self.assertIn("locked", error_msg.lower())
    self.assertIn("5", error_msg)  # Return code 5

  def test_update_state_from_status(self):
    """Test state updates from status strings - exact matching only."""
    # Test lowercase enum values (exact match required)
    self.interface._update_state_from_status("idle")
    self.assertEqual(self.interface._current_state, SiLAState.IDLE)

    self.interface._update_state_from_status("busy")
    self.assertEqual(self.interface._current_state, SiLAState.BUSY)

    self.interface._update_state_from_status("standby")
    self.assertEqual(self.interface._current_state, SiLAState.STANDBY)

    # Test camelCase enum values (exact match required)
    self.interface._update_state_from_status("inError")
    self.assertEqual(self.interface._current_state, SiLAState.INERROR)

    self.interface._update_state_from_status("errorHandling")
    self.assertEqual(self.interface._current_state, SiLAState.ERRORHANDLING)

    # Test that case mismatches are NOT accepted (exact matching only)
    # These should keep the current state and log a warning
    initial_state = self.interface._current_state
    self.interface._update_state_from_status("Idle")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged

    self.interface._update_state_from_status("BUSY")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged

    self.interface._update_state_from_status("INERROR")  # Wrong case
    self.assertEqual(self.interface._current_state, initial_state)  # Should remain unchanged

  def test_handle_return_code(self):
    """Test return code handling."""
    # Code 1, 2, 3 should not raise
    self.interface._handle_return_code(1, "Success", "GetStatus", 123)
    self.interface._handle_return_code(2, "Accepted", "ExecuteMethod", 123)
    self.interface._handle_return_code(3, "Finished", "ExecuteMethod", 123)

    # Code 4 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(4, "busy", "ExecuteMethod", 123)
    self.assertIn("return code 4", str(cm.exception))

    # Code 5 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(5, "LockId error", "ExecuteMethod", 123)
    self.assertIn("return code 5", str(cm.exception))

    # Code 9 should raise
    with self.assertRaises(RuntimeError) as cm:
      self.interface._handle_return_code(9, "Not allowed", "ExecuteMethod", 123)
    self.assertIn("return code 9", str(cm.exception))

    # Device error code should transition to InError
    with self.assertRaises(RuntimeError):
      self.interface._handle_return_code(1000, "Device error", "ExecuteMethod", 123)
    self.assertEqual(self.interface._current_state, SiLAState.INERROR)

  def test_get_terminal_state(self):
    """Test terminal state map for polling fallback."""
    self.assertEqual(self.interface._get_terminal_state("Reset"), "standby")
    self.assertEqual(self.interface._get_terminal_state("Initialize"), "idle")
    self.assertEqual(self.interface._get_terminal_state("LockDevice"), "standby")
    self.assertEqual(self.interface._get_terminal_state("UnlockDevice"), "standby")
    self.assertEqual(self.interface._get_terminal_state("OpenDoor"), "idle")
    self.assertEqual(self.interface._get_terminal_state("ExecuteMethod"), "idle")


# Minimal SOAP responses for dual-track tests (return_code 2 = async accepted; no duration = poll immediately).
_OPEN_DOOR_ASYNC_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <OpenDoorResponse xmlns="http://sila.coop">
      <OpenDoorResult>
        <returnCode>2</returnCode>
        <message>Accepted</message>
      </OpenDoorResult>
    </OpenDoorResponse>
  </s:Body>
</s:Envelope>"""

_GET_STATUS_IDLE_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <GetStatusResponse xmlns="http://sila.coop">
      <state>idle</state>
      <GetStatusResult>
        <returnCode>1</returnCode>
        <message>Success</message>
      </GetStatusResult>
    </GetStatusResponse>
  </s:Body>
</s:Envelope>"""

_GET_STATUS_BUSY_RESPONSE = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body>
    <GetStatusResponse xmlns="http://sila.coop">
      <state>busy</state>
      <GetStatusResult>
        <returnCode>1</returnCode>
        <message>Success</message>
      </GetStatusResult>
    </GetStatusResponse>
  </s:Body>
</s:Envelope>"""


class TestODTCSiLADualTrack(unittest.IsolatedAsyncioTestCase):
  """Tests for dual-track async completion (ResponseEvent + polling fallback)."""

  async def test_polling_fallback_completes_when_no_response_event(self):
    """When ResponseEvent never arrives, polling sees idle and completes Future (warn_and_continue)."""
    call_count = 0

    def mock_urlopen(req):
      nonlocal call_count
      call_count += 1
      body = _OPEN_DOOR_ASYNC_RESPONSE if call_count == 1 else _GET_STATUS_IDLE_RESPONSE
      resp = MagicMock()
      resp.read.return_value = body
      cm = MagicMock()
      cm.__enter__.return_value = resp
      cm.__exit__.return_value = None
      return cm

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), patch(
      "pylabrobot.thermocycling.inheco.odtc_sila_interface.POLLING_START_BUFFER", 0.05
    ):
      # Short POLLING_START_BUFFER in test so we don't wait 10s; lifetime still allows polling to run.
      interface = ODTCSiLAInterface(
        machine_ip="192.168.1.100",
        client_ip="127.0.0.1",
        poll_interval=0.02,
        lifetime_of_execution=2.0,
        on_response_event_missing="warn_and_continue",
      )
      interface._current_state = SiLAState.IDLE
      # Do not call setup() so we avoid binding the HTTP server (sandbox/CI friendly).
      result = await interface.send_command("OpenDoor")
      self.assertIsNone(result)
      self.assertGreaterEqual(call_count, 2)

  async def test_lifetime_of_execution_exceeded_raises(self):
    """When lifetime_of_execution is exceeded before terminal state, Future gets timeout exception."""
    call_count = 0

    def mock_urlopen(req):
      nonlocal call_count
      call_count += 1
      body = _OPEN_DOOR_ASYNC_RESPONSE if call_count == 1 else _GET_STATUS_BUSY_RESPONSE
      resp = MagicMock()
      resp.read.return_value = body
      cm = MagicMock()
      cm.__enter__.return_value = resp
      cm.__exit__.return_value = None
      return cm

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), patch(
      "pylabrobot.thermocycling.inheco.odtc_sila_interface.POLLING_START_BUFFER", 0.02
    ):
      # Short POLLING_START_BUFFER so timeout (0.5s) is hit quickly instead of waiting 10s.
      interface = ODTCSiLAInterface(
        machine_ip="192.168.1.100",
        client_ip="127.0.0.1",
        poll_interval=0.05,
        lifetime_of_execution=0.2,
        on_response_event_missing="warn_and_continue",
      )
      interface._current_state = SiLAState.IDLE
      # Do not call setup() so we avoid binding the HTTP server (sandbox/CI friendly).
      with self.assertRaises(SiLATimeoutError) as cm:
        await interface.send_command("OpenDoor")
      self.assertIn("lifetime_of_execution", str(cm.exception))


class TestODTCBackend(unittest.IsolatedAsyncioTestCase):
  """Tests for ODTCBackend."""

  def setUp(self):
    """Set up test fixtures."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      self.backend = ODTCBackend(odtc_ip="192.168.1.100")
      self.backend._sila = MagicMock(spec=ODTCSiLAInterface)
      self.backend._sila.bound_port = 8080
      self.backend._sila._machine_ip = "192.168.1.100"
      self.backend._sila._lock_id = None
      self.backend._sila._lifetime_of_execution = None
      self.backend._sila._client_ip = "127.0.0.1"

  def test_backend_odtc_ip_property(self):
    """Backend.odtc_ip returns machine IP from sila."""
    self.assertEqual(self.backend.odtc_ip, "192.168.1.100")

  def test_backend_variant_property(self):
    """Backend.variant returns normalized variant (default 960000)."""
    self.assertEqual(self.backend.variant, 960000)

  async def test_setup(self):
    """Test backend setup (full path)."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend._sila._client_ip = "192.168.1.1"  # type: ignore[attr-defined]
    setattr(self.backend._sila, "bound_port", 8080)  # type: ignore[misc]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(return_value="idle")  # type: ignore[method-assign]
    await self.backend.setup()
    self.backend._sila.setup.assert_called_once()
    self.backend.reset.assert_called_once()
    call_kwargs = self.backend.reset.call_args[1]
    self.assertFalse(call_kwargs.get("simulation_mode", False))

  async def test_setup_full_false_only_sila_setup(self):
    """Test setup(full=False) only calls _sila.setup(), not reset or initialize."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock()  # type: ignore[method-assign]
    await self.backend.setup(full=False)
    self.backend._sila.setup.assert_called_once()
    self.backend.reset.assert_not_called()
    self.backend.get_status.assert_not_called()

  async def test_setup_simulation_mode_passed_to_reset(self):
    """Test setup(simulation_mode=True) passes simulation_mode to reset."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(return_value="idle")  # type: ignore[method-assign]
    await self.backend.setup(simulation_mode=True)
    self.backend.reset.assert_called_once()
    self.assertTrue(self.backend.reset.call_args[1]["simulation_mode"])

  async def test_reset_sets_simulation_mode(self):
    """Test reset(simulation_mode=X) updates backend.simulation_mode."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 1, 0.0))  # type: ignore[method-assign]
    self.assertFalse(self.backend.simulation_mode)
    await self.backend.reset(simulation_mode=True)
    self.assertTrue(self.backend.simulation_mode)
    await self.backend.reset(simulation_mode=False)
    self.assertFalse(self.backend.simulation_mode)

  async def test_setup_retries_with_backoff(self):
    """Test setup(full=True, max_attempts=3) retries on failure with backoff."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    call_count = 0

    async def mock_get_status():
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        raise RuntimeError("transient")
      return "idle"

    self.backend.get_status = AsyncMock(side_effect=mock_get_status)  # type: ignore[method-assign]
    with patch("asyncio.sleep", new_callable=AsyncMock):
      await self.backend.setup(full=True, max_attempts=3)
    self.assertEqual(call_count, 3)
    # Full path runs 3 times (fail twice, succeed on third)
    self.assertEqual(self.backend._sila.setup.call_count, 3)
    self.assertEqual(self.backend.reset.call_count, 3)

  async def test_setup_raises_when_all_attempts_fail(self):
    """Test setup(full=True, max_attempts=2) raises when all attempts fail."""
    self.backend._sila.setup = AsyncMock()  # type: ignore[method-assign]
    self.backend.reset = AsyncMock()  # type: ignore[method-assign]
    self.backend.get_status = AsyncMock(side_effect=RuntimeError("fail"))  # type: ignore[method-assign]
    with patch("asyncio.sleep", new_callable=AsyncMock), self.assertRaises(RuntimeError) as cm:
      await self.backend.setup(full=True, max_attempts=2)
    self.assertIn("fail", str(cm.exception))

  async def test_stop(self):
    """Test backend stop."""
    self.backend._sila.close = AsyncMock()  # type: ignore[method-assign]
    await self.backend.stop()
    self.backend._sila.close.assert_called_once()

  async def test_get_status(self):
    """Test get_status."""
    self.backend._sila.send_command = AsyncMock(  # type: ignore[method-assign]
      return_value={
        "GetStatusResponse": {
          "state": "idle",
          "GetStatusResult": {"returnCode": 1, "message": "Success."},
        }
      }
    )
    status = await self.backend.get_status()
    self.assertEqual(status, "idle")
    self.backend._sila.send_command.assert_called_once_with("GetStatus")

  async def test_open_door(self):
    """Test open_door."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.open_door()
    self.backend._sila.send_command.assert_called_once_with("OpenDoor")

  async def test_close_door(self):
    """Test close_door."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.close_door()
    self.backend._sila.send_command.assert_called_once_with("CloseDoor")

  async def test_read_temperatures(self):
    """Test read_temperatures."""
    # Mock response with SensorValues XML
    sensor_xml = (
      '<SensorValues timestamp="2024-01-01T12:00:00Z">'
      "<Mount>2463</Mount>"
      "<Mount_Monitor>2642</Mount_Monitor>"
      "<Lid>2575</Lid>"
      "<Lid_Monitor>2627</Lid_Monitor>"
      "<Ambient>2450</Ambient>"
      "<PCB>3308</PCB>"
      "<Heatsink>2596</Heatsink>"
      "<Heatsink_TEC>2487</Heatsink_TEC>"
      "</SensorValues>"
    )

    # Create mock ElementTree response
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    sensor_values = await self.backend.read_temperatures()
    self.assertAlmostEqual(sensor_values.mount, 24.63, places=2)  # 2463 * 0.01
    self.assertAlmostEqual(sensor_values.lid, 25.75, places=2)  # 2575 * 0.01

  async def test_execute_method(self):
    """Test execute_method with wait=True; event-driven: wait_for_first_event then handle with eta from DataEvent."""
    self.backend.list_methods = AsyncMock(return_value=([], []))  # type: ignore[method-assign]
    self.backend.get_protocol = AsyncMock(return_value=None)  # type: ignore[method-assign]
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.get_first_event_type_for_command = MagicMock(  # type: ignore[method-assign]
      return_value=FirstEventType.DATA_EVENT
    )
    self.backend._sila.start_command = AsyncMock(  # type: ignore[method-assign]
      return_value=(fut, 12345, 0.0)
    )
    self.backend._sila.wait_for_first_event = AsyncMock(  # type: ignore[method-assign]
      return_value=_minimal_data_event_payload(remaining_s=300.0)
    )
    result = await self.backend.execute_method("MyMethod", wait=True)
    self.assertIsInstance(result, ODTCExecution)
    self.assertEqual(result.method_name, "MyMethod")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["methodName"], "MyMethod")
    self.assertNotIn("estimated_duration_seconds", call_kwargs)
    # Device does not send remaining duration; we use estimated_duration_s - elapsed_s (get_protocol returns None so effective lifetime).
    self.assertEqual(result.estimated_remaining_time, self.backend._get_effective_lifetime())

  async def test_stop_method(self):
    """Test stop_method."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.stop_method()
    self.backend._sila.send_command.assert_called_once_with("StopMethod")

  async def test_lock_device(self):
    """Test lock_device."""
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.lock_device("my_lock_id")
    self.backend._sila.send_command.assert_called_once()
    call_kwargs = self.backend._sila.send_command.call_args[1]
    self.assertEqual(call_kwargs["lock_id"], "my_lock_id")
    self.assertEqual(call_kwargs["PMSId"], "PyLabRobot")

  async def test_unlock_device(self):
    """Test unlock_device."""
    self.backend._sila._lock_id = "my_lock_id"
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await self.backend.unlock_device()
    self.backend._sila.send_command.assert_called_once_with("UnlockDevice", lock_id="my_lock_id")

  async def test_unlock_device_not_locked(self):
    """Test unlock_device when device is not locked."""
    self.backend._sila._lock_id = None
    with self.assertRaises(RuntimeError) as cm:
      await self.backend.unlock_device()
    self.assertIn("not locked", str(cm.exception))

  async def test_get_block_current_temperature(self):
    """Test get_block_current_temperature."""
    sensor_xml = "<SensorValues><Mount>2500</Mount></SensorValues>"
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    temps = await self.backend.get_block_current_temperature()
    self.assertEqual(len(temps), 1)
    self.assertAlmostEqual(temps[0], 25.0, places=2)

  async def test_get_lid_current_temperature(self):
    """Test get_lid_current_temperature."""
    sensor_xml = "<SensorValues><Lid>2600</Lid></SensorValues>"
    root = ET.Element("ResponseData")
    param = ET.SubElement(root, "Parameter", name="SensorValues")
    string_elem = ET.SubElement(param, "String")
    string_elem.text = sensor_xml

    self.backend._sila.send_command = AsyncMock(return_value=root)  # type: ignore[method-assign]
    temps = await self.backend.get_lid_current_temperature()
    self.assertEqual(len(temps), 1)
    self.assertAlmostEqual(temps[0], 26.0, places=2)

  async def test_get_progress_snapshot_with_registered_protocol_returns_enriched(self):
    """With protocol registered and DataEvent with elapsed_s, get_progress_snapshot returns step/cycle/hold."""
    request_id = 12345
    premethod = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    self.backend._protocol_by_request_id[request_id] = premethod
    self.backend._sila._data_events_by_request_id = {  # type: ignore[attr-defined]
      request_id: [_data_event_payload_with_elapsed(100.0, request_id)],
    }
    fut: asyncio.Future[Any] = asyncio.Future()
    self.backend._current_execution = ODTCExecution(
      request_id=request_id,
      command_name="ExecuteMethod",
      _future=fut,
      backend=self.backend,
      estimated_remaining_time=600.0,
      started_at=0.0,
      lifetime=660.0,
      method_name="Pre37",
    )
    try:
      progress = await self.backend.get_progress_snapshot()
      self.assertIsNotNone(progress)
      assert progress is not None
      self.assertIsInstance(progress, ODTCProgress)
      self.assertEqual(progress.elapsed_s, 100.0)
      self.assertEqual(progress.current_step_index, 0)
      self.assertEqual(progress.current_cycle_index, 0)
      self.assertGreater(progress.remaining_hold_s or 0, 0)
      self.assertEqual(progress.target_temp_c, 37.0)
      self.assertEqual(progress.estimated_duration_s, PREMETHOD_ESTIMATED_DURATION_SECONDS)
      self.assertAlmostEqual(progress.remaining_duration_s or 0, 500.0, delta=1.0)  # 600 - 100
    finally:
      self.backend._current_execution = None
      self.backend._protocol_by_request_id.pop(request_id, None)

  async def test_get_current_step_index_and_get_hold_time_with_registered_protocol(self):
    """With protocol registered and DataEvent, get_current_step_index and get_hold_time return values."""
    request_id = 12346
    premethod = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    self.backend._protocol_by_request_id[request_id] = premethod
    self.backend._sila._data_events_by_request_id = {  # type: ignore[attr-defined]
      request_id: [_data_event_payload_with_elapsed(50.0, request_id)],
    }
    fut: asyncio.Future[Any] = asyncio.Future()
    self.backend._current_execution = ODTCExecution(
      request_id=request_id,
      command_name="ExecuteMethod",
      _future=fut,
      backend=self.backend,
      estimated_remaining_time=600.0,
      started_at=0.0,
      lifetime=660.0,
      method_name="Pre37",
    )
    try:
      step_idx = await self.backend.get_current_step_index()
      self.assertEqual(step_idx, 0)
      hold_s = await self.backend.get_hold_time()
      self.assertGreaterEqual(hold_s, 0)
      cycle_idx = await self.backend.get_current_cycle_index()
      self.assertEqual(cycle_idx, 0)
    finally:
      self.backend._current_execution = None
      self.backend._protocol_by_request_id.pop(request_id, None)

  async def test_execute_method_wait_false(self):
    """Test execute_method with wait=False (returns handle); eta from our estimated_duration_s - elapsed_s (no device remaining)."""
    self.backend.list_methods = AsyncMock(return_value=([], []))  # type: ignore[method-assign]
    self.backend.get_protocol = AsyncMock(return_value=None)  # type: ignore[method-assign]
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.get_first_event_type_for_command = MagicMock(  # type: ignore[method-assign]
      return_value=FirstEventType.DATA_EVENT
    )
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, 0.0))  # type: ignore[method-assign]
    self.backend._sila.wait_for_first_event = AsyncMock(  # type: ignore[method-assign]
      return_value=_minimal_data_event_payload(remaining_s=300.0)
    )
    execution = await self.backend.execute_method("PCR_30cycles", wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, ODTCExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.method_name, "PCR_30cycles")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["methodName"], "PCR_30cycles")
    self.assertNotIn("estimated_duration_seconds", call_kwargs)
    # get_protocol returns None so we use effective lifetime for eta (device does not send remaining).
    self.assertEqual(execution.estimated_remaining_time, self.backend._get_effective_lifetime())

  async def test_execute_method_premethod_registers_protocol(self):
    """When executing a premethod, protocol is registered so progress/step/cycle/hold work."""
    premethod = ODTCProtocol(
      kind="premethod",
      name="Pre37",
      target_block_temperature=37.0,
      stages=[],
    )
    method_set = ODTCMethodSet(methods=[], premethods=[premethod])
    self.backend.list_methods = AsyncMock(return_value=([], ["Pre37"]))  # type: ignore[method-assign]
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.get_first_event_type_for_command = MagicMock(  # type: ignore[method-assign]
      return_value=FirstEventType.DATA_EVENT
    )
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 99999, 0.0))  # type: ignore[method-assign]
    self.backend._sila.wait_for_first_event = AsyncMock(  # type: ignore[method-assign]
      return_value=_minimal_data_event_payload(remaining_s=300.0)
    )
    execution = await self.backend.execute_method("Pre37", wait=False)
    assert execution is not None
    self.assertIn(execution.request_id, self.backend._protocol_by_request_id)
    registered = self.backend._protocol_by_request_id[execution.request_id]
    self.assertIsInstance(registered, ODTCProtocol)
    reg_odtc = cast(ODTCProtocol, registered)
    self.assertEqual(reg_odtc.kind, "premethod")
    self.assertEqual(reg_odtc.name, "Pre37")
    self.assertEqual(reg_odtc.target_block_temperature, 37.0)

  async def test_execute_method_first_event_timeout(self):
    """Test execute_method propagates FirstEventTimeout when no DataEvent received in time."""
    self.backend.list_methods = AsyncMock(return_value=([], []))  # type: ignore[method-assign]
    self.backend.get_protocol = AsyncMock(return_value=None)  # type: ignore[method-assign]
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.get_first_event_type_for_command = MagicMock(  # type: ignore[method-assign]
      return_value=FirstEventType.DATA_EVENT
    )
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, 0.0))  # type: ignore[method-assign]
    self.backend._sila.wait_for_first_event = AsyncMock(  # type: ignore[method-assign]
      side_effect=FirstEventTimeout("No DataEvent received for request_id 12345 within 60.0s")
    )
    with self.assertRaises(FirstEventTimeout) as cm:
      await self.backend.execute_method("MyMethod", wait=False)
    self.assertIn("12345", str(cm.exception))
    self.assertIn("60", str(cm.exception))

  async def test_method_execution_awaitable(self):
    """Test that ODTCExecution is awaitable and wait() completes (returns None)."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result("success")
    execution = ODTCExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend,
    )
    result = await execution
    self.assertIsNone(result)
    await execution.wait()  # Should not raise

  async def test_method_execution_is_running(self):
    """Test ODTCExecution.is_running() for ExecuteMethod."""
    fut: asyncio.Future[Any] = asyncio.Future()
    execution = ODTCExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend,
    )
    self.backend.get_status = AsyncMock(return_value="busy")  # type: ignore[method-assign]
    is_running = await execution.is_running()
    self.assertTrue(is_running)

  async def test_method_execution_stop(self):
    """Test ODTCExecution.stop() for ExecuteMethod."""
    fut: asyncio.Future[Any] = asyncio.Future()
    execution = ODTCExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend,
    )
    self.backend._sila.send_command = AsyncMock()  # type: ignore[method-assign]
    await execution.stop()
    self.backend._sila.send_command.assert_called_once_with("StopMethod")

  async def test_odtc_execution_has_command_and_method(self):
    """Test ODTCExecution has command_name and method_name when ExecuteMethod."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    execution = ODTCExecution(
      request_id=12345,
      command_name="ExecuteMethod",
      method_name="PCR_30cycles",
      _future=fut,
      backend=self.backend,
    )
    self.assertEqual(execution.command_name, "ExecuteMethod")
    self.assertEqual(execution.method_name, "PCR_30cycles")

  async def test_command_execution_awaitable(self):
    """Test that ODTCExecution is awaitable and wait() completes (returns None)."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result("success")
    execution = ODTCExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend,
    )
    result = await execution
    self.assertIsNone(result)
    await execution.wait()  # Should not raise

  async def test_command_execution_get_data_events(self):
    """Test ODTCExecution.get_data_events() method."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    execution = ODTCExecution(
      request_id=12345,
      command_name="OpenDoor",
      _future=fut,
      backend=self.backend,
    )
    self.backend._sila._data_events_by_request_id = {
      12345: [{"requestId": 12345, "data": "test1"}, {"requestId": 12345, "data": "test2"}],
      67890: [{"requestId": 67890, "data": "test3"}],
    }
    events = await execution.get_data_events()
    self.assertEqual(len(events), 2)
    self.assertEqual(events[0]["requestId"], 12345)

  async def test_open_door_wait_false_returns_execution_handle(self):
    """Test open_door with wait=False returns ODTCExecution; lifetime/eta from first_event_timeout."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, 0.0))  # type: ignore[method-assign]
    execution = await self.backend.open_door(wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, ODTCExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "OpenDoor")
    self.backend._sila.start_command.assert_called_once()
    self.assertEqual(self.backend._sila.start_command.call_args[0][0], "OpenDoor")
    self.assertNotIn("estimated_duration_seconds", self.backend._sila.start_command.call_args[1])
    self.assertEqual(execution.estimated_remaining_time, 60.0)
    self.assertEqual(execution.lifetime, 120.0)

  async def test_reset_wait_false_returns_handle_with_kwargs(self):
    """Test reset with wait=False returns ODTCExecution and passes deviceId/eventReceiverURI."""
    fut: asyncio.Future[Any] = asyncio.Future()
    fut.set_result(None)
    self.backend._sila.start_command = AsyncMock(return_value=(fut, 12345, 0.0))  # type: ignore[method-assign]
    execution = await self.backend.reset(wait=False)
    assert execution is not None  # Type narrowing
    self.assertIsInstance(execution, ODTCExecution)
    self.assertEqual(execution.request_id, 12345)
    self.assertEqual(execution.command_name, "Reset")
    self.backend._sila.start_command.assert_called_once()
    call_kwargs = self.backend._sila.start_command.call_args[1]
    self.assertEqual(call_kwargs["deviceId"], "ODTC")
    self.assertEqual(call_kwargs["eventReceiverURI"], "http://127.0.0.1:8080/")
    self.assertFalse(call_kwargs["simulationMode"])
    self.assertFalse(self.backend.simulation_mode)

  async def test_is_method_running(self):
    """Test is_method_running()."""
    with patch.object(ODTCBackend, "get_status", new_callable=AsyncMock, return_value="busy"):
      self.assertTrue(await self.backend.is_method_running())

    with patch.object(ODTCBackend, "get_status", new_callable=AsyncMock, return_value="idle"):
      self.assertFalse(await self.backend.is_method_running())

    with patch.object(ODTCBackend, "get_status", new_callable=AsyncMock, return_value="BUSY"):
      # Backend compares to SiLAState.BUSY.value ("busy"), so uppercase is False
      self.assertFalse(await self.backend.is_method_running())

  async def test_wait_for_method_completion(self):
    """Test wait_for_method_completion()."""
    call_count = 0

    async def mock_get_status():
      nonlocal call_count
      call_count += 1
      if call_count < 3:
        return "busy"
      return "idle"

    self.backend.get_status = AsyncMock(side_effect=mock_get_status)  # type: ignore[method-assign]
    await self.backend.wait_for_method_completion(poll_interval=0.1)
    self.assertEqual(call_count, 3)

  async def test_wait_for_method_completion_timeout(self):
    """Test wait_for_method_completion() with timeout."""
    self.backend.get_status = AsyncMock(return_value="busy")  # type: ignore[method-assign]
    with self.assertRaises(TimeoutError):
      await self.backend.wait_for_method_completion(poll_interval=0.1, timeout=0.3)

  async def test_get_data_events(self):
    """Test get_data_events()."""
    self.backend._sila._data_events_by_request_id = {
      12345: [{"requestId": 12345, "data": "test1"}, {"requestId": 12345, "data": "test2"}],
      67890: [{"requestId": 67890, "data": "test3"}],
    }

    # Get all events
    all_events = await self.backend.get_data_events()
    self.assertEqual(len(all_events), 2)
    self.assertEqual(len(all_events[12345]), 2)

    # Get events for specific request_id
    events = await self.backend.get_data_events(request_id=12345)
    self.assertEqual(len(events), 1)
    self.assertEqual(len(events[12345]), 2)

    # Get events for non-existent request_id
    events = await self.backend.get_data_events(request_id=99999)
    self.assertEqual(len(events), 1)
    self.assertEqual(len(events[99999]), 0)

  async def test_list_protocols(self):
    """Test list_protocols returns method and premethod names."""
    method_set = ODTCMethodSet(
      methods=[ODTCProtocol(kind="method", name="PCR_30", stages=[])],
      premethods=[ODTCProtocol(kind="premethod", name="Pre25", stages=[])],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    names = await self.backend.list_protocols()
    self.assertEqual(names.all, ["PCR_30", "Pre25"])

  async def test_list_methods(self):
    """Test list_methods returns (method_names, premethod_names) and matches list_protocols."""
    method_set = ODTCMethodSet(
      methods=[
        ODTCProtocol(kind="method", name="PCR_30", stages=[]),
        ODTCProtocol(kind="method", name="PCR_35", stages=[]),
      ],
      premethods=[
        ODTCProtocol(kind="premethod", name="Pre25", stages=[]),
        ODTCProtocol(kind="premethod", name="Pre37", stages=[]),
      ],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    methods, premethods = await self.backend.list_methods()
    self.assertEqual(methods, ["PCR_30", "PCR_35"])
    self.assertEqual(premethods, ["Pre25", "Pre37"])
    protocol_list = await self.backend.list_protocols()
    self.assertEqual(methods + premethods, protocol_list.all)

  async def test_get_protocol_returns_none_for_missing(self):
    """Test get_protocol returns None when name not found."""
    self.backend.get_method_set = AsyncMock(return_value=ODTCMethodSet())  # type: ignore[method-assign]
    result = await self.backend.get_protocol("nonexistent")
    self.assertIsNone(result)

  async def test_get_protocol_returns_none_for_premethod(self):
    """Test get_protocol returns None for premethod names (runnable protocols only)."""
    method_set = ODTCMethodSet(
      methods=[],
      premethods=[ODTCProtocol(kind="premethod", name="Pre25", stages=[])],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    result = await self.backend.get_protocol("Pre25")
    self.assertIsNone(result)

  async def test_get_protocol_returns_stored_for_method(self):
    """Test get_protocol returns ODTCProtocol for runnable method."""
    method_set = ODTCMethodSet(
      methods=[
        ODTCProtocol(
          kind="method",
          name="PCR_30",
          steps=[ODTCStep(number=1, plateau_temperature=95.0, plateau_time=30.0)],
          stages=[],
        )
      ],
      premethods=[],
    )
    self.backend.get_method_set = AsyncMock(return_value=method_set)  # type: ignore[method-assign]
    result = await self.backend.get_protocol("PCR_30")
    self.assertIsInstance(result, ODTCProtocol)
    assert result is not None  # narrow for type checker
    self.assertEqual(result.name, "PCR_30")
    protocol, _ = odtc_protocol_to_protocol(result)
    self.assertEqual(len(protocol.stages), 1)
    self.assertEqual(len(protocol.stages[0].steps), 1)

  async def test_run_stored_protocol_calls_execute_method(self):
    """Test run_stored_protocol calls execute_method with name, wait, protocol (no estimated_duration_seconds)."""
    self.backend.execute_method = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with patch.object(
      self.backend, "get_protocol", new_callable=AsyncMock, return_value=None
    ), patch.object(
      self.backend, "get_method_set", new_callable=AsyncMock, return_value=ODTCMethodSet()
    ):
      await self.backend.run_stored_protocol("MyMethod", wait=True)
    self.backend.execute_method.assert_called_once_with("MyMethod", wait=True, protocol=None)


class TestODTCThermocycler(unittest.TestCase):
  """Tests for ODTCThermocycler resource."""

  def test_construct_creates_backend_and_uses_dimensions(self):
    """Constructing with odtc_ip and variant creates ODTCBackend and ODTC dimensions."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(
        name="odtc1",
        odtc_ip="192.168.1.100",
        variant=384,
        child_location=Coordinate.zero(),
      )
    self.assertIsInstance(tc.backend, ODTCBackend)
    self.assertEqual(tc.backend.variant, 384000)
    self.assertEqual(tc.get_size_x(), ODTC_DIMENSIONS.x)
    self.assertEqual(tc.get_size_y(), ODTC_DIMENSIONS.y)
    self.assertEqual(tc.get_size_z(), ODTC_DIMENSIONS.z)
    self.assertEqual(tc.model, "ODTC 384")

  def test_construct_variant_96_model(self):
    """Constructing with variant=96 sets model ODTC 96."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=96)
    self.assertEqual(tc.backend.variant, 960000)
    self.assertEqual(tc.model, "ODTC 96")

  def test_serialize_includes_odtc_ip_and_variant(self):
    """serialize() includes odtc_ip and variant from backend."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(
        name="odtc1",
        odtc_ip="192.168.1.50",
        variant=384,
        child_location=Coordinate.zero(),
      )
      tc.backend._sila._machine_ip = "192.168.1.50"
    data = tc.serialize()
    self.assertEqual(data["odtc_ip"], "192.168.1.50")
    self.assertEqual(data["variant"], 384000)

  def test_get_default_config_delegates_to_backend(self):
    """get_default_config returns backend.get_default_config()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    config = tc.get_default_config(name="MyPCR")
    self.assertEqual(config.variant, 384000)
    self.assertEqual(config.name, "MyPCR")

  def test_get_constraints_delegates_to_backend(self):
    """get_constraints returns backend.get_constraints()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    constraints = tc.get_constraints()
    self.assertEqual(constraints.variant, 384000)
    self.assertEqual(constraints.variant_name, "ODTC 384")

  def test_well_count_96(self):
    """well_count is 96 when backend variant is 960000."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=96)
    self.assertEqual(tc.well_count, 96)

  def test_well_count_384(self):
    """well_count is 384 when backend variant is 384000."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
    self.assertEqual(tc.well_count, 384)

  def test_is_profile_running_delegates_to_backend(self):
    """is_profile_running delegates to backend.is_method_running()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
      tc.backend.is_method_running = AsyncMock(return_value=False)  # type: ignore[method-assign]
    result = asyncio.run(tc.is_profile_running())
    self.assertFalse(result)
    tc.backend.is_method_running.assert_called_once()

  def test_wait_for_profile_completion_delegates_to_backend(self):
    """wait_for_profile_completion delegates to backend.wait_for_method_completion()."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      tc = ODTCThermocycler(name="tc", odtc_ip="192.168.1.1", variant=384)
      tc.backend.wait_for_method_completion = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(tc.wait_for_profile_completion(poll_interval=5.0))
    tc.backend.wait_for_method_completion.assert_called_once_with(
      poll_interval=5.0,
      timeout=None,
    )

  def test_backend_provided_uses_it_dimensions_from_constant(self):
    """When backend= is provided, that backend is used; dimensions still from ODTC_DIMENSIONS."""
    with patch("pylabrobot.thermocycling.inheco.odtc_backend.ODTCSiLAInterface"):
      backend = ODTCBackend(odtc_ip="10.0.0.1", variant=384)
      backend._sila = MagicMock(spec=ODTCSiLAInterface)
      backend._sila._machine_ip = "10.0.0.1"
    tc = ODTCThermocycler(
      name="odtc1",
      odtc_ip="192.168.1.1",
      variant=384,
      backend=backend,
      child_location=Coordinate.zero(),
    )
    self.assertIs(tc.backend, backend)
    self.assertEqual(tc.get_size_x(), ODTC_DIMENSIONS.x)
    self.assertEqual(tc.get_size_y(), ODTC_DIMENSIONS.y)
    self.assertEqual(tc.get_size_z(), ODTC_DIMENSIONS.z)


class TestODTCSiLAInterfaceDataEvents(unittest.TestCase):
  """Tests for DataEvent storage in ODTCSiLAInterface."""

  def test_data_event_storage_logic(self):
    """Test that DataEvent storage logic works correctly."""
    # Test the storage logic directly without creating the full interface
    # (which requires network permissions)
    data_events_by_request_id: Dict[int, List[Dict[str, Any]]] = {}

    # Simulate receiving a DataEvent
    data_event = {"requestId": 12345, "data": "test_data"}

    # Apply the same logic as in _on_http handler
    request_id = data_event.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event)

    # Verify storage
    self.assertIn(12345, data_events_by_request_id)
    self.assertEqual(len(data_events_by_request_id[12345]), 1)
    self.assertEqual(data_events_by_request_id[12345][0]["requestId"], 12345)

    # Test multiple events for same request_id
    data_event2 = {"requestId": 12345, "data": "test_data2"}
    request_id = data_event2.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event2)

    self.assertEqual(len(data_events_by_request_id[12345]), 2)

    # Test event with None request_id (should not be stored)
    data_event_no_id = {"data": "test_data_no_id"}
    request_id = data_event_no_id.get("requestId")
    if request_id is not None and isinstance(request_id, int):
      if request_id not in data_events_by_request_id:
        data_events_by_request_id[request_id] = []
      data_events_by_request_id[request_id].append(data_event_no_id)

    # Should still only have 2 events (the one with None request_id wasn't stored)
    self.assertEqual(len(data_events_by_request_id[12345]), 2)


def _minimal_method_xml_with_nested_loops() -> str:
  """Method XML: 5 steps, inner loop 2-4 x 5, outer loop 1-5 x 30 (LoopNumber = actual count)."""
  return """<?xml version="1.0" encoding="utf-8"?>
<MethodSet>
  <DeleteAllMethods>false</DeleteAllMethods>
  <Method methodName="NestedLoops" creator="test" dateTime="2025-01-01T00:00:00">
    <Variant>960000</Variant>
    <PlateType>0</PlateType>
    <FluidQuantity>0</FluidQuantity>
    <PostHeating>false</PostHeating>
    <StartBlockTemperature>25</StartBlockTemperature>
    <StartLidTemperature>110</StartLidTemperature>
    <Step><Number>1</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>2</Number><Slope>2.2</Slope><PlateauTemperature>55</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>3</Number><Slope>4.4</Slope><PlateauTemperature>72</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>4</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>2</GotoNumber><LoopNumber>5</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>5</Number><Slope>2.2</Slope><PlateauTemperature>50</PlateauTemperature><PlateauTime>20</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>1</GotoNumber><LoopNumber>30</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <PIDSet><PID number="1"><PHeating>60</PHeating><PCooling>80</PCooling><IHeating>250</IHeating><ICooling>100</ICooling><DHeating>10</DHeating><DCooling>10</DCooling><PLid>100</PLid><ILid>70</ILid></PID></PIDSet>
  </Method>
</MethodSet>"""


def _minimal_method_xml_flat_loop() -> str:
  """Method XML: 2 steps, single loop 1-2 x 3 (flat, no nesting)."""
  return """<?xml version="1.0" encoding="utf-8"?>
<MethodSet>
  <DeleteAllMethods>false</DeleteAllMethods>
  <Method methodName="FlatLoop" creator="test" dateTime="2025-01-01T00:00:00">
    <Variant>960000</Variant>
    <PlateType>0</PlateType>
    <FluidQuantity>0</FluidQuantity>
    <PostHeating>false</PostHeating>
    <StartBlockTemperature>25</StartBlockTemperature>
    <StartLidTemperature>110</StartLidTemperature>
    <Step><Number>1</Number><Slope>4.4</Slope><PlateauTemperature>95</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>0</GotoNumber><LoopNumber>0</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <Step><Number>2</Number><Slope>2.2</Slope><PlateauTemperature>55</PlateauTemperature><PlateauTime>10</PlateauTime><OverShootSlope1>0.1</OverShootSlope1><OverShootTemperature>0</OverShootTemperature><OverShootTime>0</OverShootTime><OverShootSlope2>0.1</OverShootSlope2><GotoNumber>1</GotoNumber><LoopNumber>3</LoopNumber><PIDNumber>1</PIDNumber><LidTemp>110</LidTemp></Step>
    <PIDSet><PID number="1"><PHeating>60</PHeating><PCooling>80</PCooling><IHeating>250</IHeating><ICooling>100</ICooling><DHeating>10</DHeating><DCooling>10</DCooling><PLid>100</PLid><ILid>70</ILid></PID></PIDSet>
  </Method>
</MethodSet>"""


class TestODTCStageAndRoundTrip(unittest.TestCase):
  """Tests for ODTCStage tree, nested loops, and round-trip (steps and stages)."""

  def test_parse_nested_loops_produces_odtc_stage_tree(self):
    """Parse Method XML with nested loops; odtc_protocol_to_protocol returns Protocol with ODTCStage tree."""
    method_set = parse_method_set(_minimal_method_xml_with_nested_loops())
    self.assertEqual(len(method_set.methods), 1)
    odtc = method_set.methods[0]
    protocol, _ = odtc_protocol_to_protocol(odtc)
    stages = protocol.stages
    self.assertGreater(len(stages), 0)
    # Top level: we expect at least one ODTCStage with inner_stages (outer 1-5, inner 2-4)
    outer = next((s for s in stages if isinstance(s, ODTCStage) and s.inner_stages), None)
    self.assertIsNotNone(outer, "Expected at least one ODTCStage with inner_stages")
    assert outer is not None  # narrow for type checker
    self.assertEqual(outer.repeats, 30)
    assert outer.inner_stages is not None  # we selected for inner_stages above
    self.assertEqual(len(outer.inner_stages), 1)
    self.assertEqual(outer.inner_stages[0].repeats, 5)

  def test_round_trip_via_steps_preserves_structure(self):
    """Serialize ODTCProtocol (from parsed XML) back to XML and re-parse; structure preserved."""
    method_set = parse_method_set(_minimal_method_xml_with_nested_loops())
    odtc = method_set.methods[0]
    xml_out = method_set_to_xml(
      ODTCMethodSet(delete_all_methods=False, premethods=[], methods=[odtc])
    )
    method_set2 = parse_method_set(xml_out)
    self.assertEqual(len(method_set2.methods), 1)
    odtc2 = method_set2.methods[0]
    self.assertEqual(len(odtc2.steps), len(odtc.steps))
    for i, (a, b) in enumerate(zip(odtc.steps, odtc2.steps)):
      self.assertEqual(a.number, b.number, f"step {i} number")
      self.assertEqual(a.goto_number, b.goto_number, f"step {i} goto_number")
      self.assertEqual(a.loop_number, b.loop_number, f"step {i} loop_number")

  def test_round_trip_via_stages_serializes_and_reparses(self):
    """Build ODTCProtocol from ODTCStage tree only (no .steps); serialize uses _odtc_stages_to_steps; re-parse matches."""
    # Build tree with ODTCStep (ODTC-native, lossless): outer 1 and 5, inner 2-4 x 5; outer repeats=30
    step1 = ODTCStep(slope=4.4, plateau_temperature=95.0, plateau_time=10.0)
    step2 = ODTCStep(slope=2.2, plateau_temperature=55.0, plateau_time=10.0)
    step3 = ODTCStep(slope=4.4, plateau_temperature=72.0, plateau_time=10.0)
    step4 = ODTCStep(slope=4.4, plateau_temperature=95.0, plateau_time=10.0)
    step5 = ODTCStep(slope=2.2, plateau_temperature=50.0, plateau_time=20.0)
    inner = ODTCStage(steps=[step2, step3, step4], repeats=5, inner_stages=None)
    outer = ODTCStage(steps=[step1, step5], repeats=30, inner_stages=[inner])
    odtc = ODTCProtocol(
      kind="method",
      name="FromStages",
      variant=960000,
      start_block_temperature=25.0,
      start_lid_temperature=110.0,
      steps=[],  # No steps; serialization will use stages
      stages=[outer],
    )
    xml_str = method_set_to_xml(
      ODTCMethodSet(delete_all_methods=False, premethods=[], methods=[odtc])
    )
    method_set = parse_method_set(xml_str)
    self.assertEqual(len(method_set.methods), 1)
    reparsed = method_set.methods[0]
    self.assertEqual(len(reparsed.steps), 5)
    # Check loop structure: step 4 goto 2 loop 5, step 5 goto 1 loop 30
    by_num = {s.number: s for s in reparsed.steps}
    self.assertEqual(by_num[4].goto_number, 2)
    self.assertEqual(by_num[4].loop_number, 5)
    self.assertEqual(by_num[5].goto_number, 1)
    self.assertEqual(by_num[5].loop_number, 30)

  def test_flat_method_produces_flat_stage_list(self):
    """Flat method (single loop 1-2 x 3) produces flat list of stages (regression)."""
    method_set = parse_method_set(_minimal_method_xml_flat_loop())
    odtc = method_set.methods[0]
    protocol, _ = odtc_protocol_to_protocol(odtc)
    stages = protocol.stages
    self.assertEqual(len(stages), 1)
    self.assertEqual(len(stages[0].steps), 2)
    self.assertEqual(stages[0].repeats, 3)
    if isinstance(stages[0], ODTCStage):
      self.assertFalse(stages[0].inner_stages)


if __name__ == "__main__":
  unittest.main()
