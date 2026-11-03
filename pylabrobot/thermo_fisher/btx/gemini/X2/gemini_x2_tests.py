import asyncio
import unittest
from typing import Any, Dict, List, Optional, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.thermo_fisher.btx.gemini.X2.file_transfer_control import (
  ProtocolDeletionPendingError,
  _FileTransferControl,
)
from pylabrobot.thermo_fisher.btx.gemini.X2.gemini_x2 import BTXGeminiX2
from pylabrobot.thermo_fisher.btx.gemini.X2.ht200 import BTXHT200
from pylabrobot.thermo_fisher.btx.gemini.X2.standard import (
  ElectroporationPreparationDetails,
  ElectroporationProtocol,
  PreparedElectroporationRun,
)
from pylabrobot.thermo_fisher.btx.gemini.X2.the_ghost_touch import (
  CancelledPreparedUserProtocolResult,
  PreparedUserProtocolResult,
  ScreenSnapshotResult,
  StartedPreparedUserProtocolResult,
)


class _DummySerial:
  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    del data

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    return b""


class _FakeFileTransferControl:
  def __init__(self) -> None:
    self.port = "/dev/fake-btx"
    self.setup = AsyncMock()
    self.stop = AsyncMock()
    self.protocols = ["CD", "JJ"]
    self.log_snapshots: List[List[str]] = []
    self.log_contents: Dict[str, str] = {}
    self.add_calls: List[Dict[str, Any]] = []
    self.delete_calls: List[Dict[str, Any]] = []
    self.verify_calls: List[tuple[str, ElectroporationProtocol]] = []
    self.delete_failures_before_success = 0
    self.verify_error: Optional[Exception] = None
    self.version = "BTX Gemini 4.0.4"
    self.serial_number = "1135421"
    self.device_time = "03/09/2026 5:00:00 PM"
    self._parser = _FileTransferControl(port=self.port, serial_io=_DummySerial())

  async def list_protocols(self) -> list[str]:
    return list(self.protocols)

  async def request_protocol(self, protocol_name: str) -> Dict[str, Any]:
    return {"operation": "request_protocol", "protocol": protocol_name}

  async def verify_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol,
  ) -> Dict[str, Any]:
    self.verify_calls.append((protocol_name, protocol))
    if self.verify_error is not None:
      raise self.verify_error
    return {"operation": "verify_protocol", "protocol": protocol_name}

  async def add_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol,
    overwrite: bool = False,
  ) -> Dict[str, Any]:
    self.add_calls.append(
      {
        "protocol_name": protocol_name,
        "protocol": protocol,
        "overwrite": overwrite,
      }
    )
    self.protocols = sorted(self.protocols + [protocol_name])
    return {"operation": "add_protocol", "protocol": protocol_name, "overwrite": overwrite}

  async def delete_protocol(self, protocol_name: str, missing_ok: bool = False) -> Dict[str, Any]:
    self.delete_calls.append({"protocol_name": protocol_name, "missing_ok": missing_ok})
    if self.delete_failures_before_success > 0:
      self.delete_failures_before_success -= 1
      raise ProtocolDeletionPendingError(
        f'Protocol "{protocol_name}" still exists after repeated delete attempts.'
      )
    if protocol_name not in self.protocols:
      if missing_ok:
        return {"operation": "delete_protocol", "deleted": False, "protocol": protocol_name}
      raise FileNotFoundError(protocol_name)
    self.protocols = [name for name in self.protocols if name != protocol_name]
    return {"operation": "delete_protocol", "deleted": True, "protocol": protocol_name}

  async def list_log_files(self, root: str = "\\BTXDATA") -> list[str]:
    del root
    if self.log_snapshots:
      return list(self.log_snapshots.pop(0))
    return sorted(self.log_contents)

  async def fetch_sd_file(self, sd_path: str) -> str:
    return self.log_contents[sd_path]

  async def request_version(self) -> str:
    return self.version

  async def request_serial_number(self) -> str:
    return self.serial_number

  async def request_device_time(self) -> str:
    return self.device_time

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    return self._parser.parse_run_log(text)


