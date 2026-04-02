"""STAR device: wires STARDriver backends to PIP/Head96/iSWAP capability frontends."""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.capabilities.arms.arm import GripperArm
from pylabrobot.capabilities.arms.orientable_arm import OrientableArm
from pylabrobot.capabilities.liquid_handling.head96 import Head96
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonDeck
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonCoreGrippers

from .chatterbox import STARChatterboxDriver
from .core import CoreGripper
from .driver import STARDriver


class STAR(Device):
  """Hamilton STAR liquid handler.

  User-facing device that wires capability frontends (PIP, Head96, iSWAP) to the
  STARDriver's backends after hardware discovery during setup().
  """

  def __init__(self, deck: HamiltonDeck, chatterbox: bool = False):
    driver = STARChatterboxDriver() if chatterbox else STARDriver()
    super().__init__(driver=driver)
    self.driver: STARDriver = driver
    self.deck = deck
    self.pip: PIP  # set in setup()
    self.head96: Optional[Head96] = None  # set in setup() if installed
    self.iswap: Optional[OrientableArm] = None  # set in setup() if installed

  async def setup(self):
    await self.driver.setup()

    # PIP is always present.
    self.pip = PIP(backend=self.driver.pip)
    self._capabilities = [self.pip]

    # Head96 only if the hardware has a 96-head installed.
    if self.driver.head96 is not None:
      self.head96 = Head96(backend=self.driver.head96)
      self._capabilities.append(self.head96)

    # iSWAP only if installed.
    if self.driver.iswap is not None:
      self.iswap = OrientableArm(backend=self.driver.iswap, reference_resource=self.deck)
      self._capabilities.append(self.iswap)

    for cap in self._capabilities:
      await cap._on_setup()
    self._setup_finished = True

  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    self.head96 = None
    self.iswap = None

  # -- CoRe grippers ---------------------------------------------------------

  @asynccontextmanager
  async def core_grippers(
    self,
    front_channel: int = 7,
    front_offset: Coordinate = Coordinate.zero(),
    back_offset: Coordinate = Coordinate.zero(),
    traversal_height: float = 280.0,
  ) -> AsyncIterator[GripperArm]:
    """Context manager that picks up CoRe gripper tools on enter and returns them on exit.

    Usage::

      async with star.core_grippers(front_channel=7) as arm:
        await arm.move_resource(plate, destination)
    """

    # Park iSWAP first if it's out — the arms share the X drive.
    if self.iswap is not None and not self.iswap.backend.parked:  # type: ignore[attr-defined]
      await self.iswap.backend.park()

    core_grippers_resource = self.deck.get_resource("core_grippers")
    if not isinstance(core_grippers_resource, HamiltonCoreGrippers):
      raise TypeError("core_grippers resource must be HamiltonCoreGrippers")

    back_channel = front_channel - 1
    loc = core_grippers_resource.get_absolute_location()
    xs = loc.x + front_offset.x
    back_y = int(loc.y + core_grippers_resource.back_channel_y_center + back_offset.y)
    front_y = int(loc.y + core_grippers_resource.front_channel_y_center + front_offset.y)
    z_offset = front_offset.z

    await self.driver.pick_up_core_gripper_tools(
      x_position=xs,
      back_channel_y=back_y,
      front_channel_y=front_y,
      back_channel=back_channel,
      front_channel=front_channel,
      begin_z=235.0 + z_offset,
      end_z=225.0 + z_offset,
      traversal_height=traversal_height,
    )

    backend = CoreGripper(driver=self.driver)
    arm = GripperArm(backend=backend, reference_resource=self.deck, grip_axis="y")

    try:
      yield arm
    finally:
      await self.driver.return_core_gripper_tools(
        x_position=xs,
        back_channel_y=back_y,
        front_channel_y=front_y,
        begin_z=215.0 + z_offset,
        end_z=205.0 + z_offset,
        traversal_height=traversal_height,
      )
