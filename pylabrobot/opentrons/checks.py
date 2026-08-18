"""Native pre-dispatch verification checks for the Opentrons Flex backend.

The Flex backend drives the robot-server Protocol Engine over HTTP, which runs its
own analysis pass. These checks rebuild the equivalent verification IN PLR — the
Hamilton STAR pattern (``hamilton/STAR_backend.py``) — so a call is refused *before*
dispatch, and PLR owns the check rather than delegating it to the vendor.
"""

from typing import List, Optional

from pylabrobot.opentrons.envelope import (
  FLEX_8CH_NOZZLE_A1_Y,
  FLEX_8CH_NOZZLE_H1_Y as FLEX_8CH_NOZZLE_H1_Y,  # re-exported for callers importing from checks
  FLEX_ENVELOPE,
  nozzle_offset_y,
)
from pylabrobot.opentrons.robot import PipetteInfo
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.errors import NoLocationError
from pylabrobot.resources.tip import Tip

# OT-3 DEFAULT_GENERAL_ARC_Z_MARGIN (motion_planning/waypoints.py:12).
_DEFAULT_ARC_MARGIN = 10.0

# Every Flex tip rack (flex_96_tiprack_50ul / 200ul / 1000ul, and the filter rack)
# models to this z (tips included). The travel plane never drops below a tip rack,
# even on a deck the model shows as empty -- a rack physically present but not in
# the model (or not yet loaded on the robot) must still be cleared.
_FLEX_TIPRACK_HEIGHT = 99.0


def check_volume_bounds(volume: float, pipette: PipetteInfo, tip: Optional[Tip] = None) -> None:
  """Refuse a liquid-handling volume the pipette or tip cannot handle.

  Mirrors STAR's firmware-range asserts (``STAR_backend.py:4161``): the pipette has an
  accurate ``[min_volume, max_volume]`` range and a mounted tip has a ``maximal_volume``
  capacity; a requested volume outside those is refused before dispatch.

  Args:
    volume: the requested aspirate/dispense volume, in µL.
    pipette: the mounted pipette (its ``min_volume``/``max_volume`` bound the range).
    tip: the tip on the channel, if known (its ``maximal_volume`` is the capacity).
      When ``None`` the tip-capacity check is skipped.

  Raises:
    ValueError: if ``volume`` exceeds the pipette max, is below the pipette min (for a
      non-zero volume), or exceeds the tip capacity.
  """
  if volume > pipette.max_volume:
    raise ValueError(
      f"volume {volume}µL exceeds pipette max {pipette.max_volume}µL ({pipette.pipette_name})"
    )
  if 0 < volume < pipette.min_volume:
    raise ValueError(
      f"volume {volume}µL is below pipette min {pipette.min_volume}µL ({pipette.pipette_name})"
    )
  if tip is not None and volume > tip.maximal_volume:
    raise ValueError(f"volume {volume}µL exceeds tip capacity {tip.maximal_volume}µL")


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


def check_position_legal(coordinate: Coordinate, deck: Optional[Resource] = None) -> None:
  """Refuse a nozzle target outside the Flex's addressable deck extents (coarse bound).

  Delegates to :data:`FLEX_ENVELOPE`. ``deck`` is accepted for call-site compatibility
  but the envelope is the machine's, not the deck resource's.
  """
  FLEX_ENVELOPE.check_bounds(coordinate)


def check_eight_channel_extents(
  nozzle_target: Coordinate,
  partial: bool,
  active_nozzle_offset_y: float = FLEX_8CH_NOZZLE_A1_Y,
) -> None:
  """Refuse an 8-channel single/partial move whose head casing exceeds the reach caps.

  Delegates to :meth:`FlexEnvelope.check_eight_channel`.
  """
  FLEX_ENVELOPE.check_eight_channel(nozzle_target, partial, active_nozzle_offset_y)


def check_target_on_deck(
  resource: Resource,
  deck: Optional[Resource] = None,
  pipette: Optional[PipetteInfo] = None,
  use_channels: Optional[List[int]] = None,
) -> None:
  """Refuse if ``resource``'s absolute location is unreachable for the pipette.

  Delegates to :meth:`FlexEnvelope.check_point`. A no-op when the resource has no
  location yet (caught by the parent/labware checks, not here).

  For an 8-channel single/partial move the *active nozzle* sets the rear reach cap, so
  the reference channel (``use_channels[0]``, paired with ``resource``) is threaded
  through as its nozzle Y offset — a front (H1) nozzle at a rear row is refused where a
  back (A1) nozzle would be allowed. Without this the permissive A1 default would let an
  H1 partial move drive the head casing past the rear cap into the back panel.
  """
  try:
    coordinate = resource.get_absolute_location(x="c", y="c", z="b")
  except NoLocationError:
    return
  channels = pipette.channels if pipette is not None else 1
  n_active = len(use_channels) if use_channels is not None else channels
  offset = (
    nozzle_offset_y(use_channels[0]) if channels == 8 and use_channels else FLEX_8CH_NOZZLE_A1_Y
  )
  FLEX_ENVELOPE.check_point(
    coordinate, channels=channels, n_active=n_active, active_nozzle_offset_y=offset
  )
