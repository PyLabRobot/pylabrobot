"""EL406 protocol constants and validation sets.

This module contains all protocol constants, command codes, and validation
sets used by the BioTek EL406 plate washer backend.
"""

from __future__ import annotations

# Protocol constants
ACK_BYTE = 0x06
NAK_BYTE = 0x15  # Negative acknowledgment - device rejected command

# Control commands
ABORT_COMMAND = 0x89  # Command 137
TEST_COMM_COMMAND = 0x73  # Command 115
INIT_STATE_COMMAND = 0xA0  # Command 160 - clears device state
PAUSE_COMMAND = 0x8A  # Command 138
RESUME_COMMAND = 0x8B  # Command 139
START_STEP_COMMAND = 0x8D  # Command 141 - sent before step commands
END_OF_BATCH_COMMAND = 0x8C  # Command 140
RESET_COMMAND = 0x70  # Command 112
STATUS_POLL_COMMAND = 0x92  # Command 146 - poll for step completion

# Query commands
GET_WASHER_MANIFOLD_COMMAND = 0xD8  # Command 216
GET_SYRINGE_MANIFOLD_COMMAND = 0xBB  # Command 187
GET_SENSOR_ENABLED_COMMAND = 0xD2  # Command 210
GET_SYRINGE_BOX_INFO_COMMAND = 0xF6  # Command 246

# 16-bit command codes
GET_SERIAL_NUMBER_COMMAND = 0x0100  # Command 256
GET_PERISTALTIC_INSTALLED_COMMAND = 0x0104  # Command 260

# Action commands
HOME_VERIFY_MOTORS_COMMAND = 0xC8  # Command 200
SET_WASHER_MANIFOLD_COMMAND = 0xD9  # Command 217
RUN_SELF_CHECK_COMMAND = 0x95  # Command 149
VACUUM_PUMP_CONTROL_COMMAND = 0x012B  # Command 299

# Peristaltic pump commands
PERISTALTIC_DISPENSE_COMMAND = 0x8F  # Command 143
PERISTALTIC_PRIME_COMMAND = 0x90  # Command 144
PERISTALTIC_PURGE_COMMAND = 0x91  # Command 145

# Syringe pump commands
SYRINGE_DISPENSE_COMMAND = 0xA1  # Command 161
SYRINGE_PRIME_COMMAND = 0xA2  # Command 162

# Manifold commands
SHAKE_SOAK_COMMAND = 0xA3  # Command 163
MANIFOLD_WASH_COMMAND = 0xA4  # Command 164
MANIFOLD_ASPIRATE_COMMAND = 0xA5  # Command 165
MANIFOLD_DISPENSE_COMMAND = 0xA6  # Command 166
MANIFOLD_PRIME_COMMAND = 0xA7  # Command 167
MANIFOLD_AUTO_CLEAN_COMMAND = 0xA8  # Command 168

# Timeout constants
DEFAULT_READ_TIMEOUT = 15.0  # seconds
LONG_READ_TIMEOUT = 120.0  # seconds, for long operations (wash cycles can take >30s)

# Message framing constants
MSG_START_MARKER = 0x01  # first byte of header
MSG_VERSION_MARKER = 0x02  # second byte of header
MSG_CONSTANT = 0x01  # constant byte at position 4
MSG_HEADER_SIZE = 11  # Total header size in bytes

# Valid buffer valves
VALID_BUFFERS = {"A", "B", "C", "D"}

# Valid syringe selections
VALID_SYRINGES = {"A", "B", "BOTH"}

# Valid intensity levels
VALID_INTENSITIES = {"Slow", "Medium", "Fast", "Variable"}

# Valid peristaltic flow rates
VALID_PERISTALTIC_FLOW_RATES = {"Low", "Medium", "High"}

# Flow rate range
MIN_FLOW_RATE = 1
MAX_FLOW_RATE = 9

# Syringe-specific limits
SYRINGE_MIN_FLOW_RATE = 1
SYRINGE_MAX_FLOW_RATE = 5
SYRINGE_MIN_VOLUME = 80
SYRINGE_MAX_VOLUME = 9999
SYRINGE_MAX_PUMP_DELAY = 5000
SYRINGE_MAX_SUBMERGE_DURATION = 1439  # 23:59 in minutes

# Device state enum (from status poll data positions 2-3)
STATE_INITIAL = 1  # Idle/ready
STATE_RUNNING = 2  # Busy
STATE_PAUSED = 3  # Paused
STATE_STOPPED = 4  # Stopped/ready
