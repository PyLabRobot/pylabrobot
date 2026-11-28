import asyncio
import struct
import unittest
import unittest.mock

from pylabrobot.hamilton.mpe2 import commands as cmd
from pylabrobot.hamilton.mpe2 import errors
from pylabrobot.hamilton.mpe2 import mpe2 as mpe2_module
from pylabrobot.hamilton.mpe2.connection import MPE2Client
from pylabrobot.hamilton.mpe2.control_points import (
  PlateStep,
  parse_control_points,
  ramp,
  to_firmware,
)
from pylabrobot.hamilton.mpe2.mpe2 import MPE2
from pylabrobot.hamilton.mpe2.simulator import MPE2Simulator
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry
from pylabrobot.io.serial import HAS_SERIAL

DEST = Address(1, 1, 0x1000)


def f32(type_id: int, value: float) -> bytes:
  return bytes([type_id, 0, 4, 0]) + struct.pack("<f", value)


BOOL_TRUE = bytes([23, 1, 2, 0, 1, 0])  # A 1-byte value is padded to 2 and flagged 0x01.
BOOL_FALSE = bytes([23, 1, 2, 0, 0, 0])


def enum(value: int) -> bytes:
  return bytes([32, 0, 4, 0]) + struct.pack("<I", value)


class EncodingTests(unittest.TestCase):
  """The parameters as Hamilton's driver sends them (types and order from its DLLs)."""

  def test_filter_plate_placed(self):
    c = cmd.FilterPlatePlaced(dest=DEST, filter_height=45.0, nozzle_height=55.0)
    self.assertEqual((c.interface_id, c.action_code, c.command_id), (1, 3, 7))
    self.assertEqual(c.build_parameters().build(), f32(40, 45.0) + f32(40, 55.0))

  def test_initialize_with_parameters(self):
    c = cmd.InitializeWithParameters(
      dest=DEST,
      smart=True,
      vacuum_run_time_ms=10_000,
      waste_container=cmd.WasteContainer.CONTAINER_2,
      disable_upper_limit_check=False,
    )
    self.assertEqual(c.command_id, 22)
    self.assertEqual(
      c.build_parameters().build(),
      BOOL_TRUE + bytes([6, 0, 4, 0]) + struct.pack("<I", 10_000) + enum(1) + BOOL_FALSE,
    )

  def test_process_filter_to_waste_container(self):
    point = cmd.ControlPoint(duration=3.0, type=cmd.ControlPointType.PRESSURE, value=0.5)
    c = cmd.ProcessFilterPlateToWaste(
      dest=DEST,
      control_points=(point,),
      return_plate_to_integration_area=True,
      waste_container=cmd.WasteContainer.CONTAINER_1,
      disable_upper_limit_check=False,
    )
    self.assertEqual(c.command_id, 23)
    inner = f32(40, 3.0) + enum(1) + f32(40, 0.5)
    struct_fragment = bytes([30, 0, len(inner), 0]) + inner
    array = bytes([31, 0, len(struct_fragment), 0]) + struct_fragment
    self.assertEqual(c.build_parameters().build(), array + BOOL_TRUE + enum(0) + BOOL_FALSE)

  def test_queries_use_status_requests(self):
    for c in (
      cmd.GetControlState,
      cmd.GetManifoldInformation,
      cmd.GetLastCommandResults,
      cmd.GetVacuumState,
      cmd.GetWaste1Status,
    ):
      self.assertEqual(c.action_code, 0, c.__name__)
    self.assertEqual(cmd.GetWaste2Status.command_id, 9)

  def test_whole_message_builds(self):
    c = cmd.ClampFilterPlate(dest=DEST)
    self.assertIsInstance(c.build(Address(2, 1, 65535), 5), bytes)

  def test_decodes_manifold_info(self):
    def string(s: str) -> bytes:
      data = s.encode() + b"\x00"
      return bytes([15, 0, len(data), 0]) + data

    inner = string("M123") + string("2021-01-05") + bytes([6, 0, 4, 0]) + struct.pack("<I", 96)
    data = bytes([30, 0, len(inner), 0]) + inner
    info = cmd.GetManifoldInformation.parse_response_parameters(data)
    self.assertEqual(info.number_of_ports, 96)
    self.assertEqual(info.serial_number, "M123")


class ControlPointTests(unittest.TestCase):
  RANGES = cmd.ControlPointRanges(0.0, 0.5, 0.0, 1.0)

  def test_hamilton_text(self):
    steps = parse_control_points("pressure, 40, 3; Flow,20,60; idle, 0, 14")
    self.assertEqual(
      steps,
      [PlateStep("pressure", 40, 3), PlateStep("flow", 20, 60), PlateStep("idle", 0, 14)],
    )

  def test_bad_text_refused(self):
    for text in ("pressure 40 3", "pressure, 40", "vacuum, 1, 2", "pressure, -1, 3"):
      with self.assertRaises(ValueError, msg=text):
        parse_control_points(text)

  def test_value_divided_by_ports(self):
    points = to_firmware([PlateStep("pressure", 48, 3), PlateStep("idle", 0, 5)], 96, self.RANGES)
    self.assertAlmostEqual(points[0].value, 0.5)
    self.assertEqual(points[0].type, cmd.ControlPointType.PRESSURE)
    self.assertEqual(points[1].type, cmd.ControlPointType.NONE)

  def test_out_of_range_refused(self):
    with self.assertRaisesRegex(ValueError, "per well"):
      to_firmware(parse_control_points("flow, 96, 10"), 96, self.RANGES)


