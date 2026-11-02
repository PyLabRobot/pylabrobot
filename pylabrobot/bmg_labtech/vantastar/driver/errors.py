"""VANTAstar refusals, warnings and errors, and what their codes mean.

A ConfirmationResponse carries five code bytes after its status header.  Their meaning follows from
the header bits: a refused command's quit codes, a device warning's codes, a device error's codes,
or, otherwise, the busy codes naming what the reader is doing (the id of the command running).
"""

from typing import Dict, Tuple

Codes = Tuple[int, int, int, int, int]

# What the first quit code of a refused command means.
QUIT_CODES: Dict[int, str] = {
  3: "the command arrived with a wrong length",
  5: "the command is unknown",
  7: "a parameter is invalid (see quit code 2)",
  8: "the command was refused (see quit code 2)",
  10: "a pump parameter is invalid",
  15: "a plate move parameter is invalid",
  20: "an EEPROM parameter is invalid",
  25: "a microplate parameter is invalid",
  30: "a well layout parameter is invalid",
  38: "flying mode is not allowed here",
  39: "the measurement mode is invalid",
  40: "a shaking parameter is invalid",
  43: "the settling time is invalid",
  45: "a chromatic (wavelength) parameter is invalid",
  50: "a pause parameter is invalid",
  54: "the measurement interval time is invalid",
  55: "the focal height is invalid",
  57: "the aperture distance is invalid",
  58: "an external kinetic with equidistant timing is not possible",
  60: "a kinetic parameter is invalid",
  61: "an action parameter is invalid",
  65: "a well scan parameter is invalid",
  67: "a monochromator parameter is invalid",
  70: "a volume group parameter is invalid",
  86: "the automatic gain is not allowed",
  90: "actions overlap in time",
  93: "the measurement data does not fit the reader's memory",
  96: "an idle movement parameter is invalid",
  100: "a data request parameter is invalid",
  107: "the timing cannot be calculated (injection without injection needles)",
  110: "a temperature control parameter is invalid",
  111: "a stirrer parameter is invalid",
  115: "a spectrometer parameter is invalid",
  117: "a measurement controller parameter is invalid",
  120: "a gain adjustment parameter is invalid",
  125: "a motor controller parameter is invalid",
  130: "a motor parameter is invalid",
  135: "an atmospheric control unit parameter is invalid",
  160: "an offset parameter is invalid",
  165: "a filter check parameter is invalid",
  180: "a stop parameter is invalid",
  182: "a measurement data block parameter is invalid",
  201: "the reader is not initialized",
  202: "the reader is busy",
  203: "the plate carrier is out of the reader",
  204: "the second measurement channel is not built in",
  205: "luminescence is not available",
  209: "an injection or aperture spoon is inserted",
  211: "measuring from below is not built in or not allowed",
  212: "time-resolved fluorescence is not available",
  214: "no measurement is running",
  220: "the reader needs to be switched off and on again",
  223: "fluorescence is not built in or the method is not allowed",
  224: "fluorescence polarization is not built in or the method is not allowed",
  232: "the communication protocol version is incompatible",
  234: "injection is not allowed during the measurement",
  236: "the reader's software manager failed",
}

# What the first code of a device warning means.
WARNING_CODES: Dict[int, str] = {
  1: "general process warning",
  2: "an action was aborted on an unexpected condition",
  3: "the required timing could not be met",
  4: "a sensor inside the reader reported a problem",
  5: "the memory is full; the values need to be read",
  6: "temperature control warning",
  9: "monochromator filter slide change",
  10: "spectrometer warning",
  11: "measurement controller warning",
  12: "motor driver warning",
  13: "motor controller warning",
  14: "atmospheric control unit warning",
  15: "firmware run-time error (parameter)",
  16: "firmware run-time error (assertion)",
  17: "the reader reset after a hard fault",
  18: "IO controller warning",
  19: "transport system warning",
  20: "general information",
  21: "LED controller warning",
  22: "stirrer warning",
  23: "button event",
  24: "firmware run-time error (parameter)",
}

# What the first code of a device error means.
ERROR_CODES: Dict[int, str] = {
  1: "calibration error",
  2: "pump error",
  4: "atmospheric control unit error",
  5: "board hardware error",
  6: "motor controller hardware error",
  7: "measurement controller hardware error",
  8: "IO controller hardware error",
  9: "monochromator error",
  12: "EEPROM hardware error",
}

# The command a busy reader is running: the first busy code.
BUSY_CODES: Dict[int, str] = {
  0: "nothing",
  1: "INIT",
  2: "PRIME",
  3: "PLATE",
  4: "RUN",
  7: "OFFSET",
  8: "MOTORS",
  9: "GAINFOCUS",
  11: "STOP",
  12: "GAINPLATE",
  14: "EEPROM",
  27: "RESET",
  29: "SHAKE",
  36: "CHECK_FILTER",
  39: "IDLEMOVE",
  255: "an internal procedure",
}


def describe_codes(codes: Codes, descriptions: Dict[int, str]) -> str:
  """Format codes as `code (description), codes c1/c2/c3/c4/c5`."""
  description = descriptions.get(codes[0], "no description known")
  return f"code {codes[0]} ({description}), codes {'/'.join(str(c) for c in codes)}"


class CommandRefusedError(Exception):
  """The reader refused a command and answered with quit codes."""

  def __init__(self, payload: bytes, quit_codes: Codes):
    """
    Args:
      payload: the logical payload that was refused.
      quit_codes: the five quit codes from the reply; the first names the reason.
    """
    self.payload = payload
    self.quit_codes = quit_codes
    super().__init__(f"VANTAstar refused {payload.hex()}: {describe_codes(quit_codes, QUIT_CODES)}")


class DeviceError(Exception):
  """The reader reports a device error; it needs attention before it measures again."""

  def __init__(self, payload: bytes, error_codes: Codes):
    """
    Args:
      payload: the logical payload whose reply carried the error.
      error_codes: the five error codes from the reply; the first names the error.
    """
    self.payload = payload
    self.error_codes = error_codes
    super().__init__(
      f"VANTAstar device error after {payload.hex()}: {describe_codes(error_codes, ERROR_CODES)}"
    )
