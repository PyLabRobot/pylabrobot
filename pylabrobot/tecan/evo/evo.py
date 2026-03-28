"""Tecan Freedom EVO composite device.

Composes a TecanEVODriver with PIP (liquid handling) and GripperArm (RoMa)
capabilities using the v1b1 Device architecture.
"""

from __future__ import annotations

from typing import Optional

from pylabrobot.arms.arm import GripperArm
from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources import Coordinate, Resource

from pylabrobot.tecan.evo.air_pip_backend import AirEVOPIPBackend
from pylabrobot.tecan.evo.driver import TecanEVODriver
from pylabrobot.tecan.evo.pip_backend import EVOPIPBackend
from pylabrobot.tecan.evo.roma_backend import EVORoMaBackend


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
    if air_liha:
      pip_backend = AirEVOPIPBackend(driver=driver, deck=deck_ref, diti_count=diti_count)
    else:
      pip_backend = EVOPIPBackend(driver=driver, deck=deck_ref, diti_count=diti_count)
    self.pip = PIP(backend=pip_backend)

    # RoMa arm capability
    self.arm: Optional[GripperArm] = None
    if has_roma:
      roma_backend = EVORoMaBackend(driver=driver, deck=deck_ref)
      self.arm = GripperArm(backend=roma_backend, reference_resource=deck_ref)

    # Capabilities list: arm first (must park before LiHa X-init)
    caps = []
    if self.arm is not None:
      caps.append(self.arm)
    caps.append(self.pip)
    self._capabilities = caps