class DecodingTests(unittest.TestCase):
  def test_decodes_success_as_seen_on_the_mpe2(self):
    """GetLastCommandResults after a good initialize, as firmware V1.9.5.619 answers it."""
    empty = bytes.fromhex("1a000000" * 3 + "16000000" + "1a000000" * 2)
    data = (
      bytes([23, 1, 2, 0, 0, 0])
      + bytes([35, 0, 12, 0])
      + struct.pack("<3I", 0, 1, 2)
      + bytes([30, 0, len(empty), 0])
      + empty
    )
    results = cmd.GetLastCommandResults.parse_response_parameters(data)
    self.assertFalse(results.command_being_processed)
    self.assertEqual(results.traversed_states, [0, 1, 2])
    self.assertEqual(results.results, [])
    self.assertEqual(results.errors, [])

  def test_decodes_f32_readings(self):
    """The input pressure as the MPE2 answers it (116.4 psi)."""
    self.assertAlmostEqual(
      cmd.GetInputPressure.parse_response_parameters(f32(40, 116.38)), 116.38, places=4
    )
    self.assertEqual(cmd.GetInputPressure.action_code, 0)
    self.assertEqual(cmd.GetInputPressure.command_id, 8)
    self.assertEqual(cmd.GetModuleVersion.interface_id, 3)


class RampTests(unittest.TestCase):
  def test_rises_in_even_steps(self):
    steps = ramp(to=30, duration=5)
    self.assertEqual([s.value for s in steps], [6, 12, 18, 24, 30])
    self.assertEqual({s.duration for s in steps}, {1.0})
    self.assertEqual({s.type for s in steps}, {"pressure"})

  def test_from_a_start_with_a_count(self):
    steps = ramp(to=20, duration=6, start=10, steps=4)
    self.assertEqual([s.value for s in steps], [12.5, 15, 17.5, 20])
    self.assertEqual(steps[0].duration, 1.5)

  def test_whole_seconds_by_default(self):
    self.assertEqual([s.duration for s in ramp(to=30, duration=2.5)], [1.25, 1.25])

  def test_goes_to_the_firmware_as_control_points(self):
    steps = ramp(to=30, duration=5) + [PlateStep("pressure", 30, 10)]
    points = to_firmware(steps, 96, cmd.ControlPointRanges(0.0, 1.3, 0.0, 1.0417))
    self.assertEqual(len(points), 6)
    self.assertAlmostEqual(points[0].value, 6 / 96)

  def test_refused(self):
    for kwargs in (
      {"to": 30, "duration": 0},
      {"to": 30, "duration": 1.5},
      {"to": 30, "duration": 2, "steps": 4},
      {"to": -1, "duration": 3},
      {"to": 3, "duration": 3, "type": "idle"},
    ):
      with self.assertRaises(ValueError, msg=str(kwargs)):
        ramp(**kwargs)  # type: ignore[arg-type]


class ErrorTests(unittest.TestCase):
  NAMES = {(1, 1, 0x1000): "SPEControl", (1, 1, 0x0300): "WasteSystem"}

  def test_input_pressure(self):
    entry = HcResultEntry(1, 1, 0x1000, 1, 3, 0x0F10)
    err = errors.error_from_entries("ProcessFilterPlateToWaste", [entry], self.NAMES)
    self.assertIsInstance(err, errors.MPE2InputPressureError)
    self.assertEqual(err.name, "MinimumInputPressureNotProvided")
    self.assertIn("below 80psi", str(err))

  def test_intrusion_and_waste(self):
    err = errors.error_from_entries(
      "Clamp", [HcResultEntry(1, 1, 0x1000, 1, 3, 0x0F0B)], self.NAMES
    )
    self.assertIsInstance(err, errors.MPE2IntrusionError)
    err = errors.error_from_entries("P", [HcResultEntry(1, 1, 0x0300, 1, 3, 0x0F02)], self.NAMES)
    self.assertIsInstance(err, errors.MPE2WasteError)
    self.assertEqual(err.name, "WasteContainerBecameFull")

  def test_the_cause_names_it_not_the_abort(self):
    """As the MPE2 reports it: SPEProcessAborted, then MaximumInputPressureExceeded."""
    entries = [
      HcResultEntry(1, 1, 0x1000, 1, 65535, 0x0F05),
      HcResultEntry(1, 1, 0x1000, 1, 65535, 0x0F0E),
    ]
    err = errors.error_from_entries("ProcessFilterPlateToWaste", entries, self.NAMES)
    self.assertIsInstance(err, errors.MPE2InputPressureError)
    self.assertEqual(err.name, "MaximumInputPressureExceeded")
    self.assertEqual(len(err.entries), 2)

  def test_another_interfaces_0x05_is_a_cause(self):
    # Only ISPEControl's 0x05 is the abort; IWasteSystem's 0x05 is what caused it.
    entries = [
      HcResultEntry(1, 1, 0x1000, 1, 65535, 0x0F05),
      HcResultEntry(1, 1, 0x0300, 1, 65535, 0x0F05),
    ]
    err = errors.error_from_entries("ProcessFilterPlateToWaste", entries, self.NAMES)
    self.assertIsInstance(err, errors.MPE2WasteError)

  def test_unknown_keeps_raw(self):
    err = errors.error_from_entries("X", [HcResultEntry(1, 1, 0x1000, 1, 3, 0x020F)], self.NAMES)
    self.assertIs(type(err), errors.MPE2Error)
    self.assertIn("0x020F", str(err))

  def test_warnings_apart_from_errors(self):
    self.assertEqual(errors.ERRORS[("ISourceContainer", 0x01)][0], "LoadCellFrequencyTooLow")
    self.assertEqual(errors.WARNINGS[("ISourceContainer", 0x01)][0], "SourceContainerNotCalibrated")


