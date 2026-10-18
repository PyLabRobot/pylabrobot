"""Durable job behavior through real process death, write failure, and RPC loss."""

import asyncio
import hashlib
import sqlite3
import sys
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import grpc
import psutil
import pytest

from pylabrobot.revvity.celigo.grpc.client import PrototypeClient
from pylabrobot.revvity.celigo.grpc.model import SimulationSpec
from pylabrobot.revvity.celigo.grpc.server import SimulatorServer
from pylabrobot.revvity.celigo.grpc.storage import Store
from pylabrobot.rpc.errors import BusyError, StateError
from pylabrobot.rpc.security import create_certificates
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


async def initialize(client, request_id="initialize"):
  job = await client.create_initialization(request_id)
  await job.start()
  assert (await job.wait()).outcome == job_pb.OUTCOME_SUCCEEDED


@asynccontextmanager
async def local_server(directory, certs, **kwargs):
  server = SimulatorServer(certs, data_dir=directory, **kwargs)
  await server.start()
  try:
    async with PrototypeClient(server.endpoint, certs) as client:
      yield server, client
  finally:
    await server.stop()


# Fault injection exists only in this test process wrapper, never in the CLI.
CRASH_WRAPPER = """
import os
import sys
from pylabrobot.revvity.celigo.grpc.storage import Store
from pylabrobot.revvity.celigo.grpc.cli import main
point = sys.argv.pop(1)
def checkpoint(self, stage, status):
    if stage == point and status.request_id == 'crash-target':
        os._exit(87)
Store.checkpoint = checkpoint
main()
"""


@asynccontextmanager
async def process_server(directory, certs, crash_point="none"):
  log_path = directory.parent / "server.log"
  with log_path.open("ab") as output:
    process = await asyncio.create_subprocess_exec(
      sys.executable,
      "-c",
      CRASH_WRAPPER,
      crash_point,
      "serve",
      "--port",
      "0",
      "--certificates",
      str(certs.directory),
      "--data-dir",
      str(directory),
      stdout=asyncio.subprocess.PIPE,
      stderr=output,
    )
    try:
      import json

      ready = await asyncio.wait_for(process.stdout.readline(), 15)
      assert ready, log_path.read_text()[-4000:]
      async with PrototypeClient(json.loads(ready)["endpoint"], certs, timeout=3) as client:
        yield process, client
    finally:
      if process.returncode is None:
        process.terminate()
        try:
          await asyncio.wait_for(process.wait(), 10)
        except asyncio.TimeoutError:
          process.kill()
          await process.wait()