class _FakeGhostTouchSession:
  def __init__(self, factory: "_FakeGhostTouchFactory", port: str) -> None:
    self.factory = factory
    self.port = port

  async def setup(self) -> None:
    await self.factory.setup()

  async def stop(self) -> None:
    await self.factory.stop()

  async def ensure_home(self) -> ScreenSnapshotResult:
    self.factory.ensure_home_calls += 1
    return ScreenSnapshotResult(state="main_menu", image_path="home")

  async def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> PreparedUserProtocolResult:
    if self.factory.prepare_error is not None:
      raise self.factory.prepare_error
    self.factory.prepare_calls.append(
      {
        "protocol_name": protocol_name,
        "plate_columns": plate_columns,
        "port": self.port,
      }
    )
    run_view = ScreenSnapshotResult(state="protocol_run_view", image_path="run-view")
    return PreparedUserProtocolResult(
      protocol_name=protocol_name,
      plate_columns=plate_columns,
      run_view=run_view,
      after_set_plate_columns=None,
      prepared_verification=run_view,
    )

  async def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> StartedPreparedUserProtocolResult:
    if self.factory.start_waiter is not None:
      await self.factory.start_waiter.wait()
    if self.factory.start_error is not None:
      raise self.factory.start_error
    self.factory.start_calls.append(
      {
        "protocol_name": protocol_name,
        "home_after": home_after,
        "max_run_seconds": max_run_seconds,
        "port": self.port,
      }
    )
    verification = ScreenSnapshotResult(state="protocol_run_view", image_path="verify")
    completed = ScreenSnapshotResult(state="protocol_finish", image_path="done")
    home = ScreenSnapshotResult(state="main_menu", image_path="home") if home_after else None
    return StartedPreparedUserProtocolResult(
      protocol_name=protocol_name,
      verification=verification,
      after_start=verification,
      completed=completed,
      completed_at_utc="2026-03-09T10:00:01+00:00",
      home=home,
    )

  async def cancel_prepared_user_protocol(self) -> CancelledPreparedUserProtocolResult:
    if self.factory.cancel_error is not None:
      raise self.factory.cancel_error
    self.factory.cancel_calls += 1
    return CancelledPreparedUserProtocolResult(
      cancelled=True,
      home_after=True,
      final_state=ScreenSnapshotResult(state="main_menu", image_path="home"),
    )


class _FakeGhostTouchFactory:
  def __init__(self) -> None:
    self.created: List[str] = []
    self.prepare_calls: List[Dict[str, Any]] = []
    self.start_calls: List[Dict[str, Any]] = []
    self.cancel_calls = 0
    self.setup = AsyncMock()
    self.stop = AsyncMock()
    self.ensure_home_calls = 0
    self.prepare_error: Optional[Exception] = None
    self.start_error: Optional[Exception] = None
    self.cancel_error: Optional[Exception] = None
    self.start_waiter: Optional[asyncio.Event] = None

  def __call__(self, port: str) -> _FakeGhostTouchSession:
    self.created.append(port)
    return _FakeGhostTouchSession(self, port)


def _protocol() -> ElectroporationProtocol:
  return ElectroporationProtocol(
    protocol_type="square",
    pulse_amplitude_volts=250,
    gap_mm=1.0,
    duration_us=1000,
  )


def _prepared_run(
  protocol_name: str = "!PLR_123456789",
  serial_number: str = "1135421",
) -> PreparedElectroporationRun:
  return PreparedElectroporationRun(
    protocol_name=protocol_name,
    device_serial_number=serial_number,
    protocol=_protocol(),
    plate_columns=None,
    prefix="!PLR",
    prepared_at_utc="2026-03-09T10:00:00+00:00",
    baseline_log_paths=(),
    prepare_result=ElectroporationPreparationDetails(
      prepared_state="protocol_run_view",
      protocol_setup={},
      device_prepare={},
    ),
  )


def _make_gemini(
  file_transfer_control: _FakeFileTransferControl,
  *,
  plate_handler: Optional[BTXHT200] = None,
  temporary_protocol_prefix: str = BTXGeminiX2.DEFAULT_TEMPORARY_PROTOCOL_PREFIX,
) -> BTXGeminiX2:
  gemini = BTXGeminiX2(
    plate_handler=plate_handler,
    temporary_protocol_prefix=temporary_protocol_prefix,
  )
  gemini._file_transfer_control = cast(_FileTransferControl, file_transfer_control)
  return gemini


