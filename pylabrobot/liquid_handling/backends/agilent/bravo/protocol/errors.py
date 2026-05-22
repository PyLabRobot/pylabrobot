"""Bravo error types and error handling.

All 53 error codes with axis-aware formatting.
"""

from __future__ import annotations

from enum import IntEnum

from pylabrobot.liquid_handling.backends.agilent.bravo.types import Axis


class ErrorType(IntEnum):
    """All Bravo hardware/driver error types."""
    NO_ERROR = 0
    COULD_NOT_CONNECT = 1
    COULD_NOT_PING = 2
    COULD_NOT_QUERY_FIRMWARE = 3
    COULD_NOT_QUERY_STATE = 4
    COULD_NOT_QUERY_GO_BUTTON = 5
    UNIQUE_VALUE = 6
    REGISTER_NOT_READ = 7
    COULD_NOT_ALIGN = 8
    STOP_COMMAND = 9
    CONTROLLER_UNIDENTIFIED = 10
    ROBOT_DISABLE = 11
    MOTOR_POWER = 12
    MOVE_POSITION = 13
    MOVE_TIMEOUT = 14
    EXCEEDED_DEST = 15
    UNABLE_TO_REACH_DEST = 16
    AMP_SHORT_CIRCUIT = 17
    ENCODER = 18
    CONTROLLER_FATAL = 19
    CONTROLLER_INTERNAL = 20
    CONTROLLER_QUEUE = 21
    CONTROLLER_BRAKE = 22
    CONTROLLER_STACK = 23
    INVALID_DEST = 24
    NOT_HOMED = 25
    COULD_NOT_SEND_COMMAND = 26
    NO_RESPONSE = 27
    RABBIT_AGILE_COMM = 28
    AGILE_RABBIT_CRC = 29
    RABBIT_UNKNOWN_COMMAND = 30
    AGILE_UNKNOWN_ERROR = 31
    INVALID_AGILE_RESPONSE = 32
    DETECT_PUMPS = 33
    INVALID_NMC = 34
    UNKNOWN_RABBIT_ERROR = 35
    INVALID_TIP_TYPE = 37
    UNRESPONSIVE_NMC_MODULE = 38
    ROBOT_DISABLE_BUTTON = 39
    COULD_NOT_DETECT_HEAD = 40
    COULD_NOT_DETECT_GRIPPER = 41
    COULD_NOT_CLEAR_MOTOR_POWER = 42
    COULD_NOT_HOME = 43
    COULD_NOT_MOVE_TO_POSITION = 44
    COULD_NOT_ENABLE_MOTOR = 45
    COULD_NOT_DISABLE_MOTOR = 46
    COULD_NOT_READ_POSITION = 47
    COULD_NOT_SET_LIGHT = 48
    GRIP_POSITION = 49
    COULD_NOT_DETECT_SMART_HEAD = 50
    NODEZERO_NO_SERIAL_COMM = 51
    DARWIN_SOFTWARE_INTERNAL = 52
    DARWIN_GENERIC = 53


_ERROR_MESSAGES: dict[ErrorType, str] = {
    ErrorType.NO_ERROR: "No error.",
    ErrorType.COULD_NOT_CONNECT: "Could not connect to device.",
    ErrorType.COULD_NOT_PING: "Could not ping device.",
    ErrorType.COULD_NOT_QUERY_FIRMWARE: "Could not query firmware version.",
    ErrorType.COULD_NOT_QUERY_STATE: "Could not query device state.",
    ErrorType.COULD_NOT_QUERY_GO_BUTTON: "Could not query Go button state.",
    ErrorType.UNIQUE_VALUE: "Processor-controller communication validation failed.",
    ErrorType.REGISTER_NOT_READ: "Could not read register.",
    ErrorType.COULD_NOT_ALIGN: "Could not align motor.",
    ErrorType.STOP_COMMAND: "Motion was stopped.",
    ErrorType.CONTROLLER_UNIDENTIFIED: "Unidentified controller error.",
    ErrorType.ROBOT_DISABLE: "Robot safety interlock is active (E-stop).",
    ErrorType.MOTOR_POWER: "Motor power fault detected.",
    ErrorType.MOVE_POSITION: "Position error during move.",
    ErrorType.MOVE_TIMEOUT: "Timeout while moving to position.",
    ErrorType.EXCEEDED_DEST: "Exceeded destination position.",
    ErrorType.UNABLE_TO_REACH_DEST: "Unable to reach destination position.",
    ErrorType.AMP_SHORT_CIRCUIT: "Amplifier short circuit detected.",
    ErrorType.ENCODER: "Encoder failure.",
    ErrorType.CONTROLLER_FATAL: "Fatal controller error.",
    ErrorType.CONTROLLER_INTERNAL: "Internal controller error.",
    ErrorType.CONTROLLER_QUEUE: "Controller command queue error.",
    ErrorType.CONTROLLER_BRAKE: "Controller brake error.",
    ErrorType.CONTROLLER_STACK: "Controller stack error.",
    ErrorType.INVALID_DEST: "Invalid destination position.",
    ErrorType.NOT_HOMED: "Axis is not homed.",
    ErrorType.COULD_NOT_SEND_COMMAND: "Could not send command to device.",
    ErrorType.NO_RESPONSE: "No response from device.",
    ErrorType.RABBIT_AGILE_COMM: "Rabbit-to-Agile communication failure.",
    ErrorType.AGILE_RABBIT_CRC: "Agile-to-Rabbit CRC mismatch.",
    ErrorType.RABBIT_UNKNOWN_COMMAND: "Unknown command sent to Rabbit.",
    ErrorType.AGILE_UNKNOWN_ERROR: "Unknown Agile controller error.",
    ErrorType.INVALID_AGILE_RESPONSE: "Invalid response from Agile controller.",
    ErrorType.DETECT_PUMPS: "Could not detect pumps.",
    ErrorType.INVALID_NMC: "Invalid NMC module.",
    ErrorType.UNKNOWN_RABBIT_ERROR: "Unknown Rabbit firmware error.",
    ErrorType.INVALID_TIP_TYPE: "Invalid tip type for this head.",
    ErrorType.UNRESPONSIVE_NMC_MODULE: "NMC module is unresponsive.",
    ErrorType.ROBOT_DISABLE_BUTTON: "Robot disable button circuitry failure.",
    ErrorType.COULD_NOT_DETECT_HEAD: "Could not detect pipette head.",
    ErrorType.COULD_NOT_DETECT_GRIPPER: "Could not detect gripper.",
    ErrorType.COULD_NOT_CLEAR_MOTOR_POWER: "Could not clear motor power fault.",
    ErrorType.COULD_NOT_HOME: "Could not home axis.",
    ErrorType.COULD_NOT_MOVE_TO_POSITION: "Could not move to position.",
    ErrorType.COULD_NOT_ENABLE_MOTOR: "Could not enable motor.",
    ErrorType.COULD_NOT_DISABLE_MOTOR: "Could not disable motor.",
    ErrorType.COULD_NOT_READ_POSITION: "Could not read axis position.",
    ErrorType.COULD_NOT_SET_LIGHT: "Could not set indicator light.",
    ErrorType.GRIP_POSITION: "Gripper position error — is the plate missing?",
    ErrorType.COULD_NOT_DETECT_SMART_HEAD: "Could not detect smart head.",
    ErrorType.NODEZERO_NO_SERIAL_COMM: "Node Zero does not support serial communication (use ethernet).",
    ErrorType.DARWIN_SOFTWARE_INTERNAL: "Darwin controller internal software error.",
    ErrorType.DARWIN_GENERIC: "Error from the Gemini API.",
}


