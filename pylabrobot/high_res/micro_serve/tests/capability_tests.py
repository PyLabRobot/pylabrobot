"""Diagnostic captures and simulated measurement, manual-mode, and recovery exchanges."""

import asyncio
import unittest
from pathlib import Path

from pylabrobot.high_res.micro_serve import (
  HighResMicroServe,
  MicroServeError,
  MicroServePlateDimensions,
  MicroServeProtocolError,
  MicroServeUnresolvedPreparation,
)
from pylabrobot.high_res.micro_serve.tests.micro_serve_tests import (
  DIMENSIONS,
  GEOMETRY,
  FakeSocket,
  status,
)
from pylabrobot.io.capture import CaptureReader
from pylabrobot.io.socket import SocketValidator


class MicroServeCapabilityTests(unittest.IsolatedAsyncioTestCase):
  """Verify the public operations through a serialized simulated transport."""

  async def asyncSetUp(self) -> None:
    """Connect without opening a hardware socket."""
    self.driver = HighResMicroServe("192.0.2.1")
    self.io = FakeSocket()
    self.driver.io = self.io  # type: ignore[assignment]
    await self.driver.setup()

  async def test_native_diagnostics_retain_device_units_and_implausible_limits(self) -> None:
    """Reports captured from firmware 2.7.0.756 must not become guessed travel limits."""
    limits = "Spatula -2030043136,  Effectuator 0,  Barcode 1082617856,  Carousel 0, 1080688640"
    variables = (
      "OK! status homed, stackerNumber 2, machineState NotBusy, loaderStatus Extended, mode Ready"
    )
    self.io.add("limits", limits)
    self.io.add("varstatus", variables)
    self.io.add("getangle", "0.0000")
    self.io.add("queryhomeoffset 1", "Spatula: 0.517")
    self.io.add("help estoprecover", "estoprecover   - Recover from an EStop.")
    self.io.add("info all", "clearabort, ca - Clear abort state.")
    self.assertEqual(await self.driver.request_limits(), limits)
    self.assertEqual(await self.driver.request_variable_status(), variables)
    self.assertEqual(await self.driver.request_plate_angle(), "0.0000")
    self.assertEqual(await self.driver.request_home_offset(1), "Spatula: 0.517")
    self.assertIn("EStop", (await self.driver.request_command_help("estoprecover"))[0])
    self.assertIn("clearabort", (await self.driver.request_command_catalog())[0])

  async def test_invalid_arguments_never_reach_transport(self) -> None:
    """Reject command injection, ambiguous booleans, and invalid calibration counts."""
    for name in ("home\nmanual", "home 0", "", "é"):
      with self.subTest(name=name), self.assertRaises(ValueError):
        await self.driver.request_command_help(name)
    for value in (-1, 4, True, 0.5):
      with self.subTest(address=value), self.assertRaises(ValueError):
        await self.driver.request_home_offset(value)  # type: ignore[arg-type]
    for value in (-1, True, 0.5):
      with self.subTest(count=value), self.assertRaises(ValueError):
        await self.driver.stackers[0].set_plate_count(value)  # type: ignore[arg-type]
    for value in (0, 1, 2, True):
      with self.subTest(calibration_count=value), self.assertRaises(ValueError):
        await self.driver.calculate_plate_dimensions(value)
    with self.assertRaises(ValueError):
      await self.driver.set_barcode_laser(1)  # type: ignore[arg-type]
    self.assertEqual(self.io.writes, [])

  async def test_cached_count_setter_verifies_and_skips_repeated_target(self) -> None:
    """A bookkeeping correction is acknowledged and read back, including zero."""
    self.io.add("status", status())
    self.io.add("getplatecounts 2", "2: 3")
    self.io.add("setplatecount 2 0")
    self.io.add("getplatecounts 2", "2: 0")
    self.io.add("status", status())
    self.io.add("getplatecounts 2", "2: 0")
    await self.driver.stackers[2].set_plate_count(0)
    await self.driver.stackers[2].set_plate_count(0)
    self.assertEqual(self.io.writes.count(b"setplatecount 2 0\n"), 1)

  async def test_rejected_cached_count_is_not_reported_as_success(self) -> None:
    """An OK envelope does not replace setting readback."""
    self.io.add("status", status())
    self.io.add("getplatecounts 0", "0: 0")
    self.io.add("setplatecount 0 2")
    self.io.add("getplatecounts 0", "0: 0")
    with self.assertRaises(MicroServeProtocolError):
      await self.driver.stackers[0].set_plate_count(2)

  async def test_counting_applies_geometry_and_returns_refreshed_count(self) -> None:
    """An active count must run before reading the count cache, under one lock."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 2 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("countplates 2")
    self.io.add("status", status(stacker=2))
    self.io.add("getplatecounts 2", "2: 3")
    self.io.add("getangle", "0.0000")
    result, _ = await asyncio.gather(
      self.driver.stackers[2].count_plates(GEOMETRY), self.driver.request_plate_angle()
    )
    self.assertEqual(result, 3)
    self.assertEqual(self.io.writes[-1], b"getangle\n")

  async def test_height_measurement_converts_microns_and_retains_negative_offset(self) -> None:
    """The operation-notes calibration workflow needs small negative empty readings."""
    for reply, expected in (("25000", 25), ("-125.5", -0.1255)):
      with self.subTest(reply=reply):
        self.io.add("status", status())
        self.io.add("measurestacker 2", reply)
        self.io.add("status", status(stacker=2))
        self.assertEqual(await self.driver.stackers[2].measure_height(), expected)
    self.assertFalse(any(b"dimensions" in command for command in self.io.writes))

  async def test_height_measurement_rejects_missing_or_non_numeric_results(self) -> None:
    """Do not mistake diagnostics, nonfinite values, or multiple readings for a height."""
    for lines in ((), ("nan",), ("100", "200"), ("Error 1",)):
      with self.subTest(lines=lines):
        self.io.add("status", status())
        self.io.add("measurestacker 0", *lines)
        self.io.add("status", status())
        with self.assertRaises(MicroServeProtocolError):
          await self.driver.stackers[0].measure_height()

  async def test_measurements_refuse_unsafe_initial_states(self) -> None:
    """No measurement starts while unhomed, busy, extended, or beam-blocked."""
    for initial in (
      status(homed=False),
      status().replace("NotBusy", "Busy"),
      status(extended=True),
      status(plate=True),
    ):
      with self.subTest(initial=initial):
        self.io.add("status", initial)
        with self.assertRaises(RuntimeError):
          await self.driver.stackers[0].measure_height()
    self.assertEqual(self.io.writes, [b"status\n"] * 4)

  async def test_interrupted_measurement_survives_reconnect_until_inspected_retraction(
    self,
  ) -> None:
    """An uncertain measurement must not permit a later carousel move after reconnect."""
    self.io.add("status", status())
    self.io.scripts.append(
      ("measurestacker 2", [b"ACK! measurestacker 2 50\n", TimeoutError("lost reply")])
    )
    with self.assertRaises(TimeoutError):
      await self.driver.stackers[2].measure_height()
    await self.driver.setup()
    self.assertEqual(self.driver.unresolved_operation, "measurestacker 2")
    with self.assertRaisesRegex(RuntimeError, "unresolved"):
      await self.driver.stackers[3].move_to()
    with self.assertRaisesRegex(RuntimeError, "unresolved"):
      await self.driver.home()
    self.io.add("status", status(stacker=2))
    await self.driver.retract()
    self.assertIsNone(self.driver.unresolved_operation)
    self.assertEqual(self.io.writes.count(b"measurestacker 2\n"), 1)

  async def test_scan_failure_and_non_idle_final_status_remain_unresolved(self) -> None:
    """A completion failure or busy final state cannot be accepted as a completed scan."""
    for completion in ("ERROR", "OK"):
      with self.subTest(completion=completion):
        self.io.add("status", status())
        self.io.add("setstackerdimensions 0 11000 10000 10000")
        self.io.add("dimstatus", *DIMENSIONS)
        self.io.add("readbarcodestacker 0", completion=completion)
        if completion == "OK":
          self.io.add("status", status().replace("NotBusy", "Busy"))
        with self.assertRaises((MicroServeError, RuntimeError)):
          await self.driver.stackers[0].scan_barcodes(GEOMETRY)
        self.assertEqual(self.driver.unresolved_operation, "readbarcodestacker 0")
        self.io.add("status", status())
        await self.driver.retract()

  async def test_dimension_calculation_uses_documented_pdf_example(self) -> None:
    """Parse the API revision 756 example into PLR millimeters."""
    self.io.add("status", status())
    self.io.add(
      "calculateplatedimensions 3",
      "SPATULA_BEAM_BREAK_HEIGHT:        600",
      "Plate Height:     60000",
      "Plate Thickness: 50000",
      "Stack Height:     55000",
    )
    self.io.add("status", status(stacker=4))
    self.assertEqual(
      await self.driver.calculate_plate_dimensions(3), MicroServePlateDimensions(60, 55, 50)
    )

  async def test_manual_mode_is_verified_and_repeated_access_does_not_move(self) -> None:
    """Manual access requires a retracted loader and leaves homing explicit."""
    manual = status().replace("mode Ready", "mode Manual")
    self.io.add("status", status())
    self.io.add("manual")
    self.io.add("status", manual)
    self.io.add("status", manual)
    await self.driver.enter_manual_mode()
    await self.driver.enter_manual_mode()
    self.assertEqual(self.io.writes.count(b"manual\n"), 1)
    self.assertNotIn(b"home\n", self.io.writes)
    self.io.add("status", status(extended=True))
    with self.assertRaisesRegex(RuntimeError, "retract"):
      await self.driver.enter_manual_mode()

  async def test_manual_mode_rejects_incorrect_final_state(self) -> None:
    """The firmware ACK cannot establish that the carousel is released."""
    self.io.add("status", status())
    self.io.add("manual")
    self.io.add("status", status())
    with self.assertRaisesRegex(RuntimeError, "not confirmed"):
      await self.driver.enter_manual_mode()

  async def test_stale_ready_mode_requires_owned_manual_command(self) -> None:
    """Unhomed and unselected status alone does not prove the carousel was released."""
    manual = status(homed=False, stacker=-1)
    self.io.add("status", manual)
    self.io.add("manual")
    self.io.add("status", manual)
    self.io.add("status", manual)
    await self.driver.enter_manual_mode()
    await self.driver.enter_manual_mode()
    self.assertEqual(self.io.writes.count(b"manual\n"), 1)
    await self.driver.stop()
    await self.driver.setup()
    self.io.add("status", manual)
    self.io.add("manual")
    self.io.add("status", manual)
    await self.driver.enter_manual_mode()
    self.assertEqual(self.io.writes.count(b"manual\n"), 2)

  async def test_recovery_keeps_unresolved_handoff_and_skips_already_ready_machine(self) -> None:
    """Recovery must neither replay unload nor silently restore its ownership."""
    pending = MicroServeUnresolvedPreparation("unload", 2, 50)
    self.driver._unresolved_preparation = pending
    # Fault mode spelling is simulated; physical fault transitions are unverified.
    fault = status(homed=False).replace("mode Ready", "mode Estop")
    self.io.add("status", fault)
    self.io.add("estoprecover")
    self.io.add("status", status())
    self.io.add("status", status())
    await self.driver.recover_from_estop()
    await self.driver.recover_from_estop()
    self.assertEqual(self.driver.unresolved_preparation, pending)
    self.assertEqual(self.io.writes.count(b"estoprecover\n"), 1)

  async def test_recovery_refuses_busy_manual_and_normally_unhomed_states(self) -> None:
    """Recovery cannot race an executing command or substitute for explicit homing."""
    for initial in (
      status().replace("NotBusy", "Busy"),
      status().replace("mode Ready", "mode Manual"),
      status(homed=False),
    ):
      self.io.add("status", initial)
      with self.assertRaises(RuntimeError):
        await self.driver.recover_from_estop()
    self.assertEqual(self.io.writes, [b"status\n"] * 3)

  async def test_clear_abort_preserves_uncertain_transfer_and_checks_status(self) -> None:
    """Clearing a fault latch must not resolve plate ownership or trigger homing."""
    pending = MicroServeUnresolvedPreparation("load", 2, 50)
    self.driver._unresolved_preparation = pending
    self.io.add("status", status().replace("mode Ready", "mode Aborted"))
    self.io.add("clearabort")
    self.io.add("status", status(homed=False))
    await self.driver.clear_abort()
    self.assertEqual(self.driver.unresolved_preparation, pending)
    self.assertNotIn(b"home\n", self.io.writes)
    self.io.add("status", status().replace("NotBusy", "Busy"))
    with self.assertRaises(RuntimeError):
      await self.driver.clear_abort()

  async def test_laser_uses_absolute_state_without_axis_motion(self) -> None:
    """Enabling twice remains an on request, not a toggle."""
    for enabled in (True, True, False):
      self.io.add("status", status())
      self.io.add(f"laser {'on' if enabled else 'off'}")
      await self.driver.set_barcode_laser(enabled)
    self.assertEqual(self.io.writes.count(b"laser on\n"), 2)
    self.assertEqual(self.io.writes.count(b"laser off\n"), 1)

  async def test_angle_preparation_repeat_queries_angle_without_unloading_again(self) -> None:
    """An angle request cannot fetch a second plate after an owned preparation."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 2 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("unloadangle 2", "0.1013")
    self.io.add("status", status(stacker=2, extended=True))
    self.io.add("status", status(stacker=2, extended=True))
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("getangle", "0.1013")
    self.assertEqual(
      await self.driver.stackers[2].prepare_for_unload_with_angle(GEOMETRY), "0.1013"
    )
    self.assertEqual(
      await self.driver.stackers[2].prepare_for_unload_with_angle(GEOMETRY), "0.1013"
    )
    self.assertEqual(self.io.writes.count(b"unloadangle 2\n"), 1)


