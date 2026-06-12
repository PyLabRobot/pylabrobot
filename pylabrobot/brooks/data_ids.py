"""PreciseFlex controller parameter-database identifiers (DataIDs).

The Guidance controller exposes a large numbered parameter database, read with
the TCS ``pd <id>`` command (wrapped by ``PreciseFlexArmBackend.request_parameter``).
Naming the IDs here keeps configuration reads self-describing rather than bare
numbers, and gives a single place to add more as the database is mapped.
"""

from enum import IntEnum


class DataID(IntEnum):
  """Controller parameter-database items, read via ``request_parameter`` (``pd``).

  Each item's reply shape is noted below: a single value, descriptive text, or an
  array with one element per axis (parse with ``_parse_per_axis``).
  """

  # Identity / state - single value or descriptive text.
  MANUFACTURER = 100
  CONTROLLER_MODEL = 101
  HARDWARE_VERSION = 102
  GPL_VERSION = 103
  CONTROLLER_SERIAL = 110
  ROBOT_TYPE = 116
  POWER_STATE = 234
  ROBOT_HOMED = 2800  # 1 = all axes homed; 0 = not homed (commanded motion blocked)
  NUM_AXES = 2000
  ROBOT_NAME = 2002
  AXIS_MASK = 2003
  EXTRA_AXES = 2004
  # Per-axis arrays - one value per joint (e.g. speed differs J1..J5).
  REFERENCE_SPEED = 2700
  REFERENCE_ACCEL = 2702
  HARD_LIMIT_MAX = 16075
  HARD_LIMIT_MIN = 16076
  SOFT_LIMIT_MAX = 16077
  SOFT_LIMIT_MIN = 16078
  # Kinematic geometry. LINK_LENGTHS is per-axis (l1 at the shoulder, l2 at the
  # elbow); TOOL_OFFSET is a wrist-frame (x, y, z) transform, z = wrist->TCP.
  LINK_LENGTHS = 16050
  TOOL_OFFSET = 16051
  # Cartesian reference - (translation, rotation, ...), not per-joint.
  REFERENCE_CARTESIAN_SPEED = 2701
  REFERENCE_CARTESIAN_ACCEL = 2703
  # Global motion caps - one percentage applied to the whole profile (all joints
  # the same); per-joint maxima come from REFERENCE_* x these.
  MAX_SPEED_PERCENT = 2704
  MAX_ACCEL_PERCENT = 2705
  MAX_DECEL_PERCENT = 2706
