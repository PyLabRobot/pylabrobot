"""VSpin RPC lifecycle, ownership, validation, and cancellation over loopback channels."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

import grpc

from pylabrobot.agilent.vspin.grpc.client import VSpinClient
from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.tests.helpers import block_calls, initialize, make_server
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2 as pb
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


class VSpinRPCTests(unittest.IsolatedAsyncioTestCase):
  """Exercise the actual service using mocked hardware."""

  async def asyncSetUp(self) -> None:
    """Start an isolated server and connect a typed client."""
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.server, self.driver = make_server(Path(self.directory.name))
    await self.server.start()
    self.addAsyncCleanup(self.server.stop)
    self.client = await VSpinClient(self.server.endpoint, "vspin-test").connect()
    self.addAsyncCleanup(self.client.disconnect)

  async def test_identity_status_and_creation_do_not_open_hardware(self) -> None:
    """Reading and preparing jobs leaves the instrument uninitialized."""
    info = await self.client.get_device_info()
    self.assertEqual(info.device_type, "VSpin")
    self.assertEqual(
      set(info.services),
      {
        "pylabrobot.rpc.v1.DeviceService",
        "pylabrobot.rpc.v1.JobService",
        "vspin.v1.VSpinService",
      },
    )
    before = await self.client.get_status()
    self.assertEqual(before.state, device_pb.OPERATIONAL_STATE_UNINITIALIZED)
    self.assertEqual(before, await self.client.get_status())
    self.assertEqual(
      (await self.client.get_configuration()).maximum_relative_centrifugal_force, 1000
    )
    job = await self.client.create_spin("spin", g=400, duration=30)
    status = await job.get_status()
    self.assertEqual(status.state, job_pb.JOB_STATE_READY)
    self.assertTrue(status.state_durable)
    self.assertEqual(await job.get_spin_settings(), Spin(g=400, duration=30))
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await job.start()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    self.assertEqual(self.driver.mock_calls, [])

  async def test_spin_settings_and_single_use_execution(self) -> None:
    """Run the supplied settings once and reject a second start after completion."""
    await initialize(self.client)
    job = await self.client.create_spin(
      "spin", g=300, duration=12, acceleration=0.4, deceleration=0.3
    )
    await job.start()
    status = await job.wait(timeout=5)
    self.assertEqual(status.outcome, job_pb.OUTCOME_SUCCEEDED)
    self.assertEqual((status.completed, status.total, status.progress_unit), (1, 1, "Spins"))
    self.driver.spin.assert_awaited_once_with(
      g=300, duration=12, acceleration=0.4, deceleration=0.3
    )
    self.assertEqual((await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_READY)
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await job.start()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    again = await self.client.create_initialization("initialize-again")
    with self.assertRaises(grpc.aio.AioRpcError):
      await again.start()
    self.driver.setup.assert_awaited_once()

  async def test_request_ids_are_deduplicated_and_settings_are_checked(self) -> None:
    """Concurrent submissions return the same inert job and conflicting intent is rejected."""
    first, second = await asyncio.gather(
      self.client.create_spin("same"),
      self.client.create_spin("same"),
    )
    self.assertEqual(first.identifier, second.identifier)
    self.assertEqual((await self.client.recover_job("same")).identifier, first.identifier)
    for operation in (
      self.client.create_spin("same", g=200),
      self.client.create_initialization("same"),
    ):
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await operation
      self.assertEqual(error.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

  async def test_wire_validation_rejects_missing_nonfinite_and_out_of_range_settings(self) -> None:
    """Validate raw protobuf requests even when callers bypass the Python client."""
    invalid = [pb.SpinSettings()]
    for field, value in (
      ("relative_centrifugal_force", float("nan")),
      ("relative_centrifugal_force", 1001),
      ("relative_centrifugal_force", 0),
      ("duration_seconds", 0),
      ("duration_seconds", float("inf")),
      ("acceleration_fraction", 0),
      ("deceleration_fraction", 1.1),
      ("deceleration_fraction", float("nan")),
    ):
      values = dict(
        relative_centrifugal_force=500,
        duration_seconds=60,
        acceleration_fraction=0.8,
        deceleration_fraction=0.8,
      )
      values[field] = value
      invalid.append(pb.SpinSettings(**values))
    missing = Spin().to_proto()
    missing.ClearField("acceleration_fraction")
    invalid.append(missing)
    for settings in invalid:
      with self.subTest(settings=settings):
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await self.client.operations.CreateSpin(
            pb.SpinRequest(
              device_id="vspin-test",
              request_id="invalid",
              configuration_id=self.client.configuration_id,
              settings=settings,
            )
          )
        self.assertEqual(error.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
    self.assertFalse(self.server.session.jobs)
    self.assertEqual(self.driver.mock_calls, [])

  async def test_identity_configuration_and_request_validation(self) -> None:
    """Reject wrong-device requests and stale configurations before accepting work."""
    for device_id, request_id, config, code in (
      ("wrong", "init", self.client.configuration_id, grpc.StatusCode.NOT_FOUND),
      ("vspin-test", "", self.client.configuration_id, grpc.StatusCode.INVALID_ARGUMENT),
      ("vspin-test", "x" * 129, self.client.configuration_id, grpc.StatusCode.INVALID_ARGUMENT),
      ("vspin-test", "init", "", grpc.StatusCode.INVALID_ARGUMENT),
      ("vspin-test", "init", "stale", grpc.StatusCode.FAILED_PRECONDITION),
    ):
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await self.client.operations.CreateInitialization(
          pb.InitializationRequest(
            device_id=device_id,
            request_id=request_id,
            configuration_id=config,
          )
        )
      self.assertEqual(error.exception.code(), code)
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await self.client.operations.OpenDoor(device_pb.DeviceRequest(device_id="wrong"))
    self.assertEqual(error.exception.code(), grpc.StatusCode.NOT_FOUND)
    other = VSpinClient(self.server.endpoint, "wrong")
    with self.assertRaises(grpc.aio.AioRpcError):
      await other.connect()
    self.assertIsNone(other.channel)

  async def test_busy_job_survives_disconnect_and_repeated_start(self) -> None:
    """A running spin owns the instrument across client reconnects."""
    await initialize(self.client)
    with block_calls(self.driver.spin) as (entered, release):
      first = await self.client.create_spin("first")
      second = await self.client.create_spin("second")
      await first.start()
      await asyncio.wait_for(entered.wait(), 5)
      self.assertEqual((await first.start()).state, job_pb.JOB_STATE_RUNNING)
      for operation in (second.start(), self.client.open_door(), self.client.go_to_bucket1()):
        with self.assertRaises(grpc.aio.AioRpcError) as error:
          await operation
        self.assertEqual(error.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
      await self.client.disconnect()
      await self.client.connect()
      recovered = await self.client.recover_job("first")
      self.assertEqual((await recovered.get_status()).state, job_pb.JOB_STATE_RUNNING)
      release.set()
      self.assertEqual((await recovered.wait(timeout=5)).outcome, job_pb.OUTCOME_SUCCEEDED)
      self.driver.spin.assert_awaited_once()

  async def test_spin_cancellation_waits_for_deceleration(self) -> None:
    """CancelJob returns Running until the driver confirms that the spin has finished."""
    await initialize(self.client)
    with block_calls(self.driver.spin) as (entered, release):
      with block_calls(self.driver.stop_spin) as (stopping, stopped):
        job = await self.client.create_spin("cancel", deceleration=0.35)
        await job.start()
        await asyncio.wait_for(entered.wait(), 5)
        status = await job.cancel()
        self.assertEqual(status.state, job_pb.JOB_STATE_RUNNING)
        self.assertTrue(status.cancellation_requested)
        await asyncio.wait_for(stopping.wait(), 5)
        self.driver.stop_spin.assert_awaited_once_with(deceleration=0.35)
        self.assertEqual((await self.client.get_status()).active_job_id, job.identifier)
        stopped.set()
        release.set()
        status = await job.wait(timeout=5)
    self.assertEqual(status.outcome, job_pb.OUTCOME_CANCELLED)
    self.assertTrue(status.state_durable)
    self.assertEqual((await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_READY)
    self.driver.stop.assert_not_awaited()

  async def test_cancel_during_spin_entry_is_not_lost(self) -> None:
    """Retry cooperative stopping if cancellation arrives before the driver's spin scope."""
    await initialize(self.client)
    entered, active, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def spin(**_kwargs):
      """Simulate an event callback yielding before the driver owns the spin."""
      entered.set()
      await active.wait()
      await stopped.wait()

    async def stop_spin(**_kwargs):
      """Match the driver's no-op stop before its spin scope begins."""
      if active.is_set():
        stopped.set()

    self.driver.spin.side_effect = spin
    self.driver.stop_spin.side_effect = stop_spin
    self.addCleanup(stopped.set)
    self.addCleanup(active.set)
    job = await self.client.create_spin("entry")
    await job.start()
    await asyncio.wait_for(entered.wait(), 5)
    await job.cancel()
    await asyncio.sleep(0.02)
    active.set()
    self.assertEqual((await job.wait(timeout=5)).outcome, job_pb.OUTCOME_CANCELLED)

  async def test_cancel_ready_job_never_moves_hardware(self) -> None:
    """Cancelling inert intent is terminal without driver construction."""
    job = await self.client.create_spin("cancel-ready")
    status = await job.cancel()
    self.assertEqual(status.outcome, job_pb.OUTCOME_CANCELLED)
    self.assertEqual(await job.cancel(), status)
    with self.assertRaises(grpc.aio.AioRpcError):
      await job.start()
    self.assertEqual(self.driver.mock_calls, [])

  async def test_initialization_cancellation_waits_for_setup_and_closes_driver(self) -> None:
    """An accepted setup cannot be abandoned halfway through homing."""
    with block_calls(self.driver.setup) as (entered, release):
      job = await self.client.create_initialization("cancel-init")
      await job.start()
      await asyncio.wait_for(entered.wait(), 5)
      await job.cancel()
      self.driver.stop.assert_not_awaited()
      self.assertEqual((await job.get_status()).state, job_pb.JOB_STATE_RUNNING)
      release.set()
      self.assertEqual((await job.wait(timeout=5)).outcome, job_pb.OUTCOME_CANCELLED)
    self.driver.stop.assert_awaited_once()
    self.assertEqual(
      (await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_UNINITIALIZED
    )

  async def test_motion_deadline_keeps_ownership_until_driver_finishes(self) -> None:
    """A lost door response does not cancel motion or admit a concurrent spin."""
    await initialize(self.client)
    with block_calls(self.driver.open_door) as (entered, release):
      call = self.client.operations.OpenDoor(self.client.request, timeout=0.1)
      await asyncio.wait_for(entered.wait(), 5)
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await call
      self.assertEqual(error.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
      self.assertTrue(self.server.session.moving)
      self.assertEqual((await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_BUSY)
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await self.client.close_door()
      self.assertEqual(error.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
      release.set()
      await asyncio.gather(*list(self.server.session.commands))
      self.assertFalse(self.server.session.moving)

  async def test_motion_methods_use_the_existing_driver(self) -> None:
    """Door, interlock, and bucket RPCs preserve the driver's operation boundaries."""
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await self.client.open_door()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    await initialize(self.client)
    for call, method in (
      (self.client.open_door, self.driver.open_door),
      (self.client.close_door, self.driver.close_door),
      (self.client.lock_door, self.driver.lock_door),
      (self.client.unlock_door, self.driver.unlock_door),
      (self.client.lock_bucket, self.driver.lock_bucket),
      (self.client.unlock_bucket, self.driver.unlock_bucket),
      (self.client.go_to_bucket1, self.driver.go_to_bucket1),
      (self.client.go_to_bucket2, self.driver.go_to_bucket2),
    ):
      await call()
      method.assert_awaited_once_with()

  async def test_bucket_positioning_requires_calibration(self) -> None:
    """Missing calibration rejects positioning without faulting an idle instrument."""
    await initialize(self.client)
    with patch.object(
      type(self.driver), "bucket_1_remainder", new_callable=PropertyMock, create=True
    ) as calibration:
      calibration.side_effect = RuntimeError("bucket calibration missing")
      with self.assertRaises(grpc.aio.AioRpcError) as error:
        await self.client.go_to_bucket1()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
    self.driver.go_to_bucket1.assert_not_awaited()
    self.assertEqual((await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_READY)

  async def test_failed_spin_faults_device_and_retains_error(self) -> None:
    """Hardware failure finishes the job only after cleanup and prevents further motion."""
    await initialize(self.client)
    self.driver.spin.side_effect = RuntimeError("imbalance detected")
    job = await self.client.create_spin("failure")
    await job.start()
    status = await job.wait(timeout=5)
    self.assertEqual(status.outcome, job_pb.OUTCOME_FAILED)
    self.assertIn("imbalance", status.error)
    self.driver.stop.assert_awaited_once()
    self.assertEqual((await self.client.get_status()).state, device_pb.OPERATIONAL_STATE_FAULTED)
    with self.assertRaises(grpc.aio.AioRpcError) as error:
      await self.client.open_door()
    self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)

  async def test_watch_cleanup_and_terminal_snapshot(self) -> None:
    """Closing subscriptions releases capacity without cancelling a running job."""
    async with self.client.watch_status() as updates:
      self.assertEqual((await updates.get()).state, device_pb.OPERATIONAL_STATE_UNINITIALIZED)
    job = await self.client.create_spin("watch")
    async with job.watch_status() as updates:
      self.assertEqual((await updates.get()).state, job_pb.JOB_STATE_READY)
      await job.cancel()
      self.assertEqual((await updates.get()).outcome, job_pb.OUTCOME_CANCELLED)
      with self.assertRaises(EOFError):
        await updates.get()
    await asyncio.sleep(0.01)
    self.assertFalse(self.server.session.updates.queues)
    self.assertFalse(self.server.session.jobs[job.identifier].updates.queues)

  async def test_shutdown_waits_for_spin_cleanup(self) -> None:
    """Shutdown asks for deceleration and leaves the transport open until the spin finishes."""
    await initialize(self.client)
    with block_calls(self.driver.spin) as (entered, release):
      with block_calls(self.driver.stop_spin) as (stopping, stopped):
        job = await self.client.create_spin("shutdown")
        await job.start()
        await asyncio.wait_for(entered.wait(), 5)
        shutdown = asyncio.create_task(self.server.stop())
        await asyncio.wait_for(stopping.wait(), 5)
        self.assertFalse(shutdown.done())
        self.driver.stop.assert_not_awaited()
        stopped.set()
        release.set()
        await asyncio.wait_for(shutdown, 5)
    self.driver.stop.assert_awaited_once()
    self.assertEqual(
      self.server.session.jobs[job.identifier].status.outcome, job_pb.OUTCOME_CANCELLED
    )
