"""Vantage-specific firmware error classes and error parsing."""

import re
from typing import Dict, Optional

from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
from pylabrobot.resources.errors import HasTipError, NoTipError, TooLittleLiquidError

from .fw_parsing import parse_vantage_fw_string

# ---------------------------------------------------------------------------
# Error dictionaries (per-module error code -> human-readable message)
#
# These dictionaries map firmware integer error codes to human-readable messages
# for each Vantage subsystem module. They are used by
# ``vantage_response_string_to_error`` to produce meaningful error descriptions.
# ---------------------------------------------------------------------------

core96_errors: Dict[int, str] = {
  0: "No error",
  21: "No communication to digital potentiometer",
  25: "Wrong Flash EPROM data",
  26: "Flash EPROM not programmable",
  27: "Flash EPROM not erasable",
  28: "Flash EPROM checksum error",
  29: "Wrong FW loaded",
  30: "Undefined command",
  31: "Undefined parameter",
  32: "Parameter out of range",
  35: "Voltages out of range",
  36: "Stop during command execution",
  37: "Adjustment sensor didn't switch (no teach in signal)",
  40: "No parallel processes on level 1 permitted",
  41: "No parallel processes on level 2 permitted",
  42: "No parallel processes on level 3 permitted",
  50: "Dispensing drive initialization failed",
  51: "Dispensing drive not initialized",
  52: "Dispensing drive movement error",
  53: "Maximum volume in tip reached",
  54: "Dispensing drive position out of permitted area",
  55: "Y drive initialization failed",
  56: "Y drive not initialized",
  57: "Y drive movement error",
  58: "Y drive position out of permitted area",
  60: "Z drive initialization failed",
  61: "Z drive not initialized",
  62: "Z drive movement error",
  63: "Z drive position out of permitted area",
  65: "Squeezer drive initialization failed",
  66: "Squeezer drive not initialized",
  67: "Squeezer drive movement error",
  68: "Squeezer drive position out of permitted area",
  70: "No liquid level found",
  71: "Not enough liquid present",
  75: "No tip picked up",
  76: "Tip already picked up",
  81: "Clot detected with LLD sensor",
  82: "TADM measurement out of lower limit curve",
  83: "TADM measurement out of upper limit curve",
  84: "Not enough memory for TADM measurement",
  90: "Limit curve not resettable",
  91: "Limit curve not programmable",
  92: "Limit curve name not found",
  93: "Limit curve data incorrect",
  94: "Not enough memory for limit curve",
  95: "Not allowed limit curve index",
  96: "Limit curve already stored",
}

pip_errors: Dict[int, str] = {
  22: "Drive controller message error",
  23: "EC drive controller setup not executed",
  25: "wrong Flash EPROM data",
  26: "Flash EPROM not programmable",
  27: "Flash EPROM not erasable",
  28: "Flash EPROM checksum error",
  29: "wrong FW loaded",
  30: "Undefined command",
  31: "Undefined parameter",
  32: "Parameter out of range",
  35: "Voltages out of range",
  36: "Stop during command execution",
  37: "Adjustment sensor didn't switch (no teach in signal)",
  38: "Movement interrupted by partner channel",
  39: "Angle alignment offset error",
  40: "No parallel processes on level 1 permitted",
  41: "No parallel processes on level 2 permitted",
  42: "No parallel processes on level 3 permitted",
  50: "D drive initialization failed",
  51: "D drive not initialized",
  52: "D drive movement error",
  53: "Maximum volume in tip reached",
  54: "D drive position out of permitted area",
  55: "Y drive initialization failed",
  56: "Y drive not initialized",
  57: "Y drive movement error",
  58: "Y drive position out of permitted area",
  59: "Divergance Y motion controller to linear encoder to height",
  60: "Z drive initialization failed",
  61: "Z drive not initialized",
  62: "Z drive movement error",
  63: "Z drive position out of permitted area",
  64: "Limit stop not found",
  65: "S drive initialization failed",
  66: "S drive not initialized",
  67: "S drive movement error",
  68: "S drive position out of permitted area",
  69: "Init. position adjustment error",
  70: "No liquid level found",
  71: "Not enough liquid present",
  74: "Liquid at a not allowed position detected",
  75: "No tip picked up",
  76: "Tip already picked up",
  77: "Tip not discarded",
  78: "Wrong tip detected",
  79: "Tip not correct squeezed",
  80: "Liquid not correctly aspirated",
  81: "Clot detected",
  82: "TADM measurement out of lower limit curve",
  83: "TADM measurement out of upper limit curve",
  84: "Not enough memory for TADM measurement",
  85: "Jet dispense pressure not reached",
  86: "ADC algorithm error",
  90: "Limit curve not resettable",
  91: "Limit curve not programmable",
  92: "Limit curve name not found",
  93: "Limit curve data incorrect",
  94: "Not enough memory for limit curve",
  95: "Not allowed limit curve index",
  96: "Limit curve already stored",
}

