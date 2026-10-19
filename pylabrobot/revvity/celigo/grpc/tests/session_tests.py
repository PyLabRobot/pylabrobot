"""Instrument-session behavior over loopback RPCs, with mocked PLR driver methods."""

import asyncio
import hashlib
import io
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import grpc
import numpy as np
from PIL import Image

from pylabrobot.revvity.celigo.celigo import ControllerStatus
from pylabrobot.revvity.celigo.config import DigitalIOConfig
from pylabrobot.revvity.celigo.grpc.client import (
  CeligoClient,
  unpack_acquisition_metadata,
)
from pylabrobot.revvity.celigo.grpc.job_specification import JobSpecification
from pylabrobot.revvity.celigo.grpc.job_store import JobStore
from pylabrobot.revvity.celigo.grpc.server import CeligoServer
from pylabrobot.revvity.celigo.grpc.session import CeligoSession
from pylabrobot.revvity.celigo.grpc.tests.helpers import (
  block_calls,
  capture,
  initialize,
  make_config,
  make_driver,
)
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.rpc.errors import BusyError, StateError, StorageError
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


@asynccontextmanager
async def hardware_system(tmp_path, config):
  driver = make_driver()
  factory = Mock(return_value=driver)

  session = CeligoSession(
    tmp_path / "data", "celigo-test", config, factory, tmp_path / "hardware.lock"
  )
  server = CeligoServer(session)
  await server.start()
  try:
    async with CeligoClient(server.endpoint, device_id="celigo-test") as client:
      yield server, client, driver, factory
  finally:
    driver.stop.side_effect = None
    await server.stop()


