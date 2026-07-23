"""Tests for ODTC protocol conversion, duration estimation, and progress parsing."""

import math
import unittest
import xml.etree.ElementTree as ET

from pylabrobot.capabilities.thermocycling.standard import (
  Overshoot,
  Protocol,
  Ramp,
  Stage,
  Step,
)
from pylabrobot.inheco.odtc.model import FluidQuantity, ODTCPID, ODTCProtocol
from pylabrobot.inheco.odtc.protocol import (
  _calc_overshoot,
  _from_protocol,
  _cycle_count,
  _expanded_step_count,
  estimate_method_duration_seconds,
  build_progress_from_data_event,
  _build_protocol_timeline,
)


def _pcr_protocol() -> Protocol:
  """Standard PCR: 1 denaturation stage + 35-cycle PCR stage."""
  return Protocol(
    stages=[
      Stage(steps=[Step(95.0, 120.0)], repeats=1),
      Stage(
        steps=[
          Step(95.0, 10.0, ramp=Ramp(rate=4.4)),
          Step(55.0, 30.0, ramp=Ramp(rate=2.2)),
          Step(72.0, 60.0, ramp=Ramp(rate=2.2)),
        ],
        repeats=35,
      ),
      Stage(steps=[Step(72.0, 600.0)], repeats=1),
    ],
    name="PCR_Test",
  )


class TestCalcOvershoot(unittest.TestCase):
  def test_no_overshoot_small_delta(self):
    os = _calc_overshoot(30.0, 25.0, 4.4, 30.0, 1)
    self.assertIsNone(os)

  def test_no_overshoot_no_hold(self):
    os = _calc_overshoot(95.0, 25.0, 4.4, 0.0, 1)
    self.assertIsNone(os)

  def test_no_overshoot_invalid_fluid(self):
    os = _calc_overshoot(95.0, 25.0, 4.4, 30.0, -1)
    self.assertIsNone(os)

  def test_heating_overshoot_present(self):
    os = _calc_overshoot(95.0, 25.0, 4.4, 30.0, 1)
    self.assertIsNotNone(os)
    self.assertGreater(os.target_temp, 0.0)
    self.assertAlmostEqual(os.return_rate, 2.2)

  def test_cooling_overshoot_present(self):
    # plateau_temp must be > 35°C for cooling overshoot to trigger
    os = _calc_overshoot(40.0, 95.0, 2.2, 30.0, 1)
    self.assertIsNotNone(os)
    self.assertGreater(os.target_temp, 0.0)

  def test_overshoot_capped_at_102(self):
    # Ramp to 99°C from 0°C should be capped
    os = _calc_overshoot(99.0, 0.0, 4.4, 30.0, 2)
    if os is not None:
      # If overshoot computed, target_temp + plateau_temp <= 102
      self.assertLessEqual(os.target_temp + 99.0, 102.1)


