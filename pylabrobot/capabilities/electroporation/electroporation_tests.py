"""Tests for Electroporation."""

import unittest
from typing import Any, Dict, Mapping, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.electroporation.backend import ElectroporationBackend
from pylabrobot.capabilities.electroporation.chatterbox import ElectroporationChatterboxBackend
from pylabrobot.capabilities.electroporation.electroporation import Electroporation
from pylabrobot.capabilities.electroporation.standard import (
  ElectroporationCancellationDetails,
  ElectroporationCancellationResult,
  ElectroporationCleanup,
  ElectroporationExecutionDetails,
  ElectroporationLogCapture,
  ElectroporationPreparationDetails,
  ElectroporationProtocol,
  ElectroporationRunResult,
  PreparedElectroporationRun,
)


class _Params(BackendParams):
  pass


def _square_protocol() -> ElectroporationProtocol:
  return ElectroporationProtocol(
    protocol_type="square",
    pulse_amplitude_volts=250,
    gap_mm=1.0,
    duration_us=1000,
  )


def _prepared_run(protocol: Optional[ElectroporationProtocol] = None) -> PreparedElectroporationRun:
  protocol = protocol or _square_protocol()
  return PreparedElectroporationRun(
    protocol_name="!PLR_123456789",
    protocol=protocol,
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


class _RecordingElectroporationBackend(ElectroporationBackend):
  def __init__(self) -> None:
    self.setup_params: Optional[BackendParams] = None
    self.stop_calls = 0
    self.calls: list[dict[str, Any]] = []

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    self.setup_params = backend_params

  async def _on_stop(self):
    self.stop_calls += 1

  async def prepare_temporary_protocol(
    self,
    protocol: ElectroporationProtocol,
    plate_columns: Optional[int] = None,
    prefix: Optional[str] = None,
    backend_params: Optional[BackendParams] = None,
  ) -> PreparedElectroporationRun:
    self.calls.append(
      {
        "method": "prepare",
        "protocol": protocol,
        "plate_columns": plate_columns,
        "prefix": prefix,
        "backend_params": backend_params,
      }
    )
    return PreparedElectroporationRun(
      protocol_name="!PLR_123456789",
      protocol=protocol,
      plate_columns=plate_columns,
      prefix=prefix or "!PLR",
      prepared_at_utc="2026-03-09T10:00:00+00:00",
      baseline_log_paths=(r"\BTXDATA\baseline.TXT",),
      prepare_result=ElectroporationPreparationDetails(
        prepared_state="protocol_run_view",
        protocol_setup={},
        device_prepare={},
      ),
    )

  async def start_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
    max_run_seconds: float = 420.0,
  ) -> ElectroporationRunResult:
    prepared = (
      prepared_run
      if isinstance(prepared_run, PreparedElectroporationRun)
      else PreparedElectroporationRun.from_dict(prepared_run)
    )
    self.calls.append(
      {
        "method": "start",
        "prepared_run": prepared,
        "home_after": home_after,
        "max_run_seconds": max_run_seconds,
      }
    )
    return ElectroporationRunResult(
      prepared_run=prepared,
      started_at_utc="2026-03-09T10:01:00+00:00",
      completed_at_utc="2026-03-09T10:02:00+00:00",
      rsi_result=ElectroporationExecutionDetails(
        verification_state="protocol_run_view",
        completed_state="protocol_finish",
        final_state="main_menu",
        device_run={},
      ),
      log_capture=ElectroporationLogCapture(
        matched_log_path=None,
        summary={},
        details={},
      ),
      cleanup=ElectroporationCleanup(
        deleted=True,
        retry_used=False,
        error=None,
        details={},
      ),
    )

  async def cancel_prepared_run(
    self,
    prepared_run: Union[PreparedElectroporationRun, Mapping[str, Any]],
    home_after: bool = True,
  ) -> ElectroporationCancellationResult:
    prepared = (
      prepared_run
      if isinstance(prepared_run, PreparedElectroporationRun)
      else PreparedElectroporationRun.from_dict(prepared_run)
    )
    self.calls.append(
      {
        "method": "cancel",
        "prepared_run": prepared,
        "home_after": home_after,
      }
    )
    return ElectroporationCancellationResult(
      prepared_run=prepared,
      cancelled_at_utc="2026-03-09T10:01:00+00:00",
      rsi_result=ElectroporationCancellationDetails(
        final_state="main_menu",
        device_cancel={},
      ),
      cleanup=ElectroporationCleanup(
        deleted=True,
        retry_used=False,
        error=None,
        details={},
      ),
    )

  async def get_device_info(self) -> Dict[str, Any]:
    self.calls.append({"method": "get_device_info"})
    return {"model": "test electroporator"}


