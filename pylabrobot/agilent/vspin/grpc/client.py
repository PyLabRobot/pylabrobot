"""Python client for VSpin operations and shared device and job services."""

from typing_extensions import Self

from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.remote_job import VSpinJob
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2 as pb
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.device import DeviceClient
from pylabrobot.rpc.jobs import SERVICE_NAME as JOB_SERVICE
from pylabrobot.rpc.jobs import JobClient
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


class VSpinClient(DeviceClient):
  """Expose VSpin operations using one channel for device and job calls."""

  expected_device_type = "VSpin"

  def __init__(self, endpoint: str, device_id: str = "vspin-01", timeout: float = 20) -> None:
    """Configure the endpoint and expected instrument identity."""
    super().__init__(endpoint, device_id, timeout)
    self.jobs: JobClient
    self.operations: rpc.VSpinServiceAsyncStub

  async def connect(self) -> Self:
    """Validate identity and required services before binding operation clients."""
    await super().connect()
    try:
      info = await self.get_device_info()
      if not {JOB_SERVICE, "vspin.v1.VSpinService"}.issubset(info.services):
        raise ValueError("device does not advertise the required VSpin and job services")
      assert self.channel is not None
      self.jobs = JobClient(self.channel, self.device_id, self.timeout)
      self.operations = rpc.VSpinServiceStub(self.channel)
    except BaseException:
      await self.disconnect()
      raise
    return self

  def bind_job(self, ref: job_pb.JobRef) -> VSpinJob:
    """Bind a creation result to this device's job client."""
    job = self.jobs.bind_job(ref)
    return VSpinJob(self.jobs.stub, self.operations, job.ref, self.timeout)

  async def recover_job(self, request_id: str) -> VSpinJob:
    """Recover the original job after reconnecting."""
    return self.bind_job((await self.jobs.recover_job(request_id)).ref)

  async def get_configuration(self) -> pb.VSpinConfiguration:
    """Read the configured spin limits."""
    return await self.operations.GetConfiguration(self.request, timeout=self.timeout)

  async def create_initialization(self, request_id: str) -> VSpinJob:
    """Create inert initialization tied to the current connection configuration."""
    return self.bind_job(
      await self.operations.CreateInitialization(
        pb.InitializationRequest(
          device_id=self.device_id, request_id=request_id, configuration_id=self.configuration_id
        ),
        timeout=self.timeout,
      )
    )

  async def create_spin(
    self,
    request_id: str,
    *,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> VSpinJob:
    """Create an inert spin with the local driver's units and defaults."""
    settings = Spin(g, duration, acceleration, deceleration)
    return self.bind_job(
      await self.operations.CreateSpin(
        pb.SpinRequest(
          device_id=self.device_id,
          request_id=request_id,
          configuration_id=self.configuration_id,
          settings=settings.to_proto(),
        ),
        timeout=self.timeout,
      )
    )

  async def open_door(self) -> None:
    """Run open door and wait for motion to finish."""
    await self.operations.OpenDoor(self.request, timeout=self.timeout)

  async def close_door(self) -> None:
    """Run close door and wait for motion to finish."""
    await self.operations.CloseDoor(self.request, timeout=self.timeout)

  async def lock_door(self) -> None:
    """Run lock door and wait for motion to finish."""
    await self.operations.LockDoor(self.request, timeout=self.timeout)

  async def unlock_door(self) -> None:
    """Run unlock door and wait for motion to finish."""
    await self.operations.UnlockDoor(self.request, timeout=self.timeout)

  async def lock_bucket(self) -> None:
    """Run lock bucket and wait for motion to finish."""
    await self.operations.LockBucket(self.request, timeout=self.timeout)

  async def unlock_bucket(self) -> None:
    """Run unlock bucket and wait for motion to finish."""
    await self.operations.UnlockBucket(self.request, timeout=self.timeout)

  async def go_to_bucket1(self) -> None:
    """Run go to bucket1 and wait for motion to finish."""
    await self.operations.GoToBucket1(self.request, timeout=self.timeout)

  async def go_to_bucket2(self) -> None:
    """Run go to bucket2 and wait for motion to finish."""
    await self.operations.GoToBucket2(self.request, timeout=self.timeout)
