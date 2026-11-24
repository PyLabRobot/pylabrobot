"""Durable request recovery, exclusive ownership, and failed storage operations."""

import asyncio
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import grpc

from pylabrobot.agilent.vspin.grpc.client import VSpinClient
from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.tests.helpers import block_calls, initialize, make_server
from pylabrobot.rpc.errors import BusyError, StateError
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
  """Exercise restart behavior using real SQLite records."""

  async def asyncSetUp(self) -> None:
    """Allocate a separate job directory for each test."""
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.directory = Path(self.temp.name)

  async def connect(self, directory=None, **options):
    """Start a server and register its client and cleanup with the test."""
    server, driver = make_server(directory or self.directory, **options)
    self.addAsyncCleanup(server.stop)
    await server.start()
    client = await VSpinClient(server.endpoint, options.get("device_id", "vspin-test")).connect()
    self.addAsyncCleanup(client.disconnect)
    return server, driver, client

  async def test_restart_retains_inert_and_finished_jobs(self) -> None:
    """Settings and request identity survive restart without restoring hardware readiness."""
    server, _, client = await self.connect()
    await initialize(client)
    ready = await client.create_spin("ready", g=400, duration=12)
    finished = await client.create_spin("finished")
    await finished.start()
    status = await finished.wait(timeout=5)
    instance_id = (await client.get_device_info()).instance_id
    await client.disconnect()
    await server.stop()
    _, driver, client = await self.connect()
    self.assertNotEqual((await client.get_device_info()).instance_id, instance_id)
    self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_UNINITIALIZED)
    recovered = await client.recover_job("ready")
    self.assertEqual(recovered.identifier, ready.identifier)
    self.assertEqual(await recovered.get_spin_settings(), Spin(g=400, duration=12))
    self.assertEqual(await (await client.recover_job("finished")).get_status(), status)
    self.assertEqual(
      (await client.create_spin("ready", g=400, duration=12)).identifier, ready.identifier
    )
    self.assertEqual(driver.mock_calls, [])
    await initialize(client, "initialize-after-restart")
    await recovered.start()
    self.assertEqual((await recovered.wait(timeout=5)).outcome, job_pb.OUTCOME_SUCCEEDED)

  async def test_process_crash_marks_accepted_spin_interrupted(self) -> None:
    """An abrupt exit retains acceptance and never replays the spin on startup."""
    script = """
import asyncio
import os
import sys
from pathlib import Path
from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.tests.helpers import make_server

async def main():
  server, driver = make_server(Path(sys.argv[1]))
  session = server.session
  await session.open()
  initialization = await session.create_initialization("init", session.configuration_id)
  await initialization.start()
  await initialization.task
  entered = asyncio.Event()
  async def spin(**kwargs):
    entered.set()
    await asyncio.Event().wait()
  driver.spin.side_effect = spin
  job = await session.create_spin("crashed", Spin(g=350, duration=30), session.configuration_id)
  await job.start()
  await entered.wait()
  os._exit(23)

asyncio.run(main())
"""
    result = await asyncio.to_thread(
      subprocess.run,
      [sys.executable, "-c", script, str(self.directory)],
      capture_output=True,
      text=True,
      timeout=15,
    )
    self.assertEqual(result.returncode, 23, result.stderr)
    server, driver, client = await self.connect()
    job = await client.recover_job("crashed")
    status = await job.get_status()
    self.assertEqual(status.state, job_pb.JOB_STATE_FINISHED)
    self.assertEqual(status.outcome, job_pb.OUTCOME_INTERRUPTED)
    self.assertTrue(status.state_durable)
    self.assertEqual(await job.get_spin_settings(), Spin(g=350, duration=30))
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await job.start()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    self.assertEqual(driver.mock_calls, [])
    await server.stop()
    _, _, client = await self.connect()
    self.assertEqual(await (await client.recover_job("crashed")).get_status(), status)

  async def test_changed_connection_invalidates_ready_jobs(self) -> None:
    """Retained intent cannot run against a different configured FTDI connection."""
    server, _, client = await self.connect(connection_id="first")
    await client.create_initialization("old")
    await server.stop()
    _, driver, client = await self.connect(connection_id="second")
    job = await client.recover_job("old")
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await job.start()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    self.assertEqual(driver.mock_calls, [])

  async def test_hardware_and_store_locks_are_independent(self) -> None:
    """Neither a new directory nor a different hardware lock can bypass exclusive ownership."""
    server, _, _ = await self.connect()
    for directory, lock in (
      (self.directory / "other", self.directory / "hardware.lock"),
      (self.directory, self.directory / "other-hardware.lock"),
    ):
      duplicate, driver = make_server(directory, hardware_lock=lock)
      with self.assertRaises(BusyError):
        await duplicate.start()
      self.assertEqual(driver.mock_calls, [])
    await server.stop()
    await self.connect()

  async def test_store_rejects_a_different_device_identity(self) -> None:
    """A renamed service cannot claim the retained jobs of another device."""
    server, _, client = await self.connect()
    await client.create_spin("retained")
    await server.stop()
    renamed, _ = make_server(self.directory, device_id="other-device")
    with self.assertRaises(StateError):
      await renamed.start()
    _, _, client = await self.connect()
    self.assertEqual(
      (await (await client.recover_job("retained")).get_status()).state, job_pb.JOB_STATE_READY
    )

  async def test_storage_capacity_keeps_existing_request_identity(self) -> None:
    """Reusing retained intent works at capacity, while new requests are rejected."""
    _, _, client = await self.connect(max_jobs=1)
    first = await client.create_spin("first")
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await client.create_spin("second")
    self.assertEqual(error.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
    self.assertEqual((await client.create_spin("first")).identifier, first.identifier)

  async def test_lost_creation_response_finishes_bookkeeping(self) -> None:
    """A disconnected submitter can recover the request after its SQLite commit finishes."""
    server, _, client = await self.connect()
    entered, release = threading.Event(), threading.Event()
    create = server.session.store.create_job

    def delayed_create(spec, status):
      """Hold the storage worker across cancellation of the client RPC."""
      entered.set()
      if not release.wait(5):
        raise RuntimeError("test did not release the storage worker")
      create(spec, status)

    with patch.object(server.session.store, "create_job", side_effect=delayed_create):
      task = asyncio.create_task(client.create_spin("lost"))
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
          await task
      finally:
        release.set()
      await asyncio.gather(*list(server.session.commands))
    recovered = await client.recover_job("lost")
    self.assertEqual((await client.create_spin("lost")).identifier, recovered.identifier)
    self.assertEqual(len(server.session.jobs), 1)

  async def test_failed_start_commit_does_not_operate_hardware(self) -> None:
    """A failed durable acceptance faults the service before any driver call."""
    server, driver, client = await self.connect()
    job = await client.create_initialization("init")
    with patch.object(
      server.session.store, "save_job_status", side_effect=sqlite3.OperationalError("disk full")
    ):
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await job.start()
    self.assertEqual(error.exception.code(), grpc.StatusCode.UNAVAILABLE)
    self.assertEqual(driver.mock_calls, [])
    self.assertEqual((await job.get_status()).state, job_pb.JOB_STATE_READY)
    self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_FAULTED)

  async def test_failed_completion_is_reported_as_not_durable(self) -> None:
    """A completion write failure is visible and restart recovers the accepted job."""
    server, driver, client = await self.connect()
    await initialize(client)
    with block_calls(driver.spin) as (entered, release):
      job = await client.create_spin("finish-failure")
      await job.start()
      await asyncio.wait_for(entered.wait(), 5)
      with patch.object(
        server.session.store, "save_job_status", side_effect=sqlite3.OperationalError("disk full")
      ):
        release.set()
        status = await job.wait(timeout=5)
    self.assertEqual(status.outcome, job_pb.OUTCOME_FAILED)
    self.assertFalse(status.state_durable)
    self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_FAULTED)
    await server.stop()
    _, _, client = await self.connect()
    self.assertEqual(
      (await (await client.recover_job("finish-failure")).get_status()).outcome,
      job_pb.OUTCOME_INTERRUPTED,
    )
