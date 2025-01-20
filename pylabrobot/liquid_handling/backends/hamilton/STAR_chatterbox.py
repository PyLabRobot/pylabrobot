from typing import Optional

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR


class STARChatterboxBackend(STAR):
  """Chatterbox backend for "STAR" """

  def __init__(self, num_channels: int = 8):
    """Initialize a chatter box backend."""
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

  async def setup(self, skip_autoload=False, skip_iswap=False, skip_core96_head=False) -> None:
    await LiquidHandlerBackend.setup(self)

  async def request_tip_presence(self):
    return list(range(self.num_channels))

  async def request_machine_configuration(self):
    # configuration byte `kb` is directly copied from a STARlet w/ 8p, iswap, and autoload
    # Bit 0:   PIP Type                           0 = 300ul       1 = 1000ul
    # Bit 1:   ISWAP                              0 = none        1 = installed
    # Bit 2:   Main front cover monitoring        0 = none        1 = installed
    # Bit 3:   Auto load                          0 = none        1 = installed
    # Bit 4:   Wash station 1                     0 = none        1 = installed
    # Bit 5:   Wash station 2                     0 = none        1 = installed
    # Bit 6:   Temp. controlled carrier 1         0 = none        1 = installed
    # Bit 7:   Temp. controlled carrier 2         0 = none        1 = installed
    self.conf = {"kb": 11, "kp": self.num_channels, "id": 2}
    return self.conf

  async def request_extended_configuration(self):
    self._extended_conf = {
      "ka": 65537,
      "ke": 0,
      "xt": 30,
      "xa": 30,
      "xw": 8000,
      "xl": 3,
      "xn": 0,
      "xr": 0,
      "xo": 0,
      "xm": 3500,
      "xx": 6000,
      "xu": 3700,
      "xv": 3700,
      "kc": 0,
      "kr": 0,
      "ys": 90,
      "kl": 360,
      "km": 360,
      "ym": 6065,
      "yu": 60,
      "yx": 60,
      "id": 3,
    }
    # extended configuration is directly copied from a STARlet w/ 8p, iswap, and autoload
    return self.extended_conf

  async def request_iswap_initialization_status(self) -> bool:
    return True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def _write_and_read_command(
    self,
    id_: Optional[int],
    cmd: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    # print(f"Sending command: {module}{command} with args {args} and kwargs {kwargs}.")
    print(cmd)
    return None

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    print(command)
    return None

  async def request_z_pos_channel_n(self, channel: int) -> float:
    return 285.0
