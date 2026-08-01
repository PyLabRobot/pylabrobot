"""PreciseFlex controller parameter-database identifiers (DataIDs).

The Guidance controller exposes a large numbered parameter database, read with
the TCS ``pd <id>`` command (wrapped by ``PreciseFlexArmBackend.request_parameter``).
Naming the IDs here keeps configuration reads self-describing rather than bare
numbers, and gives a single place to add more as the database is mapped. It also
holds enums for the *values* of selected items (e.g. ``PowerState`` for the
power/system-state word) so a reply can be decoded, not just located.
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
  SOFTWARE_VERSION = 104  # 104-108: software version / revision / edit / date / qualifier
  SOFTWARE_REVISION = 105
  SOFTWARE_EDIT = 106
  SOFTWARE_DATE = 107
  SOFTWARE_QUALIFIER = 108
  CONTROLLER_NAME = 109  # free-text controller name
  CONTROLLER_SERIAL = 110
  ROBOT_TYPE = 116
  SAFETY_MODE = 117
  ROBOT_POWER_ON_HOURS = 136  # cumulative robot (motor) power-on hours
  SERVO_NODE_ID = 151  # servo network node id (NOT the controller serial)
  AUTO_EXECUTE_STATE = 230  # auto-execution state word (pairs with POWER_STATE)
  POWER_STATE = 234
  RESET_FATAL_ERROR = 247  # set to 1 to clear a latched fatal/severe error blocking power-on
  ROBOT_HOMED = 2800  # 1 = all axes homed; 0 = not homed (commanded motion blocked)
  NUM_AXES = 2000
  ROBOT_NAME = 2002
  AXIS_MASK = 2003
  EXTRA_AXES = 2004
  # Network.
  LOCAL_IP_ADDRESS = 420
  # Per-axis arrays - one value per joint (e.g. speed differs J1..J5).
  REFERENCE_SPEED = 2700
  REFERENCE_ACCEL = 2702
  HARD_LIMIT_MAX = 16075
  HARD_LIMIT_MIN = 16076
  SOFT_LIMIT_MAX = 16077
  SOFT_LIMIT_MIN = 16078
  ROBOT_SERIAL_NUMBER = 16000  # the robot's serial (distinct from CONTROLLER_SERIAL, 110)
  # Kinematic geometry. LINK_LENGTHS is per-axis (l1 at the shoulder, l2 at the
  # elbow); TOOL_OFFSET is a wrist-frame (x, y, z) transform, z = wrist->TCP.
  LINK_LENGTHS = 16050
  TOOL_OFFSET = 16051
  PAYLOAD_FEEDFORWARD_PERCENT = 16071  # dynamic feedforward default payload %
  # Cartesian reference - (translation, rotation, ...), not per-joint.
  REFERENCE_CARTESIAN_SPEED = 2701
  REFERENCE_CARTESIAN_ACCEL = 2703
  # Global motion caps - one percentage applied to the whole profile (all joints
  # the same); per-joint maxima come from REFERENCE_* x these.
  MAX_SPEED_PERCENT = 2704
  MAX_ACCEL_PERCENT = 2705
  MAX_DECEL_PERCENT = 2706


class PowerState(IntEnum):
  """Values of the controller power/system-state word - the ``sysState`` command, equal to
  ``Controller.PowerState`` (== ``DataID.POWER_STATE``, 234). Read via
  ``PreciseFlexDriver.request_system_state``.

  ``OFF_HARD_ESTOP`` (15) means a hard E-stop is engaged; ``ON_ATTACHED`` (21) is the normal
  running state. Values 25-27 (master/slave and CANopen modes) are unused on this controller.
  """

  POWERING_OFF = 3
  POWERING_OFF_AFTER_FAULT = 4
  OFF_FAULT_MUST_CLEAR = 5
  OFF_WAITING_HARDWARE_ENABLE = 6
  OFF_WAITING_ENABLE_SIGNAL = 7
  COMING_UP_ENABLING_AMPLIFIERS = 8
  ON_COMMUTATING = 9
  COMING_UP_ENABLING_SERVOS = 10
  ON_IDLE = 11
  ON_AUTO_EXECUTION = 12
  OFF_HARD_ESTOP = 15
  COMING_UP_SAFETY_DIAGNOSTICS = 16
  ON_READY_FOR_ATTACH = 20
  ON_ATTACHED = 21
  ON_DIO_MOTIONBLOCKS = 22
  ON_ANALOG_VELOCITY = 23
  ON_ANALOG_TORQUE = 24
  ON_HOMING = 28
  ON_VIRTUAL_MCP_JOG = 29
  ON_EXTERNAL_TRAJECTORY = 30
  ON_HARDWARE_MCP_JOG = 31
