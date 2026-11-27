"""What a homing command should do."""

from __future__ import annotations

import enum


class MotorHomeType(enum.IntEnum):
  """What a homing command should do.

  Initializing runs a full power-on sequence; homing drives to the home sensor; verifying only
  confirms the current position against the home sensor without a full re-reference.
  """

  INIT_ALL_MOTORS = 1
  INIT_PERI_PUMP = 2
  HOME_MOTOR = 3
  HOME_XYZ_MOTORS = 4
  VERIFY_MOTOR = 5
  VERIFY_XYZ_MOTORS = 6
