"""CERTUS command sequencing and preconditions, without hardware."""

import asyncio
import unittest
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, call, patch

from typing_extensions import NotRequired

from ._inco import Inco, IncoError
from ._inco_udp import IncoUDP
from .device import CertusFlex, CertusFlexError, Head, ValveCalibration

ROOT = "Machine.Entity.Dispenser"
POSITION = ROOT + ".Module.Entity.PosSystem"
PRESSURE = ROOT + ".Module.Entity.PressureRegulator"
HEAD = ROOT + ".Module.Entity.Head"
VALVE = ROOT + ".Module.Entity.Valve1"


class CleanCycleParameters(TypedDict):
  """Typed keyword arguments shared by clean-cycle cases."""

  prime_duration: float
  purge_duration: float
  purge_pulse_duration: float
  cycles: int
  purge_interval: NotRequired[float]


class CertusFlexTests(unittest.IsolatedAsyncioTestCase):
  """Check recovered device commands, completion barriers, and failures."""

  def setUp(self) -> None:
    self.device = CertusFlex(
      "example.invalid", target="CERTUS", head="flex5", timeout=0.03, poll_interval=0.001
    )
    self.inco = MagicMock(spec=Inco)
    self.device._inco = self.inco

  def test_connection_selection(self) -> None:
    """Default to direct UDP while preserving named-target TCP connections."""
    direct = CertusFlex("example.invalid", head="flex8_angled", source_ip="192.0.2.2")
    self.assertIsInstance(direct._inco, IncoUDP)
    self.assertEqual(direct._inco.io.serialize()["port"], 1964)
    self.assertEqual(direct._inco.io.serialize()["source_ip"], "192.0.2.2")
    server = CertusFlex("example.invalid", head="flex5", target="CERTUS")
    self.assertIsInstance(server._inco, Inco)
    self.assertEqual(server._inco.io.serialize()["port"], 7979)
    for target in (None, "CERTUS"):
      custom = CertusFlex("example.invalid", head="flex5", target=target, port=12345)
      self.assertEqual(custom._inco.io.serialize()["port"], 12345)
    with self.assertRaises(ValueError):
      CertusFlex("example.invalid", head="flex5", target="CERTUS", source_ip="192.0.2.2")

  def numbers(self, values: dict[str, list[float]]) -> None:
    """Return a sequence per path, then repeat the final reading."""

    async def read(path: str) -> float:
      sequence = values[path]
      return sequence.pop(0) if len(sequence) > 1 else sequence[0]

    self.inco.get_number.side_effect = read

  async def test_setup_only_reads_identity(self) -> None:
    self.inco.get_string.side_effect = ["CF123", "1.2.3"]
    with self.assertLogs("pylabrobot.gyger", level="INFO") as logs:
      await self.device.setup()
    self.assertIn("Connected to CERTUS FLEX CF123, firmware 1.2.3", logs.output[0])
    self.assertEqual(self.device.serial_number, "CF123")
    self.assertEqual(self.device.firmware_version, "1.2.3")
    self.assertEqual(
      self.inco.get_string.call_args_list,
      [call(ROOT + ".Act.Serialnumber"), call("Software.Revision.Dispenser")],
    )
    self.inco.call.assert_not_awaited()
    self.inco.put_number.assert_not_awaited()
    await self.device.stop()
    self.inco.stop.assert_awaited_once()
    self.inco.call.assert_not_awaited()

  async def test_failed_setup_disconnects(self) -> None:
    self.inco.get_string.side_effect = ConnectionError("failed")
    with self.assertRaises(ConnectionError):
      await self.device.setup()
    self.inco.stop.assert_awaited_once()

  async def test_head_outputs(self) -> None:
    heads: dict[Head, range] = {
      "flex5": range(1, 6),
      "flex8": range(8),
      "flex4_angled": range(4),
      "flex8_angled": range(8),
    }
    for head, outputs in heads.items():
      device = CertusFlex("example.invalid", target="CERTUS", head=head)
      self.assertEqual([valve.output_id for valve in device.valves], list(outputs))

  async def test_initialize_from_off_advances_two_states(self) -> None:
    self.numbers(
      {
        ROOT + ".Prop.State": [1, 3, 3, 4, 4],
        ROOT + ".Coord.Prop.State": [5, 4, 5, 4],
        POSITION + ".Prop.State": [3, 5, 4],
      }
    )
    await self.device.initialize()
    self.assertEqual(
      self.inco.call.call_args_list,
      [call(ROOT + ".Cmd.Start"), call(ROOT + ".Cmd.Start"), call(POSITION + ".Cmd.Init", "")],
    )

  async def test_initialize_ready_is_idempotent(self) -> None:
    self.inco.get_number.return_value = 4.0
    await self.device.initialize()
    self.inco.call.assert_not_awaited()

  async def test_initialize_fault_is_not_acknowledged(self) -> None:
    self.inco.get_number.return_value = 6.0
    with self.assertRaises(CertusFlexError):
      await self.device.initialize()
    self.inco.call.assert_not_awaited()

  async def test_move_waits_until_ready(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 4, 5, 5, 4]})
    await self.device.move_to(1, 2, 3)
    self.inco.call.assert_awaited_once_with(POSITION + ".Cmd.MoveXYZ", 1, 2, 3, "Slow")
    self.assertEqual(self.inco.get_number.await_count, 5)

  async def test_short_move_acceptance_grace(self) -> None:
    self.inco.get_number.return_value = 4.0
    with patch("pylabrobot.gyger.certus_flex.device.time") as timer:
      timer.monotonic.side_effect = [0, 0, 5]
      await self.device.move_to(1, 2, 3)
    self.assertEqual(self.inco.get_number.await_count, 3)

  async def test_move_fault_and_timeout_do_not_repeat(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 5, 6]})
    with self.assertRaises(CertusFlexError):
      await self.device.move_to(1, 2, 3)
    self.numbers({POSITION + ".Prop.State": [4, 5]})
    with self.assertRaises(asyncio.TimeoutError):
      await self.device.move_to(1, 2, 3)
    self.assertEqual(self.inco.call.await_count, 2)

  async def test_prime_position_moves_z_before_xy(self) -> None:
    self.numbers({POSITION + ".Prop.State": [4, 5, 4, 4, 5, 4, 4, 5, 4]})
    await self.device.move_to_prime()
    self.assertEqual(
      self.inco.call.call_args_list,
      [
        call(POSITION + ".Cmd.MoveZToPark", "Slow"),
        call(POSITION + ".Cmd.MoveXYToPrime", "Slow"),
        call(POSITION + ".Cmd.MoveZToPrime", "Slow"),
      ],
    )

  async def test_pressure_init_waits_for_transition_and_actual_value(self) -> None:
    self.numbers(
      {
        PRESSURE + ".Prop.State": [3, 3, 5, 5, 4, 4],
        PRESSURE + ".Act.ChargeState": [8, 3, 3],
        PRESSURE + ".Act.Pressure": [0.1, 0.2, 0.3],
      }
    )
    await self.device.set_pressure(0.3)
    self.inco.put_number.assert_awaited_once_with(PRESSURE + ".Prop.SetPressure", 0.3)
    self.inco.call.assert_awaited_once_with(PRESSURE + ".Cmd.Init")

  async def test_pressure_failure_is_reported(self) -> None:
    self.numbers({PRESSURE + ".Prop.State": [4], PRESSURE + ".Act.ChargeState": [2]})
    with self.assertRaises(CertusFlexError):
      await self.device.set_pressure(0.3)

  async def test_discharge(self) -> None:
    self.numbers({PRESSURE + ".Prop.State": [4, 5, 3], PRESSURE + ".Act.Pressure": [0.1, 0.0]})
    await self.device.set_pressure(0)
    self.inco.call.assert_awaited_once_with(PRESSURE + ".Cmd.Discharge")
    self.inco.put_number.assert_awaited_once_with(PRESSURE + ".Prop.SetPressure", 0)

  async def test_timed_pulse_uses_microseconds_and_completion_barrier(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {HEAD + ".Prop.SyncValves": [0.0, 2.0]}
    )
    await self.device.valves[0].dispense_for(0.001)
    self.assertEqual(
      self.inco.call.call_args_list,
      [call(VALVE + ".Cmd.Dispens", 1000), call(HEAD + ".Cmd.SyncValves")],
    )
    self.inco.put_number.assert_awaited_once_with(HEAD + ".Prop.SyncValves", 0)

  async def test_valve_fault_prevents_dispensing(self) -> None:
    self.numbers({ROOT + ".Prop.State": [4], VALVE + ".Prop.State": [6]})
    with self.assertRaises(CertusFlexError):
      await self.device.valves[0].dispense_for(0.001)
    self.inco.call.assert_not_awaited()

  async def test_valve_fault_after_pulse_is_reported(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD)}
      | {VALVE + ".Prop.State": [4.0, 6.0], HEAD + ".Prop.SyncValves": [1.0]}
    )
    with self.assertRaises(CertusFlexError):
      await self.device.valves[0].dispense_for(0.001)
    self.assertEqual(self.inco.call.await_count, 2)  # Pulse and barrier; no repeat.

  async def test_barrier_timeout_does_not_repeat_dose(self) -> None:
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {HEAD + ".Prop.SyncValves": [0.0]}
    )
    with self.assertRaisesRegex(asyncio.TimeoutError, "do not retry"):
      await self.device.valves[0].dispense_for(0.001)
    self.assertEqual(self.inco.call.await_count, 2)

  async def test_volume_requires_calibration_and_matching_pressure(self) -> None:
    valve = self.device.valves[0]
    with self.assertRaises(ValueError):
      await valve.dispense(1)
    valve.calibration = ValveCalibration(((1, 0.001), (2, 0.002)), pressure=0.3)
    self.numbers({PRESSURE + ".Act.Pressure": [0.1], PRESSURE + ".Prop.SetPressure": [0.3]})
    with self.assertRaises(CertusFlexError):
      await valve.dispense(1)
    self.inco.call.assert_not_awaited()

  async def test_corrected_volume_sends_vendor_open_time(self) -> None:
    """A volume correction precedes interpolation and sends microseconds to Dispens."""
    valve = self.device.valves[0]
    valve.calibration = ValveCalibration(((0.5, 0.0005), (1.5, 0.00125)), pressure=0.2)
    self.numbers(
      {path + ".Prop.State": [4.0] for path in (ROOT, POSITION, PRESSURE, HEAD, VALVE)}
      | {
        PRESSURE + ".Act.Pressure": [0.2],
        PRESSURE + ".Prop.SetPressure": [0.2],
        HEAD + ".Prop.SyncValves": [1.0],
      }
    )
    await valve.dispense(1.0, correction_factor=1.25)
    self.assertEqual(
      self.inco.call.call_args_list,
      [call(VALVE + ".Cmd.Dispens", 1062.5), call(HEAD + ".Cmd.SyncValves")],
    )
    self.inco.call.reset_mock()
    with self.assertRaises(ValueError):
      await valve.dispense(1.5, correction_factor=1.25)
    self.inco.call.assert_not_awaited()

  async def test_pressure_check_and_pulse_share_operation_lock(self) -> None:
    valve = self.device.valves[0]
    valve.calibration = ValveCalibration(((1, 0.001), (2, 0.002)), pressure=0.3)
    waiting, release = asyncio.Event(), asyncio.Event()

    async def read(path: str) -> float:
      if path == PRESSURE + ".Act.Pressure":
        waiting.set()
        await release.wait()
        return 0.3
      if path == PRESSURE + ".Prop.SetPressure":
        return 0.3
      return 4.0  # Ready module or positive barrier flag.

    self.inco.get_number.side_effect = read
    dose = asyncio.create_task(valve.dispense(1.5))
    await waiting.wait()
    # close() is another mutation and must wait until this dose completes.
    close = asyncio.create_task(valve.close())
    await asyncio.sleep(0)
    self.inco.call.assert_not_awaited()
    release.set()
    await asyncio.gather(dose, close)
    self.assertEqual(
      self.inco.call.call_args_list,
      [
        call(VALVE + ".Cmd.Dispens", 1500),
        call(HEAD + ".Cmd.SyncValves"),
        call(VALVE + ".Cmd.CloseValve"),
        call(HEAD + ".Cmd.SyncValves"),
      ],
    )

  async def test_invalid_parameters_do_not_communicate(self) -> None:
    for pressure in (-1, 0.05, 1.1, float("nan")):
      with self.assertRaises(ValueError):
        await self.device.set_pressure(pressure)
    for duration in (0, -1, 0.00001, float("inf"), 3000):
      with self.assertRaises(ValueError):
        await self.device.valves[0].dispense_for(duration)
    with self.assertRaises(ValueError):
      await self.device.move_to(float("nan"), 1, 2)
    self.inco.get_number.assert_not_awaited()
    self.inco.call.assert_not_awaited()

  async def test_invalid_state(self) -> None:
    for state in (-1, 7, 1.5, float("nan")):
      self.inco.get_number.return_value = state
      with self.assertRaises(CertusFlexError):
        await self.device.valves[0].get_state()


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
  """Check native valve maintenance, cycle sequencing, and failure containment."""

  def setUp(self) -> None:
    """Supply ready modules, regulated pressure, and a ten-second prime limit."""
    self.device = CertusFlex("example.invalid", head="flex8_angled", timeout=0.1)
    self.inco = MagicMock(spec=Inco)
    self.device._inco = self.inco

    async def read(path: str) -> float:
      """Return realistic independent preconditions for valve maintenance."""
      if path.endswith(".PrimeTimeout"):
        return 10e6
      if path in (PRESSURE + ".Act.Pressure", PRESSURE + ".Prop.SetPressure"):
        return 0.2
      if path == HEAD + ".Prop.SyncValves":
        return 1
      return 4

    self.inco.get_number.side_effect = read
    waiter = patch.object(self.device, "_wait_state", new=AsyncMock())
    waiter.start()
    self.addCleanup(waiter.stop)
    self.parameters: CleanCycleParameters = {
      "prime_duration": 0.1,
      "purge_duration": 0.02,
      "purge_pulse_duration": 0.001,
      "cycles": 1,
    }

  async def test_prime_limit_and_completion(self) -> None:
    """Prime uses microseconds and a barrier; its firmware limit is checked first."""
    await self.device.valves[1].prime(0.1)
    self.assertEqual(
      self.inco.call.call_args_list,
      [call(VALVE + ".Cmd.Prime", 100000), call(HEAD + ".Cmd.SyncValves")],
    )
    self.inco.call.reset_mock()
    with self.assertRaises(ValueError):
      await self.device.valves[1].prime(11)
    self.inco.call.assert_not_awaited()

  async def test_purge_timing_variants(self) -> None:
    """Keep pulse timing in firmware and support its explicit-interval form."""
    await self.device.valves[1].purge(0.02, pulse_duration=0.001)
    await self.device.valves[1].purge(0.02, pulse_duration=0.001, interval=0.002)
    self.assertEqual(
      self.inco.call.call_args_list,
      [
        call(VALVE + ".Cmd.Purge", 20000, 1000),
        call(HEAD + ".Cmd.SyncValves"),
        call(VALVE + ".Cmd.Purge", 20000, 1000, 2000),
        call(HEAD + ".Cmd.SyncValves"),
      ],
    )

  async def test_unsupported_purge_is_not_resubmitted(self) -> None:
    """An unsupported explicit interval cannot trigger a second pulse train."""
    self.inco.call.side_effect = IncoError(0x50403, VALVE + ".Cmd.Purge")
    with self.assertRaises(IncoError):
      await self.device.valves[1].purge(0.02, pulse_duration=0.001, interval=0.002)
    self.inco.call.assert_awaited_once_with(VALVE + ".Cmd.Purge", 20000, 1000, 2000)

  async def test_abort_failure_preserves_cycle_error(self) -> None:
    """Failed cleanup is logged without hiding the operation that failed."""
    failure = TimeoutError("lost purge reply")

    async def command(path: str, *arguments) -> None:
      """Simulate a lost connection after the first purge request."""
      if path.endswith(".Cmd.Purge"):
        raise failure
      if path.endswith(".Cmd.StopDispens"):
        raise ConnectionError("socket closed")

    self.inco.call.side_effect = command
    with self.assertLogs("pylabrobot.gyger", level="ERROR") as logs:
      with self.assertRaises(TimeoutError) as caught:
        await self.device.clean_cycle([1, 2], **self.parameters)
    self.assertIs(caught.exception, failure)
    self.assertEqual(len(logs.output), 2)

  async def test_clean_sequence_and_pressure_unchanged(self) -> None:
    """Purge all selected channels, pause, prime them, repeat, then park."""
    parameters = self.parameters.copy()
    parameters["cycles"] = 2
    with patch("pylabrobot.gyger.certus_flex.device.asyncio.sleep", new=AsyncMock()) as sleep:
      await self.device.clean_cycle([1, 2], **parameters)
    cycle = [
      call(VALVE + ".Cmd.Purge", 20000, 1000),
      call(HEAD + ".Cmd.SyncValves"),
      call(VALVE.replace("Valve1", "Valve2") + ".Cmd.Purge", 20000, 1000),
      call(HEAD + ".Cmd.SyncValves"),
      call(VALVE + ".Cmd.Prime", 100000),
      call(HEAD + ".Cmd.SyncValves"),
      call(VALVE.replace("Valve1", "Valve2") + ".Cmd.Prime", 100000),
      call(HEAD + ".Cmd.SyncValves"),
    ]
    self.assertEqual(
      self.inco.call.call_args_list,
      [
        call(POSITION + ".Cmd.MoveZToPark", "Slow"),
        call(POSITION + ".Cmd.MoveXYToPurge", "Slow"),
        call(POSITION + ".Cmd.MoveZToPurge", "Slow"),
        *cycle,
        *cycle,
        call(POSITION + ".Cmd.MoveZToPark", "Slow"),
        call(POSITION + ".Cmd.MoveXYToPark", "Slow"),
      ],
    )
    self.assertEqual(sleep.await_args_list, [call(0.25), call(0.25)])
    self.assertTrue(
      all(c.args == (HEAD + ".Prop.SyncValves", 0) for c in self.inco.put_number.call_args_list)
    )

  async def test_invalid_cycle_inputs_never_move_or_dispense(self) -> None:
    """Reject invalid channels, cycles, pulse widths, and intervals up front."""
    for channels in ([], [True], [-1], [8], [1, 1]):
      with self.subTest(channels=channels), self.assertRaises(ValueError):
        await self.device.clean_cycle(channels, **self.parameters)
    invalid_parameters: tuple[CleanCycleParameters, ...] = (
      {**self.parameters, "cycles": 0},
      {**self.parameters, "cycles": True},
      {**self.parameters, "prime_duration": float("nan")},
      {**self.parameters, "prime_duration": 11},
      {**self.parameters, "purge_pulse_duration": 0.03},
      {**self.parameters, "purge_interval": -1},
    )
    for parameters in invalid_parameters:
      with self.subTest(parameters=parameters), self.assertRaises(ValueError):
        await self.device.clean_cycle([1], **parameters)
    self.inco.call.assert_not_awaited()
    self.inco.put_number.assert_not_awaited()

  async def test_unregulated_pressure_prevents_motion(self) -> None:
    """Ready flags alone do not authorize a clean cycle with zero pressure."""
    self.inco.get_number.side_effect = None
    self.inco.get_number.return_value = 4
    parameters = self.parameters.copy()
    parameters["prime_duration"] = 0.00005
    # PrimeTimeout is returned in microseconds; supply a sufficient limit first.
    self.inco.get_number.side_effect = [1e6, 4, 4, 4, 4, 4, 0, 0]
    with self.assertRaises(CertusFlexError):
      await self.device.clean_cycle([1], **parameters)
    self.inco.call.assert_not_awaited()

  async def test_failed_purge_aborts_without_retry_or_recovery_move(self) -> None:
    """An uncertain pulse result stops selected valves and preserves the original error."""
    failure = TimeoutError("lost purge reply")

    async def command(path: str, *arguments) -> None:
      """Fail the first pulse train after its request was submitted."""
      if path.endswith(".Cmd.Purge"):
        raise failure

    self.inco.call.side_effect = command
    with self.assertRaises(TimeoutError) as caught:
      await self.device.clean_cycle([1, 2], **self.parameters)
    self.assertIs(caught.exception, failure)
    self.assertEqual(
      self.inco.call.call_args_list[-3:],
      [
        call(VALVE + ".Cmd.Purge", 20000, 1000),
        call(VALVE + ".Cmd.StopDispens"),
        call(VALVE.replace("Valve1", "Valve2") + ".Cmd.StopDispens"),
      ],
    )
    self.assertEqual(self.inco.call.await_count, 6)

  async def test_cancellation_aborts_and_releases_operation_lock(self) -> None:
    """Cancellation does not repeat a pulse or leave the device lock held."""
    started = asyncio.Event()

    async def command(path: str, *arguments) -> None:
      """Block only the purge call so abort commands can complete."""
      if path.endswith(".Cmd.Purge"):
        started.set()
        await asyncio.Event().wait()

    self.inco.call.side_effect = command
    task = asyncio.create_task(self.device.clean_cycle([1, 2], **self.parameters))
    await started.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(self.inco.call.await_count, 6)
    await asyncio.wait_for(self.device._operation_lock.acquire(), 0.1)
    self.device._operation_lock.release()

  async def test_clean_cycle_excludes_other_motion(self) -> None:
    """A second operation cannot interleave motion into an active cleaning sequence."""
    started, release = asyncio.Event(), asyncio.Event()

    async def command(path: str, *arguments) -> None:
      """Hold the first purge while another task requests a move."""
      if path.endswith(".Cmd.Purge"):
        started.set()
        await release.wait()

    self.inco.call.side_effect = command
    cleaning = asyncio.create_task(self.device.clean_cycle([1], **self.parameters))
    await started.wait()
    motion = asyncio.create_task(self.device.move_to(1, 2, 3))
    await asyncio.sleep(0)
    self.assertNotIn(
      call(POSITION + ".Cmd.MoveXYZ", 1, 2, 3, "Slow"), self.inco.call.call_args_list
    )
    release.set()
    await asyncio.gather(cleaning, motion)
    self.assertEqual(
      self.inco.call.call_args_list[-1], call(POSITION + ".Cmd.MoveXYZ", 1, 2, 3, "Slow")
    )


