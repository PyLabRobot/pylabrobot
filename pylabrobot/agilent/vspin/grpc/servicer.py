"""Translate VSpin RPC requests into instrument-session operations."""

import grpc
from google.protobuf.empty_pb2 import Empty

from pylabrobot.agilent.vspin.grpc.job_specification import Spin
from pylabrobot.agilent.vspin.grpc.session import VSpinSession
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2 as pb
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.transport import require_device, translate_rpc_errors
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


class VSpinServicer(rpc.VSpinServiceServicer):
  """Map the device-specific wire contract onto one VSpin session."""

  def __init__(self, session: VSpinSession) -> None:
    """Bind the session that owns the driver and execution."""
    self.session = session

  async def GetConfiguration(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.VSpinConfiguration:
    """Read configured spin limits."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      return self.session.get_configuration()

  async def CreateInitialization(
    self, request: pb.InitializationRequest, context: grpc.aio.ServicerContext
  ) -> job_pb.JobRef:
    """Create inert initialization and return its job reference."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      job = await self.session.create_initialization(request.request_id, request.configuration_id)
      return job.ref

  async def CreateSpin(
    self, request: pb.SpinRequest, context: grpc.aio.ServicerContext
  ) -> job_pb.JobRef:
    """Validate and retain spin settings for an explicit start."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      settings = Spin.from_proto(request.settings)
      job = await self.session.create_spin(request.request_id, settings, request.configuration_id)
      return job.ref

  async def GetSpinSettings(
    self, request: job_pb.JobRef, context: grpc.aio.ServicerContext
  ) -> pb.SpinSettings:
    """Retrieve the settings retained for a spin job."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      return self.session.get_spin_settings(request.job_id).to_proto()

  async def OpenDoor(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run open door through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.open_door()
      return Empty()

  async def CloseDoor(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run close door through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.close_door()
      return Empty()

  async def LockDoor(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run lock door through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.lock_door()
      return Empty()

  async def UnlockDoor(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run unlock door through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.unlock_door()
      return Empty()

  async def LockBucket(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run lock bucket through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.lock_bucket()
      return Empty()

  async def UnlockBucket(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run unlock bucket through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.unlock_bucket()
      return Empty()

  async def GoToBucket1(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run go to bucket1 through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.go_to_bucket1()
      return Empty()

  async def GoToBucket2(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Run go to bucket2 through the instrument session."""
    async with translate_rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.go_to_bucket2()
      return Empty()
