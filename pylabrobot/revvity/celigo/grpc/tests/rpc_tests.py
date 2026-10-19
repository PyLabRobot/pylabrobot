"""Loopback RPCs exercising jobs and complete image responses."""

import asyncio
import contextlib
import hashlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import grpc
import numpy as np
from PIL import Image

from pylabrobot.revvity.celigo.grpc.client import CeligoClient, unpack_acquisition_metadata
from pylabrobot.revvity.celigo.grpc.limits import MAX_FRAME_BYTES
from pylabrobot.revvity.celigo.grpc.tests.helpers import (
  block_calls,
  capture,
  initialize,
  make_config,
  make_driver,
  make_server,
)
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import images_pb2_grpc as image_rpc
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


@contextlib.asynccontextmanager
async def rpc_system(tmp_path, geometry=(64, 48, 16)):
  width, height, depth = geometry
  driver = make_driver(width, height, depth)
  server, _ = make_server(tmp_path / "data", config=make_config(width, height), driver=driver)
  await server.start()
  try:
    async with CeligoClient(server.endpoint, device_id="celigo-test") as client:
      await initialize(client)
      yield server, client, driver
  finally:
    await server.stop()


class RpcTests(unittest.IsolatedAsyncioTestCase):
  async def test_auto_exposure_preview_retains_actual_camera_settings(self) -> None:
    """Auto-exposure reaches the driver and reports the measured rather than requested value."""
    with TemporaryDirectory() as directory:
      async with rpc_system(Path(directory)) as (_, client, driver):
        self.assertTrue((await client.get_configuration()).auto_exposure_supported)
        job = await capture(client, "auto-exposure", exposure_ms=3, machine_auto_exposure=True)
        await job.start()
        self.assertEqual((await job.wait()).outcome, job_pb.OUTCOME_SUCCEEDED)
        self.assertTrue(driver.acquire_scan_position.await_args.kwargs["machine_auto_exposure"])
        image = await job.get_frame((await job.list_frames())[0].frame_id)
        metadata = unpack_acquisition_metadata(image)
        self.assertTrue(metadata.machine_auto_exposure)
        self.assertEqual(
          metadata.exposure_ms, driver.acquire_scan_position.return_value.frame.exposure_ms
        )
        self.assertNotEqual(metadata.exposure_ms, 3)
        with self.assertRaises(grpc.aio.AioRpcError) as conflict:
          await capture(client, "auto-exposure", exposure_ms=3, machine_auto_exposure=False)
        self.assertEqual(conflict.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

  async def test_jobs_deduplication_conflicts_progress_and_reconnect(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        server, client, driver = system
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          job = await capture(client, "first")
          assert (await job.get_status()).state == job_pb.JOB_STATE_READY
          assert not await job.list_frames()
          assert (await capture(client, "first")).identifier == job.identifier
          with self.assertRaises(grpc.aio.AioRpcError) as mismatch:
            await capture(client, "first", exposure_ms=2)
          assert mismatch.exception.code() == grpc.StatusCode.INVALID_ARGUMENT
          conflict = await capture(client, "second")
          async with job.watch_status() as updates:
            assert (await updates.get()).state == job_pb.JOB_STATE_READY
            await job.start()
            await job.start()
            with self.assertRaises(grpc.aio.AioRpcError) as busy:
              await conflict.start()
            assert busy.exception.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            running = await asyncio.wait_for(updates.get(), 5)
            assert running.state == job_pb.JOB_STATE_RUNNING
            capture_release.set()
            seen = [running]
            while seen[-1].state != job_pb.JOB_STATE_FINISHED:
              seen.append(await asyncio.wait_for(updates.get(), 5))
            assert [s.revision for s in seen] == sorted({s.revision for s in seen})
          first = (await job.list_frames(limit=1))[0]
          frame = await job.get_frame(first.frame_id)
          await client.disconnect()
          await server.session.jobs[job.identifier].task
          await client.connect()
          recovered = await client.recover_job("first")
          assert recovered.identifier == job.identifier
          assert (await recovered.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
          assert await recovered.get_frame(first.frame_id) == frame
          assert await recovered.list_frames(after_sequence=1) == []
          with self.assertRaises(grpc.aio.AioRpcError) as finished:
            await recovered.start()
          assert finished.exception.code() == grpc.StatusCode.FAILED_PRECONDITION
          assert (await recovered.cancel()).outcome == job_pb.OUTCOME_SUCCEEDED

  async def test_cancel_ready_job_never_captures(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        _, client, driver = system
        job = await capture(client, "ready")
        assert (await job.cancel()).outcome == job_pb.OUTCOME_CANCELLED
        assert (await job.cancel()).completed == 0
        assert not await job.list_frames()
        with self.assertRaises(grpc.aio.AioRpcError):
          await job.start()

  async def test_complete_png_pixels_and_concurrent_control(self) -> None:
    for geometry in [(256, 256, 8), (2048, 2048, 16), (4096, 2048, 16)]:
      with self.subTest(geometry=geometry):
        with TemporaryDirectory() as temporary_directory:
          tmp_path = Path(temporary_directory)
          async with rpc_system(tmp_path, geometry) as system:
            width, height, depth = geometry
            _, client, driver = system
            job = await capture(client, "image")
            await job.start()
            assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
            info = (await job.list_frames())[0]
            transfer = asyncio.create_task(job.get_frame(info.frame_id))
            assert (await asyncio.wait_for(job.get_status(), 5)).completed == 1
            frame = await asyncio.wait_for(transfer, 10)
            assert frame.width == width and frame.height == height
            assert frame.pixel_format == f"Mono{depth}" and frame.encoding == "PNG"
            assert len(frame.image) <= MAX_FRAME_BYTES
            assert hashlib.sha256(frame.image).hexdigest() == info.sha256 == frame.sha256
            np.testing.assert_array_equal(
              np.array(Image.open(io.BytesIO(frame.image))),
              driver.acquire_scan_position.return_value.frame.to_numpy(),
            )
            assert await job.get_frame(info.frame_id) == frame

  async def test_preview_and_status_streams(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        _, client, driver = system
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          async with client.watch_status() as status, client.watch_previews() as previews:
            assert (await status.get()).state == device_pb.OPERATIONAL_STATE_READY
            job = await capture(client, "preview")
            await job.start()
            busy = await asyncio.wait_for(status.get(), 5)
            assert (
              busy.state == device_pb.OPERATIONAL_STATE_BUSY
              and busy.active_job_id == job.identifier
            )
            capture_release.set()
            frame = await asyncio.wait_for(previews.get(), 5)
            assert frame.representation == "Preview"
            assert await job.get_frame(frame.frame_id) == frame
            await job.wait()
            assert (
              await asyncio.wait_for(status.get(), 5)
            ).state == device_pb.OPERATIONAL_STATE_READY
          async with client.watch_previews() as previews:
            assert (
              await previews.get() == frame
            )  # Subscribing replays the latest; no new acquisition.

  async def test_missing_results_and_invalid_pagination(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        server, client, driver = system
        with self.assertRaises(grpc.aio.AioRpcError) as missing:
          await client.recover_job("missing")
        assert missing.exception.code() == grpc.StatusCode.NOT_FOUND
        job = await capture(client, "one")
        with self.assertRaises(grpc.aio.AioRpcError) as missing:
          await job.get_frame("missing")
        assert missing.exception.code() == grpc.StatusCode.NOT_FOUND
        with self.assertRaises(grpc.aio.AioRpcError) as invalid:
          await job.list_frames(limit=0)
        assert invalid.exception.code() == grpc.StatusCode.INVALID_ARGUMENT

  async def test_stream_deadline_does_not_cancel_job(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        server, client, driver = system
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          job = await capture(client, "deadline")
          await job.start()
          stream = client.jobs.stub.WatchJob(job.ref, timeout=0.05)
          with self.assertRaises(grpc.aio.AioRpcError) as expired:
            async for _snapshot in stream:
              pass
          assert expired.exception.code() == grpc.StatusCode.DEADLINE_EXCEEDED
          capture_release.set()
          assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
          assert not server.session.jobs[job.identifier].stop_requested.is_set()

  async def test_receive_limit_does_not_destroy_retained_image(self) -> None:
    geometry = (2048, 2048, 16)
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path, geometry) as system:
        _, client, driver = system
        job = await capture(client, "large")
        await job.start()
        await job.wait()
        info = (await job.list_frames())[0]
        async with grpc.aio.insecure_channel(
          client.endpoint,
          options=(("grpc.max_receive_message_length", 1024 * 1024),),
        ) as channel:
          with self.assertRaises(grpc.aio.AioRpcError) as too_big:
            await image_rpc.ImageServiceStub(channel).GetFrame(
              image_pb.GetFrameRequest(job=job.ref, frame_id=info.frame_id),
              timeout=5,
            )
          assert too_big.exception.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert (await job.get_frame(info.frame_id)).sha256 == info.sha256

  async def test_devices_have_independent_ownership_and_identity(self) -> None:
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path) as system:
        _, client, driver = system
        second, other_driver = make_server(tmp_path / "second", device_id="celigo-test-02")
        with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
          with block_calls(other_driver.acquire_scan_position) as (other_entered, other_release):
            await second.start()
            try:
              async with CeligoClient(second.endpoint, "celigo-test-02") as other:
                await initialize(other)
                one = await capture(client, "same-request")
                two = await capture(other, "same-request")
                await asyncio.gather(one.start(), two.start())
                await asyncio.wait_for(
                  asyncio.gather(capture_entered.wait(), other_entered.wait()), 5
                )
                assert one.identifier != two.identifier
                with self.assertRaises(grpc.aio.AioRpcError) as wrong_device:
                  await client.jobs.stub.GetJob(two.ref, timeout=2)
                assert wrong_device.exception.code() == grpc.StatusCode.NOT_FOUND
                await client.disconnect()
                other_release.set()
                assert (await two.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
            finally:
              capture_release.set()
              other_release.set()
              await second.stop()

  async def test_interrupted_response_retries_complete_retained_image(self) -> None:
    geometry = (2048, 2048, 16)
    with TemporaryDirectory() as temporary_directory:
      tmp_path = Path(temporary_directory)
      async with rpc_system(tmp_path, geometry) as system:
        _, client, driver = system
        job = await capture(client, "interrupted")
        await job.start()
        await job.wait()
        info = (await job.list_frames())[0]
        cut_after = None
        cut = asyncio.Event()
        handlers = set()
        host, port = client.endpoint.rsplit(":", 1)

        async def proxy(reader, writer):
          nonlocal cut_after
          task = asyncio.current_task()
          assert task is not None
          handlers.add(task)
          remote_reader, remote_writer = await asyncio.open_connection(host, int(port))

          async def relay(source, destination, downstream):
            nonlocal cut_after
            try:
              while data := await source.read(65536):
                if downstream and cut_after is not None:
                  data = data[:cut_after]
                  cut_after -= len(data)
                destination.write(data)
                await destination.drain()
                if downstream and cut_after == 0:
                  cut.set()
                  break
            finally:
              destination.close()

          try:
            await asyncio.gather(
              relay(reader, remote_writer, False), relay(remote_reader, writer, True)
            )
          except (ConnectionError, asyncio.CancelledError):
            pass
          finally:
            writer.close()
            remote_writer.close()
            handlers.discard(task)

        proxy_server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        endpoint = f"127.0.0.1:{proxy_server.sockets[0].getsockname()[1]}"
        proxy_client = CeligoClient(endpoint, device_id=client.device_id, timeout=3)
        try:
          await proxy_client.connect()
          remote = await proxy_client.recover_job("interrupted")
          cut_after = 100000
          interrupted = asyncio.create_task(remote.get_frame(info.frame_id))
          await asyncio.wait_for(cut.wait(), 5)
          with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await interrupted
          assert failure.exception.code() in (
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
          )
          await proxy_client.disconnect()
          cut_after = None
          await proxy_client.connect()
          recovered = await proxy_client.recover_job("interrupted")
          frame = await recovered.get_frame(info.frame_id)
          assert hashlib.sha256(frame.image).hexdigest() == info.sha256
          assert len(frame.image) == info.byte_length
          assert (await recovered.get_status()).completed == 1
        finally:
          with contextlib.suppress(ConnectionError):
            await proxy_client.disconnect()
          proxy_server.close()
          await proxy_server.wait_closed()
          remaining = list(handlers)
          for task in remaining:
            task.cancel()
          await asyncio.gather(*remaining, return_exceptions=True)
