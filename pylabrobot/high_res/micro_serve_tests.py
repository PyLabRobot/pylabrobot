"""MicroServe query/motion captures and simulated failures; no hardware connections."""

import asyncio
import unittest
from collections import deque
from pathlib import Path
from typing import Deque, List, Optional, Tuple, Union

from pylabrobot.high_res.micro_serve import (
  HighResMicroServe,
  MicroServeError,
  MicroServePlateDimensions,
  MicroServeProtocolError,
  MicroServeStacker,
  _parse_status,
)
from pylabrobot.io.capture import CaptureReader
from pylabrobot.io.socket import SocketValidator

# Captured from MicroServe HRB-2008-10558, firmware 2.7.0.756.
STATUS = (
  "OK! status nothomed, stackerNumber -1, Flags: carouselHome ON, effectuatorHome ON, "
  "spatulaHome ON, barcodeHome ON, plateSensor OFF, button OFF, lockSensor OFF, "
  "Positions: carousel 0.000000, effectuator 0.000000, spatula 0.000000, barcode 0.000000, "
  "machineState NotBusy, loaderStatus Retracted, mode Ready"
)
VERSION = (
  "Product Name: MicroServe",
  "Serial Number: HRB-2008-10558",
  "libcommon Version:    1.1.0.756",
  "libts7600 Version:    1.0.0.756",
  "Firmware Version:     2.7.0.756",
  "Firmware Build: A620467A",
)
DIMENSIONS = tuple(
  f"Stacker {i}: PlateHeight 11000, StackHeight 10000, PlateThickness 10000" for i in range(14)
)
GEOMETRY = MicroServePlateDimensions(height=11, stack_height=10, thickness=10)


def status(
  homed: bool = True, stacker: int = 0, extended: bool = False, plate: bool = False
) -> str:
  """Build simulated states by modifying fields in the captured status line."""
  return (
    STATUS.replace("nothomed", "homed" if homed else "nothomed")
    .replace("stackerNumber -1", f"stackerNumber {stacker}")
    .replace("Retracted", "Extended" if extended else "Retracted")
    .replace("plateSensor OFF", "plateSensor ON" if plate else "plateSensor OFF")
    .replace("spatulaHome ON", "spatulaHome OFF" if extended else "spatulaHome ON")
    .replace("effectuatorHome ON", "effectuatorHome OFF" if extended else "effectuatorHome ON")
    .replace("lockSensor OFF", "lockSensor ON" if extended else "lockSensor OFF")
  )


class FakeSocket:
  """Script command/reply pairs and refuse interleaved exchanges."""

  def __init__(self) -> None:
    """Start with no scripted commands or unread replies."""
    self.scripts: Deque[Tuple[str, List[Union[bytes, BaseException]]]] = deque()
    self.pending: Deque[Union[bytes, BaseException]] = deque()
    self.writes: List[bytes] = []
    self.read_timeouts: List[float] = []
    self.setup_calls = 0
    self.stop_calls = 0
    self.next_id = 1

  def add(self, command: str, *lines: str, completion: str = "OK") -> None:
    """Queue a complete controller reply with a unique command identifier."""
    ident = self.next_id
    self.next_id += 1
    self.scripts.append(
      (
        command,
        [
          (line + "\r\n").encode("ascii")
          for line in (f"ACK! {command} {ident}", *lines, f"{completion}! {command} {ident}")
        ],
      )
    )

  async def setup(self) -> None:
    """Record connection setup."""
    self.setup_calls += 1

  async def stop(self) -> None:
    """Discard unread replies when a connection is invalidated."""
    self.stop_calls += 1
    self.pending.clear()

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:
    """Check framing, command order, and ownership of pending replies."""
    if self.pending:
      raise AssertionError("A new command was written before consuming the previous reply")
    expected, reply = self.scripts.popleft()
    if data != expected.encode("ascii") + b"\n":
      raise AssertionError(f"Expected {expected!r} with LF terminator, got {data!r}")
    self.writes.append(data)
    self.pending.extend(reply)
    await asyncio.sleep(0)

  async def readline(self, timeout: float) -> bytes:
    """Return one line or a scripted transport failure."""
    self.read_timeouts.append(timeout)
    await asyncio.sleep(0)
    item = self.pending.popleft()
    if isinstance(item, BaseException):
      raise item
    return item