class MPE2Tests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.sim = MPE2Simulator()
    self.mpe = MPE2(io=self.sim, timeout=1.0)
    await self.mpe.setup()

  async def asyncTearDown(self):
    await self.mpe.stop()

  def sent(self, kind):
    return [c for c in self.sim.sent if type(c) is kind]

  async def ready(self):
    await self.mpe.initialize(vacuum_duration=0)

  async def test_setup_reads_the_machine(self):
    assert self.mpe.manifold is not None
    self.assertEqual(self.mpe.manifold.number_of_ports, 96)
    self.assertIn("V1.9.5.619", self.mpe.firmware_version or "")
    assert self.mpe.control_point_ranges is not None
    self.assertAlmostEqual(self.mpe.control_point_ranges.max_pressure_per_well, 1.0417)

  async def test_setup_warns_untested(self):
    with self.assertLogs("pylabrobot.hamilton.mpe2.mpe2", "WARNING") as logs:
      await MPE2(io=MPE2Simulator()).setup()
    self.assertTrue(any("untested" in line for line in logs.output))

  async def test_constructor_checks(self):
    client = MPE2().io
    assert isinstance(client, MPE2Client)
    self.assertTrue(client.usb)  # found on USB at setup
    with self.assertRaises(ValueError):
      MPE2(port="COM4", device_id="D30G5GV5")
    with self.assertRaises(ValueError):
      MPE2(io=MPE2Simulator(), device_id="D30G5GV5")
    with self.assertRaises(ValueError):
      MPE2(port="COM4", host="1.2.3.4")
    with self.assertRaises(ValueError):
      MPE2(io=MPE2Simulator(), port="COM4")

  async def test_initialize_runs_vacuum_to_the_waste_in_place(self):
    await self.ready()
    (c,) = self.sent(cmd.InitializeWithParameters)
    self.assertEqual(c.waste_container, cmd.WasteContainer.CONTAINER_1)
    self.assertFalse(c.disable_upper_limit_check)
    self.assertEqual(await self.mpe.request_state(), "ready")
    self.assertTrue(await self.mpe.request_is_initialized())

  async def test_initialize_is_a_no_op_when_ready(self):
    await self.ready()
    n = len(self.sent(cmd.InitializeWithParameters))
    await self.mpe.initialize()
    self.assertEqual(len(self.sent(cmd.InitializeWithParameters)), n)

  async def test_initialize_without_waste(self):
    self.sim.waste[1].present = False
    await self.mpe.initialize()
    self.assertEqual(len(self.sent(cmd.Initialize)), 1)

  async def test_the_manuals_method_order(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    await self.mpe.clamp_filter_plate()
    self.assertEqual(await self.mpe.request_state(), "filter_plate_clamped")
    await self.mpe.process_filter_to_waste_container(
      parse_control_points("pressure,5,5;pressure,15,5"), return_plate=True
    )
    (c,) = self.sent(cmd.ProcessFilterPlateToWaste)
    self.assertAlmostEqual(c.control_points[0].value, 5 / 96)
    self.assertEqual(await self.mpe.request_state(), "filter_plate_placed")
    with self.assertRaises(errors.MPE2StateError):  # the filter plate is clamped first
      await self.mpe.collection_plate_placed(40.0, offset_from_nozzles=2.0)
    await self.mpe.clamp_filter_plate()
    await self.mpe.collection_plate_placed(40.0, offset_from_nozzles=2.0)
    await self.mpe.retrieve_filter_plate()
    await self.mpe.process_filter_to_collection_plate(
      parse_control_points("pressure,15,7;pressure,20,5")
    )
    self.assertEqual(await self.mpe.request_state(), "integration_area_plate_stack")
    with self.assertRaises(errors.MPE2StateError):  # stacked: the filter plate is clamped up first
      await self.mpe.collection_plate_removed()
    await self.mpe.clamp_filter_plate()
    self.assertEqual(await self.mpe.request_state(), "collection_plate_placed")
    await self.mpe.collection_plate_removed()
    self.assertEqual(await self.mpe.request_state(), "filter_plate_clamped")
    await self.mpe.retrieve_filter_plate()
    await self.mpe.filter_plate_removed()
    self.assertEqual(await self.mpe.request_state(), "ready")

  async def test_collection_plate_under_a_clamped_filter_plate(self):
    # The manual's order: the filter plate clamped, then the collection plate placed.
    await self.mpe.initialize()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    await self.mpe.clamp_filter_plate()
    await self.mpe.collection_plate_placed(40.0)
    self.assertEqual(len(self.sent(cmd.CollectionPlatePlaced)), 1)
    self.assertEqual(await self.mpe.request_state(), "collection_plate_placed")
    await self.mpe.retrieve_filter_plate()  # the plate stack out, to pipette into
    self.assertEqual(await self.mpe.request_state(), "integration_area_plate_stack")
    await self.mpe.process_filter_to_collection_plate(parse_control_points("pressure,10,3"))
    self.assertEqual(await self.mpe.request_state(), "integration_area_plate_stack")

  async def test_a_failed_recovery_holds(self):
    # A pressure error recovers by itself, but not when that recovery failed.
    await self.ready()
    entry = HcResultEntry(1, 1, 0x0300, 1, 3, 0x0F02)
    error = errors.error_from_entries("P", [entry], {(1, 1, 0x0300): "WasteSystem"})

    async def failing_recover(retrieve_plate=True):
      return mpe2_module.RecoveryReport(failed_steps=["filter plate retrieved: no answer"])

    self.mpe.recover = failing_recover  # type: ignore[method-assign]
    await self.mpe._after_failure(error)
    self.assertIs(self.mpe.held_after, error)
    with self.assertRaises(errors.MPE2StateError):
      await self.mpe.filter_plate_placed(45.0, 55.0)

  async def test_a_link_timeout_during_a_step_holds(self):
    # The link going silent while a clamp runs halts and holds.
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    real_query = self.mpe._query
    failed: list = []

    async def silent_once(command_class, **kwargs):
      if command_class is cmd.GetLastCommandResults and not failed:
        failed.append(True)
        raise errors.MPE2TimeoutError("no answer")
      return await real_query(command_class, **kwargs)

    self.mpe._query = silent_once  # type: ignore[method-assign]
    with self.assertRaises(errors.MPE2TimeoutError) as ctx:
      await self.mpe.clamp_filter_plate()
    self.assertIs(self.mpe.held_after, ctx.exception)
    self.assertIsNotNone(ctx.exception.recovery)

  async def test_an_interrupted_step_holds(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    real_query = self.mpe._query

    async def hangs(command_class, **kwargs):
      if command_class is cmd.GetLastCommandResults:
        await asyncio.sleep(10)
      return await real_query(command_class, **kwargs)

    self.mpe._query = hangs  # type: ignore[method-assign]
    task = asyncio.create_task(self.mpe.clamp_filter_plate())
    await asyncio.sleep(0.05)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIsNotNone(self.mpe.held_after)
    self.mpe._query = real_query  # type: ignore[method-assign]

  async def test_idempotent_steps_send_nothing(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    await self.mpe.clamp_filter_plate()
    before = (
      len(self.sent(cmd.FilterPlatePlaced)),
      len(self.sent(cmd.ClampFilterPlate)),
    )
    await self.mpe.filter_plate_placed(45.0, 55.0)
    await self.mpe.clamp_filter_plate()
    self.assertEqual(
      (len(self.sent(cmd.FilterPlatePlaced)), len(self.sent(cmd.ClampFilterPlate))), before
    )
    await self.mpe.retrieve_filter_plate()
    await self.mpe.retrieve_filter_plate()
    self.assertEqual(len(self.sent(cmd.RetrieveFilterPlate)), 1)
    await self.mpe.collection_plate_removed()
    self.assertEqual(self.sent(cmd.CollectionPlateRemoved), [])
    await self.mpe.filter_plate_removed()
    await self.mpe.filter_plate_removed()
    self.assertEqual(len(self.sent(cmd.FilterPlateRemoved)), 1)
    self.assertTrue(await self.mpe.abort())  # Nothing running: taken, nothing done.

  async def test_another_filter_plate_refused(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    with self.assertRaises(errors.MPE2StateError):
      await self.mpe.filter_plate_placed(30.0, 40.0)

  async def test_refused_before_sending(self):
    await self.ready()
    with self.assertRaises(ValueError):
      await self.mpe.filter_plate_placed(55.0, 45.0)  # nozzles above the seat
    with self.assertRaises(ValueError):
      await self.mpe.filter_plate_placed(120.0, 130.0)  # above the 114 mm stack
    await self.mpe.filter_plate_placed(80.0, 90.0)
    n = len(self.sim.sent)
    with self.assertRaises(ValueError):
      await self.mpe.collection_plate_placed(40.0)  # 80 + 40 > 114
    with self.assertRaises(ValueError):
      await self.mpe.process_filter_to_waste_container(
        parse_control_points("flow, 500, 10")
      )  # 5.2 per well, above 1.3
    with self.assertRaises(ValueError):
      await self.mpe.process_filter_to_waste_container(
        parse_control_points("pressure,5,5"),
        waste_container=3,  # type: ignore[arg-type]
      )
    self.assertEqual([c for c in self.sim.sent[n:] if not type(c).__name__.startswith("Get")], [])

  async def test_wrong_state_refused_by_the_firmware(self):
    """A command sent in the wrong state comes back as the firmware's refusal."""
    with self.assertRaises(errors.MPE2StateError) as ctx:
      await self.mpe._command(cmd.ClampFilterPlate)
    self.assertEqual(ctx.exception.name, "InvalidCommandForState")

  async def test_gas_dropping_in_the_firmware_recovers(self):
    """The firmware's own low gas error (0x10): the MPE2 loses its initialization with the plate
    clamped, so recovery can't bring it back and says so."""
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    original = self.mpe.check_input_pressure_psi

    async def drops():
      pressure = await original()
      self.sim.input_pressure_psi = 70.0  # It drops between the check and the process step.
      return pressure

    self.mpe.check_input_pressure_psi = drops  # type: ignore[method-assign]
    with self.assertRaises(errors.MPE2InputPressureError) as ctx:
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    report = ctx.exception.recovery
    assert report is not None
    self.assertNotIn("filter plate retrieved to the integration area", report.steps)
    self.assertEqual(report.final_state, "uninitialized")
    self.assertTrue(any("lost its initialization" in s for s in report.check_by_hand))
    self.assertTrue(any("smart=False" in s for s in report.check_by_hand))

  async def test_intrusion_during_processing(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.inject_error("ProcessFilterPlateToWaste", "ISPEControl", 0x0B)
    with self.assertRaises(errors.MPE2IntrusionError) as ctx:
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    report = ctx.exception.recovery
    assert report is not None
    # Something was in the way: the plate is not driven back without someone looking.
    self.assertEqual(self.sent(cmd.RetrieveFilterPlate), [])
    self.assertEqual(report.final_state, "filter_plate_clamped")
    self.assertTrue(any("retrieve_filter_plate()" in s for s in report.check_by_hand))

  async def test_full_waste_refused(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.waste[1].full = True
    with self.assertRaises(errors.MPE2WasteError):
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertEqual(self.sent(cmd.ProcessFilterPlateToWaste), [])

  async def test_recover_when_all_is_well_does_nothing(self):
    await self.ready()
    report = await self.mpe.recover()
    self.assertEqual(report.steps, [])
    self.assertEqual(report.final_state, "ready")
    self.assertEqual(report.check_by_hand, [])

  async def test_a_hung_query_times_out_then_reconnects(self):
    self.sim.hang_on = cmd.GetInputPressure
    with self.assertRaises(errors.MPE2TimeoutError):
      await self.mpe.request_input_pressure_psi()
    self.sim.hang_on = None
    setups = self.sim.setups
    self.assertAlmostEqual(await self.mpe.request_input_pressure_psi(), 116.4)
    self.assertEqual(self.sim.setups, setups + 1)

  async def test_pressures(self):
    readings = await self.mpe.request_pressures()
    self.assertAlmostEqual(readings.input_psi, 116.4)
    self.assertLess(readings.waste_vacuum_psi, 0)
    self.assertAlmostEqual(await self.mpe.check_input_pressure_psi(), 116.4)

  async def test_status(self):
    status = await self.mpe.request_status()
    self.assertEqual(status.state, "uninitialized")
    self.assertTrue(status.waste[1].present)
    self.assertFalse(status.waste[2].present)
    self.assertFalse(status.vacuum_on)

  async def test_vacuum(self):
    await self.mpe.vacuum.start()
    self.assertEqual(len(self.sent(cmd.StartVacuum)), 1)
    self.assertTrue(await self.mpe.vacuum.request_is_on())
    await self.mpe.vacuum.start()  # already on, same container: nothing sent
    self.assertEqual(len(self.sent(cmd.StartVacuum)), 1)
    await self.mpe.vacuum.stop()
    self.assertFalse(await self.mpe.vacuum.request_is_on())

  async def test_stop_is_always_sent(self):
    """A stop must not wait for an answer about the pump first."""
    await self.mpe.vacuum.stop()
    await self.mpe.vacuum.stop()
    self.assertEqual(len(self.sent(cmd.StopVacuum)), 2)

  async def test_run_for_stops_the_pump(self):
    await self.mpe.vacuum.run_for(0.2)
    self.assertFalse(self.sim.vacuum_on)
    task = asyncio.ensure_future(self.mpe.vacuum.run_for(30))
    await asyncio.sleep(0.2)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertFalse(self.sim.vacuum_on)

  async def test_vacuum_not_stable_before_timeout(self):
    """A stabilize timeout too short faults, the pump runs on, and the waste system answers
    GetVacuumState with the fault from then on."""
    self.sim.vacuum_stable_after = 1.0
    await self.mpe.vacuum.start(stabilize_timeout=0.1)
    await asyncio.sleep(0.2)
    with self.assertRaises(errors.MPE2VacuumError) as ctx:
      await self.mpe._query(cmd.GetVacuumState)
    self.assertEqual(ctx.exception.name, "VacuumNotStableBeforeTimeout")
    self.assertTrue(self.sim.vacuum_on)  # The pump goes on.
    self.assertTrue(await self.mpe.vacuum.request_is_on())  # Asked of the vacuum object instead.
    report = await self.mpe.recover(retrieve_plate=False)  # Recovery stops it.
    self.assertIn("vacuum stopped", report.steps)
    self.assertFalse(self.sim.vacuum_on)

  async def test_vacuum_refused_without_waste(self):
    with self.assertRaises(errors.MPE2WasteError):
      await self.mpe.vacuum.start(waste_container=2)
    with self.assertRaises(ValueError):
      await self.mpe.vacuum.start(stabilize_timeout=0)
    with self.assertRaises(ValueError):
      await self.mpe.vacuum.run_for(0)

  async def test_stop_restores_the_vacuum(self):
    await self.mpe.vacuum.start()
    await self.mpe.stop()
    self.assertFalse(self.sim.vacuum_on)

  async def test_interrupted_exchange_reconnects(self):
    """A cancelled exchange (an interrupted cell) leaves the link uncertain; the next query
    reconnects instead of failing."""
    self.sim.hang_on = cmd.GetInputPressure
    task = asyncio.ensure_future(self.mpe.request_input_pressure_psi())
    await asyncio.sleep(0.1)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.sim.hang_on = None
    setups = self.sim.setups
    await self.mpe.request_input_pressure_psi()
    self.assertEqual(self.sim.setups, setups + 1)

  async def test_a_stop_is_sent_again_over_a_new_link(self):
    """A broken link during a stop: reconnect and send it again."""
    await self.mpe.vacuum.start()
    self.sim.fail_link_on = cmd.StopVacuum
    original = self.sim.execute

    async def fail_once(command, *, read_timeout=None):
      if isinstance(command, cmd.StopVacuum) and self.sim.fail_link_on is not None:
        self.sim.fail_link_on = None
        raise ConnectionError("cut")
      return await original(command, read_timeout=read_timeout)

    self.sim.execute = fail_once  # type: ignore[method-assign]
    await self.mpe.vacuum.stop()
    self.assertFalse(self.sim.vacuum_on)

  async def test_stop_stops_a_pump_running_at_setup(self):
    """A pump found running at setup is a crashed session's: stop() stops it."""
    sim = MPE2Simulator()
    sim.vacuum_on = True
    mpe = MPE2(io=sim)
    await mpe.setup()
    await mpe.stop()
    self.assertFalse(sim.vacuum_on)

  async def test_setup_survives_a_faulted_waste_system(self):
    """An unnamed fault on the vacuum read doesn't stop setup."""
    sim = MPE2Simulator()
    sim.vacuum_fault = 0x07
    sim.vacuum_on = True
    mpe = MPE2(io=sim)
    await mpe.setup()
    self.assertTrue(await mpe.vacuum.request_is_on())
    await mpe.stop()
    self.assertFalse(sim.vacuum_on)

  async def test_failed_initialize_stops_the_vacuum(self):
    """A failed initialize stops the vacuum it may have started."""
    self.sim.inject_error("InitializeWithParameters", "IVacuum", 0x05)
    self.sim.vacuum_on = True  # as the initialize's vacuum run leaves it
    with self.assertRaises(errors.MPE2VacuumError):
      await self.mpe.initialize()
    self.assertFalse(self.sim.vacuum_on)

  async def test_failed_start_stops_the_pump(self):
    """A start that went out and then failed leaves no pump running."""
    self.sim.hang_on = cmd.StartVacuum
    original = self.sim.execute

    async def start_then_hang(command, *, read_timeout=None):
      if isinstance(command, cmd.StartVacuum):
        self.sim.vacuum_on = True
      return await original(command, read_timeout=read_timeout)

    self.sim.execute = start_then_hang  # type: ignore[method-assign]
    with self.assertRaises(errors.MPE2TimeoutError):
      await self.mpe.vacuum.start()
    self.sim.hang_on = None
    self.assertFalse(self.sim.vacuum_on)

  async def test_no_recovery_still_stops_the_vacuum(self):
    """Without recovery, a failed step still stops the vacuum."""
    self.mpe.recover_on_error = False
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.inject_error("ProcessFilterPlateToWaste", "ISPEControl", 0x0B)
    self.sim.vacuum_on = True
    with self.assertRaises(errors.MPE2IntrusionError):
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertFalse(self.sim.vacuum_on)

  async def test_a_plate_step_timeout_recovers_without_moving(self):
    """A timed-out plate step is aborted and recovered, the plate left where it is."""
    await self.ready()
    self.sim.busy_polls = 10_000
    original = mpe2_module.PLATE_STEP_TIMEOUT
    mpe2_module.PLATE_STEP_TIMEOUT = 0.5
    try:
      with self.assertRaises(errors.MPE2TimeoutError) as ctx:
        await self.mpe._run(
          cmd.FilterPlatePlaced, timeout=0.5, filter_height=45.0, nozzle_height=55.0
        )
    finally:
      mpe2_module.PLATE_STEP_TIMEOUT = original
      self.sim.busy_polls = 2
    self.assertIsNotNone(ctx.exception.recovery)
    self.assertEqual(self.sent(cmd.RetrieveFilterPlate), [])

  async def test_low_gas_only_warns(self):
    """No limit set: low gas warns, and the firmware's own abort
    below 80 psi is a pressure error: recovery runs by itself, and doesn't hold."""
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.input_pressure_psi = 62.0
    with self.assertLogs("pylabrobot.hamilton.mpe2.mpe2", "WARNING") as logs:
      with self.assertRaises(errors.MPE2InputPressureError) as ctx:
        await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertTrue(any("62.0 psi" in line for line in logs.output))
    self.assertEqual(len(self.sent(cmd.ProcessFilterPlateToWaste)), 1)  # Not refused by the driver.
    report = ctx.exception.recovery
    assert report is not None
    self.assertEqual(report.final_state, "uninitialized")
    self.assertIsNone(self.mpe.held_after)

  async def test_a_set_limit_refuses(self):
    self.mpe.minimum_input_pressure_psi = 85.0
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.input_pressure_psi = 82.0
    with self.assertRaises(errors.MPE2InputPressureError):
      await self.mpe.clamp_filter_plate()
    with self.assertRaises(errors.MPE2InputPressureError):
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertEqual(self.sent(cmd.ClampFilterPlate), [])
    self.assertEqual(self.sent(cmd.ProcessFilterPlateToWaste), [])

  async def test_an_intrusion_holds_until_recover(self):
    """An intrusion stops everything, leaves the plate clamped, and refuses anything that moves
    until recover()."""
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.inject_error("ProcessFilterPlateToWaste", "ISPEControl", 0x0B)
    self.sim.vacuum_on = True
    with self.assertRaises(errors.MPE2IntrusionError):
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertFalse(self.sim.vacuum_on)  # Everything that runs is stopped.
    self.assertEqual(await self.mpe.request_state(), "filter_plate_clamped")
    self.assertIsNotNone(self.mpe.held_after)
    with self.assertRaises(errors.MPE2StateError):
      await self.mpe.retrieve_filter_plate()
    with self.assertRaises(errors.MPE2StateError):
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertEqual(self.sent(cmd.RetrieveFilterPlate), [])
    report = await self.mpe.recover()
    self.assertIn("filter plate retrieved to the integration area", report.steps)
    self.assertIsNone(self.mpe.held_after)
    self.assertEqual(await self.mpe.request_state(), "filter_plate_placed")

  async def test_a_motor_error_holds(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.inject_error("ClampFilterPlate", "ISPEControl", 0x04)  # MotorControlUnitNotAvailable
    with self.assertRaises(errors.MPE2MotorError):
      await self.mpe.clamp_filter_plate()
    self.assertIsNotNone(self.mpe.held_after)
    with self.assertRaises(errors.MPE2StateError):
      await self.mpe.clamp_filter_plate()

  async def test_without_auto_recovery_a_pressure_error_still_halts(self):
    self.mpe.recover_on_error = False
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.input_pressure_psi = 70.0
    self.sim.vacuum_on = True
    with self.assertRaises(errors.MPE2InputPressureError) as ctx:
      await self.mpe.process_filter_to_waste_container(parse_control_points("pressure,5,5"))
    self.assertIsNotNone(ctx.exception.recovery)
    self.assertFalse(self.sim.vacuum_on)
    self.assertEqual(self.sent(cmd.RetrieveFilterPlate), [])

  async def test_stop_halts_everything(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    self.sim.vacuum_on = True
    await self.mpe.stop()
    self.assertFalse(self.sim.vacuum_on)
    self.assertEqual(self.sent(cmd.RetrieveFilterPlate), [])  # Nothing moves.
    await self.mpe.setup()  # asyncTearDown stops it again

  async def test_record_pressures_during_a_step(self):
    await self.ready()
    await self.mpe.filter_plate_placed(45.0, 55.0)
    async with self.mpe.record_pressures() as recording:
      await self.mpe.process_filter_to_waste_container(ramp(to=30, duration=3))
    self.assertGreater(len(recording.samples), 0)
    low = recording.lowest_input
    assert low is not None
    self.assertAlmostEqual(low.input_psi, 116.4)
    self.assertIn("readings over", str(recording))
    self.assertFalse(self.sim.connected is False)

  async def test_needs_setup(self):
    with self.assertRaises(RuntimeError):
      await MPE2(io=MPE2Simulator()).clamp_filter_plate()


class SetupLookupTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_timed_out_lookup_reconnects_and_setup_goes_on(self):
    # a lookup that times out leaves its exchange open; the next one reconnects first.
    class SlowVacuum(MPE2Simulator):
      async def resolve_path(self, path):
        if path == cmd.VACUUM_PATH and self.setups == 1:
          await asyncio.sleep(10)
        return await super().resolve_path(path)

    sim = SlowVacuum()
    mpe = MPE2(io=sim, timeout=0.05)
    await mpe.setup()
    try:
      self.assertEqual(sim.setups, 2)
      self.assertFalse(mpe._uncertain)
      self.assertEqual(await mpe.request_state(), "uninitialized")
    finally:
      await mpe.stop()


def _serial_link(mpe: MPE2):
  """The serial link an MPE2 over USB would open."""
  from typing import cast

  from pylabrobot.hamilton.transport.rs232_ppp import HamiltonSerialLink

  return cast(HamiltonSerialLink, mpe.io._create_session()._io)  # type: ignore[attr-defined]


@unittest.skipUnless(HAS_SERIAL, "pyserial is not installed")
class FindByUSBTests(unittest.IsolatedAsyncioTestCase):
  """An MPE2 found on USB by its FTDI chip at setup, through pylabrobot.io."""

  def test_made_without_searching_found_at_setup(self):
    with unittest.mock.patch("serial.tools.list_ports.comports", return_value=[]) as comports:
      MPE2()  # nothing plugged in: no search yet, so no error
      MPE2(device_id="D30G5GV5")
    comports.assert_not_called()

  def test_the_link_is_given_the_usb_ids_and_serial_number(self):
    from pylabrobot.hamilton.mpe2.connection import USB_PID, USB_VID

    link = _serial_link(MPE2(device_id="D30G5GV5"))
    self.assertEqual(
      (link.io._vid, link.io._pid, link.io.serial_number), (USB_VID, USB_PID, "D30G5GV5")
    )
    link = _serial_link(MPE2(port="COM4"))
    self.assertEqual((link.io._port, link.io._vid, link.io.serial_number), ("COM4", None, None))

  async def test_by_serial_number_with_the_windows_letter(self):
    from types import SimpleNamespace

    ports = [
      SimpleNamespace(device="COM3", hwid="USB VID:PID=0403:6015", serial_number="AAAA1111A"),
      SimpleNamespace(device="COM4", hwid="USB VID:PID=0403:6015", serial_number="D30G5GV5A"),
    ]
    link = _serial_link(MPE2(device_id="D30G5GV5"))
    with (
      unittest.mock.patch("serial.tools.list_ports.comports", return_value=ports),
      unittest.mock.patch("serial.Serial") as port,
    ):
      await link.io.setup()
      self.assertEqual(link.port, "COM4")
      self.assertEqual(port.return_value.port, "COM4")
      await link.io.stop()


class NonFiniteControlPointTests(unittest.TestCase):
  def test_nan_and_inf_are_refused(self):
    for value, duration in ((10, float("nan")), (float("nan"), 2), (10, float("inf"))):
      with self.assertRaises(ValueError):
        PlateStep("pressure", value, duration)
    with self.assertRaises(ValueError):
      ramp(to=30, duration=float("nan"))


class ResourceTests(unittest.IsolatedAsyncioTestCase):
  """The MPE2 as PLR's other devices: a name, plate holders, typed control points."""

  def test_name_and_plate_holders(self):
    mpe = MPE2("left_mpe2", io=MPE2Simulator())
    self.assertEqual(mpe.filter_plate_holder.name, "left_mpe2_filter_plate_holder")
    self.assertEqual(mpe.collection_plate_holder.name, "left_mpe2_collection_plate_holder")
    self.assertEqual(
      (mpe.filter_plate_holder.get_size_x(), mpe.filter_plate_holder.get_size_y()),
      (127.76, 85.48),
    )

  async def test_control_points_are_plate_steps(self):
    mpe = MPE2(io=MPE2Simulator(initialized=True))
    await mpe.setup()
    try:
      await mpe.filter_plate_placed(height=14.4, nozzle_height=14.4)
      with self.assertRaises(TypeError):
        await mpe.process_filter_to_waste_container("pressure,5,5")  # type: ignore[arg-type]
      with self.assertRaises(TypeError):
        parse_control_points([PlateStep("pressure", 5, 5)])  # type: ignore[arg-type]
      self.assertEqual(
        parse_control_points("pressure,5,5; flow,20,3"),
        [PlateStep("pressure", 5, 5), PlateStep("flow", 20, 3)],
      )
      self.assertEqual(await mpe.request_state(), "filter_plate_placed")
    finally:
      await mpe.stop()


class FirmwareBehaviourTests(unittest.IsolatedAsyncioTestCase):
  """The simulator as the MPE2's firmware behaves, and the driver with it."""

  async def asyncSetUp(self):
    self.sim = MPE2Simulator(initialized=True)
    self.mpe = MPE2(io=self.sim, timeout=1.0)
    await self.mpe.setup()

  async def asyncTearDown(self):
    await self.mpe.stop()

  async def test_a_gas_abort_loses_the_initialization_and_the_full_one_frees_the_plate(self):
    await self.mpe.filter_plate_placed(height=14.4, nozzle_height=14.4)
    self.sim.input_pressure_psi = 70.0
    with self.assertRaises(errors.MPE2InputPressureError):
      await self.mpe.process_filter_to_waste_container([PlateStep("pressure", 30, 2)])
    self.sim.input_pressure_psi = 110.0
    self.assertEqual(await self.mpe.request_state(), "uninitialized")
    with self.assertRaisesRegex(errors.MPE2MotorError, "CollectionPlateDetected"):
      await self.mpe.initialize(smart=True)
    await self.mpe.recover()  # ends the hold the motor error set; nothing to retrieve
    with self.assertRaisesRegex(errors.MPE2MotorError, "FilterPlateDetected"):
      await self.mpe.initialize(smart=False)  # brings the plate out, then sees it
    self.assertEqual(self.sim.plate_left, "on_shuttle")
    await self.mpe.recover()
    self.sim.take_plates_off()
    await self.mpe.initialize(smart=False)
    self.assertEqual(await self.mpe.request_state(), "ready")

  async def test_is_initialized_only_when_ready(self):
    self.assertTrue(await self.mpe.request_is_initialized())
    await self.mpe.filter_plate_placed(height=14.4, nozzle_height=14.4)
    self.assertFalse(await self.mpe.request_is_initialized())

  async def test_a_vacuum_stop_that_fails_is_said(self):
    await self.mpe.vacuum.start(1)
    self.sim.fail_stop_vacuum = True
    with self.assertRaises(errors.MPE2VacuumError):
      await self.mpe.vacuum.stop()
    self.sim.fail_stop_vacuum = False
    await self.mpe.vacuum.stop()

  async def test_collection_plate_in_the_process_area_is_brought_out_first(self):
    await self.mpe.filter_plate_placed(height=14.4, nozzle_height=14.4)
    await self.mpe.clamp_filter_plate()
    await self.mpe.collection_plate_placed(14.4)
    await self.mpe.retrieve_filter_plate()
    await self.mpe.process_filter_to_collection_plate(
      [PlateStep("pressure", 10, 1)], return_plate=False
    )
    self.assertEqual(await self.mpe.request_state(), "collection_plate_in_process_area")
    with self.assertRaisesRegex(errors.MPE2StateError, "process area"):
      await self.mpe.collection_plate_removed()

  async def test_one_reconnection_at_a_time(self):
    setups = self.sim.setups
    self.mpe._uncertain = True
    await asyncio.gather(self.mpe._reconnect(), self.mpe._reconnect())
    self.assertEqual(self.sim.setups, setups + 1)


class HoldTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.sim = MPE2Simulator(initialized=True)
    self.mpe = MPE2(io=self.sim, timeout=1.0)
    await self.mpe.setup()

  async def asyncTearDown(self):
    await self.mpe.stop()

  async def test_a_reconnection_while_a_step_runs_holds(self):
    await self.mpe.filter_plate_placed(height=14.4, nozzle_height=14.4)
    self.sim.busy_polls = 5
    original = self.mpe._query

    async def reconnecting_query(command_class, **kwargs):
      if command_class is cmd.GetLastCommandResults:
        self.mpe._reconnections += 1  # as if the pressure recording had reconnected meanwhile
      return await original(command_class, **kwargs)

    self.mpe._query = reconnecting_query  # type: ignore[method-assign]
    with self.assertRaises(errors.MPE2ConnectionError):
      await self.mpe.clamp_filter_plate()
    self.mpe._query = original  # type: ignore[method-assign]
    self.assertIsNotNone(self.mpe.held_after)

  async def test_retrieve_when_uninitialized_refuses(self):
    self.sim.state = cmd.SPEControlState.UNINITIALIZED
    with self.assertRaisesRegex(errors.MPE2StateError, "smart=False"):
      await self.mpe.retrieve_filter_plate()


if __name__ == "__main__":
  unittest.main()