@pytest.mark.asyncio
async def test_completed_ready_cancelled_and_preview_survive_restart(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  spec = SimulationSpec(1, 64, 64, 8, 0)
  async with local_server(directory, certs) as (_, client):
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
    ready = await client.create("ready", spec)
    with pytest.raises(grpc.aio.AioRpcError) as uninitialized:
      await ready.start()
    assert uninitialized.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert (await ready.status()).state == job_pb.JOB_STATE_READY
    await initialize(client)
    completed = await client.create("completed", spec, preview=True)
    await completed.start()
    final = await completed.wait()
    info = (await completed.list_frames())[0]
    frame = await completed.get_frame(info.frame_id)
    cancelled = await client.create("cancelled", spec)
    await cancelled.cancel()
    ids = (ready.identifier, completed.identifier, cancelled.identifier)

  async with local_server(directory, certs) as (server, client):
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
    ready = await client.recover("ready")
    completed = await client.recover("completed")
    cancelled = await client.recover("cancelled")
    assert (ready.identifier, completed.identifier, cancelled.identifier) == ids
    assert (await ready.status()).state == job_pb.JOB_STATE_READY
    assert await completed.status() == final
    assert await completed.get_frame(info.frame_id) == frame
    assert (await cancelled.status()).outcome == job_pb.OUTCOME_CANCELLED
    assert (await client.create("completed", spec, preview=True)).identifier == completed.identifier
    async with client.previews() as updates:
      assert await asyncio.wait_for(updates.get(), 2) == frame
    assert all(job.task is None for job in server.simulator.jobs.values())
    with pytest.raises(grpc.aio.AioRpcError):
      await ready.start()
    await initialize(client, "initialize-again")
    await ready.start()
    assert (await ready.wait()).outcome == job_pb.OUTCOME_SUCCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "point,state,outcome,frames,released",
  [
    ("create.before_commit", None, None, 0, False),
    ("create.after_commit", job_pb.JOB_STATE_READY, job_pb.OUTCOME_PENDING, 0, False),
    ("start.before_commit", job_pb.JOB_STATE_READY, job_pb.OUTCOME_PENDING, 0, False),
    ("start.after_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 0, False),
    ("frame.before_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 0, False),
    ("frame.after_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 1, False),
    ("finish.before_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 1, False),
    ("finish.after_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_SUCCEEDED, 1, False),
    ("cancel.before_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 0, False),
    ("cancel.after_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_INTERRUPTED, 0, False),
    ("release.before_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_SUCCEEDED, 1, False),
    ("release.after_commit", job_pb.JOB_STATE_FINISHED, job_pb.OUTCOME_SUCCEEDED, 1, True),
  ],
)
async def test_process_crash_at_commit_boundaries(
  tmp_path, point, state, outcome, frames, released
):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  interval = 10000 if point.startswith("cancel.") else 0
  spec = SimulationSpec(1, 64, 64, 8, interval)
  async with process_server(directory, certs, point) as (process, client):
    await initialize(client)
    with pytest.raises(grpc.aio.AioRpcError):
      job = await client.create("crash-target", spec)
      await job.start()
      if point.startswith("cancel."):
        await job.cancel()
      else:
        await job.wait()
        if point.startswith("release."):
          await job.release_images()
    assert await asyncio.wait_for(process.wait(), 5) == 87

  async with process_server(directory, certs) as (_, client):
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
    if state is None:
      with pytest.raises(grpc.aio.AioRpcError) as absent:
        await client.recover("crash-target")
      assert absent.value.code() == grpc.StatusCode.NOT_FOUND
    else:
      job = await client.recover("crash-target")
      status = await job.status()
      assert (status.state, status.outcome, status.completed) == (state, outcome, frames)
      assert status.state_durable
      assert (await job.image_retention()).images_released == released
      assert status.cancellation_requested == (point == "cancel.after_commit")
      assert (await client.create("crash-target", spec)).identifier == job.identifier
      metadata = await job.list_frames()
      assert len(metadata) == frames
      for info in metadata:
        assert info.available != released
        if released:
          with pytest.raises(grpc.aio.AioRpcError) as unavailable:
            await job.get_frame(info.frame_id)
          assert unavailable.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        else:
          image = await job.get_frame(info.frame_id)
          assert hashlib.sha256(image.image).hexdigest() == info.sha256
      # Reopening again cannot resurrect an interrupted run or replay a frame.
      assert (await job.status()).completed == frames
  with sqlite3.connect(directory / "jobs.sqlite3") as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    for stored, last_transition in db.execute(
      "SELECT j.status, t.status FROM jobs j JOIN transitions t ON t.job_id=j.job_id "
      "WHERE t.revision=(SELECT MAX(revision) FROM transitions WHERE job_id=j.job_id)"
    ):
      assert stored == last_transition


@pytest.mark.asyncio
async def test_sigkill_during_acquisition_preserves_committed_frame(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  async with process_server(directory, certs) as (process, client):
    await initialize(client)
    job = await client.create("kill", SimulationSpec(20, 64, 64, 8, 300))
    async with job.updates() as updates:
      await job.start()
      while (await asyncio.wait_for(updates.get(), 5)).completed == 0:
        pass
      info = (await job.list_frames())[0]
      frame = await job.get_frame(info.frame_id)
      process.kill()
      await process.wait()
  async with process_server(directory, certs) as (_, client):
    recovered = await client.recover("kill")
    status = await recovered.status()
    assert status.outcome == job_pb.OUTCOME_INTERRUPTED and 1 <= status.completed < 20
    assert await recovered.get_frame(info.frame_id) == frame
    with pytest.raises(grpc.aio.AioRpcError):
      await recovered.start()


@pytest.mark.asyncio
async def test_storage_quota_release_and_deduplication(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  spec = SimulationSpec(1, 64, 64, 8, 0)
  async with local_server(directory, certs, storage_quota=spec.reservation()) as (_, client):
    await initialize(client)
    job = await client.create("retained", spec)
    with pytest.raises(grpc.aio.AioRpcError) as exhausted:
      await client.create("second", spec)
    assert exhausted.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    with pytest.raises(grpc.aio.AioRpcError):
      await job.release_images()  # Ready jobs cannot silently surrender their reservation.
    await job.start()
    await job.wait()
    info = (await job.list_frames())[0]
    with pytest.raises(grpc.aio.AioRpcError):
      await client.create("second", spec)
    released = await job.release_images()
    assert released.images_released
    assert (await job.status()).outcome == job_pb.OUTCOME_SUCCEEDED
    assert await job.release_images() == released
    assert not (await job.list_frames())[0].available
    assert (await client.create("retained", spec)).identifier == job.identifier
    await client.create("second", spec)
  async with local_server(directory, certs, storage_quota=spec.reservation()) as (_, client):
    job = await client.recover("retained")
    assert (await job.image_retention()).images_released
    with pytest.raises(grpc.aio.AioRpcError) as released:
      await job.get_frame(info.frame_id)
    assert released.value.code() == grpc.StatusCode.FAILED_PRECONDITION


@pytest.mark.asyncio
async def test_sqlite_full_rolls_back_image_and_progress(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  async with local_server(directory, certs) as (server, client):
    await initialize(client)
    job = await client.create("disk-full", SimulationSpec(2, 256, 256, 8, 400))
    async with job.updates() as updates:
      await job.start()
      while (await asyncio.wait_for(updates.get(), 5)).completed < 1:
        pass
      info = (await job.list_frames())[0]
      store = server.simulator.store

      def fill_database():
        pages = store.connection.execute("PRAGMA page_count").fetchone()[0]
        store.connection.execute(f"PRAGMA max_page_count={pages + 2}")

      await store.call(fill_database)
      status = await job.wait()
    assert status.outcome == job_pb.OUTCOME_FAILED and status.completed == 1
    assert "full" in status.error
    assert len(await job.list_frames()) == 1
    assert (await job.get_frame(info.frame_id)).sha256 == info.sha256
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_FAULTED
    with pytest.raises(grpc.aio.AioRpcError):
      await client.create("unsafe-after-fault", SimulationSpec(1, 16, 16, 8, 0))
  async with local_server(directory, certs) as (_, client):
    recovered = await client.recover("disk-full")
    result = await recovered.status()
    assert result.outcome in (job_pb.OUTCOME_FAILED, job_pb.OUTCOME_INTERRUPTED)
    assert result.completed == 1
    assert (await recovered.get_frame(info.frame_id)).sha256 == info.sha256


@pytest.mark.asyncio
async def test_admission_checks_free_space_and_record_capacity(tmp_path, monkeypatch):
  certs = create_certificates(tmp_path / "certs")
  async with local_server(tmp_path / "data", certs, max_jobs=2) as (server, client):
    await initialize(client)
    with monkeypatch.context() as patch:
      patch.setattr(
        "pylabrobot.revvity.celigo.grpc.storage.shutil.disk_usage",
        lambda _: SimpleNamespace(free=0),
      )
      with pytest.raises(grpc.aio.AioRpcError) as no_disk:
        await client.create("rejected", SimulationSpec(1, 16, 16, 8, 0))
      assert no_disk.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert len(server.simulator.jobs) == 1
    first = await client.create("last-record", SimulationSpec(1, 16, 16, 8, 0))
    await first.cancel()
    await first.release_images()
    with pytest.raises(grpc.aio.AioRpcError) as no_records:
      await client.create("too-many-records", SimulationSpec(1, 16, 16, 8, 0))
    assert no_records.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert (
      await client.create("last-record", SimulationSpec(1, 16, 16, 8, 0))
    ).identifier == first.identifier


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["create.after_commit", "start.after_commit"])
async def test_lost_rpc_during_commit_does_not_lose_or_repeat_execution(
  tmp_path, monkeypatch, point
):
  certs = create_certificates(tmp_path / "certs")
  async with local_server(tmp_path / "data", certs) as (server, client):
    await initialize(client)
    entered, release = threading.Event(), threading.Event()

    def hold(self, stage, status):
      if stage == point and status.request_id == "lost-response":
        entered.set()
        if not release.wait(5):
          raise RuntimeError("test did not release the commit")

    monkeypatch.setattr(Store, "checkpoint", hold)
    spec = SimulationSpec(1, 32, 32, 8, 0)
    job = None
    if point.startswith("start"):
      job = await client.create("lost-response", spec)
    client.timeout = 0.1
    if job is not None:
      job.timeout = 0.1
    pending = asyncio.create_task(job.start() if job else client.create("lost-response", spec))
    try:
      assert await asyncio.to_thread(entered.wait, 3)
      with pytest.raises(grpc.aio.AioRpcError) as expired:
        await pending
      assert expired.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    finally:
      release.set()
      client.timeout = 20
      if job is not None:
        job.timeout = 20
    await asyncio.gather(*list(server.simulator.commands))
    recovered = await client.recover("lost-response")
    assert (await client.create("lost-response", spec)).identifier == recovered.identifier
    if point.startswith("create"):
      assert (await recovered.status()).state == job_pb.JOB_STATE_READY
      await recovered.start()
    assert (await recovered.wait()).completed == 1
    assert len(await recovered.list_frames()) == 1


@pytest.mark.asyncio
async def test_store_ownership_device_binding_and_configuration_validation(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  async with local_server(directory, certs) as (_, client):
    ready = await client.create("old-configuration", SimulationSpec(1, 16, 16, 8, 0))
    competing = SimulatorServer(certs, data_dir=directory)
    with pytest.raises(BusyError):
      await competing.start()
    assert (await ready.status()).configuration_id == "simulation-v1"
  wrong = SimulatorServer(certs, data_dir=directory, device_id="another-device")
  with pytest.raises(StateError, match="device_id mismatch"):
    await wrong.start()
  async with local_server(directory, certs, configuration_id="simulation-v2") as (_, client):
    await initialize(client)
    old = await client.recover("old-configuration")
    with pytest.raises(grpc.aio.AioRpcError) as stale:
      await old.start()
    assert stale.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    fresh = await client.create("new-configuration", SimulationSpec(1, 16, 16, 8, 0))
    await fresh.start()
    assert (await fresh.wait()).outcome == job_pb.OUTCOME_SUCCEEDED


@pytest.mark.asyncio
async def test_corrupted_image_is_reported_without_reacquisition(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  async with local_server(tmp_path / "data", certs) as (server, client):
    await initialize(client)
    job = await client.create("corruption", SimulationSpec(1, 16, 16, 8, 0))
    await job.start()
    await job.wait()
    info = (await job.list_frames())[0]
    store = server.simulator.store
    await store.call(
      lambda: store.connection.execute(
        "UPDATE frames SET image=? WHERE frame_id=?",
        (b"damaged", info.frame_id),
      )
    )
    with pytest.raises(grpc.aio.AioRpcError) as corrupt:
      await job.get_frame(info.frame_id)
    assert corrupt.value.code() == grpc.StatusCode.DATA_LOSS
    assert (await job.status()).completed == 1
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_FAULTED


@pytest.mark.asyncio
async def test_failure_to_save_completion_is_explicit_and_recovered(tmp_path, monkeypatch):
  certs = create_certificates(tmp_path / "certs")
  directory = tmp_path / "data"
  async with local_server(directory, certs) as (server, client):
    await initialize(client)
    store = server.simulator.store
    save = store.save

    def fail_completion(status, action):
      if action == "finish" and status.request_id == "unsaved-completion":
        raise sqlite3.OperationalError("disk I/O error")
      return save(status, action)

    monkeypatch.setattr(store, "save", fail_completion)
    job = await client.create("unsaved-completion", SimulationSpec(1, 16, 16, 8, 0))
    await job.start()
    result = await job.wait()
    assert result.outcome == job_pb.OUTCOME_FAILED and not result.state_durable
    assert result.completed == 1 and "could not be persisted" in result.error
    assert (await client.status()).state == device_pb.OPERATIONAL_STATE_FAULTED
  async with local_server(directory, certs) as (_, client):
    job = await client.recover("unsaved-completion")
    recovered = await job.status()
    assert recovered.state_durable and recovered.outcome == job_pb.OUTCOME_INTERRUPTED
    assert recovered.completed == len(await job.list_frames()) == 1


@pytest.mark.asyncio
async def test_pending_commands_are_bounded_when_disk_is_stalled(tmp_path, monkeypatch):
  certs = create_certificates(tmp_path / "certs")
  async with local_server(tmp_path / "data", certs) as (server, client):
    server.simulator.command_limit = 1
    entered, release = threading.Event(), threading.Event()

    def hold(self, point, status):
      if point == "create.before_commit":
        entered.set()
        if not release.wait(5):
          raise RuntimeError("test did not release the disk")

    monkeypatch.setattr(Store, "checkpoint", hold)
    pending = asyncio.create_task(client.create("blocked", SimulationSpec(1, 16, 16, 8, 0)))
    try:
      assert await asyncio.to_thread(entered.wait, 3)
      with pytest.raises(grpc.aio.AioRpcError) as overloaded:
        await client.create("overflow", SimulationSpec(1, 16, 16, 8, 0))
      assert overloaded.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
      assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
    finally:
      release.set()
    assert (await (await pending).status()).state == job_pb.JOB_STATE_READY


@pytest.mark.asyncio
async def test_scan_length_does_not_accumulate_image_payloads_in_server_memory(tmp_path):
  certs = create_certificates(tmp_path / "certs")
  async with process_server(tmp_path / "data", certs) as (process, client):
    await initialize(client)
    memory = psutil.Process(process.pid)
    job = await client.create("bounded-memory", SimulationSpec(64, 1024, 512, 16, 0))
    warm_rss = None
    late_rss = []
    async with job.updates() as updates:
      await job.start()
      while True:
        status = await asyncio.wait_for(updates.get(), 10)
        if status.completed >= 8 and warm_rss is None:
          warm_rss = memory.memory_info().rss
        if status.completed >= 56:
          late_rss.append(memory.memory_info().rss)
        if status.state == job_pb.JOB_STATE_FINISHED:
          break
    assert status.outcome == job_pb.OUTCOME_SUCCEEDED and status.completed == 64
    assert warm_rss is not None and late_rss
    # Over 48 MiB of additional scientific pixels were committed after warm-up.
    # Allow allocator/SQLite variation, but reject growth proportional to the scan.
    assert max(late_rss) - warm_rss < 24 * 1024 * 1024
    frames = await job.list_frames()
    assert len(frames) == 64 and all(info.available for info in frames)
    assert (await job.get_frame(frames[0].frame_id)).sha256 == frames[0].sha256
    assert (await job.get_frame(frames[-1].frame_id)).sha256 == frames[-1].sha256
