"""What passes over the deck: how far the tips on it reach below a channel's stop disc."""

from typing import Optional

from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot


def get_longest_tip_overhang(reference_frame: Resource) -> Optional[float]:
  """The longest overhang below a stop disc of any tip under `reference_frame`, in mm.

  A rack's spots count as the tips they make, whether or not tip tracking is on; a tip already
  mounted counts as itself. A tool, which is not a tip, does not count.

  Args:
    reference_frame: the resource whose subtree is searched, e.g. a deck.

  Returns:
    The overhang, its length less its fitting depth, or None if there is no tip.
  """
  longest: Optional[float] = None
  for resource in reference_frame.get_all_children():
    if isinstance(resource, TipSpot):
      tip = resource.make_tip()
    elif isinstance(resource, Tip):
      tip = resource
    else:
      continue
    overhang = tip.get_size_z() - tip.fitting_depth
    if longest is None or overhang > longest:
      longest = overhang
  return longest


def get_safe_deck_height_from_tips(
  reference_frame: Resource, stop_disc_z_max: float, margin: float = 5.0
) -> float:
  """How high a resource may stand under the longest tip under `reference_frame`, in mm.

  The stop discs' highest point less that tip's overhang below it, less `margin`.

  Args:
    reference_frame: the resource whose subtree is searched, e.g. a deck.
    stop_disc_z_max: the highest the channels' stop discs travel at, in mm of that frame, e.g. the
      top of the pipettes' Z range.
    margin: kept clear under the longest tip, in mm.

  Raises:
    ValueError: If there is no tip, so no tip decides the height.
  """
  overhang = get_longest_tip_overhang(reference_frame)
  if overhang is None:
    raise ValueError("the deck holds no tip, so no tip decides how high a resource may stand")
  return round(stop_disc_z_max - overhang - margin, 2)
