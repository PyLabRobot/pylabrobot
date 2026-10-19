"""Scan planning and durable execution over RPC using the actual PLR scan executor."""

import asyncio
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import grpc

from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb
from pylabrobot.revvity.celigo.celigo import FocusResult
from pylabrobot.revvity.celigo.grpc.client import CeligoClient, unpack_acquisition_metadata
from pylabrobot.revvity.celigo.grpc.job_specification import JobSpecification
from pylabrobot.revvity.celigo.grpc.job_store import DEFAULT_QUOTA
from pylabrobot.revvity.celigo.grpc.tests.helpers import (
  block_calls,
  initialize,
  make_config,
  make_driver,
  make_server,
)
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.scan import (
  Capture,
  ScanEstimateModel,
  ScanRegion,
  ScanSpec,
  build_scan_plan,
)
from pylabrobot.revvity.celigo.tests.helpers import make_celigo
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


@asynccontextmanager
async def scan_system(directory: Path, storage_quota: int = DEFAULT_QUOTA):
  """Run the real planner and executor with mocked public hardware primitives."""
  config = make_config()
  config.calibration = replace(config.calibration, microns_per_pixel_x=10, microns_per_pixel_y=10)
  config.navigation = replace(
    config.navigation, max_galvo_deflection_x_mm=1, max_galvo_deflection_y_mm=1
  )
  config.channels["green"] = replace(
    config.channels["brightfield"],
    name="green",
    display_name="Green",
    z_offset_to_brightfield_mm=0.2,
  )
  driver = make_celigo(config=config)
  hardware = make_driver()
  with (
    patch.object(driver, "setup", hardware.setup),
    patch.object(driver, "stop", hardware.stop),
    patch.object(driver, "request_controller_status", hardware.request_controller_status),
    patch.object(driver, "request_digital_input", hardware.request_digital_input),
    patch.object(driver, "move_to_scan_block", hardware.move_to_scan_block),
    patch.object(driver, "acquire_scan_position", hardware.acquire_scan_position),
  ):
    server, _ = make_server(directory, config=config, driver=driver, storage_quota=storage_quota)
    await server.start()
    try:
      async with CeligoClient(server.endpoint, device_id=server.session.device_id) as client:
        yield server, client, hardware
    finally:
      await server.stop()


