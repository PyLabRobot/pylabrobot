"""The height a pod's tool travels at, as Biomek computes it: one path for every tool.

Biomek finds what lies under a tool's footprint swept from where it is to where it goes (its
deck's covered positions), takes the tallest of them (`deck_height`), and each tool adds its own
margins to that (`Pod._safe_heights`). The deck's items are the deck check's own boxes (each
position up to its safe height, each position obstacle up to its top), so travel and check agree.

The sweep, as Biomek's deck computes it, in whole millimetres:

- the footprint's two corners (its low X / low Y corner where it is and where it goes) ordered by
  X, each rounded down; its spans grow by 2 mm and are rounded down;
- the bounding box from the left corner to the right one plus the spans, rounded outward;
- when X changes, the band between the two lines of the move's slope through the footprint's outer
  corners: the exact swept hexagon of the rectangle, rounded outward;
- an item counts when its rectangle touches that area (edges included): outside the bounding box
  it does not; inside, on a move in Y alone it does, otherwise when it meets the band.
"""

import math
from typing import Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import (
  GripperState,
  PodModel,
  PodState,
  gripper_below,
  gripper_parts,
)

# A rectangle in Biomek's frame, cm: x0, x1, y0, y1.
Rect = Tuple[float, float, float, float]


def covered(start: Rect, end: Rect, items: Iterable[Tuple[Rect, float]]) -> Sequence[float]:
  """The tops of the `items` ((rectangle, top), cm) that the footprint covers going from `start`
  to `end` (the same footprint, where it is and where it goes; cm)."""
  sx, sy = start[0] * 10, start[2] * 10
  ex, ey = end[0] * 10, end[2] * 10
  xs = math.floor(max(start[1] - start[0], end[1] - end[0]) * 10 + 2)
  ys = math.floor(max(start[3] - start[2], end[3] - end[2]) * 10 + 2)
  (lx, ly), (rx, ry) = sorted(((sx, sy), (ex, ey)))
  lx, ly, rx, ry = math.floor(lx), math.floor(ly), math.floor(rx), math.floor(ry)
  bx0, bx1 = lx, math.ceil(rx + xs)
  by0, by1 = math.floor(min(ly, ry)), math.ceil(max(ly, ry) + ys)
  band: Optional[Tuple[float, float, float]] = None
  if rx != lx:
    s = (ry - ly) / (rx - lx)
    b = ly - s * lx
    if s > 0:
      band = (s, math.floor(b - xs * s), math.ceil(b + ys))
    else:
      band = (s, math.floor(b), math.ceil(b + ys - xs * s))
  tops = []
  for rect, top in items:
    x0, x1, y0, y1 = (v * 10 for v in rect)
    if x0 > bx1 or x1 < bx0 or y0 > by1 or y1 < by0:
      continue
    if band is not None:
      s, low, high = band
      lows, highs = (s * x0 + low, s * x1 + low), (s * x0 + high, s * x1 + high)
      if min(lows) > y1 or max(highs) < y0:
        continue
    tops.append(top)
  return tops


def deck_height(start: Rect, end: Rect, items: Iterable[Tuple[Rect, float]]) -> float:
  """The tallest top the footprint covers going from `start` to `end`, cm; -inf over nothing."""
  return max(covered(start, end, items), default=-math.inf)


def rect_of(outline: Sequence[Tuple[float, float]]) -> Rect:
  """An outline's bounds."""
  xs, ys = [p[0] for p in outline], [p[1] for p in outline]
  return min(xs), max(xs), min(ys), max(ys)


# How a tool travels by default: "safe" (Biomek's safe height over its way), "max" (as high as it
# goes), or a height, mm of its lowest point above the deck.
TraverseDefault = Union[Literal["safe", "max"], float]


