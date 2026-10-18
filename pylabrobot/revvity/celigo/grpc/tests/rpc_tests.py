"""Real mutual-TLS loopback RPCs exercising jobs and complete image responses."""

import asyncio
import contextlib
import hashlib
import io
import threading
from dataclasses import replace

import grpc
import numpy as np
import pytest
import pytest_asyncio
from PIL import Image

from pylabrobot.revvity.celigo.grpc.cli import check
from pylabrobot.revvity.celigo.grpc.client import PrototypeClient
from pylabrobot.revvity.celigo.grpc.model import MAX_FRAME_BYTES, SimulationSpec, make_frame
from pylabrobot.revvity.celigo.grpc.security import create_certificates
from pylabrobot.revvity.celigo.grpc.server import SimulatorServer
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc


@pytest_asyncio.fixture
async def system(tmp_path):
  certs = create_certificates(tmp_path / "certificates")
  server = SimulatorServer(certs)
  await server.start()
  try:
    async with PrototypeClient(server.endpoint, certs) as client:
      initialization = await client.create_initialization("test-initialize")
      await initialization.start()
      assert (await initialization.wait()).outcome == pb.OUTCOME_SUCCEEDED
      yield server, client
  finally:
    await server.stop()


@pytest.mark.asyncio
async def test_remote_check_is_read_only(system):
  server, client = system
  before = set(server.simulator.jobs)
  report = await check(server.endpoint, client.certs, client.device_id)
  assert report["device"]["device_type"] == "CeligoSimulator"
  assert report["exercised"] is False
  assert set(server.simulator.jobs) == before


@pytest.mark.asyncio
async def test_remote_exercise_rejects_non_simulator_before_creating_jobs(system, monkeypatch):
  server, client = system
  info = await client.device_info()
  info.device_type = "Celigo"

  async def device_info(_self):
    return info

  monkeypatch.setattr(PrototypeClient, "device_info", device_info)
  before = set(server.simulator.jobs)
  with pytest.raises(ValueError, match="only accepts a CeligoSimulator"):
    await check(server.endpoint, client.certs, client.device_id, exercise_simulator=True)
  assert set(server.simulator.jobs) == before


@pytest.mark.asyncio
async def test_jobs_deduplication_conflicts_progress_and_reconnect(system):
  server, client = system
  spec = SimulationSpec(4, 64, 64, 16, 100)
  job = await client.create("first", spec)
  assert (await job.status()).state == pb.JOB_STATE_READY
  assert not await job.list_frames()
  assert (await client.create("first", spec)).identifier == job.identifier
  with pytest.raises(grpc.aio.AioRpcError) as mismatch:
    await client.create("first", replace(spec, frame_count=2))
  assert mismatch.value.code() == grpc.StatusCode.INVALID_ARGUMENT
  conflict = await client.create("second", spec)
  async with job.updates() as updates:
    assert (await updates.get()).state == pb.JOB_STATE_READY
    await job.start()
    await job.start()
    with pytest.raises(grpc.aio.AioRpcError) as busy:
      await conflict.start()
    assert busy.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    seen = []
    while True:
      snapshot = await asyncio.wait_for(updates.get(), 5)
      seen.append(snapshot)
      if snapshot.completed:
        break
    assert any(s.state == pb.JOB_STATE_RUNNING for s in seen)
    assert [s.revision for s in seen] == sorted({s.revision for s in seen})
  first = (await job.list_frames(limit=1))[0]
  frame = await job.get_frame(first.frame_id)
  await client.disconnect()
  await server.simulator.jobs[job.identifier].task
  await client.connect()
  recovered = await client.recover("first")
  assert recovered.identifier == job.identifier
  assert (await recovered.wait()).outcome == pb.OUTCOME_SUCCEEDED
  assert await recovered.get_frame(first.frame_id) == frame
  page = await recovered.list_frames(after_sequence=1, limit=2)
  assert [f.sequence for f in page] == [2, 3]
  assert [f.sequence for f in await recovered.list_frames(after_sequence=3)] == [4]
  with pytest.raises(grpc.aio.AioRpcError) as finished:
    await recovered.start()
  assert finished.value.code() == grpc.StatusCode.FAILED_PRECONDITION
  assert (await recovered.cancel()).outcome == pb.OUTCOME_SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_retains_frames_and_releases_instrument(system):
  _, client = system
  job = await client.create("cancel", SimulationSpec(20, 64, 64, 16, 50))
  async with job.updates() as updates:
    await job.start()
    while (await asyncio.wait_for(updates.get(), 5)).completed < 1:
      pass
    await job.cancel()
    final = await job.wait()
  assert final.outcome == pb.OUTCOME_CANCELLED
  assert final.cancellation_requested
  assert 1 <= final.completed < final.total
  info = (await job.list_frames())[0]
  assert (await job.get_frame(info.frame_id)).sha256 == info.sha256
  next_job = await client.create("next", SimulationSpec(1, 16, 16, 8, 0))
  await next_job.start()
  assert (await next_job.wait()).outcome == pb.OUTCOME_SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_ready_job_never_captures(system):
  _, client = system
  job = await client.create("ready", SimulationSpec(1, 16, 16, 8, 0))
  assert (await job.cancel()).outcome == pb.OUTCOME_CANCELLED
  assert (await job.cancel()).completed == 0
  assert not await job.list_frames()
  with pytest.raises(grpc.aio.AioRpcError):
    await job.start()


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height,depth", [(256, 256, 8), (2048, 2048, 16), (4096, 2048, 16)])
async def test_complete_png_pixels_and_concurrent_control(system, width, height, depth):
  _, client = system
  job = await client.create("image", SimulationSpec(1, width, height, depth, 0))
  await job.start()
  assert (await job.wait()).outcome == pb.OUTCOME_SUCCEEDED
  info = (await job.list_frames())[0]
  transfer = asyncio.create_task(job.get_frame(info.frame_id))
  assert (await asyncio.wait_for(job.status(), 5)).completed == 1
  frame = await asyncio.wait_for(transfer, 10)
  assert frame.width == width and frame.height == height
  assert frame.pixel_format == f"Mono{depth}" and frame.encoding == "PNG"
  assert len(frame.image) <= MAX_FRAME_BYTES
  assert hashlib.sha256(frame.image).hexdigest() == info.sha256 == frame.sha256
  dtype = np.uint16 if depth == 16 else np.uint8
  expected = np.random.default_rng(1).integers(0, 2**depth, size=(height, width), dtype=dtype)
  np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(frame.image))), expected)
  assert await job.get_frame(info.frame_id) == frame


