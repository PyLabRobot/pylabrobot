from typing import Any, Optional

from pylabrobot.hamilton.usb.driver import HamiltonUSBDriver


class HamiltonHeaterShakerBox(HamiltonUSBDriver):
  """USB control box for Hamilton Heater Shaker devices."""

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
  ):
    super().__init__(
      id_product=0x8002,
      device_address=device_address,
      serial_number=serial_number,
    )

  @property
  def module_id_length(self) -> int:
    return 2

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    idx = resp.find("id")
    if idx != -1:
      id_str = resp[idx + 2 : idx + 6]
      if id_str.isdigit():
        return int(id_str)
    return None

  def check_fw_string_error(self, resp: str):
    pass

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    return {"raw": resp}