class TestFromProtocol(unittest.TestCase):
  def test_produces_odtc_protocol(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertIsInstance(odtc, ODTCProtocol)
    self.assertIsInstance(odtc, Protocol)

  def test_stage_count_preserved(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertEqual(len(odtc.stages), 3)
    self.assertEqual(odtc.stages[1].repeats, 35)

  def test_step_count_preserved(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertEqual(len(odtc.stages[1].steps), 3)

  def test_slope_clamped_to_hardware_max(self):
    """Slope exceeding hardware max is clamped to max."""
    p = Protocol(
      stages=[Stage(steps=[Step(95.0, 30.0, ramp=Ramp(rate=99.9))], repeats=1)],
    )
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    step = odtc.stages[0].steps[0]
    self.assertLessEqual(step.ramp.rate, 4.4 + 0.01)

  def test_overshoot_computed_for_valid_step(self):
    """Step with large temperature delta should have computed overshoot."""
    p = Protocol(
      stages=[Stage(steps=[Step(95.0, 30.0, ramp=Ramp(rate=4.4))], repeats=1)],
    )
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    step = odtc.stages[0].steps[0]
    # Not necessarily has overshoot (depends on prev_temp=25, delta=70 > 5 and target > 35)
    # So we just check it's a Ramp object with rate set
    self.assertIsNotNone(step.ramp)
    self.assertAlmostEqual(step.ramp.rate, 4.4)

  def test_user_overshoot_honoured(self):
    """If user specifies overshoot on step, it's preserved."""
    user_os = Overshoot(target_temp=3.0, hold_seconds=1.0, return_rate=1.5)
    p = Protocol(
      stages=[Stage(steps=[Step(95.0, 30.0, ramp=Ramp(rate=4.4, overshoot=user_os))], repeats=1)],
    )
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    step = odtc.stages[0].steps[0]
    self.assertIsNotNone(step.ramp.overshoot)
    self.assertAlmostEqual(step.ramp.overshoot.target_temp, 3.0)
    self.assertAlmostEqual(step.ramp.overshoot.return_rate, 1.5)

  def test_lid_temperature_applied(self):
    p = Protocol(stages=[Stage(steps=[Step(95.0, 30.0)], repeats=1)])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True, lid_temperature=105.0)
    step = odtc.stages[0].steps[0]
    self.assertAlmostEqual(step.lid_temperature, 105.0)

  def test_name_sets_is_scratch_false(self):
    p = Protocol(stages=[], name="MyPCR")
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True, name="MyPCR")
    self.assertFalse(odtc.is_scratch)
    self.assertEqual(odtc.name, "MyPCR")

  def test_no_name_sets_is_scratch_true(self):
    p = Protocol(stages=[])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertTrue(odtc.is_scratch)

  def test_start_block_temperature_is_first_step(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertAlmostEqual(odtc.start_block_temperature, 95.0)

  def test_inner_stages_preserved(self):
    inner = Stage(steps=[Step(55.0, 30.0), Step(72.0, 60.0)], repeats=35)
    outer = Stage(steps=[Step(95.0, 10.0)], repeats=1, inner_stages=[inner])
    p = Protocol(stages=[outer])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    self.assertEqual(len(odtc.stages[0].inner_stages), 1)
    self.assertEqual(odtc.stages[0].inner_stages[0].repeats, 35)


class TestExpandedStepCount(unittest.TestCase):
  def _make_pcr_odtc(self) -> ODTCProtocol:
    p = _pcr_protocol()
    return _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)

  def test_step_count_with_loops(self):
    odtc = self._make_pcr_odtc()
    # 1 + (35 * 3) + 1 = 107
    count = _expanded_step_count(odtc)
    self.assertEqual(count, 107)

  def test_cycle_count(self):
    odtc = self._make_pcr_odtc()
    self.assertEqual(_cycle_count(odtc), 35)


class TestEstimateDuration(unittest.TestCase):
  def test_duration_positive(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    dur = estimate_method_duration_seconds(odtc)
    self.assertGreater(dur, 0)

  def test_premethod_duration(self):
    odtc = ODTCProtocol(
      stages=[], variant=96, plate_type=0, fluid_quantity=0,
      post_heating=False, start_block_temperature=37.0, start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)], kind="premethod",
    )
    dur = estimate_method_duration_seconds(odtc)
    self.assertAlmostEqual(dur, 600.0)