class TestBTXGeminiX2(unittest.IsolatedAsyncioTestCase):
  def setUp(self) -> None:
    self.ghost_factory = _FakeGhostTouchFactory()
    patcher = patch(
      "pylabrobot.thermo_fisher.btx.gemini.X2.gemini_x2._TheGhostTouch",
      self.ghost_factory,
    )
    patcher.start()
    self.addCleanup(patcher.stop)

  async def test_prepare_temporary_protocol_adds_protocol_and_arms_run_view(self):
    file_control = _FakeFileTransferControl()
    file_control.log_snapshots = [[r"\BTXDATA\2026-03\260309\100000.TXT"]]
    gemini = _make_gemini(
      file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=0),
    )
    protocol = ElectroporationProtocol(
      protocol_type="exponential",
      pulse_amplitude_volts=2300,
      gap_mm=2.0,
      resistance_ohms=200,
      capacitance_uf=25,
    )

    await gemini.setup()
    prepared = await gemini.prepare_temporary_protocol(
      protocol,
      plate_columns=3,
      plate_handler_reset_state=gemini.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
    )

    self.assertTrue(prepared.protocol_name.startswith("!PLR_"))
    self.assertEqual(len(prepared.protocol_name.encode("ascii")), 15)
    self.assertEqual(prepared.device_serial_number, file_control.serial_number)
    self.assertEqual(prepared.plate_columns, 3)
    self.assertEqual(prepared.baseline_log_paths, (r"\BTXDATA\2026-03\260309\100000.TXT",))
    self.assertEqual(file_control.add_calls[0]["protocol"], protocol)
    self.assertEqual(self.ghost_factory.prepare_calls[0]["protocol_name"], prepared.protocol_name)
    self.assertEqual(prepared.prepare_result.prepared_state, "protocol_run_view")
    self.assertEqual(
      prepared.prepare_result.device_prepare["plate_handler_reset_state"],
      gemini.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
    )
    self.assertEqual(prepared.prepare_result.device_prepare["assumed_plate_handler_pulse_count"], 2)
    self.assertEqual(
      prepared.prepare_result.device_prepare["assumed_plate_handler_column_adjust"], 0
    )
    self.ghost_factory.setup.assert_awaited_once_with()
    self.ghost_factory.stop.assert_awaited_once_with()

  async def test_prepare_failure_cleans_up_and_restores_file_transfer(self):
    file_control = _FakeFileTransferControl()
    self.ghost_factory.prepare_error = RuntimeError("prepare failed")
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "prepare failed"):
      await gemini.prepare_temporary_protocol(_protocol())

    self.assertEqual(len(file_control.delete_calls), 1)
    self.assertTrue(file_control.delete_calls[0]["missing_ok"])
    self.assertEqual(file_control.setup.await_count, 2)
    self.ghost_factory.stop.assert_awaited_once_with()

  async def test_start_verifies_identity_and_protocol_before_go(self):
    file_control = _FakeFileTransferControl()
    prepared = _prepared_run()
    file_control.protocols.append(prepared.protocol_name)
    log_path = r"\BTXDATA\2026-03\260309\100100.TXT"
    file_control.log_snapshots = [[], [log_path]]
    file_control.log_contents[log_path] = "\n".join(
      [
        f"Protocol Name: {prepared.protocol_name}",
        "Protocol Result: Complete",
        "Status: 0x00000000.00000000 - No error.",
      ]
    )
    gemini = _make_gemini(file_control)
    gemini.LOG_POLL_INTERVAL_SECONDS = 0
    await gemini.setup()

    result = await gemini.start_prepared_run(prepared.as_dict(), max_run_seconds=100.0)

    self.assertEqual(file_control.verify_calls, [(prepared.protocol_name, prepared.protocol)])
    self.assertEqual(self.ghost_factory.start_calls[0]["protocol_name"], prepared.protocol_name)
    self.assertEqual(result.log_capture.matched_log_path, log_path)
    self.assertEqual(result.completed_at_utc, "2026-03-09T10:00:01+00:00")
    self.assertTrue(result.cleanup.deleted)

  async def test_start_rejects_a_different_connected_device_before_touch_control(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "belongs to serial number 'different'"):
      await gemini.start_prepared_run(_prepared_run(serial_number="different"))

    self.assertEqual(file_control.verify_calls, [])
    self.assertEqual(self.ghost_factory.created, [])

  async def test_start_rejects_changed_stored_protocol_before_touch_control(self):
    file_control = _FakeFileTransferControl()
    file_control.verify_error = RuntimeError("stored protocol changed")
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "stored protocol changed"):
      await gemini.start_prepared_run(_prepared_run())

    self.assertEqual(self.ghost_factory.created, [])

  async def test_start_rejects_nonpositive_run_timeout_before_go(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(ValueError, "max_run_seconds"):
      await gemini.start_prepared_run(_prepared_run(), max_run_seconds=0)

    self.assertEqual(self.ghost_factory.created, [])

  async def test_missing_delayed_log_is_a_structured_success_result(self):
    file_control = _FakeFileTransferControl()
    prepared = _prepared_run()
    file_control.protocols.append(prepared.protocol_name)
    gemini = _make_gemini(file_control)
    gemini.LOG_POLL_TIMEOUT_SECONDS = 0
    await gemini.setup()

    result = await gemini.start_prepared_run(prepared)

    self.assertIsNone(result.log_capture.matched_log_path)
    self.assertEqual(result.log_capture.summary, {})
    self.assertTrue(result.cleanup.deleted)
    self.assertEqual(result.rsi_result.completed_state, "protocol_finish")

  async def test_start_verification_failure_leaves_protocol_for_explicit_cancel(self):
    file_control = _FakeFileTransferControl()
    prepared = _prepared_run()
    file_control.protocols.append(prepared.protocol_name)
    self.ghost_factory.start_error = RuntimeError("verification failed")
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "verification failed"):
      await gemini.start_prepared_run(prepared)

    self.assertEqual(file_control.delete_calls, [])
    self.assertIn(prepared.protocol_name, file_control.protocols)

  async def test_cancellation_stops_touch_control_and_restores_file_transfer(self):
    file_control = _FakeFileTransferControl()
    self.ghost_factory.start_waiter = asyncio.Event()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    task = asyncio.create_task(gemini.start_prepared_run(_prepared_run()))
    while self.ghost_factory.setup.await_count == 0:
      await asyncio.sleep(0)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task

    self.ghost_factory.stop.assert_awaited_once_with()
    self.assertEqual(file_control.setup.await_count, 2)

  async def test_primary_run_error_is_not_masked_by_touch_stop_error(self):
    file_control = _FakeFileTransferControl()
    self.ghost_factory.start_error = RuntimeError("primary run failure")
    self.ghost_factory.stop.side_effect = RuntimeError("secondary stop failure")
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "primary run failure"):
      await gemini.start_prepared_run(_prepared_run())

    self.assertEqual(file_control.setup.await_count, 2)

  async def test_restore_failure_marks_device_stopped_without_masking_primary_error(self):
    file_control = _FakeFileTransferControl()
    file_control.setup.side_effect = [None, RuntimeError("restore failed")]
    self.ghost_factory.start_error = RuntimeError("primary run failure")
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, "primary run failure"):
      await gemini.start_prepared_run(_prepared_run())

    with self.assertRaisesRegex(RuntimeError, "is not set up"):
      await gemini.list_protocols()

  async def test_cancel_always_homes_and_deletes(self):
    file_control = _FakeFileTransferControl()
    prepared = _prepared_run()
    file_control.protocols.append(prepared.protocol_name)
    gemini = _make_gemini(file_control)
    await gemini.setup()

    result = await gemini.cancel_prepared_run(prepared.as_dict())

    self.assertTrue(result.cleanup.deleted)
    self.assertEqual(self.ghost_factory.cancel_calls, 1)
    self.assertEqual(result.rsi_result.final_state, "main_menu")
    self.assertNotIn(prepared.protocol_name, file_control.protocols)

  async def test_cancel_retries_typed_pending_delete_after_forcing_home(self):
    file_control = _FakeFileTransferControl()
    file_control.delete_failures_before_success = 1
    prepared = _prepared_run()
    file_control.protocols.append(prepared.protocol_name)
    gemini = _make_gemini(file_control)
    await gemini.setup()

    result = await gemini.cancel_prepared_run(prepared)

    self.assertTrue(result.cleanup.retry_used)
    self.assertEqual(self.ghost_factory.ensure_home_calls, 1)
    self.assertEqual(len(file_control.delete_calls), 2)

  async def test_setup_validation_failure_closes_file_transfer(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!AAA", "CD"]
    gemini = _make_gemini(file_control)

    with self.assertRaisesRegex(RuntimeError, r"Temporary protocol prefix '!PLR' is not safe"):
      await gemini.setup()

    file_control.setup.assert_awaited_once_with()
    file_control.stop.assert_awaited_once_with()

  async def test_setup_and_stop_are_idempotent(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)

    await gemini.setup()
    await gemini.setup()
    await gemini.stop()
    await gemini.stop()

    file_control.setup.assert_awaited_once_with()
    file_control.stop.assert_awaited_once_with()

  async def test_device_lock_serializes_public_operations(self):
    file_control = _FakeFileTransferControl()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_version() -> str:
      entered.set()
      await release.wait()
      return file_control.version

    serial_number = AsyncMock(return_value=file_control.serial_number)
    with (
      patch.object(file_control, "request_version", blocked_version),
      patch.object(file_control, "request_serial_number", serial_number),
    ):
      gemini = _make_gemini(file_control)
      await gemini.setup()

      version_task = asyncio.create_task(gemini.request_version())
      await entered.wait()
      serial_task = asyncio.create_task(gemini.request_serial_number())
      await asyncio.sleep(0)
      serial_number.assert_not_awaited()
      release.set()

      self.assertEqual(await version_task, file_control.version)
      self.assertEqual(await serial_task, file_control.serial_number)

  async def test_prepare_requires_explicit_plate_handler_reset_state(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(
      file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=0),
    )
    await gemini.setup()

    with self.assertRaisesRegex(ValueError, "requires an explicit plate_handler_reset_state"):
      await gemini.prepare_temporary_protocol(_protocol(), plate_columns=3)

  async def test_prepare_requires_assumed_plate_handler_state(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control, plate_handler=BTXHT200())
    await gemini.setup()

    with self.assertRaisesRegex(ValueError, "Missing: assumed_pulse_count, assumed_column_adjust"):
      await gemini.prepare_temporary_protocol(
        _protocol(),
        plate_columns=3,
        plate_handler_reset_state=gemini.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
      )

  async def test_prepare_rejects_reset_state_without_columns(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(ValueError, "only valid when plate_columns is set"):
      await gemini.prepare_temporary_protocol(
        _protocol(),
        plate_handler_reset_state=gemini.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
      )

  async def test_prepare_validates_plate_columns_before_adding_protocol(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(ValueError, "plate_columns must be in the range"):
      await gemini.prepare_temporary_protocol(
        _protocol(),
        plate_columns=13,
        plate_handler_reset_state=gemini.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
      )

    self.assertEqual(file_control.add_calls, [])

  async def test_prepare_rejects_existing_reserved_prefix(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!PLR_OLD", "CD"]
    gemini = _make_gemini(file_control)
    await gemini.setup()

    with self.assertRaisesRegex(RuntimeError, r"not available.*!PLR_OLD"):
      await gemini.prepare_temporary_protocol(_protocol())

  async def test_request_device_info(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(
      file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=1),
    )
    await gemini.setup()

    info = await gemini.request_device_info()

    self.assertEqual(info["device"], "BTXGeminiX2")
    self.assertEqual(info["serial_number"], "1135421")
    self.assertEqual(info["protocol_count"], 2)
    self.assertTrue(info["supports_serialized_prepared_runs"])
    self.assertEqual(info["plate_handler"]["model"], "HT-200")
    self.assertNotIn("touch_control", info)

  async def test_file_transfer_methods_delegate_to_file_control(self):
    file_control = _FakeFileTransferControl()
    gemini = _make_gemini(file_control)
    await gemini.setup()

    self.assertEqual(await gemini.list_protocols(), ["CD", "JJ"])
    self.assertEqual(
      await gemini.request_protocol("CD"),
      {"operation": "request_protocol", "protocol": "CD"},
    )

  def test_temporary_names_are_unique_and_respect_ui_limit(self):
    gemini = _make_gemini(_FakeFileTransferControl())

    first = gemini._make_temporary_protocol_name("!PLR")
    second = gemini._make_temporary_protocol_name("!PLR")

    self.assertNotEqual(first, second)
    self.assertEqual(len(first.encode("ascii")), gemini.UI_PROTOCOL_NAME_BYTES)
    with self.assertRaisesRegex(ValueError, "exceed the 15-byte"):
      gemini._make_temporary_protocol_name("!PLR_TOO_LONG")
