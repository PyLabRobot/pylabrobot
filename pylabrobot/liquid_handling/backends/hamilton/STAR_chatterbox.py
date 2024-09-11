# pylint: disable=unused-argument

from typing import Optional

from pylabrobot.liquid_handling.backends.hamilton.STAR import STAR

class STARChatterboxBackend(STAR):
  """ Chatterbox backend for "STAR" """

  def __init__(self, num_channels: int = 8):
    """ Initialize a chatter box backend. """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True

  async def request_tip_presence(self):
    return list(range(self.num_channels))

  async def request_machine_configuration(self):
    # configuration is directly copied from a STARlet w/ 8p, iswap, and autoload
    self.conf = {"kb": 11, "kp": 8, "id": 2}
    return self.conf

  async def request_extended_configuration(self):
    self._extended_conf = {"ka": 65537, "ke": 0, "xt": 30, "xa": 30, "xw": 8000, "xl": 3, "xn": 0,
                          "xr": 0, "xo": 0, "xm": 3500, "xx": 6000, "xu": 3700, "xv": 3700, "kc": 0,
                          "kr": 0, "ys": 90, "kl": 360, "km": 360, "ym": 6065, "yu": 60, "yx": 60,
                          "id": 3}
    #extended configuration is directly copied from a STARlet w/ 8p, iswap, and autoload
    return self.extended_conf

  async def request_iswap_initialization_status(self) -> bool:
    return True

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  async def send_command(self, module, command, *args, **kwargs):
    print(f"Sending command: {module}{command} with args {args} and kwargs {kwargs}.")

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True
  ) -> Optional[str]:
    print(f"Sending raw command: {command}")
    return None
