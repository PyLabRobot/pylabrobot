"""A pod's planned path as moves: Biomek's direct candidate, each axis on its own in Biomek's
order. Each move is checked before anything is sent."""

from typing import Dict, List, Mapping

# A move: axis targets in counts, sent together.
Move = Dict[str, int]

# The order Biomek's direct candidate changes the travel axes in, after the rises: the pipettor's
# X and Y, then the gripper's GY and GR. Axes it does not plan (the Span-8's span) go with the
# first move.
TRAVEL_ORDER = ("X", "Y", "GY", "GR")


def direct_candidate(targets: Mapping[str, int]) -> List[Move]:
  """The travel to `targets` one axis at a time, in Biomek's order (`TRAVEL_ORDER`): each move
  changes one planned axis; axes it does not plan go with the first."""
  planned = [{a: targets[a]} for a in TRAVEL_ORDER if a in targets]
  rest = {a: v for a, v in targets.items() if a not in TRAVEL_ORDER}
  if rest:
    if planned:
      planned[0] = {**rest, **planned[0]}
    else:
      planned = [rest]
  return planned