@pytest.mark.asyncio
async def test_preview_and_status_streams(system):
  _, client = system
  async with client.status_updates() as status, client.previews() as previews:
    assert (await status.get()).state == pb.OPERATIONAL_STATE_READY
    job = await client.create("preview", SimulationSpec(1, 64, 64, 8, 100), preview=True)
    await job.start()
    busy = await asyncio.wait_for(status.get(), 5)
    assert busy.state == pb.OPERATIONAL_STATE_BUSY and busy.active_job_id == job.identifier
    frame = await asyncio.wait_for(previews.get(), 5)
    assert frame.representation == "Preview"
    assert await job.get_frame(frame.frame_id) == frame
    await job.wait()
    assert (await asyncio.wait_for(status.get(), 5)).state == pb.OPERATIONAL_STATE_READY
  async with client.previews() as previews:
    assert await previews.get() == frame  # Subscribing replays the latest; no new acquisition.


@pytest.mark.asyncio
async def test_limits_validation_and_missing_results(system):
  server, client = system
  for spec in (SimulationSpec(1, 4096, 4096), SimulationSpec(0), SimulationSpec(bit_depth=12)):
    with pytest.raises(grpc.aio.AioRpcError) as invalid:
      await client.create("invalid", spec)
    assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT
  with pytest.raises(grpc.aio.AioRpcError) as capacity:
    await client.create("too-many", SimulationSpec(64, 4096, 2048, 16, 0))
  assert capacity.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
  assert await server.simulator.store.call(server.simulator.store.reserved_bytes) == 0
  with pytest.raises(grpc.aio.AioRpcError):
    await client.create("bad-preview", SimulationSpec(2), preview=True)
  with pytest.raises(grpc.aio.AioRpcError) as missing:
    await client.recover("missing")
  assert missing.value.code() == grpc.StatusCode.NOT_FOUND
  job = await client.create("one", SimulationSpec(1, 16, 16, 8, 0))
  with pytest.raises(grpc.aio.AioRpcError) as missing:
    await job.get_frame("missing")
  assert missing.value.code() == grpc.StatusCode.NOT_FOUND
  with pytest.raises(grpc.aio.AioRpcError) as invalid:
    await job.list_frames(limit=0)
  assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_tls_rejects_untrusted_missing_and_plaintext_clients(system, tmp_path):
  _, client = system
  rogue = create_certificates(tmp_path / "rogue")
  rogue_client = PrototypeClient(client.endpoint, rogue, timeout=1)
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
        await rpc.DeviceServiceStub(channel).GetDevice(client.request, timeout=1)
    finally:
      await channel.close()
  assert (client.certs.directory / "client-key.pem").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_capture_failure_releases_device(system, monkeypatch):
  _, client = system

  def fail(*_args):
    raise RuntimeError("synthetic camera failed")

  monkeypatch.setattr("pylabrobot.revvity.celigo.grpc.simulator.make_frame", fail)
  job = await client.create("failed", SimulationSpec(1, 16, 16, 8, 0))
  await job.start()
  final = await job.wait()
  assert final.outcome == pb.OUTCOME_FAILED and "synthetic camera failed" in final.error
  assert final.completed == 0
  assert (await client.status()).state == pb.OPERATIONAL_STATE_READY


