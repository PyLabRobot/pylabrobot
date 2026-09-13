"""The X-arm: the gantry the pipetting channels ride."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)


@dataclass
class PrepXArmConfiguration:
  """How the arm is modelled.

  Measured on the arm rather than read off the device: the Prep reports no size for it.
  """

  size_x: float = 66.0
  """How wide the arm is, in mm."""
  size_y: float = 532.0
  """How deep the arm is, in mm. Its back is flush with the back of the device."""
  size_z: float = 69.0
  """How tall the arm is, in mm."""
  reference_point_from_left: float = -80.0
  """Where the gantry's x refers to, in mm from the arm's left edge. The channels hang to the arm's left,
  so their axis - which is what the Prep reports - sits about 80 mm left of that edge."""
  model: str = "hamilton_prep_x_arm"
  """Which 3D model draws it. None ships yet, so the viewer draws a box of this size."""
  appearance: Dict[str, Any] = field(
    default_factory=lambda: {"color": 0xC0C4C8, "metalness": 0.6, "roughness": 0.35}
  )
  """How the viewer draws the arm: silver, and metallic."""


class PrepXArm:
  """The X-arm the pipetting channels ride.

  Reached as `driver.x_arm`. The channels share its X: the firmware reports the gantry's position in
  each channel's `GetPositions` entry, not for the arm itself.
  """

  def __init__(self, driver: "PrepDriver", configuration: Optional[PrepXArmConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: how the arm is modelled. Defaults to `PrepXArmConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or PrepXArmConfiguration()
    # The arm on the deck, when the driver was given one. Setup puts it there; reads and moves keep it
    # in step. Without a deck it stays None and nothing is modelled.
    self.resource: Optional[Resource] = None

  def update_location_by_reference_point(self, x: float) -> None:
    """Record where the arm is on the resource that models it.

    The gantry's x refers to the channels' axis, which sits a fixed distance from the arm's left edge,
    while a resource is located by its left front bottom corner, so the two differ by that distance.
    Does nothing when the driver was given no deck to model into.

    Args:
      x: where the reference point is now, in mm on the deck.
    """
    if self.resource is None or self.resource.location is None:
      return
    self.resource.location = Coordinate(
      x - self.configuration.reference_point_from_left,
      self.resource.location.y,
      self.resource.location.z,
    )

  async def request_position(self) -> Optional[float]:
    """Request where along X the arm is.

    Read from `GetPositions`, whose entries all carry the gantry's X, and recorded on the resource that
    models the arm.

    Returns:
      The position in mm, or None when no channel reported one.
    """
    response = await self._driver.client.execute(PrepCmd.PrepGetPositions())
    if not response or not response.positions:
      return None
    x = float(response.positions[0].position_x)
    self.update_location_by_reference_point(x)
    return x
