"""Client handle for VSpin initialization and spin jobs."""

from __future__ import annotations

from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.jobs import Job
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


class VSpinJob(Job):
  """A remote job with access to its retained spin settings."""

  def __init__(
    self,
    stub: job_rpc.JobServiceAsyncStub,
    operations: rpc.VSpinServiceAsyncStub,
    ref: job_pb.JobRef,
    timeout: float = 20,
  ) -> None:
    """Share the device client's channel and operation stubs."""
    super().__init__(stub, ref, timeout)
    self.operations = operations

  async def get_spin_settings(self) -> Spin:
    """Read the exact settings without creating or starting another spin."""
    return Spin.from_proto(await self.operations.GetSpinSettings(self.ref, timeout=self.timeout))