class SessionTests(unittest.IsolatedAsyncioTestCase):
  async def test_door_commands_require_initialization_and_use_public_driver_methods(self) -> None:
    """Door RPCs use the owned driver without creating another session or a job."""
    with TemporaryDirectory() as directory:
      async with hardware_system(Path(directory), make_config()) as hardware:
        server, client, driver, factory = hardware
        for opening in (True, False):
          with self.subTest(opening=opening):
            with self.assertRaises(grpc.aio.AioRpcError) as error:
              if opening:
                await client.open_door()
              else:
                await client.close_door(sample_x_mm=63.5, sample_y_mm=43)
            self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        factory.assert_not_called()
        await initialize(client)
        await client.open_door()
        await client.close_door(sample_x_mm=63.5, sample_y_mm=43)
        driver.open_drawer.assert_awaited_once_with()
        driver.close_drawer_to_sample_mm.assert_awaited_once_with(63.5, 43)
        factory.assert_called_once_with()
        self.assertEqual(len(server.session.jobs), 1)
        self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_READY)

  async def test_invalid_door_requests_do_not_move_or_fault(self) -> None:
    """Reject missing, invalid, and misaddressed targets before hardware access."""
    with TemporaryDirectory() as directory:
      async with hardware_system(Path(directory), make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        for settings in (
          {},
          {"sample_x_mm": 63.5},
          {"sample_y_mm": 43},
          {"sample_x_mm": float("nan"), "sample_y_mm": 43},
          {"sample_x_mm": 63.5, "sample_y_mm": float("inf")},
          {"sample_x_mm": 100000, "sample_y_mm": 43},
        ):
          with self.subTest(settings=settings):
            with self.assertRaises(grpc.aio.AioRpcError) as error:
              await client.operations.CloseDoor(
                pb.CloseDoorRequest(device_id=client.device_id, **settings), timeout=5
              )
            self.assertEqual(error.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await client.operations.OpenDoor(device_pb.DeviceRequest(device_id="other"), timeout=5)
        self.assertEqual(error.exception.code(), grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await client.operations.CloseDoor(
            pb.CloseDoorRequest(device_id="other", sample_x_mm=63.5, sample_y_mm=43), timeout=5
          )
        self.assertEqual(error.exception.code(), grpc.StatusCode.NOT_FOUND)
        driver.open_drawer.assert_not_awaited()
        driver.close_drawer_to_sample_mm.assert_not_awaited()
        self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_READY)

  async def test_door_motion_keeps_ownership_after_rpc_deadline(self) -> None:
    """A lost RPC response cannot allow acquisition or another door command to overlap."""
    with TemporaryDirectory() as directory:
      async with hardware_system(Path(directory), make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        job = await capture(client)
        with block_calls(driver.open_drawer) as (entered, release):
          call = client.operations.OpenDoor(client.request, timeout=0.2)
          await asyncio.wait_for(entered.wait(), 5)
          with self.assertRaises(grpc.aio.AioRpcError) as expired:
            await call
          self.assertEqual(expired.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
          async with client.watch_status() as updates:
            busy = await asyncio.wait_for(updates.get(), 5)
            self.assertEqual(busy.state, device_pb.OPERATIONAL_STATE_BUSY)
            self.assertEqual(busy.active_job_id, "")
            with self.assertRaises(grpc.aio.AioRpcError) as conflict:
              await job.start()
            self.assertEqual(conflict.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            with self.assertRaises(grpc.aio.AioRpcError) as conflict:
              await client.close_door(sample_x_mm=63.5, sample_y_mm=43)
            self.assertEqual(conflict.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            driver.close_drawer_to_sample_mm.assert_not_awaited()
            driver.stop.assert_not_awaited()
            release.set()
            ready = await asyncio.wait_for(updates.get(), 5)
            self.assertEqual(ready.state, device_pb.OPERATIONAL_STATE_READY)
        await job.start()
        self.assertEqual((await job.wait()).outcome, job_pb.OUTCOME_SUCCEEDED)

  async def test_door_commands_reject_active_acquisition(self) -> None:
    """Drawer motion cannot interrupt a job that already owns the instrument."""
    with TemporaryDirectory() as directory:
      async with hardware_system(Path(directory), make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        with block_calls(driver.acquire_scan_position) as (entered, release):
          job = await capture(client)
          await job.start()
          await asyncio.wait_for(entered.wait(), 5)
          for opening in (True, False):
            with self.subTest(opening=opening):
              with self.assertRaises(grpc.aio.AioRpcError) as error:
                if opening:
                  await client.open_door()
                else:
                  await client.close_door(sample_x_mm=63.5, sample_y_mm=43)
              self.assertEqual(error.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
          driver.open_drawer.assert_not_awaited()
          driver.close_drawer_to_sample_mm.assert_not_awaited()
          release.set()
          self.assertEqual((await job.wait()).outcome, job_pb.OUTCOME_SUCCEEDED)

  async def test_failed_door_motion_closes_driver_and_faults_session(self) -> None:
    """Failed drawer motion blocks subsequent work until service recovery."""
    with TemporaryDirectory() as directory:
      async with hardware_system(Path(directory), make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        driver.close_drawer_to_sample_mm.side_effect = RuntimeError("motion failed")
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await client.close_door(sample_x_mm=63.5, sample_y_mm=43)
        self.assertEqual(error.exception.code(), grpc.StatusCode.INTERNAL)
        driver.stop.assert_awaited_once_with()
        self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_FAULTED)
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await client.open_door()
        self.assertEqual(error.exception.code(), grpc.StatusCode.UNAVAILABLE)
        driver.open_drawer.assert_not_awaited()

  async def test_startup_inspection_and_job_creation_never_construct_driver(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, driver, factory = hardware
        assert (await client.get_device_info()).device_type == "Celigo"
        assert (await client.get_configuration()).channels == ["brightfield"]
        assert (await client.get_status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
        job = await capture(client)
        assert (await capture(client)).identifier == job.identifier
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await job.start()
        assert error.exception.code() == grpc.StatusCode.FAILED_PRECONDITION
        await job.cancel()
        await client.create_initialization("ready-initialize")
        assert factory.call_count == 0 and driver.method_calls == []

  async def test_generic_controller_interlock_does_not_block_brightfield(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, driver, _ = hardware
        driver.request_controller_status.return_value = ControllerStatus(4, 0)
        await initialize(client)
        job = await capture(client)
        await job.start()
        assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
        assert len(await job.list_frames()) == 1
        driver.request_digital_input.assert_not_awaited()

  async def test_invalid_settings_rejected_before_hardware(self) -> None:
    invalid_settings: list[dict[str, float]] = [
      {"sample_x_mm": float("nan")},
      {"sample_x_mm": 100000},
      {"z_mm": 100},
      {"exposure_ms": 0},
      {"exposure_ms": 1001},
      {"gain": 17},
    ]
    for changes in invalid_settings:
      with self.subTest(changes=changes):
        with TemporaryDirectory() as temporary_directory:
          tmp_path = Path(temporary_directory)
          async with hardware_system(tmp_path, make_config()) as hardware:
            _, client, _, factory = hardware
            with self.assertRaises(grpc.aio.AioRpcError) as error:
              await capture(client, **changes)
            assert error.exception.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert factory.call_count == 0

  async def test_absent_fields_and_stale_configuration_rejected(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, _, factory = hardware
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await client.operations.CreateBrightfieldCapture(
            pb.BrightfieldRequest(
              device_id=client.device_id,
              request_id="absent",
              configuration_id=client.configuration_id,
              settings=pb.BrightfieldSettings(sample_x_mm=63.5),
            )
          )
        assert error.exception.code() == grpc.StatusCode.INVALID_ARGUMENT
        client.configuration_id = "stale"
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await capture(client)
        assert error.exception.code() == grpc.StatusCode.FAILED_PRECONDITION
        assert factory.call_count == 0

  async def test_capture_uses_public_plr_sequence_and_preserves_pixels_and_actual_metadata(
    self,
  ) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, driver, factory = hardware
        await initialize(client)
        job = await capture(client)
        await job.start()
        assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
        assert factory.call_count == 1 and [call[0] for call in driver.method_calls] == [
          "setup",
          "request_controller_status",
          "request_controller_status",
          "move_to_scan_block",
          "request_controller_status",
          "acquire_scan_position",
        ]
        info = (await job.list_frames())[0]
        frame = await job.get_frame(info.frame_id)
        assert hashlib.sha256(frame.image).hexdigest() == info.sha256
        np.testing.assert_array_equal(
          np.array(Image.open(io.BytesIO(frame.image))),
          driver.acquire_scan_position.return_value.frame.to_numpy(),
        )
        settings = driver.acquire_scan_position.call_args.kwargs
        assert settings["channel"] == "brightfield"
        assert settings["autofocus"] is None and settings["machine_auto_exposure"] is False
        assert settings["settled_stage_position_mm"] == driver.move_to_scan_block.return_value
        metadata = unpack_acquisition_metadata(frame)
        assert metadata.sample_x_mm == 63.5
        assert metadata.stage_x_mm != 63.5
        assert metadata.exposure_ms == 1.25 and metadata.gain == 1.1
        self.assertAlmostEqual(metadata.z_mm, 2.601)
        assert metadata.configuration_id == client.configuration_id
        assert frame.captured_at.seconds == 1700000000 and frame.captured_at.nanos == 125000000
        again = await client.create_initialization("reinitialize")
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await again.start()
        assert error.exception.code() == grpc.StatusCode.FAILED_PRECONDITION

  async def test_cancel_keeps_ownership_until_capture_and_cleanup_finish(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          with block_calls(driver.stop) as (stop_entered, stop_release):
            job = await capture(client)
            other = await capture(client, "other")
            await job.start()
            await asyncio.wait_for(capture_entered.wait(), 5)
            await job.cancel()
            assert (await job.get_status()).state == job_pb.JOB_STATE_RUNNING
            capture_release.set()
            await asyncio.wait_for(stop_entered.wait(), 5)
            assert (await job.get_status()).state == job_pb.JOB_STATE_RUNNING
            with self.assertRaises(grpc.aio.AioRpcError) as busy:
              await other.start()
            assert busy.exception.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            stop_release.set()
            final = await job.wait()
            assert final.outcome == job_pb.OUTCOME_CANCELLED and final.completed == 1
            assert (await client.get_status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED

  async def test_client_disconnection_does_not_cancel_capture(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        _, client, driver, _ = hardware
        await initialize(client)
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          job = await capture(client)
          await job.start()
          await asyncio.wait_for(capture_entered.wait(), 5)
          await client.disconnect()
          capture_release.set()
          await client.connect()
          recovered = await client.recover_job("capture")
          assert recovered.identifier == job.identifier
          assert (await recovered.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
          driver.acquire_scan_position.assert_awaited_once()

  async def test_hardware_failure_cleans_up_and_faults_without_retry(self) -> None:
    for failure in ["controller", "camera", "cleanup"]:
      with self.subTest(failure=failure):
        with TemporaryDirectory() as temporary_directory:
          tmp_path = Path(temporary_directory)
          async with hardware_system(tmp_path, make_config()) as hardware:
            _, client, driver, _ = hardware
            await initialize(client)
            job = await capture(client)
            if failure == "controller":
              driver.request_controller_status.return_value = ControllerStatus(2, 0)
            else:
              driver.acquire_scan_position.side_effect = RuntimeError("camera failure")
            if failure == "cleanup":
              driver.stop.side_effect = RuntimeError("cleanup failure")
            await job.start()
            final = await job.wait()
            assert final.outcome == job_pb.OUTCOME_FAILED
            assert (await client.get_status()).state == device_pb.OPERATIONAL_STATE_FAULTED
            driver.stop.assert_awaited_once()
            if failure == "controller":
              driver.move_to_scan_block.assert_not_awaited()
              driver.acquire_scan_position.assert_not_awaited()
            else:
              driver.acquire_scan_position.assert_awaited_once()
            with self.assertRaises(grpc.aio.AioRpcError) as error:
              await client.create_initialization("retry")
            assert error.exception.code() == grpc.StatusCode.UNAVAILABLE

  async def test_start_commit_failure_never_constructs_driver(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        server, client, _, factory = hardware
        job = await client.create_initialization("initialize")

        def fail(*_args):
          raise StorageError("disk full")

        with patch.object(server.session.store, "save_job_status", fail):
          with self.assertRaises(grpc.aio.AioRpcError) as error:
            await job.start()
          assert error.exception.code() == grpc.StatusCode.UNAVAILABLE
          assert factory.call_count == 0

  async def test_controller_fault_after_setup_prevents_ready_and_capture(self) -> None:
    for flags in [2, 8]:
      with self.subTest(flags=flags):
        with TemporaryDirectory() as temporary_directory:
          tmp_path = Path(temporary_directory)
          async with hardware_system(tmp_path, make_config()) as hardware:
            _, client, driver, _ = hardware
            driver.request_controller_status.return_value = ControllerStatus(flags, 0)
            job = await client.create_initialization("controller-fault")
            await job.start()
            final = await job.wait()
            assert final.outcome == job_pb.OUTCOME_FAILED
            assert "controller fault" in final.error
            assert [call[0] for call in driver.method_calls] == [
              "setup",
              "request_controller_status",
              "stop",
            ]
            assert (await client.get_status()).state == device_pb.OPERATIONAL_STATE_FAULTED
            assert not await job.list_frames()

  async def test_configured_door_input_blocks_imaging(self) -> None:
    for inverted in [False, True]:
      with self.subTest(inverted=inverted):
        for phase in ["initialization", "before_move", "after_move"]:
          with self.subTest(phase=phase):
            with TemporaryDirectory() as temporary_directory:
              tmp_path = Path(temporary_directory)
              config = make_config()
              door = DigitalIOConfig(1000, "In", 5, inverted, True, "DoorInterlock")
              config = replace(
                config,
                hardware=replace(
                  config.hardware, io=replace(config.hardware.io, digital_ios=[door])
                ),
              )
              async with hardware_system(tmp_path, config) as (_, client, driver, _):
                driver.request_digital_input.return_value = not inverted
                if phase == "initialization":
                  driver.request_digital_input.return_value = inverted
                  job = await client.create_initialization("open-door")
                else:
                  await initialize(client)
                  job = await capture(client)
                  if phase == "before_move":
                    driver.request_digital_input.return_value = inverted
                  else:
                    driver.request_digital_input.side_effect = [not inverted, inverted]
                await job.start()
                final = await job.wait()
                assert final.outcome == job_pb.OUTCOME_FAILED
                assert "configured door interlock is open" in final.error
                driver.acquire_scan_position.assert_not_awaited()
                assert driver.move_to_scan_block.await_count == (1 if phase == "after_move" else 0)
                driver.stop.assert_awaited_once()
                driver.request_digital_input.assert_called_with(5)

  async def test_unexpected_executor_cancel_waits_for_driver_and_cannot_report_success(
    self,
  ) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with hardware_system(tmp_path, make_config()) as hardware:
        server, client, driver, _ = hardware
        await initialize(client)
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          job = await capture(client)
          await job.start()
          await asyncio.wait_for(capture_entered.wait(), 5)
          execution = server.session.jobs[job.identifier]
          execution.task.cancel()
          await asyncio.sleep(0)
          assert (await job.get_status()).state == job_pb.JOB_STATE_RUNNING
          assert server.session.active_job is execution
          capture_release.set()
          assert (await job.wait()).outcome == job_pb.OUTCOME_INTERRUPTED
          driver.stop.assert_awaited_once()

  async def test_restart_restores_images_and_ready_intent_without_constructing_driver(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      config = make_config()
      async with hardware_system(tmp_path, config) as (_, client, _, _):
        await initialize(client)
        job = await capture(client)
        ready = await capture(client, "ready")
        await job.start()
        await job.wait()
        frame_info = (await job.list_frames())[0]
        frame = await job.get_frame(frame_info.frame_id)
      async with hardware_system(tmp_path, config) as (_, client, driver, factory):
        assert factory.call_count == 0 and driver.method_calls == []
        recovered = await client.recover_job("capture")
        assert await recovered.get_frame(frame_info.frame_id) == frame
        inert = await client.recover_job("ready")
        assert inert.identifier == ready.identifier
        assert (await inert.get_status()).state == job_pb.JOB_STATE_READY
        with self.assertRaises(grpc.aio.AioRpcError):
          await inert.start()
        assert factory.call_count == 0

  async def test_service_lock_and_store_codec_prevent_cross_backend_execution(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      config = make_config()
      async with hardware_system(tmp_path, config):
        other = CeligoSession(
          tmp_path / "other", "celigo-test", config, make_driver, tmp_path / "hardware.lock"
        )
        with self.assertRaises(BusyError):
          await other.open()
        await other.stop()
      store = JobStore(
        tmp_path / "data",
        "celigo-test",
        decode_spec=JobSpecification.decode,
        spec_format="unsupported.v1",
      )
      with self.assertRaisesRegex(StateError, "specification format"):
        await store.open()
