"""Per-arm configuration resolved from the controller during setup, and the axis enumeration.

The identity, limit, and envelope fields are read from the controller once at setup into a single
immutable `PreciseFlexConfiguration` record; the kinematics/flags tier is supplied or derived. The
backend holds it as `Optional[PreciseFlexConfiguration]` (None pre-setup).
"""

import dataclasses
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Literal

from pylabrobot.capabilities.arms.standard import JointPose

from . import kinematics
from .kinematics import WorkEnvelope

# -- axis addressing -------------------------------------------------------


class Axis(IntEnum):
  BASE = 1
  SHOULDER = 2
  ELBOW = 3
  WRIST = 4
  GRIPPER = 5
  RAIL = 6


# ---------------------------------------------------------------------------
# Configuration - resolved once at setup
# ---------------------------------------------------------------------------


# -- device configuration --------------------------------------------------


@dataclass(frozen=True)
class PreciseFlexConfiguration:
  """Device configuration resolved once at setup; immutable afterwards.

  The identity/limit/envelope fields are read from the controller (`pd <DataID>`
  via ``request_parameter`` and the ``version`` command). The kinematics/flags
  tier is supplied at construction or derived: link lengths are not on the arm,
  ``has_rail`` comes from the joint set, ``is_dual_gripper`` from the axis_mask
  ``&H80`` bit, ``is_vision_gripper`` from the model name, and ``reach_class`` from the
  controller-read link lengths.
  """

  # --- identity / version (DataIDs 100-110, 2002, 116; version command) ---
  manufacturer: str
  controller_model: str
  hardware_version: str
  gpl_version: str
  controller_serial: str
  robot_name: str
  robot_type: int
  tcs_version: str
  modules: tuple
  # --- axes / limits / motion envelope ---
  num_axes: int
  extra_axes: int
  axis_mask: int
  soft_limits: Dict[Axis, tuple]
  hard_limits: Dict[Axis, tuple]
  # Effective per-joint maxima (reference x the global percent cap, already applied).
  max_joint_speed: Dict[Axis, float]
  max_joint_acceleration: Dict[Axis, float]
  max_joint_deceleration: Dict[Axis, float]
  max_cartesian_speed: float
  max_cartesian_acceleration: float
  power_state: int
  # --- supplied / derived ---
  kinematics: "kinematics.PF400Params" = dataclasses.field(default_factory=kinematics.PF400Params)
  kinematics_source: Literal["device", "provided", "default"] = "default"
  has_rail: bool = False
  is_dual_gripper: bool = False
  is_vision_gripper: bool = False
  # "unknown" if the controller-read link lengths match neither known arm; defaults to "extended"
  # to match the default PF400Params (the extended/XR link lengths)
  reach_class: Literal["standard", "extended", "unknown"] = "extended"

  @property
  def gripper_width_range(self) -> tuple:
    return self.soft_limits[Axis.GRIPPER]

  @property
  def z_range(self) -> tuple:
    return self.soft_limits[Axis.BASE]

  @property
  def work_envelope(self) -> WorkEnvelope:
    """Reachable tool-tip annulus, swept from the shoulder/elbow soft limits.

    Sweeps the two planar joints across their soft-limit range (Z held constant -
    it is an independent axis on a SCARA), takes the base->wrist radius at each
    sample, and brackets it by +/- the tool length (the wrist can orient the tool
    radially either way). This respects the joint limits rather than assuming full
    extension, so the outer radius is the real reach, not l1 + l2 + tool.
    """
    wrist_only = dataclasses.replace(self.kinematics, gripper_length=0.0)
    tool = self.kinematics.gripper_length
    sh_lo, sh_hi = self.soft_limits[Axis.SHOULDER]
    el_lo, el_hi = self.soft_limits[Axis.ELBOW]
    steps = 60
    outer, inner = 0.0, float("inf")
    for i in range(steps + 1):
      shoulder = sh_lo + (sh_hi - sh_lo) * i / steps
      for j in range(steps + 1):
        elbow = el_lo + (el_hi - el_lo) * j / steps
        joints: JointPose = {
          Axis.BASE: 0.0,
          Axis.SHOULDER: shoulder,
          Axis.ELBOW: elbow,
          Axis.WRIST: 0.0,
          Axis.GRIPPER: 0.0,
          Axis.RAIL: 0.0,
        }
        wrist = kinematics.fk(joints, wrist_only).location
        radius = (wrist.x * wrist.x + wrist.y * wrist.y) ** 0.5
        outer = max(outer, radius + tool)
        inner = min(inner, abs(radius - tool))
    zmin, zmax = self.z_range
    return WorkEnvelope(inner=inner, outer=outer, zmin=zmin, zmax=zmax)
