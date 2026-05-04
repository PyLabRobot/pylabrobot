"""Generic kinematic helpers shared across arm backends.

Per-arm forward/inverse kinematics live in the arm's own module
(e.g. ``pylabrobot.paa.kx2.kinematics``); this file is for utilities
that work over *any* forward-kinematics function regardless of DOF
count, joint topology, or link layout.
"""

from math import sqrt
from typing import Callable, Dict, Hashable, Iterator, Tuple, TypeVar

from pylabrobot.resources import Coordinate


K = TypeVar("K", bound=Hashable)


def gripper_speed(
  fk: Callable[[Dict[K, float]], Coordinate],
  joints: Dict[K, float],
  joint_velocities: Dict[K, float],
  eps: float = 1e-6,
) -> float:
  """Cartesian gripper speed at this joint snapshot.

  Computes |d/dt fk(joints(t))| via a central finite difference along the
  joint-velocity direction -- two ``fk`` evaluations regardless of DOF
  count. Equivalent to forming the Jacobian and computing |J*q_dot|, but
  avoids materializing J.

  Args:
    fk: forward kinematics, joints dict -> ``Coordinate``.
    joints: current joint positions, in whatever units ``fk`` expects.
    joint_velocities: per-joint rate (same units / second). Missing keys
      are treated as zero, so callers can pass only the moving joints.
    eps: finite-difference step in joint units. ``Coordinate`` rounds
      to 0.1 micron, so ``eps * q_dot`` must shift the coordinate by
      more than that or the diff collapses to zero -- pass ``eps=1e-3``
      for typical mm/deg-scale arms. Default ``1e-6`` is fine for
      non-rounding fixtures (tests).

  Returns:
    Magnitude of the Cartesian velocity in whatever length units ``fk``
    returns, per second.
  """
  plus = fk({k: q + eps * joint_velocities.get(k, 0.0) for k, q in joints.items()})
  minus = fk({k: q - eps * joint_velocities.get(k, 0.0) for k, q in joints.items()})
  inv_2eps = 1.0 / (2.0 * eps)
  dx = (plus.x - minus.x) * inv_2eps
  dy = (plus.y - minus.y) * inv_2eps
  dz = (plus.z - minus.z) * inv_2eps
  return sqrt(dx * dx + dy * dy + dz * dz)


def sample_gripper_speed_along_trajectory(
  fk: Callable[[Dict[K, float]], Coordinate],
  joints_start: Dict[K, float],
  joint_deltas: Dict[K, float],
  joint_velocities: Dict[K, float],
  num_samples: int = 20,
  eps: float = 1e-6,
) -> Iterator[Tuple[float, float]]:
  """Yield ``(alpha, gripper_speed)`` along the joint-space path
  ``joints_start + alpha * joint_deltas`` for ``alpha`` in ``[0, 1]``.

  ``joint_deltas`` is the *direction-aware* per-axis change (signed):
  for unlimited-travel rotary axes the caller should resolve the
  shortest-way / always-CW / always-CCW convention before passing in, so
  this helper walks the same path the arm physically takes (e.g. a
  ShortestWay move from 350 deg to 10 deg has ``delta = +20``, not
  ``-340``).

  ``joint_velocities`` is held constant across all samples (representing
  cruise-phase joint rates); each sample returns the gripper speed at
  that pose.

  Use ``alpha`` to plot, find the worst-case sample, etc.::

    max_speed = max(s for _, s in sample_gripper_speed_along_trajectory(...))

  Missing keys in either ``joints_start`` or ``joint_deltas`` are
  treated as zero, so callers can pass only moving joints.
  """
  if num_samples < 2:
    raise ValueError(f"num_samples must be >= 2, got {num_samples}")
  keys = set(joints_start) | set(joint_deltas)
  for i in range(num_samples):
    alpha = i / (num_samples - 1)
    sample = {
      k: joints_start.get(k, 0.0) + alpha * joint_deltas.get(k, 0.0) for k in keys
    }
    yield alpha, gripper_speed(fk, sample, joint_velocities, eps=eps)


def joint_velocities_for_max_gripper_speed(
  fk: Callable[[Dict[K, float]], Coordinate],
  joints_start: Dict[K, float],
  joint_deltas: Dict[K, float],
  joint_max_velocities: Dict[K, float],
  max_gripper_speed: float,
  num_samples: int = 20,
  eps: float = 1e-6,
) -> Dict[K, float]:
  """Signed joint velocities such that worst-case gripper speed along
  the joint-space path ``joints_start + alpha * joint_deltas`` is at
  most ``max_gripper_speed`` -- or each axis's firmware ceiling,
  whichever is tighter.

  Sign of each output velocity is taken from ``joint_deltas`` (axes
  with zero delta get zero velocity). The caller is responsible for
  resolving any wrap-around / shortest-way / direction conventions when
  computing ``joint_deltas`` -- this helper just walks the path it's
  handed.

  Gripper speed is linear in joint velocities (the Jacobian is), so a
  single sweep determines the scale factor exactly: if running every
  axis at ``joint_max_velocities`` produces a worst-case path speed
  ``M``, the returned velocities are scaled by ``min(1, max_gripper_speed / M)``.
  No iteration.

  Equally applicable to acceleration -- pass joint max accelerations as
  ``joint_max_velocities`` and the gripper-accel cap as
  ``max_gripper_speed``; the math is identical because both reduce to
  ``|J * rate|`` at each pose.

  Args:
    fk: forward kinematics, joints dict -> ``Coordinate``.
    joints_start: starting joint positions.
    joint_deltas: signed per-axis change along the trajectory. Axis sign
      determines output velocity direction; zero means "don't move".
    joint_max_velocities: per-axis firmware ceiling, *unsigned*.
    max_gripper_speed: Cartesian cap, in whatever length units ``fk``
      returns, per second.
    num_samples: poses sampled along the path. 20 is plenty for a SCARA.
    eps: see :func:`gripper_speed`.

  Returns:
    Dict of *signed* joint velocities, one per key in
    ``joint_max_velocities``.
  """
  signed_max: Dict[K, float] = {}
  for k in joint_max_velocities:
    delta = joint_deltas.get(k, 0.0)
    if delta > 0:
      signed_max[k] = joint_max_velocities[k]
    elif delta < 0:
      signed_max[k] = -joint_max_velocities[k]
    else:
      signed_max[k] = 0.0

  max_v = max(
    s
    for _, s in sample_gripper_speed_along_trajectory(
      fk, joints_start, joint_deltas, signed_max, num_samples=num_samples, eps=eps
    )
  )
  if max_v == 0.0:
    return signed_max
  scale = min(1.0, max_gripper_speed / max_v)
  return {k: scale * v for k, v in signed_max.items()}
