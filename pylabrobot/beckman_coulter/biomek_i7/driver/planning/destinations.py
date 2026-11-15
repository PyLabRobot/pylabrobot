"""Where a pod's planned operations go: each operation's targets (counts) from the pod's
configuration and pose, as Biomek sets them. The pod plans and executes the way there."""

from typing import Dict, Literal, Mapping, Optional, Sequence

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration


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
