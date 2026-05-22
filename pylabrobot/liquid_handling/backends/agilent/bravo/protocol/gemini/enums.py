"""Gemini wire-protocol enums"""

from __future__ import annotations

from enum import IntEnum


# --- GeminiAPI.Communication.Core ---------------------------------------------


class TCPMessageType(IntEnum):
  """Outer TCP frame payload type (header byte 4-5)."""

  PACKET = 1
  MULTIPACKET = 4
  SERIAL_DATA = 5


class CommandTypes(IntEnum):
  """``Packet.Cmd`` — low 4 bits of packet byte 2."""

  SETCMD = 1
  SETCMD_RESP = 2
  GETCMD = 3
  GETCMD_RESP = 4
  SETCMD_ERR_RESP = 5
  GETCMD_ERR_RESP = 6
  STREAM = 7


class CommandNAKTypes(IntEnum):
  """Error codes returned in ``*_ERR_RESP`` packets and multipacket responses."""

  INVALID_SUBCMD = 1
  INVALID_DEVICE = 2
  OUT_OF_RANGE = 3
  READ_ONLY = 4
  WRITE_ONLY = 5
  INSTR_TBL_FULL = 6
  PLATE_DETECT_NOT_AVAILABLE = 7
  BRAKE_NOT_AVAILABLE = 8
  FLASH_PROTECTED = 9
  UNSUCCESSFUL_OPERATION = 10
  MOVE_IN_PROGRESS = 11


class SubCommandDataType(IntEnum):
  UINT32 = 0
  FLOAT32 = 1


class CommonSubCommands(IntEnum):
  """Subcommands valid on every node (master + controller). 0-18."""

  TRIGGER = 0
  DBG_VALUE = 1
  DBGLOG_SIZE = 2
  UPDATE_FW = 3
  FW_VERSION = 4
  BKUP_VERSION = 5
  PARAM_DB_RD_PTR = 6
  PARAM_DB_WR_PTR = 7
  PARAM_DB_VALUE = 8
  PARAM_DB_COUNT = 9
  PARAM_DB_APPLY = 10
  PARAM_DB_RESET = 11
  PARAM_DB_SAVE = 12
  PARAM_DB_LOAD = 13
  FILE_CRC = 14
  FILE_LENGTH = 15
  FILE_READY = 16
  FILE_CMD = 17
  REBOOT = 18


class GeminiSubCommands(IntEnum):
  """Subcommands valid on non-master nodes (axis controllers). 19-88."""

  INSTR_CLEAR = 19
  INSTR_NEW_INSTR = 20
  INSTR_TBL_VAL = 21
  START_EVT = 22
  SEND_EVT = 23
  STATUS_START_STR = 24
  STATUS_STOP_STR = 25
  DLOG_START_STR = 26
  DLOG_STOP_STR = 27
  STREAM_STATUS = 28
  STREAM_DLOG = 29
  POSITION = 30
  CLOT_MARGIN = 31
  CLOT_DURATION = 32
  LLD_MARGIN = 33
  LLD_POS = 34
  ERRCODE = 35
  LEDSTATE = 36
  FLASH_WRITE_SEC = 37
  FLASH_WRITE_PTR = 38
  FLASH_READ_SEC = 39
  FLASH_READ_PTR = 40
  FLASH_VAL = 41
  HOMING_FLAG = 42
  FORCE_MOVE_MAX_POS_ERR = 43
  DLOG_LOG_VALUE = 44
  DLOG_ITEM = 45
  DLOG_BUF_SIZE = 46
  DLOG_TRIGGER = 47
  DLOG_PARAM_RESET = 48
  DLOG_PTS = 49
  DLOG_INTERVAL = 50
  DLOG_START_DELAY = 51
  DLOG_START_EVENT = 52
  DLOG_DURATION = 53
  HIDX_REC_DIST = 54
  BRAKE_CTRL = 55
  MOTOR_STATE = 56
  HOMING_FLAG_STATE = 57
  STEP_MODE = 58
  STEP_MIN = 59
  STEP_MAX = 60
  STEP_PRESCALER = 61
  STEP_CYCLE_COUNT = 62
  TRACE_CONFIG = 63
  TRACE_POINTS = 64
  TRACE_READ = 65
  CMOVE_TBL_REC = 66
  CMOVE_TBL_WORD = 67
  CMOVE_TBL_VAL = 68
  CURRENT_DRAW = 69
  HOLDING_CURRENT = 70
  FLASH_PROTECT = 71
  EXEVT_TRIG_TYPE = 72
  EXEVT_DISTANCE = 73
  EXEVT_DESTINATION = 74
  EXEVT_SENDEVT = 75
  PLATE_PRESENT = 76
  MIN_FORCE = 77
  CLOT_MOVEDONE_MARGIN = 78
  CLOT_MOVEDONE_WAIT = 79
  CLOT_MOVEDONE_DWELL = 80
  DUMP_HISTOGRAM_DATA = 81
  ZERO_HISTOGRAM_DATA = 82
  PHASE_ERR_COUNT = 83
  PWM_OUTPUT_MODE = 84


