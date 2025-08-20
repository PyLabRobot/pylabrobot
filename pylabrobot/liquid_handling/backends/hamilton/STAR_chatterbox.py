from contextlib import asynccontextmanager
from typing import List, Literal, Optional, Union

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.resources.well import Well


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(self, num_channels: int = 8, core96_head_installed: bool = True):
    """Initialize a chatter box backend."""
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True
    self.core96_head_installed = core96_head_installed

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
    return {"kb": 11, "kp": self.num_channels, "id": 2}

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
    return self._extended_conf

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

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    print(
      f"stepping off foil | wells: {wells} | front channel: {front_channel} | back channel: {back_channel} | "
      f"move inwards: {move_inwards} | move height: {move_height}"
    )

  async def move_channel_y(self, channel: int, y: float):
    print(f"moving channel {channel} to y: {y}")

  @asynccontextmanager
  async def slow_iswap(self, wrist_velocity: int = 20_000, gripper_velocity: int = 20_000):
    """A context manager that sets the iSWAP to slow speed during the context."""
    assert 20 <= gripper_velocity <= 75_000, "Gripper velocity out of range."
    assert 20 <= wrist_velocity <= 65_000, "Wrist velocity out of range."

    messages = ["start slow iswap"]
    try:
      yield
    finally:
      messages.append("end slow iswap")
      print(" | ".join(messages))

  async def pierce_foil(
    self,
    wells: Union[Well, List[Well]],
    piercing_channels: List[int],
    hold_down_channels: List[int],
    move_inwards: float,
    spread: Literal["wide", "tight"] = "wide",
    one_by_one: bool = False,
    distance_from_bottom: float = 20.0,
  ):
    print(
      f"piercing foil | wells: {wells} | piercing channels: {piercing_channels} | hold down channels: {hold_down_channels} | "
      f"move inwards: {move_inwards} | spread: {spread} | one by one: {one_by_one} | distance from bottom: {distance_from_bottom}"
    )