class MicroServeTests(unittest.IsolatedAsyncioTestCase):
  """Exercise the public driver through a scripted PyLabRobot transport."""

  async def asyncSetUp(self) -> None:
    """Connect to a fake transport with no implicit commands."""
    self.driver = HighResMicroServe(host="192.0.2.1")
    self.io = FakeSocket()
    self.driver.io = self.io  # type: ignore[assignment]
    await self.driver.setup()

  async def test_setup_and_stop_do_not_move_hardware(self) -> None:
    """Lifecycle calls are idempotent and do not home or abort the device."""
    await self.driver.setup()
    await self.driver.stop()
    await self.driver.stop()
    self.assertEqual(self.io.setup_calls, 1)
    self.assertEqual(self.io.writes, [])

  async def test_captured_status_then_version_consumes_both_completions(self) -> None:
    """The status data's OK! prefix must not leave a completion on the stream."""
    self.io.add("status", STATUS)
    self.io.add("version", *VERSION)
    result = await self.driver.request_status()
    self.assertFalse(result.homed)
    self.assertIsNone(result.stacker)
    self.assertFalse(result.plate_sensor_blocked)
    self.assertEqual(result.loader, "retracted")
    self.assertTrue(result.flags["carouselHome"])
    self.assertIn("HRB-2008-10558", await self.driver.request_version())
    self.assertFalse(self.io.pending)

  async def test_real_geometry_and_counts(self) -> None:
    """Device micrometers become millimeters and all fourteen indices survive."""
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("getplatecounts", *(f"{i}: 0" for i in range(14)))
    self.assertEqual((await self.driver.request_dimensions())[0], GEOMETRY)
    self.assertEqual(await self.driver.request_plate_counts(), dict.fromkeys(range(14), 0))

  async def test_diagnostics_do_not_change_settings(self) -> None:
    """Readiness, firmware, settings, and errors use query commands only."""
    self.io.add("microserveready", "0")
    self.io.add("firmwareversion", "2.7.0.756")
    self.io.add("settings", "IP_ADDRESS     = 10.10.1.67", "PRODUCT_NAME = MicroServe")
    self.io.add("errors 10")
    self.assertFalse(await self.driver.is_ready())
    self.assertEqual(await self.driver.request_firmware_version(), "2.7.0.756")
    self.assertEqual((await self.driver.request_settings())["IP_ADDRESS"], "10.10.1.67")
    self.assertEqual(await self.driver.request_errors(), ())

  async def test_device_motor_identity_and_filtered_settings_captures(self) -> None:
    """Parse the additional read-only replies captured from the device itself."""
    self.io.add(
      "copleyserial",
      "Carousel: 925198662 : Carousel v2",
      "Spatula: 939194509 : Spatula v2",
      "Effectuator: 935190508 : Effectuator v2",
      "Barcode: 925198664 : Barcode v2",
    )
    self.io.add("detailedversion", "Firmware Version:     2.7.0.756", "Firmware Checksum: A620467A")
    self.io.add("settings CAROUSEL_STACKER_COUNT", "CAROUSEL_STACKER_COUNT = 14")
    self.io.add("getplatecounts 0", "0: 0")
    motors = await self.driver.request_motor_information()
    self.assertEqual(motors["Spatula"].serial_number, "939194509")
    self.assertEqual(motors["Barcode"].name, "Barcode v2")
    self.assertIn("A620467A", await self.driver.request_detailed_version())
    self.assertEqual(
      await self.driver.request_settings("CAROUSEL_STACKER_COUNT"), {"CAROUSEL_STACKER_COUNT": "14"}
    )
    self.assertEqual(await self.driver.stackers[0].request_plate_count(), 0)

  async def test_command_record_and_history_data_have_their_own_status_tokens(self) -> None:
    """Status tokens in a command record are data rather than envelope completions."""
    self.io.add("commandstat 1", "    1 OK!  - version")
    self.io.add(
      "history 3",
      "   33 (passed)  - detailedversion",
      "   34 (passed)  - copleyserial",
      "   35 (running) - history 3",
    )
    result = await self.driver.request_command_status(1)
    self.assertEqual((result.command_id, result.state, result.command), (1, "ok", "version"))
    self.assertEqual(len(await self.driver.request_history(3)), 3)

  async def test_command_record_rejects_wrong_record_id(self) -> None:
    """Matching envelope IDs do not make data about another command acceptable."""
    self.io.add("commandstat 1", "    2 OK!  - version")
    with self.assertRaises(MicroServeProtocolError):
      await self.driver.request_command_status(1)

  async def test_query_arguments_cannot_inject_commands(self) -> None:
    """Reject embedded commands and invalid identifiers before writing anything."""
    for search in ("IP_ADDRESS\nreboot", "", "PRODUCT NAME"):
      with self.subTest(search=search), self.assertRaises(ValueError):
        await self.driver.request_settings(search)
    for count in (0, -1, True):
      with self.subTest(count=count), self.assertRaises(ValueError):
        await self.driver.request_command_status(count)
    self.assertEqual(self.io.writes, [])

  async def test_terminal_failures_keep_diagnostics_and_session_alignment(self) -> None:
    """Errors, aborts, and warnings are never silently treated as success."""
    for terminal in ("ERROR", "ABORTED", "WARNING"):
      with self.subTest(terminal=terminal):
        self.io.add("version", "Error 7: (14:03:30) -150: Example fault", completion=terminal)
        with self.assertRaises(MicroServeError) as caught:
          await self.driver.request_version()
        self.assertEqual(caught.exception.status, terminal)
        self.assertIn("Example fault", str(caught.exception))
        self.assertGreater(caught.exception.command_id, 0)
        self.io.add("version", *VERSION)
        await self.driver.request_version()
    self.assertEqual(self.io.stop_calls, 0)

  async def test_bad_envelopes_close_connection(self) -> None:
    """Wrong commands, identifiers, and duplicate ACKs invalidate the stream."""
    for bad in (
      b"ACK! status 1\n",
      b"ACK! version 1\nOK! version 2\n",
      b"ACK! version 1\nOK! status 1\n",
      b"ACK! version 1\nACK! version 1\n",
    ):
      with self.subTest(reply=bad):
        await self.driver.setup()
        self.io.scripts.append(("version", list(bad.splitlines(keepends=True))))
        with self.assertRaises(MicroServeProtocolError):
          await self.driver.request_version()
        with self.assertRaisesRegex(RuntimeError, "setup"):
          await self.driver.request_version()

  async def test_timeout_eof_partial_line_and_cancellation_do_not_retry(self) -> None:
    """No uncertain command is replayed; reconnect is an explicit action."""
    for failure in (TimeoutError("stalled"), b"", b"partial", asyncio.CancelledError()):
      with self.subTest(failure=failure):
        await self.driver.setup()
        self.io.scripts.append(("version", [b"ACK! version 1\n", failure]))
        with self.assertRaises((TimeoutError, ConnectionError, asyncio.CancelledError)):
          await self.driver.request_version()
        with self.assertRaises(RuntimeError):
          await self.driver.request_version()
    self.assertEqual(len(self.io.writes), 4)

  async def test_query_concurrency_serializes_entire_exchanges(self) -> None:
    """Concurrent requests cannot consume each other's data or completion."""
    self.io.add("version", *VERSION)
    self.io.add("status", STATUS)
    version, state = await asyncio.gather(
      self.driver.request_version(), self.driver.request_status()
    )
    self.assertIn("MicroServe", version)
    self.assertFalse(state.homed)

  async def test_home_only_when_needed_and_verify(self) -> None:
    """A second home call does not repeat a completed homing sequence."""
    self.io.add("status", STATUS)
    self.io.add("home")
    self.io.add("status", status())
    self.io.add("status", status())
    await self.driver.home()
    await self.driver.home()
    self.assertEqual(self.io.writes.count(b"home\n"), 1)

  async def test_home_rejects_blocked_detection_beam(self) -> None:
    """A blocked beam prevents homing without implying an inventory state."""
    self.io.add("status", status(homed=False, plate=True))
    with self.assertRaisesRegex(RuntimeError, "detection beam"):
      await self.driver.home()

  async def test_home_checks_result(self) -> None:
    """An OK envelope does not override an unhomed result."""
    self.io.add("status", STATUS)
    self.io.add("home")
    self.io.add("status", STATUS)
    with self.assertRaises(RuntimeError):
      await self.driver.home()

  async def test_home_can_leave_manual_mode_as_described_in_operation_notes(self) -> None:
    """Homing restores ready mode even when the previous homed flag remains set."""
    self.io.add("status", status().replace("mode Ready", "mode Manual"))
    self.io.add("home")
    self.io.add("status", status())
    await self.driver.home()
    self.assertIn(b"home\n", self.io.writes)

  async def test_load_and_unload_apply_geometry_then_verify_target(self) -> None:
    """Each preparation sends geometry before motion and skips repeated intent."""
    for command, prepare, present in (
      ("load", self.driver.stackers[2].prepare_for_load, False),
      ("unload", self.driver.stackers[2].prepare_for_unload, True),
    ):
      with self.subTest(command=command):
        self.io.add("status", status())
        self.io.add("setstackerdimensions 2 11000 10000 10000")
        self.io.add("dimstatus", *DIMENSIONS)
        self.io.add(f"{command} 2")
        self.io.add("status", status(stacker=2, extended=True, plate=present))
        self.io.add("status", status(stacker=2, extended=True, plate=present))
        self.io.add("dimstatus", *DIMENSIONS)
        await prepare(GEOMETRY)
        await prepare(GEOMETRY)
        self.assertEqual(self.io.writes.count(f"{command} 2\n".encode()), 1)

  async def test_prepare_rejects_other_handoff_and_wrong_final_state(self) -> None:
    """Never take ownership of another handoff or accept the wrong carousel position."""
    self.io.add("status", status(stacker=1, extended=True, plate=True))
    with self.assertRaisesRegex(RuntimeError, "handoff"):
      await self.driver.stackers[2].prepare_for_unload(GEOMETRY)
    self.io.add("status", status())
    self.io.add("setstackerdimensions 2 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("unload 2")
    self.io.add("status", status(stacker=1, extended=True, plate=False))
    with self.assertRaisesRegex(RuntimeError, "not reached"):
      await self.driver.stackers[2].prepare_for_unload(GEOMETRY)

  async def test_dimension_readback_failure_prevents_motion(self) -> None:
    """A rejected geometry update must stop the sequence before unload."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 13900 11900 11900")
    self.io.add("dimstatus", *DIMENSIONS)
    with self.assertRaises(MicroServeProtocolError):
      await self.driver.stackers[0].prepare_for_unload(MicroServePlateDimensions(13.9, 11.9, 11.9))

  async def test_uncertain_unload_requires_reconnect_and_does_not_fetch_twice(self) -> None:
    """An interrupted unload needs command-record reconciliation, not a beam guess."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 2 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.scripts.append(("unload 2", [b"ACK! unload 2 50\n", TimeoutError("lost completion")]))
    with self.assertRaises(TimeoutError):
      await self.driver.stackers[2].prepare_for_unload(GEOMETRY)
    with self.assertRaisesRegex(RuntimeError, "unresolved"):
      await self.driver.stackers[2].prepare_for_unload(GEOMETRY)
    pending = self.driver.unresolved_preparation
    assert pending is not None
    self.assertEqual(pending.command_id, 50)
    await self.driver.setup()
    self.io.add("commandstat 50", "   50 OK!  - unload 2")
    self.io.add("status", status(stacker=2, extended=True, plate=False))
    await self.driver.reconcile_preparation()
    self.assertIsNone(self.driver.unresolved_preparation)
    self.io.add("status", status(stacker=2, extended=True, plate=True))
    self.io.add("dimstatus", *DIMENSIONS)
    await self.driver.stackers[2].prepare_for_unload(GEOMETRY)
    self.assertEqual(self.io.writes.count(b"unload 2\n"), 1)

  async def test_preparation_sequence_is_atomic_with_concurrent_query(self) -> None:
    """A competing caller cannot interleave between geometry and transfer checks."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 2 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("load 2")
    self.io.add("status", status(stacker=2, extended=True))
    self.io.add("version", *VERSION)
    await asyncio.gather(
      self.driver.stackers[2].prepare_for_load(GEOMETRY), self.driver.request_version()
    )
    self.assertEqual(self.io.writes[-1], b"version\n")

  async def test_unload_repeat_does_not_infer_occupancy_from_beam(self) -> None:
    """A changing beam does not authorize fetching another plate before retraction."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("unload 0")
    self.io.add("status", status(extended=True, plate=False))
    self.io.add("status", status(extended=True, plate=True))
    self.io.add("dimstatus", *DIMENSIONS)
    await self.driver.stackers[0].prepare_for_unload(GEOMETRY)
    await self.driver.stackers[0].prepare_for_unload(GEOMETRY)
    self.assertEqual(self.io.writes.count(b"unload 0\n"), 1)

  async def test_extended_loader_without_owned_preparation_is_not_adopted(self) -> None:
    """An extended stacker alone cannot identify an earlier load versus unload."""
    self.io.add("status", status(extended=True, plate=True))
    with self.assertRaisesRegex(RuntimeError, "handoff"):
      await self.driver.stackers[0].prepare_for_unload(GEOMETRY)
    self.assertEqual(self.io.writes, [b"status\n"])

  async def test_running_failed_or_mismatched_preparation_stays_unresolved(self) -> None:
    """Reconciliation only adopts an exact successful command record."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.scripts.append(("unload 0", [b"ACK! unload 0 50\n", TimeoutError("lost completion")]))
    with self.assertRaises(TimeoutError):
      await self.driver.stackers[0].prepare_for_unload(GEOMETRY)
    await self.driver.setup()
    for record in ("50 RUNNING! - unload 0", "50 FAIL! - unload 0", "50 OK! - load 0"):
      self.io.add("commandstat 50", record)
      with self.subTest(record=record), self.assertRaisesRegex(RuntimeError, "not confirmed"):
        await self.driver.reconcile_preparation()
      self.assertIsNotNone(self.driver.unresolved_preparation)

  async def test_preparation_without_ack_cannot_be_reconciled_from_a_stale_id(self) -> None:
    """A failed write/ACK cannot borrow the identifier of the geometry query."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.scripts.append(("load 0", [TimeoutError("no ACK")]))
    with self.assertRaises(TimeoutError):
      await self.driver.stackers[0].prepare_for_load(GEOMETRY)
    pending = self.driver.unresolved_preparation
    assert pending is not None
    self.assertIsNone(pending.command_id)
    await self.driver.setup()
    with self.assertRaisesRegex(RuntimeError, "No ACK"):
      await self.driver.reconcile_preparation()

  async def test_retract_checks_state_and_is_idempotent(self) -> None:
    """Retraction is explicit and not repeated when already retracted."""
    self.io.add("status", status(extended=True))
    self.io.add("retract")
    self.io.add("status", status())
    self.io.add("status", status())
    await self.driver.retract()
    await self.driver.retract()
    self.assertEqual(self.io.writes.count(b"retract\n"), 1)

  async def test_rotation_requires_clear_retracted_loader(self) -> None:
    """Carousel motion cannot start during a robot handoff."""
    self.io.add("status", status(extended=True))
    with self.assertRaises(RuntimeError):
      await self.driver.stackers[2].move_to()
    self.io.add("status", status())
    self.io.add("spin 2")
    self.io.add("status", status(stacker=2))
    await self.driver.stackers[2].move_to()

  async def test_rotation_uses_home_sensors_instead_of_stale_loader_field(self) -> None:
    """A stale Extended field must not block a physically retracted loader."""
    self.io.add("status", status().replace("loaderStatus Retracted", "loaderStatus Extended"))
    self.io.add("spin 2")
    self.io.add("status", status(stacker=2))
    await self.driver.stackers[2].move_to()

  async def test_rotation_rejects_retracted_field_with_unsafe_home_or_lock_sensor(self) -> None:
    """A Retracted field alone cannot authorize carousel motion."""
    for old, new in (
      ("spatulaHome ON", "spatulaHome OFF"),
      ("effectuatorHome ON", "effectuatorHome OFF"),
      ("lockSensor OFF", "lockSensor ON"),
    ):
      with self.subTest(sensor=old):
        self.io.add("status", status().replace(old, new))
        with self.assertRaisesRegex(RuntimeError, "retract"):
          await self.driver.stackers[2].move_to()
    self.assertEqual(self.io.writes, [b"status\n"] * 3)

  async def test_retraction_rejects_ok_reply_when_home_sensor_stays_off(self) -> None:
    """The command envelope must agree with the physical home sensors."""
    self.io.add("status", status(extended=True))
    self.io.add("retract")
    self.io.add("status", status().replace("spatulaHome ON", "spatulaHome OFF"))
    with self.assertRaisesRegex(RuntimeError, "did not retract"):
      await self.driver.retract()

  async def test_bad_status_and_incomplete_counts_are_rejected(self) -> None:
    """Unknown sensor states and partial inventory are not interpreted as empty."""
    self.io.add("status", STATUS.replace("plateSensor OFF", "plateSensor UNKNOWN"))
    with self.assertRaises(MicroServeProtocolError):
      await self.driver.request_status()
    self.io.add("getplatecounts", "0: 0")
    with self.assertRaises(MicroServeProtocolError):
      await self.driver.request_plate_counts()

  async def test_barcode_scan_uses_geometry_and_scan_timeout(self) -> None:
    """Barcode data remains raw and uses the longer motion timeout."""
    self.io.add("status", status())
    self.io.add("setstackerdimensions 0 11000 10000 10000")
    self.io.add("dimstatus", *DIMENSIONS)
    self.io.add("readbarcodestacker 0", '0: "EXAMPLE"')
    result = await self.driver.stackers[0].scan_barcodes(GEOMETRY)
    self.assertEqual(result, ('0: "EXAMPLE"',))
    self.assertGreater(self.io.read_timeouts[-1], 290)


class MicroServeCaptureTests(unittest.IsolatedAsyncioTestCase):
  """Replay actual query and motion exchanges without opening a network connection."""

  async def test_homing_capture(self) -> None:
    """Replay the transition from unhomed to homed at stacker zero."""
    driver = HighResMicroServe(host="10.253.253.253")
    reader = CaptureReader(str(Path(__file__).with_name("micro_serve_home_capture.json")))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      self.assertFalse((await driver.request_status()).homed)
      await driver.home()
      result = await driver.request_status()
      self.assertTrue(result.homed)
      self.assertTrue(result.loader_retracted)
      self.assertEqual(result.stacker, 0)
      self.assertEqual(await driver.request_errors(), ())
      self.assertIn("   59 (passed)  - home", await driver.request_history(8))
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_all_carousel_positions_capture(self) -> None:
    """Replay all fourteen positions, repeated targets, and reconnection."""
    driver = HighResMicroServe(host="10.253.253.253")
    reader = CaptureReader(str(Path(__file__).with_name("micro_serve_carousel_capture.json")))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      before = await driver.request_status()
      await driver.home()
      await driver.retract()
      dimensions = await driver.request_dimensions()
      for index in [*range(3, 14), 0, 1, 2]:
        await driver.stackers[index].move_to()
        await driver.stackers[index].move_to()
        result = await driver.request_status()
        self.assertEqual(result.stacker, index)
        self.assertTrue(result.loader_retracted)
        self.assertFalse(result.busy)
        self.assertEqual(await driver.stackers[index].request_plate_count(), 0)
      self.assertFalse(await driver.is_ready())
      self.assertEqual(await driver.request_plate_counts(), dict.fromkeys(range(14), 0))
      self.assertEqual(await driver.request_dimensions(), dimensions)
      self.assertEqual(await driver.request_errors(), ())
      await driver.stop()
      await driver.setup()
      self.assertEqual(before, await driver.request_status())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_empty_load_retract_with_stale_firmware_loader_flag(self) -> None:
    """Actual retraction succeeds although firmware leaves loaderStatus Extended."""
    driver = HighResMicroServe(host="10.253.253.253")
    reader = CaptureReader(str(Path(__file__).with_name("micro_serve_empty_load_capture.json")))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      self.assertEqual((await driver.request_status()).stacker, 2)
      dimensions = await driver.request_dimensions()
      self.assertFalse(await driver.is_ready())
      await driver.stackers[2].prepare_for_load(dimensions[2])
      extended = await driver.request_status()
      self.assertTrue(extended.plate_sensor_blocked)
      self.assertTrue(await driver.is_ready())
      await driver.stackers[2].prepare_for_load(dimensions[2])
      self.assertEqual(extended, await driver.request_status())
      await driver.retract()
      self.assertIsNone(driver._prepared)
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_empty_cycle_and_rotation_after_retraction_capture(self) -> None:
    """Replay a complete empty handoff and subsequent rotation with a stale loader field."""
    driver = HighResMicroServe(host="10.253.253.253")
    reader = CaptureReader(str(Path(__file__).with_name("micro_serve_empty_cycle_capture.json")))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      self.assertIn("HRB-2008-10558", await driver.request_version())
      before = await driver.request_status()
      self.assertEqual(before.loader, "extended")
      self.assertTrue(before.loader_retracted)
      await driver.retract()
      dimensions = await driver.request_dimensions()
      await driver.stackers[2].set_dimensions(dimensions[2])
      await driver.stackers[2].prepare_for_load(dimensions[2])
      self.assertTrue((await driver.request_status()).loader_extended)
      self.assertTrue(await driver.is_ready())
      await driver.stackers[2].prepare_for_load(dimensions[2])
      await driver.retract()
      await driver.retract()
      retracted = await driver.request_status()
      self.assertEqual(retracted.loader, "extended")
      self.assertTrue(retracted.loader_retracted)
      self.assertFalse(retracted.loader_extended)
      self.assertIsNone(driver._prepared)
      self.assertFalse(await driver.is_ready())
      await driver.stackers[3].move_to()
      await driver.stackers[2].move_to()
      self.assertEqual(before, await driver.request_status())
      self.assertEqual(dimensions, await driver.request_dimensions())
      self.assertEqual(await driver.request_errors(), ())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()

  async def test_device_native_read_only_capture(self) -> None:
    """Exercise all implemented queries without opening a network connection."""
    driver = HighResMicroServe(host="10.253.253.253")
    reader = CaptureReader(str(Path(__file__).with_name("micro_serve_query_capture.json")))
    driver.io = SocketValidator(reader, "HighRes MicroServe", host="10.253.253.253", port=1000)
    await driver.setup()
    try:
      before = await driver.request_status()
      self.assertIn("HRB-2008-10558", await driver.request_version())
      version_id = driver.last_command_id
      assert version_id is not None
      self.assertEqual(await driver.request_firmware_version(), "2.7.0.756")
      self.assertIn("A620467A", await driver.request_detailed_version())
      self.assertEqual(
        (await driver.request_motor_information())["Barcode"].serial_number, "925198664"
      )
      self.assertEqual(len(await driver.request_dimensions()), 14)
      self.assertEqual(len(await driver.request_plate_counts()), 14)
      self.assertEqual(await driver.stackers[0].request_plate_count(), 0)
      self.assertFalse(await driver.is_ready())
      self.assertEqual(
        await driver.request_settings("CAROUSEL_STACKER_COUNT"), {"CAROUSEL_STACKER_COUNT": "14"}
      )
      self.assertEqual(await driver.request_errors(), ())
      self.assertEqual(len(await driver.request_history(3)), 3)
      record = await driver.request_command_status(version_id)
      self.assertEqual((record.state, record.command), ("ok", "version"))
      self.assertEqual(before, await driver.request_status())
      with self.assertRaises(IndexError):
        reader.next_command()
    finally:
      await driver.stop()


class MicroServeValidationTests(unittest.TestCase):
  """Validate parameters and status variants before hardware communication."""

  def test_dimensions_reject_nonphysical_values(self) -> None:
    """Dimensions must survive conversion to device-native micrometers."""
    for value in (0, -1, float("nan"), float("inf"), 0.0001):
      with self.subTest(value=value), self.assertRaises(ValueError):
        MicroServePlateDimensions(value, 10, 10)
    with self.assertRaises(ValueError):
      MicroServePlateDimensions(10, 10, 11)

  def test_indices_and_timeouts(self) -> None:
    """Expose exactly fourteen zero-based stackers and finite positive timeouts."""
    device = HighResMicroServe(host="192.0.2.1")
    self.assertEqual([stacker.index for stacker in device.stackers], list(range(14)))
    for index in (-1, 14, True):
      with self.subTest(index=index), self.assertRaises(ValueError):
        MicroServeStacker(device, index)
    for timeout in (0, -1, float("nan")):
      with self.subTest(timeout=timeout), self.assertRaises(ValueError):
        HighResMicroServe(host="192.0.2.1", ack_timeout=timeout)

  def test_manual_status_example_and_unknown_machine_state(self) -> None:
    """Device-native position annotations do not interfere with state parsing."""
    manual = status(extended=True).replace("effectuator 0.000000", "effectuator -1 (-1 um)")
    parsed = _parse_status((manual,))
    self.assertTrue(parsed.homed)
    self.assertEqual(parsed.loader, "extended")
    with self.assertRaises(MicroServeProtocolError):
      _parse_status((STATUS.replace("NotBusy", "Unknown"),))

  def test_missing_loader_sensors_cannot_authorize_motion(self) -> None:
    """Incomplete physical state must fail before a motion method can consume it."""
    for sensor in ("spatulaHome ON", "effectuatorHome ON", "lockSensor OFF"):
      with self.subTest(sensor=sensor), self.assertRaises(MicroServeProtocolError):
        _parse_status((STATUS.replace(sensor, ""),))
