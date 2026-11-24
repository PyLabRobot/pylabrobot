"""Host VSpin operations and shared device and job services."""

from pylabrobot.agilent.vspin.grpc.servicer import VSpinServicer
from pylabrobot.agilent.vspin.grpc.session import VSpinSession
from pylabrobot.agilent.vspin.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc.device import DeviceServer
from pylabrobot.rpc.jobs import JobService
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


class VSpinServer(DeviceServer):
  """Expose one instrument session through VSpin and shared RPC services."""

  def __init__(self, session: VSpinSession, *, host: str = "127.0.0.1", port: int = 0) -> None:
    """Register services around the supplied session."""
    self.session = session
    super().__init__(session, host=host, port=port)
    job_rpc.add_JobServiceServicer_to_server(JobService(session), self.server)
    rpc.add_VSpinServiceServicer_to_server(VSpinServicer(session), self.server)

  async def start(self) -> None:
    """Recover durable records before accepting requests."""
    try:
      await self.session.open()
      await super().start()
    except BaseException:
      await self.stop()
      raise

  async def stop(self) -> None:
    """Stop accepting commands and finish hardware cleanup before closing storage."""
    self.session.closing = True
    await super().stop()
    await self.session.stop()
