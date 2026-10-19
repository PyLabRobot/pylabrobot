"""Host Celigo operations and shared device, job, and image services."""

from pylabrobot.revvity.celigo.grpc.limits import MAX_MESSAGE_BYTES
from pylabrobot.revvity.celigo.grpc.servicer import CeligoServicer
from pylabrobot.revvity.celigo.grpc.session import CeligoSession
from pylabrobot.revvity.celigo.grpc.v1 import service_pb2_grpc as rpc
from pylabrobot.rpc import images, jobs
from pylabrobot.rpc.device import DeviceServer
from pylabrobot.rpc.v1 import images_pb2_grpc as image_rpc
from pylabrobot.rpc.v1 import jobs_pb2_grpc as job_rpc


class CeligoServer(DeviceServer):
  """Expose one instrument session through Celigo and shared RPC services."""

  def __init__(self, session: CeligoSession, *, host: str = "127.0.0.1", port: int = 0) -> None:
    """Build transport around the session without opening physical hardware."""
    self.session = session
    super().__init__(session, host=host, port=port, maximum_message_bytes=MAX_MESSAGE_BYTES)
    job_rpc.add_JobServiceServicer_to_server(jobs.JobService(session), self.server)
    image_rpc.add_ImageServiceServicer_to_server(images.ImageService(session), self.server)
    rpc.add_CeligoServiceServicer_to_server(CeligoServicer(session), self.server)

  async def start(self) -> None:
    """Recover durable records before accepting requests; never replay interrupted work."""
    try:
      await self.session.open()
      await super().start()
    except BaseException:
      await self.stop()
      raise

  async def stop(self) -> None:
    """Stop accepting commands, finish in-flight work, and close the owned store."""
    self.session.closing = True
    await super().stop()
    await self.session.stop()