class ScanRpcTests(unittest.IsolatedAsyncioTestCase):
  async def test_default_capacity_admits_a_full_resolution_plate_plan(self) -> None:
    """A 128-frame plate can reserve worst-case pixels without a quota override."""
    with TemporaryDirectory() as directory:
      server, hardware = make_server(Path(directory), config=make_config(2048, 2048))
      await server.start()
      try:
        async with CeligoClient(server.endpoint, device_id=server.session.device_id) as client:
          plan = await client.build_scan_plan(
            ScanSpec.points([(63.5, 43)] * 128, channel="brightfield")
          )
          job = await client.create_scan("plate", plan=plan)
          self.assertEqual((await job.get_status()).total, 128)
          self.assertEqual(hardware.method_calls, [])
      finally:
        await server.stop()

  async def test_notebook_geometries_and_estimates_match_local_planning(self) -> None:
    """All notebook geometries survive RPC without constructing a hardware session."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    region = ScanRegion(5, 5, 8, 7)
    specs = (
      ScanSpec.points([(5, 5), (6, 6)], block_shape=(2, 3), channel="brightfield"),
      ScanSpec.wells(plate, ["A1", "B2"], channel="brightfield", autofocus="image"),
      ScanSpec.random(region, count=3, block_shape=(2, 1), seed=42, channel="brightfield"),
      ScanSpec.full_coverage(
        region, captures=[Capture("brightfield", 1, 1), Capture("green", 20, 2)]
      ),
    )
    estimates = ScanEstimateModel(0.1, 2, 3, 2)
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (server, client, hardware):
        for spec in specs:
          with self.subTest(geometry=type(spec.geometry).__name__):
            plan = await client.build_scan_plan(spec, estimate_model=estimates)
            expected_spec = replace(
              spec,
              captures=tuple(
                replace(
                  capture,
                  exposure_ms=capture.exposure_ms or 1,
                  gain=1 if capture.gain is None else capture.gain,
                )
                for capture in spec.captures
              ),
            )
            expected = build_scan_plan(
              server.session.config, expected_spec, estimate_model=estimates
            )
            self.assertEqual(plan, expected)
            self.assertEqual(await client.build_scan_plan(spec, estimate_model=estimates), plan)
        self.assertIsNone(server.session.driver)
        self.assertEqual(hardware.method_calls, [])
        self.assertEqual(
          (await client.get_status()).state, device_pb.OPERATIONAL_STATE_UNINITIALIZED
        )

  async def test_scan_executes_each_planned_frame_and_preserves_focus_and_metadata(self) -> None:
    """Reuse block movement and focus propagation while retaining complete RPC images."""
    plate = cor_96_wellplate_360uL_Fb(name="plate")
    spec = ScanSpec.wells(
      plate,
      ["A1", "B2"],
      block_shape=(2, 3),
      captures=[Capture("brightfield", 1, 1), Capture("green", 20, 2, machine_auto_exposure=True)],
      autofocus="image",
    )
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (_, client, hardware):
        base = hardware.acquire_scan_position.return_value

        async def acquire(**kwargs):
          frame = replace(
            base.frame,
            exposure_ms=7.5 if kwargs["machine_auto_exposure"] else kwargs["exposure_ms"],
            gain=kwargs["gain"],
          )
          focus = FocusResult(2700, 2.7, 10, ((2700, 10),), frame) if kwargs["autofocus"] else None
          return replace(
            base,
            frame=frame,
            channel=kwargs["channel"],
            focus=focus,
            z_mm=2.7 if focus else kwargs["z_mm"],
          )

        hardware.acquire_scan_position.side_effect = acquire
        plan = await client.build_scan_plan(spec)
        job = await client.create_scan("scan", plan=plan)
        self.assertEqual((await job.get_status()).total, 24)
        self.assertEqual(await job.get_scan_plan(), plan)
        self.assertEqual((await client.create_scan("scan", plan=plan)).identifier, job.identifier)
        with self.assertRaises(grpc.aio.AioRpcError) as uninitialized:
          await job.start()
        self.assertEqual(uninitialized.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        await initialize(client)
        await job.start()
        final = await job.wait()
        self.assertEqual(
          (final.outcome, final.completed, final.total), (job_pb.OUTCOME_SUCCEEDED, 24, 24)
        )
        self.assertEqual(hardware.move_to_scan_block.await_count, 2)
        self.assertEqual(hardware.acquire_scan_position.await_count, 24)
        frames = await job.list_frames()
        self.assertEqual([frame.sequence for frame in frames], list(range(1, 25)))
        for planned, info, call in zip(
          plan.frames, frames, hardware.acquire_scan_position.await_args_list
        ):
          image = await job.get_frame(info.frame_id)
          metadata = unpack_acquisition_metadata(image)
          self.assertEqual(metadata.planned_frame_index, planned.index)
          self.assertEqual(metadata.block_index, planned.block.index)
          self.assertEqual(metadata.label, planned.block.label)
          self.assertEqual(metadata.channel, planned.capture.channel)
          self.assertEqual(
            metadata.exposure_ms,
            7.5 if planned.capture.machine_auto_exposure else planned.capture.exposure_ms,
          )
          self.assertEqual(metadata.machine_auto_exposure, planned.capture.machine_auto_exposure)
          self.assertEqual(
            call.kwargs["machine_auto_exposure"], planned.capture.machine_auto_exposure
          )
          self.assertEqual(metadata.gain, planned.capture.gain)
          self.assertEqual(
            (metadata.tile_row, metadata.tile_column),
            (planned.position.tile_row, planned.position.tile_column),
          )
          self.assertEqual(call.kwargs["position"], planned.position)
          if planned.index % 12 == 0:
            self.assertEqual(call.kwargs["autofocus"], "image")
            self.assertEqual(metadata.focus_score, 10)
          else:
            self.assertIsNone(call.kwargs["autofocus"])
            self.assertAlmostEqual(
              call.kwargs["z_mm"], 2.7 if planned.capture.channel == "brightfield" else 2.9
            )

  async def test_changed_plans_and_stale_configuration_are_rejected_before_motion(self) -> None:
    """Only the inspected plan compiled from its specification can become a job."""
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (_, client, hardware):
        plan = await client.build_scan_plan(ScanSpec.points([(5, 5)], channel="brightfield"))
        changes = (
          replace(plan, frame_count=2),
          replace(plan, blocks=(replace(plan.blocks[0], stage_x_mm=0),)),
          replace(
            plan,
            frames=(
              replace(plan.frames[0], position=replace(plan.frames[0].position, sample_x_mm=0)),
            ),
          ),
        )
        for changed in changes:
          with self.assertRaises(grpc.aio.AioRpcError) as invalid:
            await client.create_scan("changed", plan=changed)
          self.assertEqual(invalid.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        with self.assertRaises(grpc.aio.AioRpcError) as stale:
          await client.create_scan("stale", plan=replace(plan, configuration_fingerprint="stale"))
        self.assertEqual(stale.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        await client.create_scan("original", plan=plan)
        other = await client.build_scan_plan(ScanSpec.points([(6, 6)], channel="brightfield"))
        with self.assertRaises(grpc.aio.AioRpcError) as conflict:
          await client.create_scan("original", plan=other)
        self.assertEqual(conflict.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        self.assertEqual(hardware.method_calls, [])

  async def test_scan_survives_client_disconnect_and_retains_its_plan_across_restart(self) -> None:
    """Recover the same scan, images, and plan without replaying completed work."""
    with TemporaryDirectory() as directory:
      path = Path(directory)
      async with scan_system(path) as (_, client, hardware):
        await initialize(client)
        plan = await client.build_scan_plan(
          ScanSpec.points([(5, 5), (6, 6)], channel="brightfield")
        )
        job = await client.create_scan("reconnect", plan=plan)
        await client.create_scan("ready", plan=plan)
        with block_calls(hardware.acquire_scan_position) as (entered, release):
          await job.start()
          await asyncio.wait_for(entered.wait(), 5)
          await client.disconnect()
          release.set()
          await client.connect()
          recovered = await client.recover_job("reconnect")
          self.assertEqual((await recovered.wait()).outcome, job_pb.OUTCOME_SUCCEEDED)
          self.assertEqual(await recovered.get_scan_plan(), plan)
        image = await recovered.get_frame((await recovered.list_frames())[0].frame_id)
      async with scan_system(path) as (_, client, hardware):
        recovered = await client.recover_job("reconnect")
        self.assertEqual(await recovered.get_scan_plan(), plan)
        self.assertEqual(await recovered.get_frame(image.frame_id), image)
        ready = await client.recover_job("ready")
        self.assertEqual(await ready.get_scan_plan(), plan)
        self.assertEqual((await ready.get_status()).state, job_pb.JOB_STATE_READY)
        self.assertEqual(hardware.method_calls, [])

  async def test_cancellation_waits_for_current_frame_and_prevents_the_next_block(self) -> None:
    """Finish the owned acquisition and persist its image before releasing the driver."""
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (_, client, hardware):
        await initialize(client)
        plan = await client.build_scan_plan(
          ScanSpec.points([(5, 5), (6, 6)], channel="brightfield")
        )
        job = await client.create_scan("cancel", plan=plan)
        with block_calls(hardware.acquire_scan_position) as (entered, release):
          await job.start()
          await asyncio.wait_for(entered.wait(), 5)
          self.assertEqual((await job.cancel()).state, job_pb.JOB_STATE_RUNNING)
          hardware.stop.assert_not_awaited()
          release.set()
          final = await job.wait()
        self.assertEqual((final.outcome, final.completed), (job_pb.OUTCOME_CANCELLED, 1))
        self.assertEqual(len(await job.list_frames()), 1)
        hardware.move_to_scan_block.assert_awaited_once_with(plan.blocks[0])
        hardware.acquire_scan_position.assert_awaited_once()
        hardware.stop.assert_awaited_once()

  async def test_storage_reservation_covers_the_whole_plan(self) -> None:
    """Admission uses encoded-image capacity, independently of display estimates."""
    one_frame = JobSpecification(width=64, height=48).calculate_storage_reservation()
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory), storage_quota=2 * one_frame) as (_, client, hardware):
        plan = await client.build_scan_plan(
          ScanSpec.points([(5, 5), (6, 6), (7, 7)], channel="brightfield"),
          estimate_model=ScanEstimateModel(bytes_per_pixel=1),
        )
        with self.assertRaises(grpc.aio.AioRpcError) as full:
          await client.create_scan("too-big", plan=plan)
        self.assertEqual(full.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
        self.assertEqual(hardware.method_calls, [])

  async def test_images_are_readable_during_scanning_and_survive_a_later_failure(self) -> None:
    """Progress and pixels are durable before another frame can fail."""
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (_, client, hardware):
        await initialize(client)
        plan = await client.build_scan_plan(
          ScanSpec.points([(5, 5), (6, 6)], channel="brightfield")
        )
        job = await client.create_scan("partial", plan=plan)
        entered, release = asyncio.Event(), asyncio.Event()

        async def acquire(**kwargs):
          if hardware.acquire_scan_position.await_count == 1:
            return hardware.acquire_scan_position.return_value
          entered.set()
          await release.wait()
          raise RuntimeError("camera failed on the second frame")

        hardware.acquire_scan_position.side_effect = acquire
        try:
          await job.start()
          await asyncio.wait_for(entered.wait(), 5)
          status = await job.get_status()
          self.assertEqual(
            (status.state, status.completed, status.total), (job_pb.JOB_STATE_RUNNING, 1, 2)
          )
          frames = await job.list_frames()
          self.assertEqual(len(frames), 1)
          image = await job.get_frame(frames[0].frame_id)
          self.assertTrue(image.image)
          async with client.watch_previews() as previews:
            self.assertEqual(await asyncio.wait_for(previews.get(), 5), image)
        finally:
          release.set()
        final = await job.wait()
        self.assertEqual((final.outcome, final.completed), (job_pb.OUTCOME_FAILED, 1))
        self.assertEqual(await job.get_frame(image.frame_id), image)
        self.assertEqual((await client.get_status()).state, device_pb.OPERATIONAL_STATE_FAULTED)
        hardware.stop.assert_awaited_once()

  async def test_invalid_requests_and_non_scan_plan_lookup(self) -> None:
    """Validate identity, geometry, capture settings, and job kind on the wire."""
    with TemporaryDirectory() as directory:
      async with scan_system(Path(directory)) as (_, client, hardware):
        with self.assertRaises(grpc.aio.AioRpcError) as wrong:
          await client.operations.BuildScanPlan(pb.BuildScanPlanRequest(device_id="other"))
        self.assertEqual(wrong.exception.code(), grpc.StatusCode.NOT_FOUND)
        with self.assertRaises(grpc.aio.AioRpcError) as missing:
          await client.operations.BuildScanPlan(pb.BuildScanPlanRequest(device_id=client.device_id))
        self.assertEqual(missing.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        for capture in (
          Capture("unknown"),
          Capture("green", 1001),
          Capture("brightfield", gain=17),
        ):
          with self.assertRaises(grpc.aio.AioRpcError) as invalid:
            await client.build_scan_plan(ScanSpec.points([(5, 5)], captures=[capture]))
          self.assertEqual(invalid.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        job = await client.create_initialization("init")
        with self.assertRaises(grpc.aio.AioRpcError) as wrong_kind:
          await job.get_scan_plan()
        self.assertEqual(wrong_kind.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        self.assertEqual(hardware.method_calls, [])
