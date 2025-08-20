from pylabrobot.machine import Machine


class YoLinkDevice(Machine):
  def __init__(self, backend: YoLinkBackend):
    super().__init__(backend=backend)

  async def get_device_status(self):
    return await self.backend.get_status()
