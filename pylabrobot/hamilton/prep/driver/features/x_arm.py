"""The X-arm: the gantry the pipetting channels ride, modelled as the STAR's arm."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from pylabrobot.hamilton.star.driver.features.x_arm import XArmConfiguration
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)

# The STAR's large arm, whose measurements and 3D model the Prep's arm borrows.
_STAR_ARM = XArmConfiguration()


@dataclass
class PrepXArmConfiguration:
  """How the arm is modelled.

  None of this is read off a Prep: it reports no size for its arm, so the arm is modelled as the STAR's
  large arm, with that arm's measurements and 3D model.
  """

  size_x: float = field(default=_STAR_ARM.large_arm_size_x)
  """How wide the arm is, in mm, end to end."""
  reference_point_from_left: float = field(default=_STAR_ARM.large_arm_reference_from_left)
  """Where along the arm, from its left edge in mm, the gantry's x refers to."""
  size_z: float = 140.0
  """How tall to model the arm, in mm, as the STAR's arm is modelled."""
  model: str = "hamilton_legacy_star_dual_rail_arm"
  """Which 3D model draws it: the STAR's large arm."""


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

    The gantry's x refers to a point along the arm, while a resource is located by its left front
    bottom corner, so the two differ by how far along the arm that point sits. Does nothing when the
    driver was given no deck to model into.

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