ipg_errors: Dict[int, str] = {
  0: "No error",
  22: "Drive controller message error",
  23: "EC drive controller setup not executed",
  25: "Wrong Flash EPROM data",
  26: "Flash EPROM not programmable",
  27: "Flash EPROM not erasable",
  28: "Flash EPROM checksum error",
  29: "Wrong FW loaded",
  30: "Undefined command",
  31: "Undefined parameter",
  32: "Parameter out of range",
  35: "Voltages out of range",
  36: "Stop during command execution",
  37: "Adjustment sensor didn't switch (no teach in signal)",
  39: "Angle alignment offset error",
  40: "No parallel processes on level 1 permitted",
  41: "No parallel processes on level 2 permitted",
  42: "No parallel processes on level 3 permitted",
  50: "Y Drive initialization failed",
  51: "Y Drive not initialized",
  52: "Y Drive movement error",
  53: "Y Drive position out of permitted area",
  54: "Diff. motion controller and lin. encoder counter too high",
  55: "Z Drive initialization failed",
  56: "Z Drive not initialized",
  57: "Z Drive movement error",
  58: "Z Drive position out of permitted area",
  59: "Z Drive limit stop not found",
  60: "Rotation Drive initialization failed",
  61: "Rotation Drive not initialized",
  62: "Rotation Drive movement error",
  63: "Rotation Drive position out of permitted area",
  65: "Wrist Twist Drive initialization failed",
  66: "Wrist Twist Drive not initialized",
  67: "Wrist Twist Drive movement error",
  68: "Wrist Twist Drive position out of permitted area",
  70: "Gripper Drive initialization failed",
  71: "Gripper Drive not initialized",
  72: "Gripper Drive movement error",
  73: "Gripper Drive position out of permitted area",
  80: "Plate not found",
  81: "Plate is still held",
  82: "No plate is held",
}


# ---------------------------------------------------------------------------
# Module ID -> name mapping
# ---------------------------------------------------------------------------

VANTAGE_MODULE_NAMES: Dict[str, str] = {
  "I1AM": "Cover",
  "C0AM": "Master",
  "A1PM": "Pip",
  "A1HM": "Core 96",
  "A1RM": "IPG",
  "A1AM": "Arm",
  "A1XM": "X-arm",
}
"""Mapping from Vantage 4-character firmware module IDs to human-readable names.

Used for error reporting and diagnostics. Each key is the module prefix that appears
at the start of firmware response strings (e.g. ``A1PM`` for the pipetting module).
"""


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class VantageFirmwareError(Exception):
  """Error raised when the Vantage firmware returns an error response.

  Attributes:
    errors: Dictionary mapping subsystem names (e.g. ``"Pipetting channel 1"``,
      ``"Core 96"``, ``"IPG"``) to human-readable error messages.
    raw_response: The original firmware response string that triggered the error.
  """

  def __init__(self, errors: Dict[str, str], raw_response: str):
    self.errors = errors
    self.raw_response = raw_response

  def __str__(self) -> str:
    return f"VantageFirmwareError(errors={self.errors}, raw_response={self.raw_response})"

  def __eq__(self, other: object) -> bool:
    return (
      isinstance(other, VantageFirmwareError)
      and self.errors == other.errors
      and self.raw_response == other.raw_response
    )


# ---------------------------------------------------------------------------
# Parsing firmware error responses
# ---------------------------------------------------------------------------


