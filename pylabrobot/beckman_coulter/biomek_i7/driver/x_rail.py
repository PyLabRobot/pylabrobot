"""The shared X rail: keeps the two pods from running into each other (design.md I7).

Both pods ride one rail and nothing below the driver stops them colliding: the Master
Controller plans each pod on its own, and each board knows only its pod. Biomek keeps them
apart. Before a pod moves, its swept range must clear the other pod's
range; if it does not, the other pod is moved out of the way first and the mover waits. A pod's
range counts its bridge, its pipettor and its gripper, turned and opened as it is, with what it
holds (`driver/pod_geometry.py`).

`XRail` applies the same rule. It differs in three ways:
- **It refuses** a move into where the other pod stands, or will stand once its move ends,
  rather than moving the other pod; `make_space` comes later.
- **It waits** when a move conflicts only with a move of the other pod still running, then
  checks again: the other pod is passing through, not staying (Biomek runs such moves together).
- **It reserves** every move in flight, so two moves started together cannot pass through each
  other.

It works from the declared configuration (`driver/configuration.py`), never from the resource
tree, so it holds with no deck. Poses are in Biomek's units: deck X in cm, GR in degrees, GG in cm.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Dict, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import PodConfiguration, Side

logger = logging.getLogger(__name__)

OTHER: Dict[Side, Side] = {"left": "right", "right": "left"}

# How long a move waits for the other pod's move to clear, in seconds. Longer than any single
# move (the Master Controller's Move All timeouts are seconds), so a wait this long means
# something is stuck.
WAIT_TIMEOUT = 120.0


class RailCollisionError(ValueError):
  """A move would bring a pod into the other pod's X range. Raised before anything is sent."""


@dataclass(frozen=True)
class PodPose:
  """Where a pod is, as the rail needs it: deck X (cm) and its gripper's pose, None when not
  known (then every pose the gripper could take is counted)."""

  x: float
  gripper: Optional[g.GripperPose] = None