class MicroServeDiagnosticCaptureTests(unittest.IsolatedAsyncioTestCase):
  """Replay the complete read-only diagnostic session recorded on the controller."""

  async def test_diagnostic_capture(self) -> None:
    """Queries preserve the physical status and consume every captured reply."""
    driver = HighResMicroServe("10.253.253.253")
    reader = CaptureReader(str(Path(__file__).parent / "captures" / "diagnostics.json"))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      before = await driver.request_status()
      self.assertIn("stackerNumber 2", await driver.request_variable_status())
      self.assertIn("Spatula -2030043136", await driver.request_limits())
      self.assertEqual(await driver.request_plate_angle(), "0.0000")
      for address, expected in enumerate(
        ("Carousel: -24.389", "Spatula: 0.517", "Effectuator: 0.813", "Barcode: 4.804")
      ):
        self.assertEqual(await driver.request_home_offset(address), expected)
      self.assertIn("microns", (await driver.request_command_help("measurestacker"))[0])
      self.assertIn("homesel, hs", "\n".join(await driver.request_command_catalog()))
      self.assertEqual(before, await driver.request_status())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_empty_measurement_leaves_loader_extended(self) -> None:
    """Replay the measured empty-stack offset and require explicit retraction afterward."""
    driver = HighResMicroServe("10.253.253.253")
    reader = CaptureReader(str(Path(__file__).parent / "captures" / "measurement_empty.json"))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      self.assertTrue((await driver.request_status()).loader_retracted)
      self.assertEqual((await driver.request_dimensions())[0], GEOMETRY)
      self.assertEqual(await driver.stackers[0].measure_height(), 0.069)
      result = await driver.request_status()
      self.assertTrue(result.loader_extended)
      self.assertTrue(result.plate_sensor_blocked)
      self.assertIsNone(driver.unresolved_operation)
      self.assertEqual(await driver.request_errors(), ())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_plate_discovery_measurements_and_retractions(self) -> None:
    """Replay discovery of the single loaded stacker without changing geometry."""
    driver = HighResMicroServe("10.253.253.253")
    reader = CaptureReader(str(Path(__file__).parent / "captures" / "measurement_discovery.json"))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      await driver.retract()
      for index, expected in enumerate((0.086, 0.064, 0.059, 0.059, 13.629), start=1):
        self.assertEqual(await driver.stackers[index].measure_height(), expected)
        measured = await driver.request_status()
        self.assertEqual(measured.stacker, index)
        self.assertTrue(measured.loader_extended)
        await driver.retract()
        self.assertTrue((await driver.request_status()).loader_retracted)
      self.assertEqual(await driver.request_errors(), ())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_actual_barcode_geometry_error_preserves_unresolved_operation(self) -> None:
    """Invalid geometry is a firmware failure, not an empty or successful barcode scan."""
    driver = HighResMicroServe("10.253.253.253")
    reader = CaptureReader(str(Path(__file__).parent / "captures" / "barcode_geometry_error.json"))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      self.assertTrue((await driver.request_status()).loader_retracted)
      geometry = (await driver.request_dimensions())[5]
      with self.assertRaisesRegex(MicroServeError, "Invalid plate count"):
        await driver.stackers[5].scan_barcodes(geometry)
      self.assertEqual(driver.unresolved_operation, "readbarcodestacker 5")
      self.assertTrue((await driver.request_status()).loader_extended)
      self.assertIn("Invalid plate count", (await driver.request_errors())[0])
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_manual_home_and_laser_capture(self) -> None:
    """Replay stale-mode manual ownership, explicit home, and absolute laser commands."""
    driver = HighResMicroServe("10.253.253.253")
    reader = CaptureReader(str(Path(__file__).parent / "captures" / "manual_laser.json"))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      self.assertTrue((await driver.request_status()).loader_retracted)
      geometry = await driver.request_dimensions()
      errors = await driver.request_errors()
      await driver.enter_manual_mode()
      manual = await driver.request_status()
      self.assertFalse(manual.homed)
      self.assertIsNone(manual.stacker)
      self.assertEqual(manual.mode, "ready")
      await driver.enter_manual_mode()
      self.assertTrue((await driver.request_status()).loader_retracted)
      await driver.home()
      self.assertTrue((await driver.request_status()).homed)
      for enabled in (True, True, False):
        await driver.set_barcode_laser(enabled)
      self.assertTrue((await driver.request_status()).loader_retracted)
      self.assertEqual(await driver.request_errors(), errors)
      self.assertEqual(await driver.request_dimensions(), geometry)
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()
