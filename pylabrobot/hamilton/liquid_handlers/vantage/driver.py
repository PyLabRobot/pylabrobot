"""VantageDriver: inherits HamiltonLiquidHandler, adds Vantage-specific config and error handling."""

import re
from typing import Any, Dict, List, Optional, cast

from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.hamilton.liquid_handlers.base import HamiltonLiquidHandler
from pylabrobot.resources.hamilton import TipPickupMethod, TipSize


# ---------------------------------------------------------------------------
# Firmware string parsing
# ---------------------------------------------------------------------------


def parse_vantage_fw_string(s: str, fmt: Optional[Dict[str, str]] = None) -> dict:
  """Parse a Vantage firmware string into a dict.

  The identifier parameter (id<int>) is added automatically.

  `fmt` is a dict that specifies the format of the string. The keys are the parameter names and the
  values are the types. The following types are supported:

    - ``"int"``: a single integer
    - ``"str"``: a string
    - ``"[int]"``: a list of integers
    - ``"hex"``: a hexadecimal number

  Example:
    >>> parse_vantage_fw_string("id0xs30 -100 +1 1000", {"id": "int", "x": "[int]"})
    {"id": 0, "x": [30, -100, 1, 1000]}

    >>> parse_vantage_fw_string("es\\"error string\\"", {"es": "str"})
    {"es": "error string"}
  """

  parsed: dict = {}

  if fmt is None:
    fmt = {}

  if not isinstance(fmt, dict):
    raise TypeError(f"invalid fmt for fmt: expected dict, got {type(fmt)}")

  if "id" not in fmt:
    fmt["id"] = "int"

  for key, data_type in fmt.items():
    if data_type == "int":
      matches = re.findall(rf"{key}([-+]?\d+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0])
    elif data_type == "str":
      matches = re.findall(rf'{key}"(.*)"', s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = matches[0]
    elif data_type == "[int]":
      matches = re.findall(rf"{key}((?:[-+]?[\d ]+)+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = [int(x) for x in matches[0].split()]
    elif data_type == "hex":
      matches = re.findall(rf"{key}([0-9a-fA-F]+)", s)
      if len(matches) != 1:
        raise ValueError(f"Expected exactly one match for {key} in {s}")
      parsed[key] = int(matches[0], 16)
    else:
      raise ValueError(f"Unknown data type {data_type}")

  return parsed


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


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


class VantageFirmwareError(Exception):
  def __init__(self, errors, raw_response):
    self.errors = errors
    self.raw_response = raw_response

  def __str__(self):
    return f"VantageFirmwareError(errors={self.errors}, raw_response={self.raw_response})"

  def __eq__(self, __value: object) -> bool:
    return (
      isinstance(__value, VantageFirmwareError)
      and self.errors == __value.errors
      and self.raw_response == __value.raw_response
    )


def vantage_response_string_to_error(
  string: str,
) -> VantageFirmwareError:
  """Convert a Vantage firmware response string to a VantageFirmwareError. Assumes that the
  response is an error response."""

  try:
    error_format = r"[A-Z0-9]{2}[0-9]{2}"
    error_string = parse_vantage_fw_string(string, {"es": "str"})["es"]
    error_codes = re.findall(error_format, error_string)
    errors = {}
    num_channels = 16
    for error in error_codes:
      module, error_code = error[:2], error[2:]
      error_code = int(error_code)
      for channel in range(1, num_channels + 1):
        if module == f"P{channel}":
          errors[f"Pipetting channel {channel}"] = pip_errors.get(error_code, "Unknown error")
        elif module in ("H0", "HM"):
          errors["Core 96"] = core96_errors.get(error_code, "Unknown error")
        elif module == "RM":
          errors["IPG"] = ipg_errors.get(error_code, "Unknown error")
        elif module == "AM":
          errors["Cover"] = "Unknown error"
  except ValueError:
    module_id = string[:4]
    module_name = {
      "I1AM": "Cover",
      "C0AM": "Master",
      "A1PM": "Pip",
      "A1HM": "Core 96",
      "A1RM": "IPG",
      "A1AM": "Arm",
      "A1XM": "X-arm",
    }.get(module_id, "Unknown module")
    error_string = parse_vantage_fw_string(string, {"et": "str"})["et"]
    errors = {module_name: error_string}

  return VantageFirmwareError(errors, string)


# ---------------------------------------------------------------------------
# VantageDriver
# ---------------------------------------------------------------------------


class VantageDriver(HamiltonLiquidHandler):
  """Driver for Hamilton Vantage liquid handlers.

  Inherits USB I/O, command assembly, and background reading from HamiltonLiquidHandler.
  Adds Vantage-specific firmware parsing, error handling, and module management.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 60,
    write_timeout: int = 30,
  ):
    super().__init__(
      id_product=0x8003,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    self._num_channels: Optional[int] = None
    self._traversal_height: float = 245.0

    # Populated during setup().
    self.pip: PIPBackend  # set in setup()
    self.head96: Optional[Head96Backend] = None  # set in setup() if installed
    self.ipg: Optional["VantageIPG"] = None  # set in setup() if installed

  # -- HamiltonLiquidHandler abstract methods --------------------------------

  @property
  def module_id_length(self) -> int:
    return 4

  @property
  def num_channels(self) -> int:
    if self._num_channels is None:
      raise RuntimeError("num_channels is not set. Call setup() first.")
    return self._num_channels

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    parsed = parse_vantage_fw_string(resp, {"id": "int"})
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str):
    if "er" in resp and "er0" not in resp:
      error = vantage_response_string_to_error(resp)
      raise error

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    return parse_vantage_fw_string(resp, fmt)

  # -- lifecycle ------------------------------------------------------------

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    await super().setup()
    self.id_ = 0

    tip_presences = await self.query_tip_presence()
    self._num_channels = len(tip_presences)

    arm_initialized = await self.arm_request_instrument_initialization_status()
    if not arm_initialized:
      await self.arm_pre_initialize()

    # Create backends based on discovered hardware.
    from .pip_backend import VantagePIPBackend  # deferred to avoid circular imports

    self.pip = VantagePIPBackend(self, tip_presences=tip_presences)

    # TODO: detect core96 installation from hardware rather than skip flag.
    if not skip_core96:
      from .head96_backend import VantageHead96Backend

      self.head96 = VantageHead96Backend(self)
    else:
      self.head96 = None

    if not skip_ipg:
      from .ipg import VantageIPG

      self.ipg = VantageIPG(driver=self)
    else:
      self.ipg = None

    # LED backend (always present).
    from .led_backend import VantageLEDBackend

    self.led = VantageLEDBackend(self)

    # Create plain subsystems.
    from .loading_cover import VantageLoadingCover
    from .x_arm import VantageXArm

    self.loading_cover = VantageLoadingCover(driver=self)
    self.x_arm = VantageXArm(driver=self)

    if not skip_loading_cover:
      loading_cover_initialized = await self.loading_cover.request_initialization_status()
      if not loading_cover_initialized:
        await self.loading_cover.initialize()

  async def stop(self):
    await super().stop()
    self._num_channels = None
    self.head96 = None
    self.ipg = None
    self.led = None
    self.loading_cover = None
    self.x_arm = None

  # -- traversal height -----------------------------------------------------

  def set_minimum_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the robot.

    This refers to the bottom of the pipetting channel when no tip is present, or the bottom of the
    tip when a tip is present. This value will be used as the default value for the
    `minimal_traverse_height_at_begin_of_command` and `minimal_height_at_command_end` parameters
    unless they are explicitly set.
    """
    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"
    self._traversal_height = traversal_height

  @property
  def traversal_height(self) -> float:
    return self._traversal_height

  # -- device-level commands ------------------------------------------------

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ):
    """Tip/needle definition.

    Args:
      tip_type_table_index: tip_table_index
      has_filter: with(out) filter
      tip_length: Tip length [0.1mm]
      maximum_tip_volume: Maximum volume of tip [0.1ul] Note! it's automatically limited to max.
        channel capacity
      tip_size: Type of tip collar (Tip type identification)
      pickup_method: pick up method.  Attention! The values set here are temporary and apply only
        until power OFF or RESET. After power ON the default values apply. (see Table 3)
    """

    if not 0 <= tip_type_table_index <= 99:
      raise ValueError(
        f"tip_type_table_index must be between 0 and 99, but is {tip_type_table_index}"
      )
    if not 1 <= tip_length <= 1999:
      raise ValueError(f"tip_length must be between 1 and 1999, but is {tip_length}")
    if not 1 <= maximum_tip_volume <= 56000:
      raise ValueError(
        f"maximum_tip_volume must be between 1 and 56000, but is {maximum_tip_volume}"
      )

    return await self.send_command(
      module="A1AM",
      command="TT",
      ti=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  async def query_tip_presence(self) -> List[bool]:
    """Query tip presence on all channels."""
    resp = await self.send_command(module="A1PM", command="QA", fmt={"rt": "[int]"})
    presences_int = cast(List[int], resp["rt"])
    return [bool(p) for p in presences_int]

  async def arm_request_instrument_initialization_status(self) -> bool:
    """Request the instrument initialization status.

    Returns:
      True if the arm module is initialized, False otherwise.
    """
    resp = await self.send_command(module="A1AM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def arm_pre_initialize(self):
    """Initialize the arm module."""
    return await self.send_command(module="A1AM", command="MI")

