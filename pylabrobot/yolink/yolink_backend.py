from abc import abstractmethod

from pylabrobot.machine import MachineBackend


class YoLinkBackend(MachineBackend):
  def __init__(self, api_key: str, device_id: str):
    super().__init__()
    self.api_key = api_key
    self.device_id = device_id

  @abstractmethod
  async def get_status(self):
    pass

  # Add your YoLink API methods here
