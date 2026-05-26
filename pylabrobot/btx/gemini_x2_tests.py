import unittest
from typing import Any, Dict, List, Optional, cast

from pylabrobot.btx.file_transfer_control import FileTransferControl
from pylabrobot.btx.gemini_x2 import (
  BTXGeminiX2Driver,
  BTXGeminiX2ElectroporationBackend,
  GhostTouchFactory,
)
from pylabrobot.btx.ht200 import BTXHT200
from pylabrobot.capabilities.electroporation.standard import (
  ElectroporationPreparationDetails,
  ElectroporationProtocol,
  PreparedElectroporationRun,
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

  async def readline(self) -> bytes:
    return b""


class _FakeFileTransferControl:
  def __init__(self) -> None:
    self.port = "/dev/fake-btx"
    self.setup_calls = 0
    self.stop_calls = 0
    self.protocols = ["CD", "JJ"]
    self.log_snapshots: List[List[str]] = []
    self.log_contents: Dict[str, str] = {}
    self.add_calls: List[Dict[str, Any]] = []
    self.delete_calls: List[Dict[str, Any]] = []
    self.delete_failures_before_success = 0
    self.version = "BTX Gemini 4.0.4"
    self.serial_number = "1135421"
    self.device_time = "03/09/2026 5:00:00 PM"
    self._parser = FileTransferControl(port=self.port, serial_io=_DummySerial())

  async def setup(self) -> None:
    self.setup_calls += 1

  async def stop(self) -> None:
    self.stop_calls += 1

  async def list_protocols(self) -> list[str]:
    return list(self.protocols)

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
      raise RuntimeError(f'Protocol "{protocol_name}" still exists after repeated delete attempts.')
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

  async def get_version(self) -> str:
    return self.version

  async def get_serial_number(self) -> str:
    return self.serial_number

  async def get_device_time(self) -> str:
    return self.device_time

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    return self._parser.parse_run_log(text)


class _FakeGhostTouchSession:
  def __init__(self, factory: "_FakeGhostTouchFactory", kwargs: Dict[str, Any]) -> None:
    self.factory = factory
    self.kwargs = kwargs

  def __enter__(self) -> "_FakeGhostTouchSession":
    self.factory.entered += 1
    return self

  def __exit__(self, exc_type, exc, tb) -> None:
    del exc_type, exc, tb
    self.factory.exited += 1

  def ensure_home(self) -> Dict[str, Any]:
    self.factory.ensure_home_calls += 1
    return {"state": "main_menu"}

  def prepare_user_protocol(
    self,
    protocol_name: str,
    plate_columns: Optional[int] = None,
  ) -> dict[str, object]:
    if self.factory.prepare_error is not None:
      raise self.factory.prepare_error
    call = {
      "protocol_name": protocol_name,
      "plate_columns": plate_columns,
      "port": self.kwargs["port"],
    }
    self.factory.prepare_calls.append(call)
    return {
      "protocol_name": protocol_name,
      "plate_columns": plate_columns,
      "run_view": {"state": "protocol_run_view"},
      "prepared_verification": {"state": "protocol_run_view"},
    }

  def start_prepared_user_protocol(
    self,
    protocol_name: str,
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> dict[str, object]:
    if self.factory.start_error is not None:
      raise self.factory.start_error
    call = {
      "protocol_name": protocol_name,
      "home_after": home_after,
      "max_run_seconds": max_run_seconds,
      "port": self.kwargs["port"],
    }
    self.factory.start_calls.append(call)
    return {
      "protocol_name": protocol_name,
      "verification": {"state": "protocol_run_view"},
      "after_start": {"state": "protocol_run_view"},
      "completed": {"state": "protocol_finish"},
    }

  def cancel_prepared_user_protocol(self, home_after: bool = True) -> dict[str, object]:
    if self.factory.cancel_error is not None:
      raise self.factory.cancel_error
    self.factory.cancel_calls.append({"home_after": home_after, "port": self.kwargs["port"]})
    return {
      "cancelled": True,
      "final_state": {"state": "main_menu"},
      "home_after": home_after,
    }


class _FakeGhostTouchFactory:
  def __init__(self) -> None:
    self.created: List[Dict[str, Any]] = []
    self.prepare_calls: List[Dict[str, Any]] = []
    self.start_calls: List[Dict[str, Any]] = []
    self.cancel_calls: List[Dict[str, Any]] = []
    self.entered = 0
    self.exited = 0
    self.ensure_home_calls = 0
    self.prepare_error: Exception | None = None
    self.start_error: Exception | None = None
    self.cancel_error: Exception | None = None

  def __call__(self, **kwargs: Any) -> _FakeGhostTouchSession:
    self.created.append(dict(kwargs))
    return _FakeGhostTouchSession(self, dict(kwargs))


def _make_backend(
  *,
  file_transfer_control: Optional[_FakeFileTransferControl] = None,
  plate_handler: Optional[BTXHT200] = None,
  ghost_touch_factory: Optional[_FakeGhostTouchFactory] = None,
  temporary_protocol_prefix: str = (
    BTXGeminiX2ElectroporationBackend.DEFAULT_TEMPORARY_PROTOCOL_PREFIX
  ),
) -> BTXGeminiX2ElectroporationBackend:
  driver = BTXGeminiX2Driver(
    file_transfer_control=cast(Optional[FileTransferControl], file_transfer_control),
    ghost_touch_factory=cast(Optional[GhostTouchFactory], ghost_touch_factory),
  )
  return BTXGeminiX2ElectroporationBackend(
    driver=driver,
    plate_handler=plate_handler,
    temporary_protocol_prefix=temporary_protocol_prefix,
  )


async def _setup_backend(backend: BTXGeminiX2ElectroporationBackend) -> None:
  await backend.driver.setup()
  await backend._on_setup()


def _prepare_params(
  backend: BTXGeminiX2ElectroporationBackend,
  state: str,
) -> BTXGeminiX2ElectroporationBackend.PrepareRunParams:
  return backend.PrepareRunParams(plate_handler_reset_state=state)


class TestBTXGeminiX2Backend(unittest.IsolatedAsyncioTestCase):
  async def test_prepare_temporary_protocol_adds_protocol_and_arms_run_view(self):
    file_control = _FakeFileTransferControl()
    file_control.log_snapshots = [[r"\BTXDATA\2026-03\260309\100000.TXT"]]
    ghost_factory = _FakeGhostTouchFactory()
    backend = _make_backend(
      file_transfer_control=file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=0),
      ghost_touch_factory=ghost_factory,
    )
    protocol = ElectroporationProtocol(
      protocol_type="exponential",
      pulse_amplitude_volts=2300,
      gap_mm=2.0,
      resistance_ohms=200,
      capacitance_uf=25,
    )

    await _setup_backend(backend)
    prepared = await backend.prepare_temporary_protocol(
      protocol,
      plate_columns=3,
      backend_params=_prepare_params(
        backend,
        backend.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
      ),
    )

    self.assertTrue(prepared.protocol_name.startswith("!PLR_"))
    self.assertEqual(prepared.plate_columns, 3)
    self.assertEqual(prepared.baseline_log_paths, (r"\BTXDATA\2026-03\260309\100000.TXT",))
    self.assertEqual(file_control.add_calls[0]["protocol"], protocol)
    self.assertEqual(ghost_factory.prepare_calls[0]["protocol_name"], prepared.protocol_name)
    self.assertEqual(prepared.prepare_result.prepared_state, "protocol_run_view")
    self.assertEqual(
      prepared.prepare_result.device_prepare["plate_handler_reset_state"],
      backend.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
    )
    self.assertEqual(prepared.prepare_result.device_prepare["assumed_plate_handler_pulse_count"], 2)
    self.assertEqual(
      prepared.prepare_result.device_prepare["assumed_plate_handler_column_adjust"], 0
    )

  async def test_prepare_temporary_protocol_cleans_up_if_ui_prepare_fails(self):
    file_control = _FakeFileTransferControl()
    file_control.log_snapshots = [[r"\BTXDATA\2026-03\260309\100000.TXT"]]
    ghost_factory = _FakeGhostTouchFactory()
    ghost_factory.prepare_error = RuntimeError("prepare failed")
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
    )
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)
    with self.assertRaisesRegex(RuntimeError, "prepare failed"):
      await backend.prepare_temporary_protocol(protocol)

    self.assertEqual(len(file_control.delete_calls), 1)
    self.assertTrue(file_control.delete_calls[0]["missing_ok"])

  async def test_prepare_temporary_protocol_uses_backend_default_prefix_when_not_overridden(self):
    file_control = _FakeFileTransferControl()
    ghost_factory = _FakeGhostTouchFactory()
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
      temporary_protocol_prefix="!TMP",
    )
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)
    prepared = await backend.prepare_temporary_protocol(protocol)

    self.assertEqual(prepared.prefix, "!TMP")
    self.assertTrue(prepared.protocol_name.startswith("!TMP_"))

  async def test_prepare_temporary_protocol_requires_explicit_plate_handler_reset_state(self):
    file_control = _FakeFileTransferControl()
    backend = _make_backend(
      file_transfer_control=file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=0),
    )
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)

    with self.assertRaisesRegex(ValueError, "requires an explicit plate_handler_reset_state"):
      await backend.prepare_temporary_protocol(protocol, plate_columns=3)

  async def test_prepare_temporary_protocol_requires_assumed_plate_handler_manual_state(self):
    file_control = _FakeFileTransferControl()
    backend = _make_backend(
      file_transfer_control=file_control,
      plate_handler=BTXHT200(),
    )
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)

    with self.assertRaisesRegex(
      ValueError,
      "Missing: assumed_pulse_count, assumed_column_adjust",
    ):
      await backend.prepare_temporary_protocol(
        protocol,
        plate_columns=3,
        backend_params=_prepare_params(
          backend,
          backend.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
        ),
      )

  async def test_prepare_temporary_protocol_rejects_plate_handler_reset_state_without_columns(self):
    file_control = _FakeFileTransferControl()
    backend = _make_backend(file_transfer_control=file_control)
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)

    with self.assertRaisesRegex(ValueError, "only valid when plate_columns is set"):
      await backend.prepare_temporary_protocol(
        protocol,
        backend_params=_prepare_params(
          backend,
          backend.PLATE_HANDLER_RESET_STATE_RESET_CONFIRMED,
        ),
      )

  async def test_serialize_includes_plate_handler_backend_manual_state(self):
    backend = _make_backend(
      file_transfer_control=_FakeFileTransferControl(),
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=1),
    )

    serialized_handler = backend.serialize()["plate_handler"]
    self.assertEqual(serialized_handler["device"], "BTXHT200")
    self.assertEqual(serialized_handler["assumed_pulse_count"], 2)
    self.assertEqual(serialized_handler["assumed_column_adjust"], 1)

  async def test_start_prepared_run_verifies_runs_collects_log_and_cleans_up(self):
    file_control = _FakeFileTransferControl()
    file_control.log_snapshots = [
      [r"\BTXDATA\2026-03\260309\100000.TXT", r"\BTXDATA\2026-03\260309\100100.TXT"],
    ]
    file_control.log_contents[r"\BTXDATA\2026-03\260309\100100.TXT"] = "\n".join(
      [
        "Protocol Name: !PLR_123456789",
        "Protocol Result: Complete",
        "Status: 0x00000000.00000000 - No error.",
      ]
    )
    ghost_factory = _FakeGhostTouchFactory()
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
    )
    prepared = PreparedElectroporationRun(
      protocol_name="!PLR_123456789",
      protocol=ElectroporationProtocol(
        protocol_type="exponential",
        pulse_amplitude_volts=2300,
        gap_mm=2.0,
        resistance_ohms=200,
        capacitance_uf=25,
      ),
      plate_columns=3,
      prefix="!PLR",
      prepared_at_utc="2026-03-09T10:00:00+00:00",
      baseline_log_paths=(r"\BTXDATA\2026-03\260309\100000.TXT",),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state="protocol_run_view",
        protocol_setup={},
        device_prepare={"prepared_verification": {"state": "protocol_run_view"}},
      ),
    )

    await _setup_backend(backend)
    file_control.protocols.append("!PLR_123456789")
    result = await backend.start_prepared_run(prepared.as_dict(), max_run_seconds=100.0)

    self.assertEqual(result.prepared_run.protocol_name, prepared.protocol_name)
    self.assertEqual(ghost_factory.start_calls[0]["protocol_name"], prepared.protocol_name)
    self.assertEqual(result.log_capture.matched_log_path, r"\BTXDATA\2026-03\260309\100100.TXT")
    self.assertTrue(result.cleanup.deleted)
    self.assertIsNone(result.cleanup.error)

  async def test_start_prepared_run_leaves_protocol_for_explicit_cancel_if_verification_fails(self):
    file_control = _FakeFileTransferControl()
    ghost_factory = _FakeGhostTouchFactory()
    ghost_factory.start_error = RuntimeError("verification failed")
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
    )
    prepared = PreparedElectroporationRun(
      protocol_name="!PLR_123456789",
      protocol=ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        duration_us=1000,
      ),
      plate_columns=None,
      prefix="!PLR",
      prepared_at_utc="2026-03-09T10:00:00+00:00",
      baseline_log_paths=(),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state=None,
        protocol_setup={},
        device_prepare={},
      ),
    )

    await _setup_backend(backend)
    file_control.protocols.append("!PLR_123456789")
    with self.assertRaisesRegex(RuntimeError, "verification failed"):
      await backend.start_prepared_run(prepared.as_dict())

    self.assertEqual(file_control.delete_calls, [])
    self.assertIn(prepared.protocol_name, file_control.protocols)

  async def test_cancel_prepared_run_homes_and_deletes(self):
    file_control = _FakeFileTransferControl()
    ghost_factory = _FakeGhostTouchFactory()
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
    )
    prepared = PreparedElectroporationRun(
      protocol_name="!PLR_123456789",
      protocol=ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        duration_us=1000,
      ),
      plate_columns=None,
      prefix="!PLR",
      prepared_at_utc="2026-03-09T10:00:00+00:00",
      baseline_log_paths=(),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state=None,
        protocol_setup={},
        device_prepare={},
      ),
    )

    await _setup_backend(backend)
    file_control.protocols.append("!PLR_123456789")
    result = await backend.cancel_prepared_run(prepared.as_dict())

    self.assertTrue(result.cleanup.deleted)
    self.assertEqual(ghost_factory.cancel_calls[0]["home_after"], True)
    self.assertNotIn(prepared.protocol_name, file_control.protocols)

  async def test_setup_rejects_unsafe_default_temp_prefix(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!AAA", "CD"]
    backend = _make_backend(file_transfer_control=file_control)

    with self.assertRaisesRegex(RuntimeError, r"Temporary protocol prefix '!PLR' is not safe"):
      await _setup_backend(backend)

    self.assertEqual(file_control.setup_calls, 1)
    self.assertEqual(file_control.stop_calls, 0)

  async def test_setup_allows_existing_reserved_temp_prefix_for_resume(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!PLR_OLD", "CD"]
    backend = _make_backend(file_transfer_control=file_control)

    await _setup_backend(backend)

  async def test_prepare_temporary_protocol_rejects_unsafe_custom_prefix(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!PLX_OLD", "CD"]
    backend = _make_backend(file_transfer_control=file_control)
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)

    with self.assertRaisesRegex(RuntimeError, r"Temporary protocol prefix '!PLY' is not available"):
      await backend.prepare_temporary_protocol(protocol, prefix="!PLY")

  async def test_prepare_temporary_protocol_rejects_existing_reserved_temp_prefix(self):
    file_control = _FakeFileTransferControl()
    file_control.protocols = ["!PLR_OLD", "CD"]
    backend = _make_backend(file_transfer_control=file_control)
    protocol = ElectroporationProtocol(
      protocol_type="square",
      pulse_amplitude_volts=250,
      gap_mm=1.0,
      duration_us=1000,
    )

    await _setup_backend(backend)

    with self.assertRaisesRegex(RuntimeError, r"not available.*!PLR_OLD"):
      await backend.prepare_temporary_protocol(protocol)

  async def test_cancel_prepared_run_retries_delete_after_forcing_home(self):
    file_control = _FakeFileTransferControl()
    file_control.delete_failures_before_success = 1
    ghost_factory = _FakeGhostTouchFactory()
    backend = _make_backend(
      file_transfer_control=file_control,
      ghost_touch_factory=ghost_factory,
    )
    prepared = PreparedElectroporationRun(
      protocol_name="!PLR_123456789",
      protocol=ElectroporationProtocol(
        protocol_type="square",
        pulse_amplitude_volts=250,
        gap_mm=1.0,
        duration_us=1000,
      ),
      plate_columns=None,
      prefix="!PLR",
      prepared_at_utc="2026-03-09T10:00:00+00:00",
      baseline_log_paths=(),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state=None,
        protocol_setup={},
        device_prepare={},
      ),
    )

    await _setup_backend(backend)
    file_control.protocols.append("!PLR_123456789")
    result = await backend.cancel_prepared_run(prepared.as_dict())

    self.assertTrue(result.cleanup.retry_used)
    self.assertEqual(ghost_factory.ensure_home_calls, 1)
    self.assertEqual(len(file_control.delete_calls), 2)

  async def test_get_device_info(self):
    file_control = _FakeFileTransferControl()
    backend = _make_backend(
      file_transfer_control=file_control,
      plate_handler=BTXHT200(assumed_pulse_count=2, assumed_column_adjust=1),
    )

    await _setup_backend(backend)
    info = await backend.get_device_info()

    self.assertEqual(info["model"], "Gemini X2")
    self.assertEqual(info["serial_number"], "1135421")
    self.assertEqual(info["protocol_count"], 2)
    self.assertTrue(info["supports_prepared_temporary_runs"])
    self.assertTrue(info["supports_serialized_prepared_runs"])
    self.assertFalse(info["supports_stored_protocol_runs"])
    self.assertTrue(info["supports_plate_handler_reset_state"])
    self.assertIn("reset_confirmed", info["plate_handler_reset_states"])
    self.assertEqual(info["plate_handler"]["model"], "HT-200")
    self.assertEqual(info["plate_handler"]["assumed_pulse_count"], 2)
    self.assertEqual(info["plate_handler"]["assumed_column_adjust"], 1)
    self.assertEqual(info["temporary_protocol_prefix"], "!PLR")

  async def test_requires_setup_before_use(self):
    backend = _make_backend(file_transfer_control=_FakeFileTransferControl())

    with self.assertRaisesRegex(RuntimeError, r"Call setup\(\) before"):
      await backend.prepare_temporary_protocol(
        ElectroporationProtocol(
          protocol_type="square",
          pulse_amplitude_volts=250,
          gap_mm=1.0,
          duration_us=1000,
        )
      )

  async def test_temp_name_rejects_overlong_prefix(self):
    backend = _make_backend(file_transfer_control=_FakeFileTransferControl())

    with self.assertRaisesRegex(ValueError, "exceeds the 15-byte"):
      backend._make_temporary_protocol_name("!PLR_TOO_LONG")