class DarwinMasterNodeSubCommands(IntEnum):
  """Master-node subcommands specific to DARWIN. 19-33."""

  STATUS_LIGHTS = 19
  CHASSIS_LIGHTS = 20
  SAFETY_STATUS = 21
  MUTE_MODE = 22
  STUPID_HEAD_COUNTS = 23
  SMART_INIT = 24
  SMART_BAUD = 25
  SMART_DEV_ADDR = 26
  SMART_SOFT_RESET = 27
  SMART_RD_EEPROM = 28
  SMART_RD_EEPROM_VAL = 29
  SMART_SET_ADDR_BYTES = 30
  SMART_WR_EEPROM_VAL = 31
  SMART_WR_EEPROM = 32
  CLEAR_GO_BTN_LATCH = 33


class InstructionTypes(IntEnum):
  """Motion / logical instruction type — encoded into word0 low byte."""

  MOVE_TO = 0
  MOVE_BY = 1
  CMOVE_TO = 2
  DELAY = 3
  TIPS_OFF = 4
  SOLENOID_ON = 5
  SOLENOID_OFF = 6


class ExtraEventTriggerType(IntEnum):
  TRIG_NONE = 0
  TRIG_ON_FLAG = 1


class FirmwareUpdateType(IntEnum):
  IMG = 1
  IMG_AND_VAR = 4


class FileCmd(IntEnum):
  CAL_WR = 0
  CAL_RD = 1
  FW_WR = 2
  FW_RD = 3
  DATA_WR = 4
  DATA_RD = 5
  CAL_CLEAR = 6
  DATA_CLEAR = 7
  TIPS_WR = 8
  TIPS_RD = 9
  TIPS_CLEAR = 10


# --- GeminiAPI.Axis -----------------------------------------------------------


class MotorState(IntEnum):
  """BLDC axis lifecycle states."""

  CALIBRATE = 0
  INITIAL = 1
  COMMUTATE = 2
  COMMUTATING = 3
  COMMUTATED = 4
  HOME = 5
  HOME_INTERNAL = 6
  FINDING_FLAG = 7
  STOP_ON_FLAG = 8
  MOVE_TO_FLAG = 9
  FLAG_FOUND = 10
  FINDING_INDEX = 11
  STOP_ON_INDEX = 12
  MOVE_TO_INDEX = 13
  INDEX_FOUND = 14
  HOME_SPARE_1 = 15
  HOME_SPARE_2 = 16
  HOMED = 17
  READY = 18
  BUSY = 19
  DISABLE = 20
  DISABLED = 21
  ENABLE = 22
  ETCH_ENABLE = 23
  ETCH_ENABLED = 24
  ETCH_DISABLE = 25
  FOLLOW = 26
  PUSH_WAIT = 27
  FOLLOWING = 28


# --- GeminiAPI.Axis.Interfaces ------------------------------------------------


class AxisDirection(IntEnum):
  NEGATIVE = 0
  POSITIVE = 1


# --- GeminiAPI.Parameter.Interfaces -------------------------------------------


