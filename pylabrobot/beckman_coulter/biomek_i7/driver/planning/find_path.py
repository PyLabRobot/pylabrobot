"""How a pod's planned operation gets where it goes, stage by stage, as Biomek plans it: the rise
out of the deck positions the pod stands in (its start-in-collision escape), the rise to the
travel heights, the travel, the descent. Each stage is planned from where the one before leaves
the pod: the direct way when the check passes it (`clear`), else Biomek's search (`search_to`).
The pod merges the stages' moves as one path (`merge.merge_path`), as Biomek merges the path it
concatenates, before anything moves.

`clear(moves, start=None)` checks moves made in order (from the pod's position record by
default) as `move_axes` checks them; `search_to(destination)` is the pod's search there
(`search.pod_search`), raising `search.NoPathError`.
"""

from typing import Callable, Dict, List, Mapping, Sequence

from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.planning import merge
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.check import Escape
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import PodState

Clear = Callable[..., bool]
SearchTo = Callable[[Mapping[str, int]], List[merge.Move]]


def direct_or_searched(
  move: merge.Move, destination: Mapping[str, int], clear: Clear, search_to: SearchTo
) -> List[merge.Move]:
  """`move` alone when it is clear; else the search's path to `destination`.

  Raises:
    search.NoPathError: when the search finds no path.
  """
  if clear([move]):
    return [move]
  return search_to(destination)


def travel(targets: Mapping[str, int], clear: Clear, search_to: SearchTo) -> List[merge.Move]:
  """The travel to `targets`, the rise made: Biomek's direct candidate (`merge.direct_candidate`)
  when each of its moves is clear; else the search's path.

  Raises:
    search.NoPathError: when the search finds no path.
  """
  candidate = merge.direct_candidate(targets)
  if clear(candidate):
    return candidate
  return search_to(targets)


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
