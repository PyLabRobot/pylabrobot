import abc
from typing import Optional

from pylabrobot.io.usb import USB


class HamiltonHeaterShakerInterface(abc.ABC):
  """Interface for communicating with Hamilton Heater Shakers.

  Either a control box or a STAR: the API is the same.
  """

  @abc.abstractmethod
  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    pass


class HamiltonHeaterShakerBox(HamiltonHeaterShakerInterface):
  """USB control box for Hamilton Heater Shaker devices."""

  def __init__(
    self,
    id_vendor: int = 0x8AF,
    id_product: int = 0x8002,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
  ):
    self.io = USB(
      id_vendor=id_vendor,
      id_product=id_product,
      human_readable_device_name="Hamilton Heater Shaker Box",
      device_address=device_address,
      serial_number=serial_number,
    )
    self._id = 0

  def _generate_id(self) -> int:
    """Continuously generate unique ids 0 <= x < 10000."""
    self._id += 1
    return self._id % 10000

  async def setup(self):
    await self.io.setup()

  async def stop(self):
    await self.io.stop()

  async def send_hhs_command(self, index: int, command: str, **kwargs) -> str:
    args = "".join([f"{key}{value}" for key, value in kwargs.items()])
    id_ = str(self._generate_id()).zfill(4)
    await self.io.write(f"T{index}{command}id{id_}{args}".encode())
    return (await self.io.read()).decode("utf-8")