class TestElectroporation(unittest.IsolatedAsyncioTestCase):
  async def test_prepare_temporary_protocol_forwards_to_backend(self):
    backend = _RecordingElectroporationBackend()
    cap = Electroporation(backend=backend)
    setup_params = _Params()
    prepare_params = _Params()
    protocol = _square_protocol()

    await cap._on_setup(backend_params=setup_params)
    prepared = await cap.prepare_temporary_protocol(
      protocol=protocol,
      plate_columns=3,
      prefix="!TMP",
      backend_params=prepare_params,
    )

    self.assertIs(backend.setup_params, setup_params)
    self.assertEqual(prepared.protocol_name, "!PLR_123456789")
    self.assertEqual(prepared.protocol, protocol)
    self.assertEqual(prepared.plate_columns, 3)
    self.assertEqual(prepared.prefix, "!TMP")
    self.assertEqual(
      backend.calls[0],
      {
        "method": "prepare",
        "protocol": protocol,
        "plate_columns": 3,
        "prefix": "!TMP",
        "backend_params": prepare_params,
      },
    )

  async def test_start_and_cancel_prepared_run_forward_to_backend(self):
    backend = _RecordingElectroporationBackend()
    cap = Electroporation(backend=backend)
    prepared = _prepared_run()

    await cap._on_setup()
    started = await cap.start_prepared_run(
      prepared.as_dict(),
      home_after=False,
      max_run_seconds=12.0,
    )
    cancelled = await cap.cancel_prepared_run(prepared, home_after=False)

    self.assertEqual(started.prepared_run, prepared)
    self.assertEqual(cancelled.prepared_run, prepared)
    self.assertEqual(backend.calls[0]["method"], "start")
    self.assertEqual(backend.calls[0]["prepared_run"], prepared)
    self.assertEqual(backend.calls[0]["home_after"], False)
    self.assertEqual(backend.calls[0]["max_run_seconds"], 12.0)
    self.assertEqual(backend.calls[1]["method"], "cancel")
    self.assertEqual(backend.calls[1]["prepared_run"], prepared)
    self.assertEqual(backend.calls[1]["home_after"], False)

  async def test_methods_require_setup(self):
    backend = _RecordingElectroporationBackend()
    cap = Electroporation(backend=backend)

    with self.assertRaisesRegex(RuntimeError, "capability has not been set up"):
      await cap.prepare_temporary_protocol(_square_protocol())

  async def test_get_device_info_forwards_and_stop_resets_setup(self):
    backend = _RecordingElectroporationBackend()
    cap = Electroporation(backend=backend)

    await cap._on_setup()
    info = await cap.get_device_info()
    await cap._on_stop()

    self.assertEqual(info, {"model": "test electroporator"})
    self.assertFalse(cap.setup_finished)
    self.assertEqual(backend.stop_calls, 1)

  async def test_chatterbox_prepares_and_starts_serialized_run(self):
    backend = ElectroporationChatterboxBackend()
    cap = Electroporation(backend=backend)
    protocol = _square_protocol()

    await cap._on_setup()
    prepared = await cap.prepare_temporary_protocol(protocol, plate_columns=3)
    started = await cap.start_prepared_run(prepared.as_dict(), home_after=False)

    self.assertEqual(prepared.protocol_name, "!PLR_000001")
    self.assertEqual(prepared.protocol, protocol)
    self.assertEqual(prepared.plate_columns, 3)
    self.assertEqual(started.prepared_run, prepared)
    self.assertEqual(started.rsi_result.verification_state, "protocol_run_view")
    self.assertEqual(started.rsi_result.completed_state, "protocol_finish")
    self.assertEqual(started.rsi_result.final_state, "protocol_finish")
    self.assertEqual(started.log_capture.summary["protocol"], prepared.protocol_name)
    self.assertTrue(started.cleanup.deleted)
    self.assertNotIn(prepared.protocol_name, backend.prepared_runs)

  async def test_chatterbox_cancels_prepared_run_and_reports_info(self):
    backend = ElectroporationChatterboxBackend(temporary_protocol_prefix="!SIM")
    cap = Electroporation(backend=backend)

    await cap._on_setup()
    info = await cap.get_device_info()
    prepared = await cap.prepare_temporary_protocol(_square_protocol())
    cancelled = await cap.cancel_prepared_run(prepared, home_after=True)

    self.assertEqual(info["backend"], "ElectroporationChatterboxBackend")
    self.assertEqual(info["temporary_protocol_prefix"], "!SIM")
    self.assertEqual(prepared.protocol_name, "!SIM_000001")
    self.assertEqual(cancelled.prepared_run, prepared)
    self.assertEqual(cancelled.rsi_result.final_state, "main_menu")
    self.assertTrue(cancelled.cleanup.deleted)
    self.assertNotIn(prepared.protocol_name, backend.prepared_runs)


if __name__ == "__main__":
  unittest.main()
