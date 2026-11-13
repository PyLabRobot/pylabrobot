"""Mocked drivers and loopback services for VSpin RPC tests."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import create_autospec

from pylabrobot.agilent.vspin.grpc.server import VSpinServer
from pylabrobot.agilent.vspin.grpc.session import VSpinSession
from pylabrobot.agilent.vspin.vspin import VSpin
from pylabrobot.rpc.v1 import jobs_pb2 as pb


def make_driver():
  """Mock the existing asynchronous driver interface."""
  return create_autospec(VSpin, instance=True, spec_set=True)


def make_server(
  directory: Path,
  *,
  driver=None,
  device_id="vspin-test",
  connection_id="test",
  hardware_lock=None,
  max_jobs=1024,
):
  """Construct the real service with its hardware driver replaced."""
  driver = driver or make_driver()
  session = VSpinSession(
    directory,
    device_id,
    lambda: driver,
    hardware_lock or directory / "hardware.lock",
    connection_id=connection_id,
    max_jobs=max_jobs,
  )
  return VSpinServer(session), driver


@contextmanager
def block_calls(method):
  """Hold a mocked hardware call until released, including when an assertion fails."""
  entered, release = asyncio.Event(), asyncio.Event()

  async def wait(*_args, **_kwargs):
    """Expose entry and wait for the test to release the operation."""
    entered.set()
    await release.wait()

  method.side_effect = wait
  try:
    yield entered, release
  finally:
    release.set()
    method.side_effect = None


async def initialize(client, request_id="initialize"):
  """Run explicit initialization and require successful completion."""
  job = await client.create_initialization(request_id)
  await job.start()
  assert (await job.wait(timeout=5)).outcome == pb.OUTCOME_SUCCEEDED
  return job
