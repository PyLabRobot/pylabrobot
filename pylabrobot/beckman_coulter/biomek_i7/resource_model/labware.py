"""Biomek's labware by its class name, and Biomek's touch-off distance.

The definitions are PyLabRobot's (`pylabrobot.resources.beckman_coulter`), one per Biomek labware
or tip class. This module names them as a Biomek method does (`LABWARE`, `TIPS`), so a method's
Instrument Setup can be put on the deck (`layouts.place_labware`), and gives the distance Biomek
touches off at for a container with a height↔volume function and a `BiomekTip`
(`biomek_touch_distance`).
"""

import math
from typing import Any, Callable, Dict, List, Optional

from pylabrobot.resources.beckman_coulter import (
  BiomekTip,
  beckman_coulter_biomek_24_wellplate_square_rb,
  beckman_coulter_biomek_96_deepwell_square,
  beckman_coulter_biomek_96_tiprack_50uL_filter,
  beckman_coulter_biomek_96_tiprack_190uL_filter,
  beckman_coulter_biomek_96_tiprack_230uL,
  beckman_coulter_biomek_96_tiprack_1025uL_filter,
  beckman_coulter_biomek_96_tiprack_1070uL,
  beckman_coulter_biomek_96_wellplate_flat,
  beckman_coulter_biomek_96_wellplate_vb,
  beckman_coulter_biomek_modular_reservoir,
  beckman_coulter_biomek_reservoir,
  beckman_coulter_biomek_reservoir_350cb,
  beckman_coulter_biomek_tip_50uL_filter,
  beckman_coulter_biomek_tip_190uL_filter,
  beckman_coulter_biomek_tip_230uL,
  beckman_coulter_biomek_tip_1025uL_filter,
  beckman_coulter_biomek_tip_1070uL,
)
from pylabrobot.resources.container import Container
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.well import CrossSectionType, Well

TIPS: Dict[str, Callable[[str], BiomekTip]] = {
  "T50F": beckman_coulter_biomek_tip_50uL_filter,
  "T190F": beckman_coulter_biomek_tip_190uL_filter,
  "T230": beckman_coulter_biomek_tip_230uL,
  "T1025F": beckman_coulter_biomek_tip_1025uL_filter,
  "T1070": beckman_coulter_biomek_tip_1070uL,
}
TIP_BOXES: Dict[str, Callable[..., TipRack]] = {
  "BC50F": beckman_coulter_biomek_96_tiprack_50uL_filter,
  "BC190F": beckman_coulter_biomek_96_tiprack_190uL_filter,
  "BC230": beckman_coulter_biomek_96_tiprack_230uL,
  "BC1025F": beckman_coulter_biomek_96_tiprack_1025uL_filter,
  "BC1070": beckman_coulter_biomek_96_tiprack_1070uL,
}
PLATES: Dict[str, Callable[..., Plate]] = {
  "Thermo96VB_PP": beckman_coulter_biomek_96_wellplate_vb,
  "BCFlat96": beckman_coulter_biomek_96_wellplate_flat,
  "BCDeep96Square": beckman_coulter_biomek_96_deepwell_square,
  "Thomson_24w_Square_RB": beckman_coulter_biomek_24_wellplate_square_rb,
  "ThermoNUNC_350CB": beckman_coulter_biomek_reservoir_350cb,
  "Reservoir": beckman_coulter_biomek_reservoir,
  "ModularReservoir": beckman_coulter_biomek_modular_reservoir,
}
# The lid class a plate's definition seats on it (`with_lid`).
LIDS = {
  "Thermo96VB_PP": "TiterplateLid",
  "BCFlat96": "TiterplateLid",
  "Thomson_24w_Square_RB": "Thomson_24w_Square_RB_Lid",
  "ThermoNUNC_350CB": "TiterplateLid",
}
LABWARE: Dict[str, Callable[..., Resource]] = {**TIP_BOXES, **PLATES}


