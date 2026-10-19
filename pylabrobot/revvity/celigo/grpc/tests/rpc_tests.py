"""Real mutual-TLS loopback RPCs exercising jobs and complete image responses."""

import asyncio
import contextlib
import hashlib
import io

import grpc
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image

from pylabrobot.revvity.celigo.grpc.client import CeligoClient
from pylabrobot.revvity.celigo.grpc.model import MAX_FRAME_BYTES
from pylabrobot.revvity.celigo.grpc.tests.helpers import (
  block_calls,
  capture,
  initialize,
  make_config,
  make_driver,
  make_server,
)
from pylabrobot.rpc.security import create_certificates
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import device_pb2_grpc as device_rpc
from pylabrobot.rpc.v1 import images_pb2 as image_pb
from pylabrobot.rpc.v1 import images_pb2_grpc as image_rpc
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


@pytest.fixture
def geometry():
  return 64, 48, 16


@pytest_asyncio.fixture
async def system(tmp_path, geometry):
  width, height, depth = geometry
  certs = create_certificates(tmp_path / "certificates")
  driver = make_driver(width, height, depth)
  server, _ = make_server(
    tmp_path / "data", certs, config=make_config(width, height), driver=driver
  )
  await server.start()
  try:
    async with CeligoClient(server.endpoint, certs, device_id="celigo-test") as client:
      await initialize(client)
      yield server, client, driver
  finally:
    await server.stop()