def traverse_default(value: Union[str, float]) -> TraverseDefault:
  """`value` as a tool's default traverse height.

  Raises:
    ValueError: for anything but "safe", "max" or a number.
  """
  if value in ("safe", "max"):
    return value  # type: ignore[return-value]
  if isinstance(value, (int, float)) and not isinstance(value, bool):
    return float(value)
  raise ValueError(f"a default traverse height is 'safe', 'max' or a height in mm, not {value!r}")


# -- a pod's travel heights ----------------------------------------------------------------------


def head_box(config: PodConfiguration, state: PodState) -> Rect:
  """The 96-head's pipettor box seen from above (`PodXSpan` x `PodYSpan` from mandrel A1 less
  `Mandrel1XOffset` / `Mandrel1YOffset`), widened by `AvoidanceMargin`, cm."""
  geo = config.geometry
  m = geo.avoidance_margin
  x0 = state.x - geo.mandrel1_x_offset
  y0 = state.pipettor.ys[0] - geo.mandrel1_y_offset
  return x0 - m, x0 + geo.pod_x_span + m, y0 - m, y0 + geo.pod_y_span + m


def tips_block(config: PodConfiguration, state: PodState) -> Rect:
  """The block of the 96-head's loaded tips seen from above (half a pitch round the mandrels that
  carry one; every mandrel without a block given), widened by `AvoidanceMargin`, cm."""
  geo = config.geometry
  m = geo.avoidance_margin
  x, y = state.x, state.pipettor.ys[0]
  sx, sy = geo.mandrel_x_spacing, geo.mandrel_y_spacing
  c0, c1, r0, r1 = state.pipettor.tip_block or (
    0,
    (geo.mandrels_x or 1) - 1,
    0,
    (geo.mandrels_y or 1) - 1,
  )
  return (
    x + (c0 - 0.5) * sx - m, x + (c1 + 0.5) * sx + m,
    y + (r0 - 0.5) * sy - m, y + (r1 + 0.5) * sy + m,
  )  # fmt: skip


def gripper_all_angles(config: PodConfiguration, x: float, state: GripperState) -> Rect:
  """What the gripper (and what it holds) covers seen from above turned through every angle, cm:
  its turning parts each a box over its farthest point's circle about the rotation centre, its
  upper hand (which does not turn) as it is."""
  model = PodModel.of(config).gripper_model
  assert model is not None
  geo = config.geometry
  cx = x + geo.gripper_x_offset - model.pads_center_to_rotation_center
  cy = state.gy + geo.gripper_y_offset - model.finger_center_offset
  parts = gripper_parts(config, x, state)
  xs: List[float] = []
  ys: List[float] = []
  for part in parts:
    if part.name == "upper hand":
      xs += [px for px, _ in part.outline]
      ys += [py for _, py in part.outline]
      continue
    r = max(math.hypot(px - cx, py - cy) for px, py in part.outline)
    xs += [cx - r, cx + r]
    ys += [cy - r, cy + r]
  return min(xs), max(xs), min(ys), max(ys)


def channel_footprint(config: PodConfiguration, state: PodState) -> Rect:
  """What the pipettor covers seen from above for its travel height, as Biomek boxes it (cm),
  each widened by `AvoidanceMargin`:

  - the 96-head's pipettor box (`head_box`), joined with the block of its tips (`tips_block`)
    when it carries some;
  - a Span-8's band `PodXSpan` wide from probe 1's X less `Mandrel1XOffset`, across its probes
    and `Mandrel1YOffset` beyond the end ones (its box starts that far behind probe 1, as the
    logged heights over a tall neighbour behind the probes show).
  """
  x, pipettor = state.x, state.pipettor
  geo = config.geometry
  m = geo.avoidance_margin
  if config.module_type == "span8":
    x0, dy = x - geo.mandrel1_x_offset, geo.mandrel1_y_offset
    return x0 - m, x0 + geo.pod_x_span + m, min(pipettor.ys) - dy - m, max(pipettor.ys) + dy + m
  box = head_box(config, state)
  if pipettor.tips[0] <= 0:
    return box
  block = tips_block(config, state)
  return (
    min(box[0], block[0]),
    max(box[1], block[1]),
    min(box[2], block[2]),
    max(box[3], block[3]),
  )