def vantage_response_string_to_error(string: str) -> VantageFirmwareError:
  """Parse a Vantage firmware error response string into a VantageFirmwareError.

  Extracts per-module error codes from the ``es`` (error string) field of the firmware
  response, maps them to human-readable messages using the module-specific error
  dictionaries (``pip_errors``, ``core96_errors``, ``ipg_errors``), and returns a
  structured :class:`VantageFirmwareError`.

  The firmware error string contains pairs of module ID + error code (e.g. ``"P170"``
  means pipetting channel 1, error code 70 = "No liquid level found"). Multiple errors
  may be present in a single response.

  If the ``es`` field cannot be parsed, falls back to the ``et`` (error text) field.

  Args:
    string: Raw firmware response string containing an error.

  Returns:
    A :class:`VantageFirmwareError` with parsed error details.
  """

  try:
    # FIXME: regex [A-Z0-9]{2}[0-9]{2} only captures 2-char module IDs, so channels
    # 10-16 (P10, P11, ...) can never match. Pre-existing bug from legacy.
    error_format = r"[A-Z0-9]{2}[0-9]{2}"
    error_string = parse_vantage_fw_string(string, {"es": "str"})["es"]
    error_codes = re.findall(error_format, error_string)
    errors: Dict[str, str] = {}
    num_channels = 16
    for error in error_codes:
      module, error_code = error[:2], error[2:]
      error_code_int = int(error_code)
      for channel in range(1, num_channels + 1):
        if module == f"P{channel}":
          errors[f"Pipetting channel {channel}"] = pip_errors.get(error_code_int, "Unknown error")
        elif module in ("H0", "HM"):
          errors["Core 96"] = core96_errors.get(error_code_int, "Unknown error")
        elif module == "RM":
          errors["IPG"] = ipg_errors.get(error_code_int, "Unknown error")
        elif module == "AM":
          errors["Cover"] = "Unknown error"
  except ValueError:
    module_id = string[:4]
    module_name = VANTAGE_MODULE_NAMES.get(module_id, "Unknown module")
    error_string = parse_vantage_fw_string(string, {"et": "str"})["et"]
    errors = {module_name: error_string}

  return VantageFirmwareError(errors, string)


# ---------------------------------------------------------------------------
# Conversion to standard PLR errors
# ---------------------------------------------------------------------------


def convert_vantage_firmware_error_to_plr_error(
  error: VantageFirmwareError,
) -> Optional[Exception]:
  """Convert a VantageFirmwareError to a standard PyLabRobot error if possible.

  Checks whether all errors in the :class:`VantageFirmwareError` are pipetting channel
  errors, and if so, maps each to the appropriate PLR exception type:

  - Error 76 ("Tip already picked up") -> :class:`HasTipError`
  - Error 75 ("No tip picked up") -> :class:`NoTipError`
  - Error 70/71 ("No liquid level found" / "Not enough liquid present") ->
    :class:`TooLittleLiquidError`
  - All other errors -> generic :class:`Exception`

  The result is wrapped in a :class:`ChannelizedError` with per-channel error details.

  Args:
    error: The Vantage firmware error to convert.

  Returns:
    A :class:`ChannelizedError` if all errors are pipetting channel errors, or None
    if the error involves non-pipetting modules (Core 96, IPG, etc.).
  """

  # If all errors are pipetting channel errors, return a ChannelizedError.
  if all(key.startswith("Pipetting channel ") for key in error.errors):
    channel_errors: Dict[int, Exception] = {}
    for channel_name, message in error.errors.items():
      channel_idx = int(channel_name.split(" ")[-1]) - 1  # 1-indexed -> 0-indexed

      if message == pip_errors.get(76):  # "Tip already picked up"
        channel_errors[channel_idx] = HasTipError()
      elif message == pip_errors.get(75):  # "No tip picked up"
        channel_errors[channel_idx] = NoTipError(message)
      elif message in (pip_errors.get(70), pip_errors.get(71)):
        channel_errors[channel_idx] = TooLittleLiquidError(message)
      else:
        channel_errors[channel_idx] = Exception(message)

    return ChannelizedError(errors=channel_errors, raw_response=error.raw_response)

  return None
