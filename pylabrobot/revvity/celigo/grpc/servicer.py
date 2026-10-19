"""Translate Celigo gRPC requests into instrument-session operations."""

import grpc
from google.protobuf.empty_pb2 import Empty

from pylabrobot.revvity.celigo.grpc.job_specification import Brightfield
from pylabrobot.revvity.celigo.grpc.session import CeligoSession
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2 as pb
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.transport import require_device, rpc_errors
from pylabrobot.rpc.v1 import device_pb2 as device_pb
from pylabrobot.rpc.v1 import jobs_pb2 as job_pb


class CeligoServicer(rpc.CeligoServiceServicer):
  """Map the device-specific wire contract onto one existing Celigo session."""

  def __init__(self, session: CeligoSession) -> None:
    """Bind the session without constructing a driver or creating work."""
    self.session = session

  async def GetConfiguration(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> pb.CeligoConfiguration:
    """Return configured capture limits without accessing hardware."""
    async with rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      return self.session.configuration()

  async def OpenDoor(
    self, request: device_pb.DeviceRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Eject the drawer through the instrument session."""
    async with rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      await self.session.open_door()
      return Empty()

  async def CloseDoor(
    self, request: pb.CloseDoorRequest, context: grpc.aio.ServicerContext
  ) -> Empty:
    """Load the drawer at an explicit sample-relative position."""
    async with rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      if not request.HasField("sample_x_mm") or not request.HasField("sample_y_mm"):
        raise ValueError("sample_x_mm and sample_y_mm are required")
      await self.session.close_door(
        sample_x_mm=request.sample_x_mm, sample_y_mm=request.sample_y_mm
      )
      return Empty()

  async def CreateInitialization(
    self, request: pb.InitializationRequest, context: grpc.aio.ServicerContext
  ) -> job_pb.JobRef:
    """Create an inert initialization job and return its remote reference."""
    async with rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      job = await self.session.create_initialization(request.request_id, request.configuration_id)
      return job.ref

  async def CreateBrightfieldCapture(
    self, request: pb.BrightfieldRequest, context: grpc.aio.ServicerContext
  ) -> job_pb.JobRef:
    """Decode capture settings and return the created job's remote reference."""
    async with rpc_errors(context):
      require_device(request.device_id, self.session.device_id)
      if not request.configuration_id or not request.HasField("settings"):
        raise ValueError("configuration_id and settings are required")
      job = await self.session.create_brightfield_capture(
        request.request_id,
        Brightfield.from_proto(request.settings),
        request.configuration_id,
      )
      return job.ref