class BravoError(Exception):
    """Structured error from the Bravo hardware or driver."""

    def __init__(
        self,
        error_type: ErrorType,
        axis: Axis | None = None,
        custom_text: str | None = None,
    ):
        self.error_type = error_type
        self.axis = axis
        self.custom_text = custom_text
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.custom_text:
            return self.custom_text
        msg = _ERROR_MESSAGES.get(self.error_type, f"Unknown error ({self.error_type}).")
        if self.axis is not None:
            msg = f"{msg} ({self.axis.label})"
        return msg

    def __repr__(self) -> str:
        parts = [f"error_type={self.error_type.name}"]
        if self.axis is not None:
            parts.append(f"axis={self.axis.name}")
        if self.custom_text:
            parts.append(f"custom_text={self.custom_text!r}")
        return f"BravoError({', '.join(parts)})"


# Rabbit-to-PC error codes (response byte 0)
class RabbitErrorCode(IntEnum):
    """Error codes returned by the Rabbit firmware in response byte 0."""
    NONE = 0x00
    BAD_COMMUNICATION = 0x01
    UNKNOWN_COMMAND = 0x03
    AGILE_CRC = 0x04
    AGILE_UNKNOWN = 0x05
    BAD_ARGS = 0x06
    ROBOT_DISABLE = 0x07
    MOTOR_POWER_FAULT = 0x08
    PUMP_INIT = 0x09
    INVALID_NMC_MODULE = 0x0A
    UNRESPONSIVE_NMC_MODULE = 0x0B
    ROBOT_DISABLE_BUTTON = 0x0C
    GRIP_POSITION = 0x0D
    # 0x20-0x25: NOT_HOMED + axis offset
    NOT_HOMED_X = 0x20
    NOT_HOMED_Y = 0x21
    NOT_HOMED_Z = 0x22
    NOT_HOMED_W = 0x23
    NOT_HOMED_G = 0x24
    NOT_HOMED_ZG = 0x25


def rabbit_error_to_bravo_error(code: int) -> BravoError:
    """Convert a Rabbit firmware error code to a BravoError."""
    if code == RabbitErrorCode.NONE:
        return BravoError(ErrorType.NO_ERROR)
    if 0x20 <= code <= 0x25:
        axis = Axis(code - 0x20)
        return BravoError(ErrorType.NOT_HOMED, axis=axis)

    _mapping: dict[int, ErrorType] = {
        0x01: ErrorType.RABBIT_AGILE_COMM,
        0x03: ErrorType.RABBIT_UNKNOWN_COMMAND,
        0x04: ErrorType.AGILE_RABBIT_CRC,
        0x05: ErrorType.AGILE_UNKNOWN_ERROR,
        0x06: ErrorType.INVALID_AGILE_RESPONSE,
        0x07: ErrorType.ROBOT_DISABLE,
        0x08: ErrorType.MOTOR_POWER,
        0x09: ErrorType.DETECT_PUMPS,
        0x0A: ErrorType.INVALID_NMC,
        0x0B: ErrorType.UNRESPONSIVE_NMC_MODULE,
        0x0C: ErrorType.ROBOT_DISABLE_BUTTON,
        0x0D: ErrorType.GRIP_POSITION,
    }
    error_type = _mapping.get(code, ErrorType.UNKNOWN_RABBIT_ERROR)
    return BravoError(error_type)
