"""The way out of the deck positions a pod stands in, as Biomek's planner leaves them when a
planned operation starts in collision (a pod left low in one by the operation before)."""

from typing import Dict, Mapping, Sequence

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.check import Escape
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import PodState


def escape_targets(
  config: PodConfiguration,
  pose: PodState,
  out: Escape,
  heights: Mapping[str, float],
  channels: Sequence[str],
) -> Dict[str, int]:
  """The rise out of the deck positions the pod stands in (`out`, `check.escape`): each part inside
  them (a channel, or the gripper) straight up until its bottom is at their tops, or to its height
  in `heights` (cm) if that is higher. When a channel escapes, the other channels below their
  heights rise to them in the same move, as Biomek's merged path does (the logged escapes are one
  Z move of the probes); the others stay."""
  targets: Dict[str, int] = {}

  def up(name: str, now: float, rise: float) -> None:
    targets[name] = config.to_counts(name, max(now + rise, heights.get(name, now)))

  escaping = any(rise > 0 for rise in out.pipettor)
  for name, z, rise in zip(channels, pose.pipettor.zs, out.pipettor):
    if rise > 0 or (escaping and z < heights.get(name, z)):
      up(name, z, rise)
  if out.gripper > 0 and pose.gripper is not None:
    up("GZ", pose.gripper.gz, out.gripper)
  return targets
