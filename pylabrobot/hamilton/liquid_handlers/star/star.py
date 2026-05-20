"""STAR device: wires STARDriver backends to PIP/Head96/iSWAP capability frontends."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.capabilities.arms.arm import FixedAxisGripperArm
from pylabrobot.capabilities.arms.orientable_arm import OrientableGripperArm
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.head96 import Head96
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate
from pylabrobot.resources.hamilton import HamiltonDeck, STARDeck, STARLetDeck
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonCoreGrippers

from .chatterbox import STARChatterboxDriver
from .core import CoreGripper
from .driver import STARDriver


class _HamiltonSTAR(Device):
  """Base class for Hamilton STAR/STARLet liquid handlers.

  Wires capability frontends (PIP, Head96, iSWAP) to the STARDriver's backends
  after hardware discovery during setup().
  """

  def __init__(self, deck: HamiltonDeck, chatterbox: bool = False):
    driver = STARChatterboxDriver(deck=deck) if chatterbox else STARDriver(deck=deck)
    super().__init__(driver=driver)
    self.driver: STARDriver = driver
    self.deck = deck
    self.pip: PIP  # set in setup()
    self.head96: Optional[Head96] = None  # set in setup() if installed
    self.iswap: Optional[OrientableGripperArm] = None  # set in setup() if installed

  async def setup(
    self,
    backend_params: Optional[BackendParams] = None,
    *,
    skip_pip: bool = False,
    skip_iswap: bool = False,
    skip_core96_head: bool = False,
    skip_autoload: bool = False,
  ):
    """Set up the STAR. Optional skip flags mirror the legacy backend.

    Args:
      backend_params: forwarded to the driver setup.
      skip_pip: if True, do not initialize the PIP capability.
      skip_iswap: if True, do not initialize the iSWAP capability.
      skip_core96_head: if True, do not initialize the 96-head capability.
      skip_autoload: if True, do not initialize the autoload.
    """
    await self.driver.setup(backend_params=backend_params)

    # PIP is always present.
    self.pip = PIP(backend=self.driver.pip, deck=self.deck)
    self._capabilities = [self.pip]

    # Head96 only if the hardware has a 96-head installed and the user did not opt out.
    if self.driver.head96 is not None and not skip_core96_head:
      self.head96 = Head96(backend=self.driver.head96, deck=self.deck)
      self._capabilities.append(self.head96)

    # iSWAP only if installed and the user did not opt out.
    if self.driver.iswap is not None and not skip_iswap:
      self.iswap = OrientableGripperArm(backend=self.driver.iswap, reference_resource=self.deck)
      self._capabilities.append(self.iswap)

    # Matches legacy: autoload runs in parallel with arm modules.
    # Arm modules run sequentially (pip → iswap → head96) because they share the left x-arm.
    async def setup_arm_modules():
      if not skip_pip:
        await self.pip._on_setup()
      if self.iswap is not None and not skip_iswap:
        await self.iswap._on_setup()
      if self.head96 is not None and not skip_core96_head:
        await self.head96._on_setup()

    async def setup_autoload():
      if self.driver.autoload is not None and not skip_autoload:
        await self.driver.autoload._on_setup()

    await asyncio.gather(setup_autoload(), setup_arm_modules())
    self._setup_finished = True

  async def stop(self):
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False
    self.head96 = None
    self.iswap = None

  # -- traversal-height convenience aliases ----------------------------------
  #
  # Mirror the top-level API from the legacy STARBackend: `set_minimum_channel_
  # traversal_height` updates the PIP backend's traversal height, and
  # `set_minimum_iswap_traversal_height` / `iswap_minimum_traversal_height`
  # update / context-scope the iSWAP backend's traversal height.

  def set_minimum_channel_traversal_height(self, traversal_height: float) -> None:
    """Set the minimum traversal height (mm) for all PIP-channel operations."""
    if not 0 < traversal_height < 285:
      raise ValueError("Traversal height must be between 0 and 285 mm")
    pip = getattr(self.driver, "pip", None)
    if pip is None:
      raise RuntimeError("STAR has not been set up yet; call setup() first.")
    pip.traversal_height = traversal_height
    if self.driver.head96 is not None:
      self.driver.head96.traversal_height = traversal_height

  def set_minimum_iswap_traversal_height(self, traversal_height: float) -> None:
    """Set the minimum traversal height (mm) for iSWAP plate movements."""
    if self.driver.iswap is None:
      raise RuntimeError(
        "iSWAP is not installed or STAR has not been set up; cannot set its traversal height."
      )
    if not 0 < traversal_height < 285:
      raise ValueError("Traversal height must be between 0 and 285 mm")
    self.driver.iswap.traversal_height = traversal_height

  def set_minimum_traversal_height(self, traversal_height: float) -> None:
    """Deprecated: use ``set_minimum_channel_traversal_height`` /
    ``set_minimum_iswap_traversal_height`` instead."""
    raise NotImplementedError(
      "set_minimum_traversal_height is deprecated. Use "
      "set_minimum_channel_traversal_height or set_minimum_iswap_traversal_height instead."
    )

  @asynccontextmanager
  async def iswap_minimum_traversal_height(self, traversal_height: float):
    """Context-manage the iSWAP traversal height for the duration of the block."""
    if self.driver.iswap is None:
      raise RuntimeError("iSWAP is not installed; cannot set its traversal height.")
    with self.driver.iswap.use_traversal_height(traversal_height):
      yield

  # -- CoRe grippers ---------------------------------------------------------

  @asynccontextmanager
  async def core_grippers(
    self,
    front_channel: int = 7,
    front_offset: Coordinate = Coordinate.zero(),
    back_offset: Coordinate = Coordinate.zero(),
    traversal_height: float = 280.0,
  ) -> AsyncIterator[FixedAxisGripperArm]:
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

    if back_y <= front_y:
      raise ValueError(
        f"back_channel_y ({back_y}) must be > front_channel_y ({front_y}) for CoRe gripper pickup"
      )
    ext = self.driver.extended_conf
    if front_y < ext.left_arm_min_y_position:
      raise ValueError(
        f"front_channel_y ({front_y}) is below the left arm's minimum y "
        f"position ({ext.left_arm_min_y_position})"
      )

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
    arm = FixedAxisGripperArm(backend=backend, reference_resource=self.deck, grip_axis="y")

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


class STAR(_HamiltonSTAR):
  """Hamilton STAR liquid handler."""

  def __init__(self, chatterbox: bool = False):
    super().__init__(deck=STARDeck(), chatterbox=chatterbox)


class STARLet(_HamiltonSTAR):
  """Hamilton STARLet liquid handler."""

  def __init__(self, chatterbox: bool = False):
    super().__init__(deck=STARLetDeck(), chatterbox=chatterbox)