@pytest.mark.asyncio
async def test_cancel_waits_for_inflight_frame(system, monkeypatch):
  _, client = system
  entered, release = threading.Event(), threading.Event()

  def capture(*args):
    entered.set()
    if not release.wait(10):
      raise RuntimeError("test capture was not released")
    return make_frame(*args)

  monkeypatch.setattr("pylabrobot.revvity.celigo.grpc.simulator.make_frame", capture)
  job = await client.create("inflight", SimulationSpec(3, 16, 16, 8, 0))
  try:
    await job.start()
    assert await asyncio.to_thread(entered.wait, 5)
    status = await job.cancel()
    assert (status.state, status.phase, status.outcome) == (
      pb.JOB_STATE_RUNNING,
      "Stopping",
      pb.OUTCOME_PENDING,
    )
    assert status.completed == 0
  finally:
    release.set()
  final = await job.wait()
  assert (final.outcome, final.completed) == (pb.OUTCOME_CANCELLED, 1)
  assert len(await job.list_frames()) == 1


@pytest.mark.asyncio
async def test_stream_deadline_does_not_cancel_job(system):
  server, client = system
  job = await client.create("deadline", SimulationSpec(3, 16, 16, 8, 100))
  await job.start()
  stream = client.jobs.WatchJob(job.ref, timeout=0.05)
  with pytest.raises(grpc.aio.AioRpcError) as expired:
    async for _snapshot in stream:
      pass
  assert expired.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
  assert (await job.wait()).outcome == pb.OUTCOME_SUCCEEDED
  assert not server.simulator.jobs[job.identifier].stop_requested.is_set()


@pytest.mark.asyncio
async def test_receive_limit_does_not_destroy_retained_image(system):
  _, client = system
  job = await client.create("large", SimulationSpec(1, 2048, 2048, 16, 0))
  await job.start()
  await job.wait()
  info = (await job.list_frames())[0]
  async with grpc.aio.secure_channel(
    client.endpoint,
    client.certs.client_credentials(),
    options=(("grpc.max_receive_message_length", 1024 * 1024),),
  ) as channel:
    with pytest.raises(grpc.aio.AioRpcError) as too_big:
      await rpc.ImageServiceStub(channel).GetFrame(
        pb.GetFrameRequest(job=job.ref, frame_id=info.frame_id),
        timeout=5,
      )
    assert too_big.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
  assert (await job.get_frame(info.frame_id)).sha256 == info.sha256


@pytest.mark.asyncio
async def test_devices_have_independent_ownership_and_identity(system):
  _, client = system
  second = SimulatorServer(client.certs, device_id="celigo-sim-02")
  await second.start()
  try:
    async with PrototypeClient(second.endpoint, client.certs, "celigo-sim-02") as other:
      initialization = await other.create_initialization("test-initialize")
      await initialization.start()
      await initialization.wait()
      one = await client.create("same-request", SimulationSpec(2, 16, 16, 8, 100))
      two = await other.create("same-request", SimulationSpec(2, 16, 16, 8, 100))
      await asyncio.gather(one.start(), two.start())
      assert one.identifier != two.identifier
      with pytest.raises(grpc.aio.AioRpcError) as wrong_device:
        await client.jobs.GetJob(two.ref, timeout=2)
      assert wrong_device.value.code() == grpc.StatusCode.NOT_FOUND
      await client.disconnect()
      assert (await two.wait()).outcome == pb.OUTCOME_SUCCEEDED
  finally:
    await second.stop()


@pytest.mark.asyncio
async def test_interrupted_response_retries_complete_retained_image(system):
  _, client = system
  job = await client.create("interrupted", SimulationSpec(1, 2048, 2048, 16, 0))
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
  proxy_client = PrototypeClient(endpoint, client.certs, timeout=3)
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
