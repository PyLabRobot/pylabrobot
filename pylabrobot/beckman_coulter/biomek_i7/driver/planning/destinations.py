"""Where a pod's planned operations go: each operation's targets (counts) from the pod's
configuration and pose, as Biomek sets them. The pod plans and executes the way there."""

from typing import Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration

# How far past the next 1/16 cm grid line a pod moved out of the other's way puts its edge (cm),
# as Biomek's clearing of the way does.
GRID_GAP = 1 / 64


def park_targets(
  config: PodConfiguration, side: Literal["left", "right"], over: Optional[Mapping[str, int]]
) -> Dict[str, int]:
  """Biomek's park location (`ParkLocation`): X `XMargin` in from the pod's own end of the rail and
  `Y` (unless `over` gives them: a pod whose park declares none, a Span-8 over its wash station),
  the gripper's `GR` and `GY`."""
  p = config.park
  targets: Dict[str, int] = dict(over or {})
  if "XMargin" in p and "X" not in targets:
    low, high = config.limits["X"]
    x = low + p["XMargin"] if side == "left" else high - p["XMargin"]
    targets["X"] = config.to_counts("X", x)
  if "Y" in p and "Y" not in targets:
    targets["Y"] = config.to_counts("Y", p["Y"])
  targets.update({a: config.to_counts(a, p[a]) for a in ("GR", "GY") if a in p})
  return targets


def rest_heights(config: PodConfiguration, channels: Sequence[str]) -> Dict[str, int]:
  """Where the channels rest once parked: the top less the park margin (`ZMargin`), and on a
  Span-8 less `DiscardOffset` as well (39.1079 cm on Z1, 9029 counts: lower than the probes
  travel)."""
  shuck = config.settings["DiscardOffset"] if config.module_type == "span8" else 0.0
  margin = config.park.get("ZMargin", 0.0)
  return {z: config.to_counts(z, config.limits[z][1] + margin - shuck) for z in channels}


def step_aside(
  config: PodConfiguration,
  side: Literal["left", "right"],
  x: float,
  gr: float,
  gg: float,
  held: Optional[g.HeldLabware],
  zone: g.Range,
  range_at: Callable[[float, g.GripperPose], g.Range],
) -> List[Tuple[float, float, Tuple[float, float]]]:
  """Biomek's ways out of the X `zone` another pod's move needs, in the order it tries them: each
  (GR, X target, where X may go). Away from the other pod, the pod's near edge at the next
  1/16 cm grid line past the zone plus `GRID_GAP`, its gripper turned to the nearest quarter turn;
  then the same with the gripper at 0 degrees (90 if the nearest quarter was 0). `range_at`: the
  pod's X range at an X and gripper pose (the rail's)."""
  low, high = config.limits["X"]
  nearest = round(gr / 90) * 90.0
  out = []
  for turn in (nearest, 90.0 if nearest == 0 else 0.0):
    left, right = range_at(x, g.GripperPose(turn, gg, held))
    if side == "right":
      target = (g.grid_cell(zone[1]) + 1) / g.GRID_PER_CM + GRID_GAP - (left - x)
      bounds = (x, high)
    else:
      target = g.grid_cell(zone[0]) / g.GRID_PER_CM - GRID_GAP - (right - x)
      bounds = (low, x)
    out.append((turn, target, bounds))
  return out
