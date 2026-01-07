from contextlib import asynccontextmanager
from typing import List, Literal, Optional, Union

from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.resources.well import Well


class STARChatterboxBackend(STARBackend):
  """Chatterbox backend for 'STAR'"""

  def __init__(self, num_channels: int = 8, core96_head_installed: bool = True):
    """Initialize a chatter box backend.

    Args:
      num_channels: Number of pipetting channels (default: 8)
      core96_head_installed: Whether the CoRe 96 head is installed (default: True)
    """
    super().__init__()
    self._num_channels = num_channels
    self._iswap_parked = True
    self._core96_head_installed = core96_head_installed

  async def setup(
    self,
    skip_instrument_initialization=False,
    skip_pip=False,
    skip_autoload=False,
    skip_iswap=False,
    skip_core96_head=False,
  ):
    """Initialize the chatterbox backend and detect installed modules.

    Args:
      skip_instrument_initialization: If True, skip instrument initialization.
      skip_pip: If True, skip pipetting channel initialization.
      skip_autoload: If True, skip initializing the autoload module, if applicable.
      skip_iswap: If True, skip initializing the iSWAP module, if applicable.
      skip_core96_head: If True, skip initializing the CoRe 96 head module, if applicable.
    """
    await LiquidHandlerBackend.setup(self)

    self.id_ = 0

    # Request machine information
    conf = await self.request_machine_configuration()
    self._extended_conf = await self.request_extended_configuration()

    # Parse left X-drive configuration byte (xl) to identify installed modules
    xl_value = self.extended_conf["xl"]
    # xl is a bit field: bit 0 (LSB) reserved, bit 1 = iSWAP, bit 2 = 96-head
    # Use bitwise operations to check specific bits
    self.iswap_installed = bool(xl_value & 0b10)  # Check bit 1
    self.core96_head_installed = bool(xl_value & 0b100)  # Check bit 2

    # Parse autoload from kb configuration byte
    configuration_data1 = bin(conf["kb"]).split("b")[-1].zfill(8)
    autoload_configuration_byte = configuration_data1[-4]
    self.autoload_installed = autoload_configuration_byte == "1"

    self.installations = {}

    # Mock firmware information for 96-head if installed
    if self.core96_head_installed and not skip_core96_head:
      self.installations["96head"] = {
        "fw_version_raw": "v2023-01-01",
        "fw_version": "2023-01-01",
        "fw_year": 2023,
      }

  async def request_tip_presence(self):
    return list(range(self.num_channels))

  async def request_machine_configuration(self):
    """Return mock machine configuration data.

    Configuration byte `kb` is directly copied from a STARlet with 8-channel pipettor,
    iSWAP, and autoload installed.

    Bit mapping for kb:
      Bit 0: PIP Type (0=300µL, 1=1000µL)
      Bit 1: ISWAP (0=none, 1=installed)
      Bit 2: Main front cover monitoring (0=none, 1=installed)
      Bit 3: Auto load (0=none, 1=installed)
      Bit 4: Wash station 1 (0=none, 1=installed)
      Bit 5: Wash station 2 (0=none, 1=installed)
      Bit 6: Temp. controlled carrier 1 (0=none, 1=installed)
      Bit 7: Temp. controlled carrier 2 (0=none, 1=installed)

    Returns:
      Dict with configuration parameters: kb (config byte), kp (num channels), id (command ID)
    """
    return {"kb": 11, "kp": self.num_channels, "id": 2}

  async def request_extended_configuration(self):
    """Return mock extended configuration data.

    Extended configuration is dynamically generated based on __init__ parameters.

    Returns:
      Dict with extended configuration parameters including xl byte for module detection.
    """
    # Calculate xl byte based on installed modules
    # Bit 0: (reserved)
    # Bit 1: iSWAP (always True in this mock)
    # Bit 2: 96-head (based on __init__ parameter)
    xl_value = 0b10  # iSWAP installed (bit 1)
    if self._core96_head_installed:
      xl_value |= 0b100  # Add 96-head (bit 2)
    # Result: xl = 6 (0b110) if 96-head installed, 2 (0b10) if not

    self._extended_conf = {
      "ka": 65537,
      "ke": 0,
      "xt": 30,
      "xa": 30,
      "xw": 8000,
      "xl": xl_value,  # Dynamic based on core96_head_installed from __init__
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
    return self._extended_conf

  async def request_iswap_initialization_status(self) -> bool:
    """Return mock iSWAP initialization status."""
    return True

  async def request_96head_firmware_version(self) -> str:
    """Return mock 96-head firmware version."""
    return "v2023-01-01"

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
      f"stepping off foil | wells: {wells} | front channel: {front_channel} | "
      f"back channel: {back_channel} | move inwards: {move_inwards} | move height: {move_height}"
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
      f"piercing foil | wells: {wells} | piercing channels: {piercing_channels} | "
      f"hold down channels: {hold_down_channels} | move inwards: {move_inwards} | "
      f"spread: {spread} | one by one: {one_by_one} | distance from bottom: {distance_from_bottom}"
    )

  async def move_iswap_x(self, x_position: float):
    print("moving iswap x to", x_position)

  async def move_iswap_y(self, y_position: float):
    print("moving iswap y to", y_position)

  async def move_iswap_z(self, z_position: float):
    print("moving iswap z to", z_position)