class XRail:
  """Where each pod is on the rail, and what each pod's move in flight has reserved.

  Args:
    pods: the declared configuration of each pod on the rail. A side with None has a pod whose
      geometry is not declared: it cannot be guarded, so neither pod may move.
  """

  def __init__(self, pods: Dict[Side, Optional[PodConfiguration]]):
    self.pods = pods
    self._pose: Dict[Side, Optional[PodPose]] = {side: None for side in pods}
    self._reserved: Dict[Side, Optional[g.Range]] = {side: None for side in pods}
    self._end: Dict[Side, Optional[g.Range]] = {side: None for side in pods}
    self._done: Dict[Side, asyncio.Event] = {side: asyncio.Event() for side in pods}
    self._lock = asyncio.Lock()
    # How to read a pod's pose when the rail does not know it. Set by the driver.
    self.readers: Dict[Side, Callable[[], Awaitable[PodPose]]] = {}
    self._models = {
      side: g.GripperModel.from_bif(p.gripper_model) if p and p.gripper_model else None
      for side, p in pods.items()
    }

  def _require_pod(self, side: Side) -> PodConfiguration:
    pod = self.pods.get(side)
    if pod is None:
      raise RuntimeError(
        f"the {side} pod has no declared geometry, so the X rail cannot keep the pods apart; "
        "pass a declared configuration that includes it"
      )
    return pod

  def _gripper(self, side: Side, pose: Optional[g.GripperPose]) -> Optional[g.Gripper]:
    model = self._models.get(side)
    if model is None or pose is None:
      return None
    return g.Gripper(model, pose)

  def range_at(self, side: Side, pose: PodPose) -> g.Range:
    """The X a pod takes up at a pose. An unknown gripper pose counts every pose it could take:
    the whole of GR's travel, fully open."""
    pod = self._require_pod(side)
    if pose.gripper is not None or self._models.get(side) is None:
      return g.pod_x_range(pod.geometry, pose.x, self._gripper(side, pose.gripper))
    return self.swept(side, PodPose(pose.x, None), PodPose(pose.x, None))

  def swept(self, side: Side, start: PodPose, end: PodPose) -> g.Range:
    """The X a pod takes up while moving from `start` to `end`."""
    pod = self._require_pod(side)
    a, b = start.gripper, end.gripper
    if self._models.get(side) is not None and (a is None or b is None):
      # Not known: count every pose the gripper could take, fully open, holding what it holds.
      known = a or b
      held = known.held if known is not None else None
      gr_low, gr_high = pod.limits["GR"]
      gg_open = pod.limits["GG"][1]
      a, b = g.GripperPose(gr_low, gg_open, held), g.GripperPose(gr_high, gg_open, held)
    return g.swept_x_range(
      pod.geometry, start.x, end.x, self._gripper(side, a), self._gripper(side, b)
    )

  def update(self, side: Side, pose: Optional[PodPose]) -> None:
    """Record where a pod is, or None when not known (after homing, or a move that failed). A
    known pose ends the reservation a failed move left, but never one of a move still running: a
    read can come back after that move started."""
    if side not in self.pods:
      return
    self._pose[side] = pose
    if pose is not None and not self._in_flight(side):
      self._reserved[side] = None

  def _in_flight(self, side: Side) -> bool:
    return self._end[side] is not None

  def occupied(self, side: Side) -> Optional[g.Range]:
    """The X a pod takes up now: its move's reservation while one is in flight or a failed one
    left, else its range where it is. None when its position is not known."""
    if self._reserved.get(side) is not None:
      return self._reserved[side]
    pose = self._pose.get(side)
    return None if pose is None else self.range_at(side, pose)

  def check(self, side: Side, start: PodPose, end: PodPose) -> g.Range:
    """Check a move of `side` against the other pod. Returns the move's swept range.

    Raises:
      RailCollisionError: if the swept range overlaps where the other pod stands, or will stand
        once its move ends.
      RuntimeError: if the other pod's position is not known and the move goes towards it, or a
        pod on the rail has no declared geometry.
    """
    swept = self.swept(side, start, end)
    other = OTHER[side]
    if other not in self.pods:
      return swept
    self._require_pod(other)
    end_range = self._end.get(other)
    if end_range is not None:  # the other pod is moving
      if g.grid_overlap(swept, end_range):
        raise self._collision(side, swept, other, end_range, "will stand")
      return swept  # any overlap with its sweep is waited out in `moving`
    taken = self.occupied(other)
    if taken is None:
      if (
        (swept[1] > self.range_at(side, start)[1])
        if side == "left"
        else (swept[0] < self.range_at(side, start)[0])
      ):
        raise RuntimeError(
          f"the {other} pod's position is not known, and the {side} pod's move goes towards it"
        )
      return swept
    if g.grid_overlap(swept, taken):
      raise self._collision(side, swept, other, taken, "stands")
    return swept

  def _collision(
    self, side: Side, swept: g.Range, other: Side, taken: g.Range, how: str
  ) -> RailCollisionError:
    return RailCollisionError(
      f"the {side} pod's move would take up deck X {swept[0]:.2f}..{swept[1]:.2f} cm, into "
      f"{taken[0]:.2f}..{taken[1]:.2f}, where the {other} pod {how}"
    )

  async def ensure_known(self, side: Side) -> None:
    """Read a pod's pose if the rail does not know it and a reader is set. Moves nothing."""
    if side in self.pods and self.occupied(side) is None and side in self.readers:
      self.update(side, await self.readers[side]())

  @contextlib.asynccontextmanager
  async def moving(self, side: Side, start: PodPose, end: PodPose) -> AsyncIterator[None]:
    """Hold a move's swept range for as long as the move runs.

    Checked and reserved together, under one lock. A move that conflicts only with the other
    pod's move in flight waits for that move to finish, then is checked again. On success the
    pod's pose becomes `end`. On failure it becomes unknown, but the reservation stays until a
    pose is read: the pod is somewhere in that range.

    Raises:
      RailCollisionError, RuntimeError: as `check`, before the move starts.
      TimeoutError: if the other pod's move does not finish within `WAIT_TIMEOUT`.
    """
    other = OTHER[side]
    await self.ensure_known(other)
    while True:
      async with self._lock:
        swept = self.check(side, start, end)
        running = (
          self._reserved.get(other) if other in self.pods and self._in_flight(other) else None
        )
        if running is None or not g.grid_overlap(swept, running):
          self._reserved[side] = swept
          self._end[side] = self.range_at(side, end)
          self._done[side].clear()
          break
        done = self._done[other]
      logger.info("%s pod waits for the %s pod's move to clear its path", side, other)
      await asyncio.wait_for(done.wait(), WAIT_TIMEOUT)
    try:
      yield
    except BaseException:
      self._pose[side] = None
      raise
    finally:
      self._end[side] = None
      self._done[side].set()
    self.update(side, end)