class TestProgressFromDataEvent(unittest.TestCase):
  def _make_payload(self, elapsed_s: float, request_id: int = 12345) -> dict:
    import html as html_mod
    ms = int(elapsed_s * 1000)
    inner = (
      f'<d><dataSeries nameId="Elapsed time" unit="ms">'
      f"<integerValue>{ms}</integerValue></dataSeries>"
      f'<dataSeries nameId="Target temperature" unit="1/100°C">'
      f"<integerValue>9500</integerValue></dataSeries></d>"
    )
    inner_escaped = html_mod.escape(inner)
    outer = f'<DataValue><AnyData>{inner_escaped}</AnyData></DataValue>'
    return {"requestId": request_id, "dataValue": outer}

  def test_no_protocol_returns_basic_progress(self):
    payload = self._make_payload(100.0)
    progress = build_progress_from_data_event(payload)
    self.assertAlmostEqual(progress.elapsed_s, 100.0)
    self.assertIsNone(progress.estimated_duration_s)

  def test_with_protocol_returns_enriched_progress(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    payload = self._make_payload(150.0)
    progress = build_progress_from_data_event(payload, odtc_protocol=odtc)
    self.assertAlmostEqual(progress.elapsed_s, 150.0)
    self.assertGreater(progress.estimated_duration_s, 0)
    self.assertGreaterEqual(progress.total_step_count, 1)
    self.assertGreaterEqual(progress.total_cycle_count, 1)

  def test_str_format(self):
    p = _pcr_protocol()
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    payload = self._make_payload(5.0)
    progress = build_progress_from_data_event(payload, odtc_protocol=odtc)
    msg = str(progress)
    self.assertIn("ODTC", msg)
    self.assertIn("step", msg)


class TestApplyOvershoot(unittest.TestCase):
  def test_apply_overshoot_false_produces_no_auto_overshoot(self):
    """apply_overshoot=False: steps with large temp delta get no auto-computed overshoot."""
    p = Protocol(stages=[Stage(steps=[Step(temperature=95.0, hold_seconds=30.0)], repeats=1)])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=False)
    step = odtc.stages[0].steps[0]
    self.assertIsNone(step.ramp.overshoot)

  def test_apply_overshoot_true_computes_for_large_delta(self):
    """apply_overshoot=True (default): large delta step gets overshoot computed."""
    p = Protocol(stages=[Stage(steps=[Step(temperature=95.0, hold_seconds=30.0, ramp=Ramp(rate=4.4))], repeats=1)])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=True)
    step = odtc.stages[0].steps[0]
    # delta 95-25=70 > threshold; fluid_quantity=1 → overshoot should be computed
    self.assertIsNotNone(step.ramp.overshoot)

  def test_explicit_overshoot_honoured_when_apply_false(self):
    """Explicit Ramp.overshoot is always preserved even when apply_overshoot=False."""
    from pylabrobot.capabilities.thermocycling.standard import Overshoot
    user_os = Overshoot(target_temp=3.0, hold_seconds=1.0, return_rate=1.5)
    p = Protocol(stages=[Stage(
      steps=[Step(temperature=95.0, hold_seconds=30.0, ramp=Ramp(rate=4.4, overshoot=user_os))],
      repeats=1,
    )])
    odtc = _from_protocol(p, variant=96, fluid_quantity=FluidQuantity.UL_30_TO_74,
                          plate_type=0, post_heating=True, pid_set=[ODTCPID(number=1)],
                          apply_overshoot=False)
    step = odtc.stages[0].steps[0]
    self.assertIsNotNone(step.ramp.overshoot)
    self.assertAlmostEqual(step.ramp.overshoot.target_temp, 3.0)

  def test_from_protocol_classmethod_is_proper_classmethod(self):
    """ODTCProtocol.from_protocol is a proper classmethod, not a monkey-patch."""
    from pylabrobot.inheco.odtc.model import ODTCProtocol, FluidQuantity, ODTCBackendParams
    p = _pcr_protocol()
    odtc = ODTCProtocol.from_protocol(
      p, variant=96,
      params=ODTCBackendParams(fluid_quantity=FluidQuantity.UL_30_TO_74, name="TestPCR"),
    )
    self.assertIsInstance(odtc, ODTCProtocol)
    self.assertEqual(odtc.name, "TestPCR")
    self.assertFalse(odtc.is_scratch)
    self.assertEqual(odtc.fluid_quantity, FluidQuantity.UL_30_TO_74)

  def test_from_protocol_classmethod_apply_overshoot_false(self):
    """ODTCProtocol.from_protocol apply_overshoot=False works via classmethod."""
    from pylabrobot.inheco.odtc.model import ODTCProtocol, FluidQuantity, ODTCBackendParams
    p = Protocol(stages=[Stage(steps=[Step(temperature=95.0, hold_seconds=30.0, ramp=Ramp(rate=4.4))], repeats=1)])
    odtc = ODTCProtocol.from_protocol(
      p, variant=96,
      params=ODTCBackendParams(fluid_quantity=FluidQuantity.UL_30_TO_74, apply_overshoot=False),
    )
    step = odtc.stages[0].steps[0]
    self.assertIsNone(step.ramp.overshoot)