class ParamDBs(IntEnum):
  """Parameter-database indices — 0..151.

  Used with ``CommonSubCommands.PARAM_DB_RD_PTR`` / ``PARAM_DB_WR_PTR`` to
  address individual parameters stored on a device.
  """

  HW_TYPE = 0
  REVISION_NUM = 1
  MOTOR_TYPE = 2
  MOTOR_PRESENT = 3
  POLE_PAIRS = 4
  COUNTS_PER_ROTATION = 5
  POS_SCALE = 6
  MOTOR_TO_ENC_SIGN = 7
  MOTOR_DIR_SIGN = 8
  COUNTS_PER_INDEX = 9
  INFINITE_ROTATIONS = 10
  ENCODER_TYPE = 11
  BRAKE_PRESENT = 12
  BRAKE_DELAY = 13
  PLATE_DETECT_PRESENT = 14
  IA_ADC_CHANNEL = 15
  IB_ADC_CHANNEL = 16
  IA_PWM_CHANNEL = 17
  IB_PWM_CHANNEL = 18
  IC_PWM_CHANNEL = 19
  ISR_FREQ = 20
  PWM_FREQ = 21
  SPEED_LOOP_PS = 22
  POS_LOOP_PS = 23
  IQ_PTERM = 24
  IQ_ITERM = 25
  IQ_DTERM = 26
  IQ_CURR_OUT_SATURATION = 27
  ID_PTERM = 28
  ID_ITERM = 29
  ID_DTERM = 30
  ID_CURR_OUT_SATURATION = 31
  ID_SETPOINT = 32
  VEL_PTERM = 33
  VEL_ITERM = 34
  VEL_DTERM = 35
  VEL_CURR_OUT_SATURATION = 36
  VEL_FB_CUTOFF = 37
  SPEED_LOOP_BYPASS = 38
  SM_VEL_PTERM = 39
  SM_VEL_ITERM = 40
  SM_VEL_DTERM = 41
  STATIONARY_VEL_PTERM = 42
  STATIONARY_VEL_ITERM = 43
  STATIONARY_VEL_DTERM = 44
  SM_STATIONARY_VEL_PTERM = 45
  SM_STATIONARY_VEL_ITERM = 46
  SM_STATIONARY_VEL_DTERM = 47
  SPEED_SCALE = 48
  POS_PTERM = 49
  POS_ITERM = 50
  POS_DTERM = 51
  POS_CURR_OUT_SATURATION = 52
  POS_PID_MIN_ERR_THOLD = 53
  SM_POS_PTERM = 54
  SM_POS_ITERM = 55
  SM_POS_DTERM = 56
  STATIONARY_POS_PTERM = 57
  STATIONARY_POS_ITERM = 58
  STATIONARY_POS_DTERM = 59
  SM_STATIONARY_POS_PTERM = 60
  SM_STATIONARY_POS_ITERM = 61
  SM_STATIONARY_POS_DTERM = 62
  POS_D_ERR_CUTOFF = 63
  SPD_D_ERR_CUTOFF = 64
  SM_POS_D_ERR_CUTOFF = 65
  SM_SPD_D_ERR_CUTOFF = 66
  SPEED_FEED_FWD_GAIN = 67
  CURRENT_FEED_FWD_GAIN1 = 68
  CURRENT_FEED_FWD_GAIN2 = 69
  CURRENT_FEED_FWD_GAIN3 = 70
  SM_SPEED_FEED_FWD_GAIN = 71
  SM_CURRENT_FEED_FWD_GAIN1 = 72
  SM_CURRENT_FEED_FWD_GAIN2 = 73
  SM_CURRENT_FEED_FWD_GAIN3 = 74
  SPEED_FILTER_CENTER_FREQ = 75
  SPEED_FILTER_BANDWIDTH = 76
  DEAD_BAND_TYPE = 77
  STATIONARY_MAX_ERROR = 78
  SM_STATIONARY_MAX_ERROR = 79
  ALIGN_HS_CHECK_THRESH = 80
  ALIGN_PTERM = 81
  ALIGN_ITERM = 82
  ALIGN_DTERM = 83
  ALIGN_MAX_DISC_TIME = 84
  ALIGN_DISC_THRESHOLD = 85
  ALIGN_PID_ERR_THRESHOLD = 86
  ALIGN_REF_TIME = 87
  ALIGN_SETTLE_TIME = 88
  ALIGN_RAMP_CYCLES = 89
  ALIGN_PID_AVG_TOL = 90
  ALIGN_PID_AVG_COUNTS = 91
  ALIGN_RAMP_CURRENT_TARGET = 92
  ALIGN_FSPARE1 = 93
  ALIGN_FSPARE2 = 94
  ALIGN_FSPARE3 = 95
  ALIGN_USPARE4 = 96
  ALIGN_USPARE5 = 97
  MAX_HOLDING_CURRENT = 98
  HOMING_OVERSHOOT = 99
  HOMING_TYPE = 100
  HOMING_DIR = 101
  HOMING_INDEX_DIR = 102
  HOMING_INDEX_DIST = 103
  HOMING_INDEX_DIST_ERR_LIMIT = 104
  HOMING_SPEED = 105
  HOMING_ACCEL = 106
  HOMING_HS_CURRENT_LIMIT = 107
  HOMING_POS = 108
  HOMING_HARDSTOP_POS_ERR = 109
  HOMING_TIMEOUT = 110
  HOMING_INVERT_FLAG = 111
  ACCELERATION = 112
  JERK = 113
  SPEED = 114
  SM_ACCELERATION = 115
  SM_JERK = 116
  SM_SPEED = 117
  MOVE_DONE_MARGIN_TIME = 118
  POS_MARGIN = 119
  SM_POS_MARGIN = 120
  FORCE_MOVE_POS_SETTLE_TIME = 121
  FORCE_MOVE_POS_MARGIN = 122
  MAX_FORCE_CURRENT = 123
  POS_ERR_LIMIT = 124
  SM_THRESHOLD = 125
  I2T_TIME = 126
  I2T_CONT_CURRENT = 127
  I2T_PEAK_CURRENT = 128
  CURRENT_LOOP_OFFSET = 129
  PISTON_SELECT = 130
  LIN_ENCODER_AVG_PTS = 131
  SM_LIN_ENCODER_AVG_PTS = 132
  AUX_POS_SCALE = 133
  LIN_ENCODER_CUTOFF_FREQ = 134
  TIPS_OFF_COUNTS_MAX = 135
  TIPS_OFF_LOW_OUTPUT_COUNT = 136
  TIPS_OFF_DOWN_SPD_SLOW = 137
  FSPARE1 = 138
  FSPARE2 = 139
  FSPARE3 = 140
  FSPARE4 = 141
  FSPARE5 = 142
  USPARE6 = 143
  USPARE7 = 144
  USPARE8 = 145
  USPARE9 = 146
  USPARE10 = 147
  MIN_FW_FOR_PDB = 148


