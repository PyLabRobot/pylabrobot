"""Per-machine Mantis homing calibration, loaded from the vendor RobotArm.config.

Every machine-specific homing value lives in
``Data/Device/Configs/RobotArm.config`` — the home offsets, homing
directions/pins/velocities, the pre-home retract/enforce steps, the post-home
ready walk and the concurrent-home delay. This module reads them and converts to
the firmware "packet" units the driver puts on the wire, so :class:`MantisDriver`
carries no machine-specific magic numbers.

The conversions mirror the vendor ``FopleyMotorToMotorAdapter`` /
``LinearStageMotorPositionMapper``:

* position packet = ``deg * (StepsPerRevolution / Pitch) / MicroSteps``
* velocity/accel packet = ``(|slope * value| + 0.5) / MicroSteps``

and the post-home walk is ``MantisKinematics.xy_to_theta(PostHomingPosition)``
fed through :meth:`MotorConfig.to_packet_units` (vendor ``ThetaMotorOffset`` is 0).
All of this was verified byte-exact against coldstart/warmstart captures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .mantis_kinematics import MOTOR_1_CONFIG, MOTOR_2_CONFIG, MantisKinematics

# Firmware motor id (0/1/2) -> config "Motor.N" index (1/2/3).
_CFG_INDEX: Dict[int, int] = {0: 1, 1: 2, 2: 3}


@dataclass
class HomeArgs:
  """Arguments for the firmware Home command (one motor)."""

  method: int  # = HomingPin
  pos_edge: bool  # = HomingPinActiveState
  pos_dir: bool  # = HomingDirection
  slow: float  # = fast / 10
  fast: float
  acc: float


@dataclass
class MantisHomingConfig:
  """Homing calibration in firmware packet units.

  Constructing ``MantisHomingConfig()`` yields the validated values for the
  reference machine (M-001294) as an offline fallback; prefer
  :meth:`from_robotarm_config` so the values come from the actual machine's
  vendor install.
  """

  retract: float = -27.77777777777778  # RetractStep -50 deg
  enforce: float = 11.11111111111111  # EnforceStep +20 deg (added to live position)
  jog_vel: float = 5555.5606  # Motor DefaultVelocity
  jog_acc: float = 833.3383  # Motor DefaultAcceleration
  xy_homing_delay_s: float = 0.200  # MotorXYHomingDelay
  home_offset: Dict[int, float] = field(
    default_factory=lambda: {0: -48.95, 1: 118.23}  # Motor.1/2.HomingPositionN
  )
  home_args: Dict[int, HomeArgs] = field(
    default_factory=lambda: {
      0: HomeArgs(3, True, False, 5.556055555555556, 55.56055555555556, 1388.893888888889),
      1: HomeArgs(3, True, True, 5.556055555555556, 55.56055555555556, 1388.893888888889),
      2: HomeArgs(0, True, False, 59.05561811023622, 590.5561811023622, 15748.03649606299),
    }
  )
  walk: List[Tuple[float, float]] = field(
    default_factory=lambda: [
      (-50.03, 50.9518),
      (-19.3419, 46.4986),
      (4.3180815265170873e-05, 4.3180815265170873e-05),
    ]
  )
  walk_vel: float = 55.56055555555556
  walk_acc: float = 1388.893888888889
  z_reset_pos: float = 0.0
  z_reset_vel: float = 3937.012874015748
  z_reset_acc: float = 15748.03649606299

  @classmethod
  def from_robotarm_config(cls, path: str) -> "MantisHomingConfig":
    """Build the calibration from a vendor ``RobotArm.config`` file."""
    cfg: Dict[str, str] = {}
    with open(path) as fh:
      for line in fh:
        m = re.search(r'<add key="([^"]+)" value="([^"]+)"', line)
        if m:
          cfg[m.group(1)] = m.group(2)

    def val(key: str) -> float:
      return float(cfg[key])

    def slope_us(cidx: int) -> Tuple[float, float]:
      return (
        val(f"Motor.{cidx}.StepsPerRevolution") / val(f"Motor.{cidx}.PositionMapper.Pitch"),
        val(f"Motor.{cidx}.MicroSteps"),
      )

    def pos_packet(cidx: int, deg: float) -> float:
      slope, micro = slope_us(cidx)
      return slope * deg / micro

    def va_packet(cidx: int, value: float) -> float:
      slope, micro = slope_us(cidx)
      return (abs(slope * value) + 0.5) / micro

    # Pre-home retract/enforce scale by the arm motor (config Motor.1).
    retract = pos_packet(1, val("Arm.PreHomingMovement.RetractStep"))
    enforce = pos_packet(1, val("Arm.PreHomingMovement.EnforceStep"))
    jog_vel = va_packet(1, val("Motor.1.DefaultVelocity"))
    jog_acc = va_packet(1, val("Motor.1.DefaultAcceleration"))
    delay_s = val("MotorXYHomingDelay") / 1000.0

    home_offset = {0: val("Motor.1.HomingPositionN"), 1: val("Motor.2.HomingPositionN")}

    home_args: Dict[int, HomeArgs] = {}
    for fid, cidx in _CFG_INDEX.items():
      fast = va_packet(cidx, val(f"Motor.{cidx}.HomingVelocity"))
      home_args[fid] = HomeArgs(
        method=int(val(f"Motor.{cidx}.HomingPin")),
        pos_edge=bool(int(val(f"Motor.{cidx}.HomingPinActiveState"))),
        pos_dir=bool(int(val(f"Motor.{cidx}.HomingDirection"))),
        slow=fast / 10.0,
        fast=fast,
        acc=va_packet(cidx, val(f"Motor.{cidx}.HomingAcceleration")),
      )

    # Post-home walk: each PostHomingPosition XY -> arm-equation angles -> packet.
    count = int(val("Arm.PostHomingPosition.Count"))
    walk: List[Tuple[float, float]] = []
    for i in range(1, count + 1):
      x, y, _z = (float(v) for v in cfg[f"Arm.PostHomingPosition.{i}"].split(","))
      theta1, theta2 = MantisKinematics.xy_to_theta(x, y)
      walk.append(
        (MOTOR_1_CONFIG.to_packet_units(theta1), MOTOR_2_CONFIG.to_packet_units(theta2))
      )

    # XY walk uses the arm motor's homing vel/acc; the Z reset move uses the arm
    # motor's homing velocity through the Z slope and the Z motor's homing accel.
    return cls(
      retract=retract,
      enforce=enforce,
      jog_vel=jog_vel,
      jog_acc=jog_acc,
      xy_homing_delay_s=delay_s,
      home_offset=home_offset,
      home_args=home_args,
      walk=walk,
      walk_vel=va_packet(1, val("Motor.1.HomingVelocity")),
      walk_acc=va_packet(1, val("Motor.1.HomingAcceleration")),
      z_reset_pos=0.0,
      z_reset_vel=va_packet(3, val("Motor.1.HomingVelocity")),
      z_reset_acc=va_packet(3, val("Motor.3.HomingAcceleration")),
    )