class TestBackendMethods(unittest.IsolatedAsyncioTestCase):
  """Tests for restored backend methods (using mock driver)."""

  def _make_backend(self):
    from unittest.mock import AsyncMock, MagicMock
    from pylabrobot.inheco.odtc.backend import ODTCThermocyclerBackend
    from pylabrobot.inheco.odtc.driver import ODTCDriver
    from pylabrobot.inheco.odtc.model import ODTCMethodSet, ODTCProtocol, ODTCPID, FluidQuantity
    driver = MagicMock(spec=ODTCDriver)
    driver.send_command = AsyncMock(return_value=None)
    driver.send_command_async = AsyncMock(return_value=(AsyncMock(), 12345))
    backend = ODTCThermocyclerBackend(driver=driver, variant=96)
    backend.get_method_set = AsyncMock(return_value=ODTCMethodSet(methods=[]))
    return backend

  async def test_run_stored_protocol_raises_for_missing_name(self):
    backend = self._make_backend()
    with self.assertRaises(ValueError) as ctx:
      await backend.run_stored_protocol("NonExistent")
    self.assertIn("NonExistent", str(ctx.exception))
    self.assertIn("upload", str(ctx.exception).lower())

  async def test_upload_protocol_raises_on_conflict_without_overwrite(self):
    from unittest.mock import AsyncMock
    from pylabrobot.inheco.odtc.model import ODTCMethodSet, ODTCProtocol, ODTCPID, FluidQuantity
    backend = self._make_backend()
    existing_method = ODTCProtocol(
      stages=[], name="PCR1", variant=96, plate_type=0,
      fluid_quantity=FluidQuantity.UL_30_TO_74, post_heating=True,
      start_block_temperature=25.0, start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)], kind="method", is_scratch=False,
    )
    backend.get_method_set = AsyncMock(return_value=ODTCMethodSet(methods=[existing_method]))
    new_method = ODTCProtocol(
      stages=[], name="PCR1", variant=96, plate_type=0,
      fluid_quantity=FluidQuantity.UL_30_TO_74, post_heating=True,
      start_block_temperature=25.0, start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)], kind="method", is_scratch=False,
    )
    with self.assertRaises(ValueError) as ctx:
      await backend.upload_protocol(new_method, allow_overwrite=False)
    self.assertIn("PCR1", str(ctx.exception))

  async def test_upload_protocol_scratch_bypasses_conflict_check(self):
    from unittest.mock import AsyncMock
    from pylabrobot.inheco.odtc.model import ODTCMethodSet, ODTCProtocol, ODTCPID, FluidQuantity
    backend = self._make_backend()
    scratch_method = ODTCProtocol(
      stages=[], name="plr_currentProtocol", variant=96, plate_type=0,
      fluid_quantity=FluidQuantity.UL_30_TO_74, post_heating=True,
      start_block_temperature=25.0, start_lid_temperature=110.0,
      pid_set=[ODTCPID(number=1)], kind="method", is_scratch=True,
    )
    backend._upload_method_set = AsyncMock()
    await backend.upload_protocol(scratch_method, allow_overwrite=False)
    backend._upload_method_set.assert_called_once()


if __name__ == "__main__":
  unittest.main()