class CalibrationTests(unittest.TestCase):
  """Calibrations interpolate only within their measured range."""

  def test_interpolation_and_boundaries(self) -> None:
    calibration = ValveCalibration(((1, 0.001), (3, 0.005)), pressure=0.3)
    self.assertEqual(calibration.duration_for_volume(1), 0.001)
    self.assertEqual(calibration.duration_for_volume(2), 0.003)
    self.assertEqual(calibration.duration_for_volume(3), 0.005)
    for volume in (0, 4, float("nan")):
      with self.assertRaises(ValueError):
        calibration.duration_for_volume(volume)

  def test_bad_calibrations(self) -> None:
    for points in (
      (),
      ((1, 1),),
      ((1, 1), (1, 2)),
      ((1, 2), (2, 1)),
      ((-1, 1), (2, 2)),
      ((1, 1), (float("inf"), 2)),
    ):
      with self.assertRaises(ValueError):
        ValveCalibration(points, pressure=0.3)

  def test_vendor_volume_conversion(self) -> None:
    """Reference values from CERTUS CONTROL's volume correction and linear interpolation.

    These synthetic points exercise the algorithm; they are not a measured valve
    calibration. Reference methods: DispenseCalibration.CalculateValveOpenTime and
    CalibrationUtils.InterpolateValveOpenTime in CERTUS CONTROL 2.1.17.2517.
    """
    calibration = ValveCalibration(((0.5, 0.0005), (1.5, 0.00125), (4.0, 0.003)), pressure=0.2)
    for volume, factor, expected_us in (
      (0.5, 1.0, 500),
      (1.0, 1.0, 875),
      (1.5, 1.0, 1250),
      (2.0, 1.0, 1600),
      (4.0, 1.0, 3000),
      (1.0, 0.5, 500),
      (1.0, 1.25, 1062.5),
      (1.0, 1.5, 1250),
      (2.0, 1.25, 1950),
    ):
      with self.subTest(volume=volume, correction_factor=factor):
        self.assertAlmostEqual(
          calibration.duration_for_volume(volume, correction_factor=factor) * 1e6,
          expected_us,
        )
    for factor in (0, 0.4999, 1.5001, True, float("nan"), float("inf")):
      with self.subTest(correction_factor=factor), self.assertRaises(ValueError):
        calibration.duration_for_volume(1.0, correction_factor=factor)
    for volume, factor in ((0.5, 0.5), (4.0, 1.5)):
      with self.subTest(volume=volume, correction_factor=factor), self.assertRaises(ValueError):
        calibration.duration_for_volume(volume, correction_factor=factor)