# --- Protocol constants -------------------------------------------------------

MSG_SYNC = 0xAAAA
PROTOCOL_VERSION = 1
FRAME_HEADER_SIZE = 8
PACKET_SIZE = 8
SERIAL_PACKET_SIZE = 9
MAX_MULTIPACKET_SIZE = 512
MAX_PACKETS_PER_MULTIPACKET = 64
TCP_PORT = 7613
TFTP_PORT = 69

# Well-known addresses (InstructionAddress node_id)
NODE_BROADCAST = 63
NODE_MASTER = 1

# Reserved event number used for instruction-triggering broadcasts
EVENT_RESERVED = 127


class ReservedEvent(IntEnum):
  """Subcodes of EVENT_RESERVED (127), broadcast by the master.

  Decoded from the high bits of a composite InstructionEvent value
  """

  STOP = 1
  CONTINUE = 2
  ERROR = 3
  FAULT = 4
  ETEACH_PRESSED = 5
  ETEACH_RELEASED = 6
  SAFETY_NOTICE = 7
  STOP_DISABLE = 8  # light-curtain trip / E-stop press → motors disabled


# Engine timing constants from GeminiEngine.cs
BROADCAST_WAIT_MS = 6


def decode_instruction_event(evt: int) -> tuple[bool, int, int]:
  """Decode an InstructionEvent uint into (is_composite, event_no, mask).

  Mirrors ``InstructionEvent.cs``::
      composite = (evt & 0x80) != 0
      event_no  = evt & 0x7F
      mask      = evt >> 8
  """
  composite = bool(evt & 0x80)
  event_no = evt & 0x7F
  mask = (evt >> 8) & 0xFFFFFF
  return composite, event_no, mask


def is_reserved_event(evt: int) -> ReservedEvent | None:
  """If ``evt`` is a composite RESERVED InstructionEvent, return its subcode."""
  composite, event_no, mask = decode_instruction_event(evt)
  if not composite or event_no != EVENT_RESERVED:
    return None
  reserved = mask & 0xFFFF
  try:
    return ReservedEvent(reserved)
  except ValueError:
    return None