@pytest.mark.asyncio
async def test_jobs_deduplication_conflicts_progress_and_reconnect(system):
  server, client, driver = system
  with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
    job = await capture(client, "first")
    assert (await job.status()).state == job_pb.JOB_STATE_READY
    assert not await job.list_frames()
    assert (await capture(client, "first")).identifier == job.identifier
    with pytest.raises(grpc.aio.AioRpcError) as mismatch:
      await capture(client, "first", exposure_ms=2)
    assert mismatch.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    conflict = await capture(client, "second")
    async with job.updates() as updates:
      assert (await updates.get()).state == job_pb.JOB_STATE_READY
      await job.start()
      await job.start()
      with pytest.raises(grpc.aio.AioRpcError) as busy:
        await conflict.start()
      assert busy.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
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
    await server.core.jobs[job.identifier].task
    await client.connect()
    recovered = await client.recover("first")
    assert recovered.identifier == job.identifier
    assert (await recovered.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
    assert await recovered.get_frame(first.frame_id) == frame
    assert await recovered.list_frames(after_sequence=1) == []
    with pytest.raises(grpc.aio.AioRpcError) as finished:
      await recovered.start()
    assert finished.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert (await recovered.cancel()).outcome == job_pb.OUTCOME_SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_ready_job_never_captures(system):
  _, client, driver = system
  job = await capture(client, "ready")
  assert (await job.cancel()).outcome == job_pb.OUTCOME_CANCELLED
  assert (await job.cancel()).completed == 0
  assert not await job.list_frames()
  with pytest.raises(grpc.aio.AioRpcError):
    await job.start()


@pytest.mark.asyncio
@pytest.mark.parametrize("geometry", [(256, 256, 8), (2048, 2048, 16), (4096, 2048, 16)])
async def test_complete_png_pixels_and_concurrent_control(system, geometry):
  width, height, depth = geometry
  _, client, driver = system
  job = await capture(client, "image")
  await job.start()
  assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
  info = (await job.list_frames())[0]
  transfer = asyncio.create_task(job.get_frame(info.frame_id))
  assert (await asyncio.wait_for(job.status(), 5)).completed == 1
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


@pytest.mark.asyncio
async def test_preview_and_status_streams(system):
  _, client, driver = system
  with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
    async with client.status_updates() as status, client.previews() as previews:
      assert (await status.get()).state == device_pb.OPERATIONAL_STATE_READY
      job = await capture(client, "preview")
      await job.start()
      busy = await asyncio.wait_for(status.get(), 5)
      assert busy.state == device_pb.OPERATIONAL_STATE_BUSY and busy.active_job_id == job.identifier
      capture_release.set()
      frame = await asyncio.wait_for(previews.get(), 5)
      assert frame.representation == "Preview"
      assert await job.get_frame(frame.frame_id) == frame
      await job.wait()
      assert (await asyncio.wait_for(status.get(), 5)).state == device_pb.OPERATIONAL_STATE_READY
    async with client.previews() as previews:
      assert await previews.get() == frame  # Subscribing replays the latest; no new acquisition.


@pytest.mark.asyncio
async def test_missing_results_and_invalid_pagination(system):
  server, client, driver = system
  with pytest.raises(grpc.aio.AioRpcError) as missing:
    await client.recover("missing")
  assert missing.value.code() == grpc.StatusCode.NOT_FOUND
  job = await capture(client, "one")
  with pytest.raises(grpc.aio.AioRpcError) as missing:
    await job.get_frame("missing")
  assert missing.value.code() == grpc.StatusCode.NOT_FOUND
  with pytest.raises(grpc.aio.AioRpcError) as invalid:
    await job.list_frames(limit=0)
  assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_tls_rejects_untrusted_missing_and_plaintext_clients(system, tmp_path):
  _, client, driver = system
  rogue = create_certificates(tmp_path / "rogue")
  rogue_client = CeligoClient(client.endpoint, rogue, device_id=client.device_id, timeout=1)
  with pytest.raises(grpc.aio.AioRpcError):
    await rogue_client.connect()  # Wrong server trust.
  (rogue.directory / "ca.pem").write_bytes(client.certs.read("ca.pem"))
  with pytest.raises(grpc.aio.AioRpcError):
    await rogue_client.connect()  # Right server trust, wrong client issuer.
  channels = [
    grpc.aio.insecure_channel(client.endpoint),
    grpc.aio.secure_channel(
      client.endpoint, grpc.ssl_channel_credentials(client.certs.read("ca.pem"))
    ),
  ]
  for channel in channels:
    try:
      with pytest.raises(grpc.aio.AioRpcError):
        await device_rpc.DeviceServiceStub(channel).GetDevice(client.request, timeout=1)
    finally:
      await channel.close()
  assert (client.certs.directory / "client-key.pem").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_stream_deadline_does_not_cancel_job(system):
  server, client, driver = system
  with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
    job = await capture(client, "deadline")
    await job.start()
    stream = client.jobs.stub.WatchJob(job.ref, timeout=0.05)
    with pytest.raises(grpc.aio.AioRpcError) as expired:
      async for _snapshot in stream:
        pass
    assert expired.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    capture_release.set()
    assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
    assert not server.core.jobs[job.identifier].stop_requested.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("geometry", [(2048, 2048, 16)])
async def test_receive_limit_does_not_destroy_retained_image(system):
  _, client, driver = system
  job = await capture(client, "large")
  await job.start()
  await job.wait()
  info = (await job.list_frames())[0]
  async with grpc.aio.secure_channel(
    client.endpoint,
    client.certs.client_credentials(),
    options=(("grpc.max_receive_message_length", 1024 * 1024),),
  ) as channel:
    with pytest.raises(grpc.aio.AioRpcError) as too_big:
      await image_rpc.ImageServiceStub(channel).GetFrame(
        image_pb.GetFrameRequest(job=job.ref, frame_id=info.frame_id),
        timeout=5,
      )
    assert too_big.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
  assert (await job.get_frame(info.frame_id)).sha256 == info.sha256


@pytest.mark.asyncio
async def test_devices_have_independent_ownership_and_identity(system, tmp_path):
  _, client, driver = system
  second, other_driver = make_server(tmp_path / "second", client.certs, device_id="celigo-test-02")
  with block_calls(driver.acquire_scan_position) as (capture_entered, capture_release):
    with block_calls(other_driver.acquire_scan_position) as (other_entered, other_release):
      await second.start()
      try:
        async with CeligoClient(second.endpoint, client.certs, "celigo-test-02") as other:
          await initialize(other)
          one = await capture(client, "same-request")
          two = await capture(other, "same-request")
          await asyncio.gather(one.start(), two.start())
          await asyncio.wait_for(asyncio.gather(capture_entered.wait(), other_entered.wait()), 5)
          assert one.identifier != two.identifier
          with pytest.raises(grpc.aio.AioRpcError) as wrong_device:
            await client.jobs.stub.GetJob(two.ref, timeout=2)
          assert wrong_device.value.code() == grpc.StatusCode.NOT_FOUND
          await client.disconnect()
          other_release.set()
          assert (await two.wait()).outcome == job_pb.OUTCOME_SUCCEEDED
      finally:
        capture_release.set()
        other_release.set()
        await second.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("geometry", [(2048, 2048, 16)])
async def test_interrupted_response_retries_complete_retained_image(system):
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
      await asyncio.gather(relay(reader, remote_writer, False), relay(remote_reader, writer, True))
    except (ConnectionError, asyncio.CancelledError):
      pass
    finally:
      writer.close()
      remote_writer.close()
      handlers.discard(task)

  proxy_server = await asyncio.start_server(proxy, "127.0.0.1", 0)
  endpoint = f"127.0.0.1:{proxy_server.sockets[0].getsockname()[1]}"
  proxy_client = CeligoClient(endpoint, client.certs, device_id=client.device_id, timeout=3)
  try:
    await proxy_client.connect()
    remote = await proxy_client.recover("interrupted")
    cut_after = 100000
    interrupted = asyncio.create_task(remote.get_frame(info.frame_id))
    await asyncio.wait_for(cut.wait(), 5)
    with pytest.raises(grpc.aio.AioRpcError) as failure:
      await interrupted
    assert failure.value.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
    await proxy_client.disconnect()
    cut_after = None
    await proxy_client.connect()
    recovered = await proxy_client.recover("interrupted")
    frame = await recovered.get_frame(info.frame_id)
    assert hashlib.sha256(frame.image).hexdigest() == info.sha256
    assert len(frame.image) == info.byte_length
    assert (await recovered.status()).completed == 1
  finally:
    with contextlib.suppress(ConnectionError):
      await proxy_client.disconnect()
    proxy_server.close()
    await proxy_server.wait_closed()
    remaining = list(handlers)
    for task in remaining:
      task.cancel()
    await asyncio.gather(*remaining, return_exceptions=True)
