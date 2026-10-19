"""Loopback gRPC simulator and a reproducible two-process image/job experiment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from google.protobuf.json_format import MessageToDict

from pylabrobot.revvity.celigo.grpc.client import PrototypeClient
from pylabrobot.revvity.celigo.grpc.model import SimulationSpec
from pylabrobot.revvity.celigo.grpc.server import SimulatorServer
from pylabrobot.rpc.security import Certificates, create_certificates
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


async def serve(args) -> None:
  server = SimulatorServer(
    Certificates(args.certificates),
    port=args.port,
    device_id=args.device_id,
    data_dir=args.data_dir,
    storage_quota=args.storage_quota_mib * 1024 * 1024,
    max_jobs=args.max_jobs,
  )
  stopped = asyncio.Event()
  for signum in (signal.SIGINT, signal.SIGTERM):
    asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
  await server.start()
  try:
    print(json.dumps({"endpoint": server.endpoint, "device_id": args.device_id}), flush=True)
    await stopped.wait()
  finally:
    await server.stop()


async def measure_profile(
  client: PrototypeClient, name: str, spec: SimulationSpec, *, request_id: str | None = None
) -> dict:
  request_id = request_id or name
  job = await client.create(request_id, spec, preview=name == "preview")
  observed = []
  started = time.perf_counter()
  async with job.updates() as updates:
    await job.start()
    while True:
      snapshot = await asyncio.wait_for(updates.get(), 20)
      observed.append(snapshot.completed)
      if snapshot.completed:
        break
    first_frame_seconds = time.perf_counter() - started
    info = (await job.list_frames(limit=1))[0]

    async def transfer_frame():
      started = time.perf_counter()
      frame = await job.get_frame(info.frame_id)
      return frame, time.perf_counter() - started

    transfer = asyncio.create_task(transfer_frame())
    await asyncio.sleep(0)
    control_started = time.perf_counter()
    await job.status()
    control_seconds = time.perf_counter() - control_started
    frame, transfer_seconds = await transfer
    if hashlib.sha256(frame.image).hexdigest() != info.sha256:
      raise RuntimeError("image checksum mismatch")
    final = await job.wait()
  if final.outcome != job_pb.OUTCOME_SUCCEEDED:
    raise RuntimeError(f"acquisition failed: {final.error}")
  await client.disconnect()
  await client.connect()
  recovered = await client.recover(request_id)
  retry = await recovered.get_frame(info.frame_id)
  if frame != retry:
    raise RuntimeError("retained frame changed after reconnection")
  return {
    "profile": name,
    "request_id": request_id,
    "job_id": job.identifier,
    "width": spec.width,
    "height": spec.height,
    "bit_depth": spec.bit_depth,
    "image_bytes": len(frame.image),
    "frame_count": final.completed,
    "first_frame_seconds": round(first_frame_seconds, 4),
    "whole_image_response_seconds": round(transfer_seconds, 4),
    "concurrent_status_seconds": round(control_seconds, 4),
    "checksum_verified": True,
    "reconnect_retry_identical": True,
    "observed_progress": observed,
  }


async def measure_cancel(client: PrototypeClient, request_id="cancel-benchmark") -> dict:
  job = await client.create(request_id, SimulationSpec(8, 2048, 2048, 16, 20))
  async with job.updates() as updates:
    await job.start()
    while (await asyncio.wait_for(updates.get(), 20)).completed < 1:
      pass
    info = (await job.list_frames(limit=1))[0]
    transfer = asyncio.create_task(job.get_frame(info.frame_id))
    await asyncio.sleep(0)
    started = time.perf_counter()
    await job.cancel()
    acknowledged = time.perf_counter() - started
    final = await job.wait()
    stopped = time.perf_counter() - started
    frame = await transfer
  if (
    final.outcome != job_pb.OUTCOME_CANCELLED
    or hashlib.sha256(frame.image).hexdigest() != info.sha256
  ):
    raise RuntimeError("cancellation/image-transfer experiment failed")
  return {
    "request_id": request_id,
    "job_id": job.identifier,
    "cancel_acknowledgement_seconds": round(acknowledged, 4),
    "cancel_completion_seconds": round(stopped, 4),
    "completed_frames_retained": final.completed,
    "outcome": "Cancelled",
  }


async def check(
  endpoint: str, certs: Certificates, device_id: str, *, exercise_simulator=False
) -> dict:
  """Inspect an existing service, optionally exercising its synthetic job API."""
  async with PrototypeClient(endpoint, certs, device_id=device_id) as client:
    info = await client.device_info()
    report = {
      "measured_at": datetime.now(timezone.utc).isoformat(),
      "endpoint": endpoint,
      "device": MessageToDict(info, preserving_proto_field_name=True),
      "initial_status": MessageToDict(await client.status(), preserving_proto_field_name=True),
      "exercised": False,
    }
    if not exercise_simulator:
      return report
    if info.device_type != "CeligoSimulator":
      raise ValueError("the exercise command only accepts a CeligoSimulator")
    run_id = f"check-{uuid4().hex}"
    initialization = await client.create_initialization(f"{run_id}-initialize")
    await initialization.start()
    if (await initialization.wait()).outcome != job_pb.OUTCOME_SUCCEEDED:
      raise RuntimeError("simulated initialization failed")
    profiles = []
    for name, spec in (
      ("preview", SimulationSpec(1, 256, 256, 8, 0)),
      ("full", SimulationSpec(3, 2048, 2048, 16, 100)),
      ("maximum", SimulationSpec(3, 4096, 2048, 16, 100)),
    ):
      profiles.append(await measure_profile(client, name, spec, request_id=f"{run_id}-{name}"))
    cancellation = await measure_cancel(client, f"{run_id}-cancel")

    # Disconnect while a job is running, then recover without issuing another Start.
    request_id = f"{run_id}-disconnect"
    job = await client.create(request_id, SimulationSpec(4, 256, 256, 8, 250))
    await job.start()
    await client.disconnect()
    await asyncio.sleep(1.25)
    await client.connect()
    recovered = await client.recover(request_id)
    at_reconnect = await recovered.status()
    final = await recovered.wait()
    if recovered.identifier != job.identifier or final.outcome != job_pb.OUTCOME_SUCCEEDED:
      raise RuntimeError("job did not survive client disconnection")
    report.update(
      exercised=True,
      scope="synthetic images and jobs on the connected server; no instrument operations",
      run_id=run_id,
      profiles=profiles,
      cancellation_during_transfer=cancellation,
      disconnected_job={
        "request_id": request_id,
        "job_id": job.identifier,
        "completed_at_reconnect": at_reconnect.completed,
        "completed": final.completed,
        "outcome": job_pb.Outcome.Name(final.outcome),
      },
      final_status=MessageToDict(await client.status(), preserving_proto_field_name=True),
    )
    return report


async def demo(report_path: Path | None) -> None:
  with tempfile.TemporaryDirectory(prefix="celigo-grpc-") as temporary:
    directory = Path(temporary)
    certs = create_certificates(directory / "certificates")
    with (directory / "server.log").open("w+") as server_log:
      process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pylabrobot.revvity.celigo.grpc.cli",
        "serve",
        "--port",
        "0",
        "--certificates",
        str(certs.directory),
        "--data-dir",
        str(directory / "data"),
        stdout=asyncio.subprocess.PIPE,
        stderr=server_log,
      )
      try:
        assert process.stdout is not None
        ready = await asyncio.wait_for(process.stdout.readline(), 30)
        if not ready:
          server_log.seek(0)
          raise RuntimeError(f"simulator startup failed: {server_log.read()}")
        endpoint = json.loads(ready)["endpoint"]
        async with PrototypeClient(endpoint, certs) as client:
          initialization = await client.create_initialization("demo-initialize")
          await initialization.start()
          if (await initialization.wait()).outcome != job_pb.OUTCOME_SUCCEEDED:
            raise RuntimeError("simulated initialization failed")
          profiles = []
          for name, spec in (
            ("preview", SimulationSpec(1, 256, 256, 8, 0)),
            ("full", SimulationSpec(3, 2048, 2048, 16, 100)),
            ("maximum", SimulationSpec(3, 4096, 2048, 16, 100)),
          ):
            profiles.append(await measure_profile(client, name, spec))
          cancellation = await measure_cancel(client)
        report = {
          "measured_at": datetime.now(timezone.utc).isoformat(),
          "transport": "gRPC with mutual TLS",
          "scope": "two local processes; synthetic images; SQLite persistence",
          "grpcio_version": importlib.metadata.version("grpcio"),
          "protobuf_version": importlib.metadata.version("protobuf"),
          "python_version": sys.version.split()[0],
          "profiles": profiles,
          "cancellation_during_transfer": cancellation,
        }
        rendered = json.dumps(report, indent=2) + "\n"
        if report_path:
          report_path.write_text(rendered)
        print(rendered, end="")
      finally:
        if process.returncode is None:
          process.terminate()
          try:
            await asyncio.wait_for(process.wait(), 15)
          except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  certificates = commands.add_parser("certificates", help="create fresh local test identities")
  certificates.add_argument("directory", type=Path)
  server = commands.add_parser("serve", help="run a hardware-free mutual-TLS loopback server")
  server.add_argument("--certificates", type=Path, required=True)
  server.add_argument(
    "--data-dir", type=Path, required=True, help="persistent local job/image directory"
  )
  server.add_argument("--storage-quota-mib", type=int, default=256)
  server.add_argument("--max-jobs", type=int, default=1024)
  server.add_argument("--port", type=int, default=50051)
  server.add_argument("--device-id", default="celigo-sim-01")
  experiment = commands.add_parser(
    "demo", help="measure encrypted images and jobs in two processes"
  )
  experiment.add_argument("--report", type=Path)
  probe = commands.add_parser("check", help="inspect an existing server without starting jobs")
  probe.add_argument("--endpoint", required=True)
  probe.add_argument("--certificates", type=Path, required=True)
  probe.add_argument("--device-id", required=True)
  probe.add_argument(
    "--exercise-simulator",
    action="store_true",
    help="also run synthetic jobs and whole-image transfer checks; retains test images",
  )
  probe.add_argument("--report", type=Path)
  args = parser.parse_args()
  logging.basicConfig(level=logging.ERROR)
  if args.command == "certificates":
    create_certificates(args.directory)
  elif args.command == "serve":
    asyncio.run(serve(args))
  elif args.command == "check":
    report = asyncio.run(
      check(
        args.endpoint,
        Certificates(args.certificates),
        args.device_id,
        exercise_simulator=args.exercise_simulator,
      )
    )
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
      args.report.write_text(rendered)
    print(rendered, end="")
  else:
    asyncio.run(demo(args.report))


if __name__ == "__main__":
  main()