def _named(table: Dict[str, Callable], class_name: str, kind: str) -> str:
  """The table's key for `class_name`: Biomek's classes name each other in any case."""
  if class_name in table:
    return class_name
  match = {k.lower(): k for k in table}.get(class_name.lower())
  if match is None:
    raise NotImplementedError(f"no PyLabRobot definition of Biomek {kind} class {class_name!r}")
  return match


def biomek_labware_classes() -> List[str]:
  """Every Biomek labware class with a definition here."""
  return sorted(LABWARE)


def biomek_labware(
  class_name: str, name: str, with_tips: bool = True, lid: Optional[str] = None
) -> Any:
  """The definition of Biomek labware class `class_name` (`BC230`, `Thermo96VB_PP`, ...): a
  `TipRack` or a `Plate`.

  Args:
    with_tips: for a tip box, whether it holds its tips.
    lid: the lid class to seat on it (`LIDS`), or "default" for the one its definition has.

  Raises:
    NotImplementedError: for a class with no definition yet (tube racks, ...).
    ValueError: for a lid its definition does not have.
  """
  key = _named(LABWARE, class_name, "labware")
  if key in TIP_BOXES:
    return TIP_BOXES[key](name, with_tips=with_tips)
  if lid is None:
    return PLATES[key](name)
  own = LIDS.get(key)
  if own is None or lid.lower() not in ("default", own.lower()):
    raise ValueError(
      f"{key} has no seat for a {lid} lid here" + (f"; its lid is {own}" if own else "")
    )
  return PLATES[key](name, with_lid=True)


def biomek_tip(class_name: str, name: str) -> BiomekTip:
  """A tip of Biomek tip class `class_name` (`T1070`, `T190F`, ...)."""
  return TIPS[_named(TIPS, class_name, "tip")](name)


def _cross_section_area_mm2(container: Container, z_mm: float) -> Optional[float]:
  """Cross-section area, mm^2, `z_mm` above the cavity bottom from the height↔volume curve."""
  if not container.supports_compute_height_volume_functions():
    return None
  depth = container.get_size_z()
  low, high = max(0.0, min(z_mm, depth) - 0.05), min(depth, max(z_mm, 0.0) + 0.05)
  if high <= low:
    return None
  try:
    area = (
      container.compute_volume_from_height(high) - container.compute_volume_from_height(low)
    ) / (high - low)
  except ValueError:
    return None
  return area if area > 0 else None


def _wall_distance_mm(container: Container, z_mm: float, angle: float) -> float:
  """How far, mm, the wall is from the container's axis at `z_mm`, in direction `angle` (radians).

  Uses the volume curve's slope as area, shaped like the top outline (circle or rectangle).
  """
  hx, hy = container.get_size_x() / 2, container.get_size_y() / 2
  circle = isinstance(container, Well) and container.cross_section_type == CrossSectionType.CIRCLE
  area = _cross_section_area_mm2(container, z_mm)
  if area is not None:
    if circle:
      return math.sqrt(area / math.pi)
    scale = math.sqrt(area / (4 * hx * hy))
    hx, hy = hx * scale, hy * scale
  elif circle:
    return min(hx, hy)
  c, s = abs(math.cos(angle)), abs(math.sin(angle))
  return min(hx / c if c > 1e-12 else math.inf, hy / s if s > 1e-12 else math.inf)


def biomek_touch_distance(
  container: Resource, tip: Tip, above_bottom: float, theta: float = 90.0
) -> Optional[float]:
  """Biomek's touch-off distance, mm, for a container with a height↔volume function and a
  `BiomekTip`, the tip's end `above_bottom` mm over the well's bottom: the wall's distance at that
  height in direction (360 - theta) degrees, less the tip's radius where it crosses the rim; 0 when
  that leaves no room. None without a volume function or a `BiomekTip`. Pass it as
  `TipTouch.distance`."""
  if not isinstance(container, Container) or not isinstance(tip, BiomekTip):
    return None
  if not container.supports_compute_height_volume_functions():
    return None
  wall = _wall_distance_mm(container, above_bottom, math.radians(360.0 - theta))
  radius = tip.radius_at(container.get_size_z() - above_bottom)
  return max(wall - radius, 0.0)