def max_traverse_heights(config: PodConfiguration, channels: Sequence[str]) -> Dict[str, float]:
  """The highest each Z-type axis (`channels` and GZ) travels, cm of its bottom above the deck: the
  top of travel less the park margin (`ParkLocation` `ZMargin`, `GZMargin`), as Biomek parks them
  (the Multichannel's rest Z, 39.763 cm, is -256 counts). A Span-8 probe goes no higher than its
  top less `DiscardOffset`: above that its stripper pushes a tip off (Biomek travels carrying
  probes there, as its logs show)."""
  shuck = config.settings["DiscardOffset"] if config.module_type == "span8" else None
  ceilings = {}
  for name in [*channels, "GZ"]:
    top = config.limits[name][1]
    ceiling = top + config.park.get("GZMargin" if name == "GZ" else "ZMargin", 0.0)
    if shuck is not None and name != "GZ":
      ceiling = min(ceiling, top - shuck)
    ceilings[name] = ceiling
  return ceilings


def safe_heights(
  config: PodConfiguration,
  start: PodState,
  end: PodState,
  obstacles: Obstacles,
  tip_lengths: Mapping[str, float],
  channels: Sequence[str],
) -> Dict[str, float]:
  """Biomek's safe height for each Z-type axis (`channels`, and GZ with a gripper) on the way
  from `start` to `end`, cm: the tallest deck item (`deck_height`) under each tool's footprint
  swept from here to there, floored by `GlobalMinSafeHeight` and the axis's lowest reach, plus the
  tool's own: a channel's tip (`tip_lengths`; the 96-head's `SubMandrelSafeHeight` without one)
  and `AdditionalSafeHeight`; the gripper, swept over every angle, its offset
  to its lowest point (what it holds included) and `AdditionalSafeHeightGripper`;
  `AvoidanceMargin` on every one. The items are the deck
  check's own boxes (`obstacles.deck`, exempting none): a travel at this height is one the check
  passes."""
  d = config
  items = [(rect_of(p.outline), p.z_high) for p in obstacles.deck]
  geo = d.geometry
  floor = float(d.settings["GlobalMinSafeHeight"])
  margin = geo.avoidance_margin
  lengths = tip_lengths
  out: Dict[str, float] = {}
  if d.module_type == "span8":
    h = deck_height(channel_footprint(d, start), channel_footprint(d, end), items)
    lowest = max(d.limits[n][0] for n in channels)
    for n in channels:
      out[n] = max(floor, h, lowest) + lengths.get(n, 0.0) + geo.additional_safe_height + margin
  else:
    # The head's box joined with its tips' block, by the tip's length (`SubMandrelSafeHeight`
    # without tips): the logged heights clear what lies under the box alone by the tip's length.
    h = deck_height(channel_footprint(d, start), channel_footprint(d, end), items)
    tip = lengths.get("Z") or (geo.sub_mandrel_safe_height or 0.0)
    out["Z"] = max(floor, h, d.limits["Z"][0]) + tip + geo.additional_safe_height + margin
  g0, g1 = start.gripper, end.gripper
  if g0 is not None and g1 is not None:
    wide = max(g0.gg, g1.gg)
    # Swept over every angle, as Biomek's planner sweeps the gripper for its travel height.
    ra = gripper_all_angles(d, start.x, GripperState(g0.gy, g0.gz, g0.gr, wide, g0.held))
    rb = gripper_all_angles(d, end.x, GripperState(g1.gy, g1.gz, g1.gr, wide, g1.held))
    hg = deck_height(ra, rb, items)
    below = gripper_below(d, start.x, GripperState(g0.gy, g0.gz, g0.gr, wide, g0.held))
    lowest = d.limits["GZ"][0] - below
    out["GZ"] = max(floor, hg, lowest) + below + geo.additional_safe_height_gripper + margin
  return out
