"""Tecan Freedom EVO composite device.

Composes a TecanEVODriver with PIP (liquid handling) and GripperArm (RoMa)
capabilities using the v1b1 Device architecture.
"""

from __future__ import annotations

import logging
from typing import Optional

from pylabrobot.tecan.evo.arm import TecanGripperArm
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Resource

from pylabrobot.tecan.evo.air_pip_backend import AirEVOPIPBackend
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.errors import TecanError
from pylabrobot.tecan.evo.pip_backend import EVOPIPBackend
from pylabrobot.tecan.evo.roma_backend import EVORoMaBackend

logger = logging.getLogger(__name__)


class TecanEVO(Resource, Device):
  """Tecan Freedom EVO liquid handling platform.

  Composes a USB driver with independent-channel pipetting (PIP) and
  optionally a RoMa plate handling arm (GripperArm).

  Example::

    from pylabrobot.tecan.evo import TecanEVO, TecanEVODriver
    from pylabrobot.resources.tecan.tecan_decks import EVO150Deck

    deck = EVO150Deck()
    evo = TecanEVO(name="evo", deck=deck, diti_count=8, air_liha=True)
    await evo.setup()

    # Pipetting via PIP capability
    await evo.pip.pick_up_tips(...)
    await evo.pip.aspirate(...)

    # Plate handling via arm capability (if RoMa present)
    await evo.arm.pick_up_resource(...)

    await evo.stop()

  Args:
    name: Device name.
    deck: Deck resource (e.g. EVO150Deck).
    diti_count: Number of channels configured for disposable tips.
    air_liha: If True, use AirEVOPIPBackend (ZaapMotion). Otherwise syringe.
    has_roma: If True, include RoMa arm capability.
    packet_read_timeout: USB packet read timeout in seconds.
    read_timeout: USB read timeout in seconds.
    write_timeout: USB write timeout in seconds.
  """

  def __init__(
    self,
    name: str = "evo",
    deck: Optional[Resource] = None,
    diti_count: int = 0,
    air_liha: bool = False,
    has_roma: bool = True,
    packet_read_timeout: int = 12,
    read_timeout: int = 60,
    write_timeout: int = 60,
    size_x: float = 1315,
    size_y: float = 780,
    size_z: float = 765,
  ):
    driver = TecanEVODriver(
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    Resource.__init__(self, name=name, size_x=size_x, size_y=size_y, size_z=size_z)
    Device.__init__(self, driver=driver)

    # Assign deck as child resource
    if deck is not None:
      self.assign_child_resource(deck, location=Coordinate.zero())

    deck_ref = deck or self

    # PIP capability
    pip_backend: EVOPIPBackend
    if air_liha:
      pip_backend = AirEVOPIPBackend(driver=driver, deck=deck_ref, diti_count=diti_count)
    else:
      pip_backend = EVOPIPBackend(driver=driver, deck=deck_ref, diti_count=diti_count)
    self.pip = PIP(backend=pip_backend)
    self._pip_backend = pip_backend

    # RoMa arm capability
    self.arm: Optional[TecanGripperArm] = None
    if has_roma:
      roma_backend = EVORoMaBackend(driver=driver, deck=deck_ref)
      self.arm = TecanGripperArm(backend=roma_backend, reference_resource=deck_ref)

    # Capabilities list: PIP first (LiHa PIA), then arm (RoMa PIA + park)
    caps: list = [self.pip]
    if self.arm is not None:
      caps.append(self.arm)
    self._capabilities = caps

  async def setup(self):
    """Initialize EVO with collision-safe ordering.

    If the LiHa is already initialized but the RoMa needs PIA, the LiHa
    is homed first to clear the RoMa's path.
    """
    await self._driver.setup()

    # Initialize PIP (LiHa) first
    await self.pip._on_setup()

    # Before RoMa init, check if RoMa needs PIA — if so, home LiHa first
    if self.arm is not None:
      roma_backend = self.arm.backend
      roma_needs_init = await self._roma_needs_init(roma_backend)

      if (
        roma_needs_init
        and self._pip_backend.liha is not None
        and self._pip_backend._z_range is not None
      ):
        logger.info("RoMa needs PIA — homing LiHa to clear path.")
        z_range = self._pip_backend._z_range
        num_ch = self._pip_backend.num_channels
        await self._pip_backend.liha.set_z_travel_height([z_range] * num_ch)
        await self._pip_backend.liha.position_absolute_all_axis(45, 1031, 90, [z_range] * num_ch)

      await self.arm._on_setup()

    self._setup_finished = True

  async def _roma_needs_init(self, roma_backend: EVORoMaBackend) -> bool:
    """Check if RoMa needs PIA (not already initialized)."""
    from pylabrobot.tecan.evo.firmware.arm_base import EVOArm

    arm = EVOArm(self._driver, "C1")  # type: ignore[arg-type]
    try:
      roma_err = await arm.read_error_register()
    except TecanError as e:
      if e.error_code == 5:
        return False  # RoMa not present
      return True

    if roma_err and all(c == "@" for c in roma_err):
      return False  # Already initialized
    return True
