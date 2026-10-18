"""Read schema-version-1 records through the shared RPC contracts without replay."""

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import grpc
import pytest

from pylabrobot.revvity.celigo.grpc.client import PrototypeClient, acquisition_metadata
from pylabrobot.revvity.celigo.grpc.server import SimulatorServer
from pylabrobot.rpc.security import create_certificates
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


def restore_fixture(directory: Path) -> dict[str, bytes]:
  """Restore synthetic SQLite records serialized with the original Celigo wire types."""
  tables = json.loads(Path(__file__).with_name("legacy_store.json").read_text())
  directory.mkdir()
  expected = {}
  with sqlite3.connect(directory / "jobs.sqlite3") as db:
    for name in ("metadata", "jobs", "frames", "transitions"):
      table = tables[name]
      db.execute(table["schema"])
      rows = [
        [base64.b64decode(value["bytes"]) if isinstance(value, dict) else value for value in row]
        for row in table["rows"]
      ]
      placeholders = ",".join("?" for _ in rows[0])
      db.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
      if name == "frames":
        expected = {row[0]: row[4] for row in rows if row[4] is not None}
  return expected


@pytest.mark.asyncio
async def test_stored_jobs_images_and_release_survive_protocol_split(tmp_path: Path) -> None:
  """Recover interrupted work, preserve pixels/metadata and release flags, and never replay."""
  directory = tmp_path / "data"
  expected = restore_fixture(directory)
  certs = create_certificates(tmp_path / "certs")
  instance_id = ""
  for _ in range(2):
    server = SimulatorServer(certs, device_id="legacy-device", data_dir=directory)
    await server.start()
    try:
      async with PrototypeClient(server.endpoint, certs, device_id="legacy-device") as client:
        info = await client.device_info()
        assert info.instance_id and info.instance_id != instance_id
        instance_id = info.instance_id
        assert (await client.status()).state == device_pb.OPERATIONAL_STATE_UNINITIALIZED
        assert len(server.simulator.jobs) == 3
        for name in ("retained", "released", "interrupted"):
          job = await client.recover(name)
          status = await job.status()
          assert status.state == job_pb.JOB_STATE_FINISHED and status.completed == 1
          assert status.outcome == (
            job_pb.OUTCOME_INTERRUPTED if name == "interrupted" else job_pb.OUTCOME_SUCCEEDED
          )
          retention = await job.image_retention()
          assert retention.images_released == (name == "released")
          frames = await job.list_frames()
          assert len(frames) == 1 and frames[0].available != retention.images_released
          if retention.images_released:
            with pytest.raises(grpc.aio.AioRpcError) as released:
              await job.get_frame(frames[0].frame_id)
            assert released.value.code() == grpc.StatusCode.FAILED_PRECONDITION
          else:
            frame = await job.get_frame(frames[0].frame_id)
            assert frame.image == expected[name]
            assert hashlib.sha256(frame.image).hexdigest() == frame.sha256
            assert frame.width == frame.height == 8
            assert acquisition_metadata(frame).channel == "brightfield"
        assert all(job.task is None for job in server.simulator.jobs.values())
    finally:
      await server.stop()
