"""The STAR's CoRe grippers: the tools, and what they take."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Optional, Tuple

from pylabrobot.resources.deck import Deck

if TYPE_CHECKING:
  from ..master import STARDriver
  from .pipettes import Pipettes
  from .x_arm import XArm


@dataclasses.dataclass
class CoreGrippersConfiguration:
  """Facts of the grip itself; positions, speeds and accelerations are the pipettes'."""

  grip_strength_range: Tuple[int, int] = (0, 99)


class CoreGrippers:
  """The CoRe grip tools a pair of channels carries, and what they take."""

  # Grip strength index, 0 (low) to 99 (high).
  default_grip_strength: int = 15
  # Z speed while a resource is held, in mm/s.
  default_z_speed_with_resource_held: float = 50.0

  def __init__(
    self, driver: "STARDriver", configuration: Optional[CoreGrippersConfiguration] = None
  ) -> None:
    """
    Args:
      driver: the driver to send commands through.
      configuration: the grip's device facts. Defaults to `CoreGrippersConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or CoreGrippersConfiguration()
    self._tools_mounted = False

  # -- what carries them ---------------------------------------------------------------------------

  @property
  def arm(self) -> "XArm":
    """The arm carrying these grippers."""
    return next(a for a in self._driver.arms if a.core_grippers is self)

  @property
  def _pipettes(self) -> "Pipettes":
    """The channels that carry the tools.

    Raises:
      RuntimeError: If the arm has no pipettes.
    """
    pipettes = self.arm.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to carry the grippers; have you called `star.setup()`?")
    return pipettes

  @property
  def _deck(self) -> Deck:
    """The deck positions are measured from.

    Raises:
      RuntimeError: If the driver was given no deck.
    """
    if self._driver.deck is None:
      raise RuntimeError("the CoRe grippers are placed from the deck; this driver was given none")
    return self._driver.deck

  # -- state ---------------------------------------------------------------------------------------

  @property
  def tools_mounted(self) -> bool:
    """Whether the tools are on the channels."""
    return self._tools_mounted

  def _require_mounted(self) -> None:
    """Raise unless the tools are on the channels.

    Raises:
      RuntimeError: If they are not.
    """
    if not self._tools_mounted:
      raise RuntimeError(
        "the CoRe gripper tools are not mounted. Call "
        "`await star.core_grippers.pick_up_tools()` first, or use "
        "`async with star.core_grippers.mounted():`."
      )
