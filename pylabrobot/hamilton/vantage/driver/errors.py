"""What a Vantage's firmware replies look like, and the errors they report.

A reply repeats the module and command it answers, then carries parameters whose names are two
characters followed by their value: `id<n>` for the identifier a command was sent with, `er<n>` for
the error code (0 is none), and one per parameter the machine reports. An error reply has no `/`
trace. It has `et"<text>"`, or from a module that names which drives failed `es"<codes>"`:

  A1AMQWid2er0qw0                                  no error
  A1PMQAid1er30et"Command: Unknown command"        the module did not understand the command
  A1PMDAid262er99es"P170 P270"                     channels 1 and 2 reported error 70

The machine writes the id and the error code without padding, however many digits the command
carried.
"""

import re
from typing import Dict, Optional

core96_errors = {
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

pip_errors = {
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

ipg_errors = {
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


def _strip_quoted(text: str) -> str:
  """The text with what is inside double quotes emptied, so that it cannot be mistaken for
  parameters."""
  return re.sub(r'"[^"]*"', '""', text)


def parse_vantage_fw_string(s: str, fmt: Optional[Dict[str, str]] = None) -> dict:
  """Parse a Vantage firmware string into a dict.

  The identifier parameter (id<int>) is added automatically.

  `fmt` is a dict that specifies the format of the string. The keys are the parameter names and the
  values are the types. The following types are supported:

    - `"int"`: a single integer
    - `"str"`: text in double quotes
    - `"[int]"`: a list of integers
    - `"hex"`: a hexadecimal number

  A number is looked for outside the quotes, so text that happens to contain a parameter name is
  not mistaken for it. `fmt` is not changed.

  Example:
    >>> parse_vantage_fw_string("id0xs30 -100 +1 1000", {"id": "int", "xs": "[int]"})
    {"id": 0, "xs": [30, -100, 1, 1000]}

    >>> parse_vantage_fw_string('es"error string"', {"es": "str"})
    {"es": "error string", "id": ...}

  Raises:
    TypeError: If `fmt` is not a dict.
    ValueError: If a parameter is not in the string exactly once, or its type is unknown.
  """

  if fmt is None:
    fmt = {}
  if not isinstance(fmt, dict):
    raise TypeError(f"invalid fmt for fmt: expected dict, got {type(fmt)}")
  fmt = {**fmt, **({} if "id" in fmt else {"id": "int"})}

  parsed: dict = {}
  outside_quotes = _strip_quoted(s)

  for key, data_type in fmt.items():
    name = re.escape(key)
    if data_type == "int":
      matches = re.findall(rf"{name}([-+]?\d+)", outside_quotes)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0])
    elif data_type == "str":
      matches = re.findall(rf'{name}"([^"]*)"', s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = matches[0]
    elif data_type == "[int]":
      matches = re.findall(rf"{name}((?:[-+]?[\d ]+)+)", outside_quotes)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = [int(x) for x in matches[0].split()]
    elif data_type == "hex":
      matches = re.findall(rf"{name}([0-9a-fA-F]+)", outside_quotes)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0], 16)
    else:
      raise ValueError(f"Unknown data type {data_type}")

  return parsed


def get_id_from_fw_response(resp: str) -> Optional[int]:
  """The id a reply carries, however many digits the machine wrote it with.

  Raises:
    ValueError: If the reply carries no id.
  """
  return int(parse_vantage_fw_string(resp, {"id": "int"})["id"])


class VantageFirmwareError(Exception):
  """A Vantage module reported an error.

  Attributes:
    errors: What failed, by name (`"Pipetting channel 3"`, `"Core 96"`, `"Arm"`, ...), with the
      machine's description of it.
    raw_response: The reply as received.
  """

  def __init__(self, errors: Dict[str, str], raw_response: str):
    super().__init__(errors, raw_response)
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

  def __hash__(self) -> int:
    return hash((tuple(sorted(self.errors.items())), self.raw_response))


# The modules a reply can come from, by their identifier.
_MODULES = {
  "I1AM": "Cover",
  "C0AM": "Master",
  "A1PM": "Pip",
  "A1HM": "Core 96",
  "A1RM": "IPG",
  "A1AM": "Arm",
  "A1XM": "X-arm",
}


def vantage_response_string_to_error(string: str) -> VantageFirmwareError:
  """Convert a Vantage firmware response string to a VantageFirmwareError. Assumes that the
  response is an error response.

  A reply that names the drives that failed, in `es"..."`, gives one entry for each. Any other
  error reply gives one entry for the module that sent it, with its `et"..."` text.
  """

  errors: Dict[str, str] = {}
  try:
    error_string = parse_vantage_fw_string(string, {"es": "str"})["es"]
  except ValueError:
    module = _MODULES.get(string[:4], "Unknown module")
    try:
      errors[module] = parse_vantage_fw_string(string, {"et": "str"})["et"]
    except ValueError:
      errors[module] = "Unknown error"
    return VantageFirmwareError(errors, string)

  for error in re.findall(r"[A-Z0-9]{2}[0-9]{2}", error_string):
    module, error_code = error[:2], int(error[2:])
    if module[0] == "P" and module[1].isdigit() and int(module[1]) >= 1:
      errors[f"Pipetting channel {module[1]}"] = pip_errors.get(error_code, "Unknown error")
    elif module in ("H0", "HM"):
      errors["Core 96"] = core96_errors.get(error_code, "Unknown error")
    elif module == "RM":
      errors["IPG"] = ipg_errors.get(error_code, "Unknown error")
    elif module == "AM":
      errors["Cover"] = "Unknown error"

  return VantageFirmwareError(errors, string)


def _error_code(resp: str) -> Optional[int]:
  """The error code a reply reports, or None if it has no error field.

  The field is looked for right after the module, command and id, which is where a machine writes
  it, and failing that anywhere outside the quotes.
  """
  outside_quotes = _strip_quoted(resp)
  found = re.match(r"\w{6}(?:id\d+)?er(\d+)", outside_quotes) or re.search(
    r"er(\d+)", outside_quotes
  )
  return int(found.group(1)) if found else None


def check_fw_string_error(resp: str) -> None:
  """Raise a `VantageFirmwareError` if a reply reports an error.

  Args:
    resp: The reply as received.

  Raises:
    VantageFirmwareError: If the reply carries an error code other than 0.
  """
  code = _error_code(resp)
  if code is not None and code != 0:
    raise vantage_response_string_to_error(resp)


# The pattern a machine's unasked messages are recognised by: the address, then `__` where a command
# code would be, then a lowercase parameter name. They carry id 0, for example
# `A1AM__id0sm"Spontan Message"`, or with a node prefix `00A0.A1AM__id0sm"..."`.
_SPONTANEOUS = re.compile(r"^[A-Z0-9_.]+__[a-z]")


def is_spontaneous_message(line: str) -> bool:
  """Whether a line is a message the machine sent without being asked.

  Such a message answers no command, and the reply router discards it.
  """
  return _SPONTANEOUS.match(line) is not None
