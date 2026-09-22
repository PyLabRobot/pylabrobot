"""Compute the Flex travel clearance from the deck's resources."""

from pylabrobot.resources import Resource
from pylabrobot.resources.errors import NoLocationError

# OT-3 DEFAULT_GENERAL_ARC_Z_MARGIN (motion_planning/waypoints.py:12).
_DEFAULT_ARC_MARGIN = 10.0

# Every Flex tip rack (flex_96_tiprack_50ul / 200ul / 1000ul, and the filter rack)
# models to this z (tips included). The travel plane never drops below a tip rack,
# even on a deck the model shows as empty -- a rack physically present but not in
# the model (or not yet loaded on the robot) must still be cleared.
_FLEX_TIPRACK_HEIGHT = 99.0


def traversal_z(deck: Resource, arc_margin: float = _DEFAULT_ARC_MARGIN) -> float:
  """The tip-safe travel plane (tip-end / critical-point frame), from the resource model.

  The higher of: the tallest labware top on the deck plus ``arc_margin``, and an
  unconditional tip-rack floor (``_FLEX_TIPRACK_HEIGHT`` + ``arc_margin``). The floor
  means travel is always above a tip rack even when the deck model shows none -- a
  rack physically present but unmodeled, or not yet loaded on the robot, is still
  cleared. Pure function of the resource tree; no server round-trip.
  """
  tops = []
  for child in deck.get_all_children():
    try:
      tops.append(child.get_absolute_location(z="t").z)
    except NoLocationError:
      continue
  computed = (max(tops) if tops else 0.0) + arc_margin
  return max(computed, _FLEX_TIPRACK_HEIGHT + arc_margin)
