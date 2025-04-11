import datetime
import enum
import functools
import logging
import re
from abc import ABCMeta
from contextlib import asynccontextmanager, contextmanager
from typing import (
  Callable,
  Dict,
  List,
  Literal,
  Optional,
  Sequence,
  Type,
  TypeVar,
  Union,
  cast,
)

from pylabrobot import audio
from pylabrobot.liquid_handling.backends.hamilton.base import (
  HamiltonLiquidHandler,
)
from pylabrobot.liquid_handling.errors import ChannelizedError
from pylabrobot.liquid_handling.liquid_classes.hamilton import (
  HamiltonLiquidClass,
  get_star_liquid_class,
)
from pylabrobot.liquid_handling.standard import (
  Drop,
  DropTipRack,
  GripDirection,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  Pickup,
  PickupTipRack,
  ResourceDrop,
  ResourceMove,
  ResourcePickup,
  SingleChannelAspiration,
  SingleChannelDispense,
)
from pylabrobot.liquid_handling.utils import (
  get_tight_single_resource_liquid_op_offsets,
  get_wide_single_resource_liquid_op_offsets,
)
from pylabrobot.resources import (
  Carrier,
  Coordinate,
  Resource,
  TipRack,
  TipSpot,
  Well,
)
from pylabrobot.resources.errors import (
  HasTipError,
  NoTipError,
  TooLittleLiquidError,
  TooLittleVolumeError,
)
from pylabrobot.resources.hamilton import (
  HamiltonTip,
  TipDropMethod,
  TipPickupMethod,
  TipSize,
)
from pylabrobot.resources.hamilton.hamilton_decks import (
  STAR_SIZE_X,
  STARLET_SIZE_X,
)
from pylabrobot.resources.liquid import Liquid
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.trash import Trash

T = TypeVar("T")


logger = logging.getLogger("pylabrobot")


def need_iswap_parked(method: Callable):
  """Ensure that the iSWAP is in parked position before running command.

  If the iSWAP is not parked, it get's parked before running the command.
  """

  @functools.wraps(method)
  async def wrapper(self: "STAR", *args, **kwargs):
    if self.iswap_installed and not self.iswap_parked:
      await self.park_iswap(
        minimum_traverse_height_at_beginning_of_a_command=int(self._iswap_traversal_height * 10)
      )

    result = await method(self, *args, **kwargs)

    return result

  return wrapper


def _fill_in_defaults(val: Optional[List[T]], default: List[T]) -> List[T]:
  """Util for converting an argument to the appropriate format for low level star methods."""
  t = type(default[0])
  # if the val is None, use the default.
  if val is None:
    return default
  # repeat val if it is not a list.
  if not isinstance(val, list):
    return [val] * len(default)
  # if the val is a list, it must be of the correct length.
  if len(val) != len(default):
    raise ValueError(f"Value length must equal num operations ({len(default)}), but is {val}")
  # replace None values in list with default values.
  val = [v if v is not None else d for v, d in zip(val, default)]
  # the values must be of the right type. automatically handle int and float.
  if t in [int, float]:
    return [t(v) for v in val]  # type: ignore
  if not all(isinstance(v, t) for v in val):
    raise ValueError(f"Value must be a list of {t}, but is {val}")
  # the value is ready to be used.
  return val


def parse_star_fw_string(resp: str, fmt: str = "") -> dict:
  """Parse a machine command or response string according to a format string.

  The format contains names of parameters (always length 2),
  followed by an arbitrary number of the following, but always
  the same:
  - '&': char
  - '#': decimal
  - '*': hex

  The order of parameters in the format and response string do not
  have to (and often do not) match.

  The identifier parameter (id####) is added automatically.

  TODO: string parsing
  The firmware docs mention strings in the following format: '...'
  However, the length of these is always known (except when reading
  barcodes), so it is easier to convert strings to the right number
  of '&'. With barcode reading the length of the barcode is included
  with the response string. We'll probably do a custom implementation
  for that.

  TODO: spaces
  We should also parse responses where integers are separated by spaces,
  like this: `ua#### #### ###### ###### ###### ######`

  Args:
    resp: The response string to parse.
    fmt: The format string.

  Raises:
    ValueError: if the format string is incompatible with the response.

  Returns:
    A dictionary containing the parsed values.

  Examples:
    Parsing a string containing decimals (`1111`), hex (`0xB0B`) and chars (`'rw'`):

    ```
    >>> parse_fw_string("aa1111bbrwccB0B", "aa####bb&&cc***")
    {'aa': 1111, 'bb': 'rw', 'cc': 2827}
    ```
  """

  # Remove device and cmd identifier from response.
  resp = resp[4:]

  # Parse the parameters in the fmt string.
  info = {}

  def find_param(param):
    name, data = param[0:2], param[2:]
    type_ = {"#": "int", "*": "hex", "&": "str"}[data[0]]

    # Build a regex to match this parameter.
    exp = {
      "int": r"[-+]?[\d ]",
      "hex": r"[\da-fA-F ]",
      "str": ".",
    }[type_]
    len_ = len(data.split(" ")[0])  # Get length of first block.
    regex = f"{name}((?:{exp}{ {len_} }"

    if param.endswith(" (n)"):
      regex += " ?)+)"
      is_list = True
    else:
      regex += "))"
      is_list = False

    # Match response against regex, save results in right datatype.
    r = re.search(regex, resp)
    if r is None:
      raise ValueError(f"could not find matches for parameter {name}")

    g = r.groups()
    if len(g) == 0:
      raise ValueError(f"could not find value for parameter {name}")
    m = g[0]

    if is_list:
      m = m.split(" ")

      if type_ == "str":
        info[name] = m
      elif type_ == "int":
        info[name] = [int(m_) for m_ in m if m_ != ""]
      elif type_ == "hex":
        info[name] = [int(m_, base=16) for m_ in m if m_ != ""]
    else:
      if type_ == "str":
        info[name] = m
      elif type_ == "int":
        info[name] = int(m)
      elif type_ == "hex":
        info[name] = int(m, base=16)

  # Find params in string. All params are identified by 2 lowercase chars.
  param = ""
  prevchar = None
  for char in fmt:
    if char.islower() and prevchar != "(":
      if len(param) > 2:
        find_param(param)
        param = ""
    param += char
    prevchar = char
  if param != "":
    find_param(param)  # last parameter is not closed by loop.

  # If id not in fmt, add it.
  if "id" not in info:
    find_param("id####")

  return info


class STARModuleError(Exception, metaclass=ABCMeta):
  """Base class for all Hamilton backend errors, raised by a single module."""

  def __init__(
    self,
    message: str,
    trace_information: int,
    raw_response: str,
    raw_module: str,
  ):
    self.message = message
    self.trace_information = trace_information
    self.raw_response = raw_response
    self.raw_module = raw_module

  def __repr__(self) -> str:
    return f"{self.__class__.__name__}('{self.message}')"


class CommandSyntaxError(STARModuleError):
  """Command syntax error

  Code: 01
  """


class HardwareError(STARModuleError):
  """Hardware error

  Possible cause(s):
    drive blocked, low power etc.

  Code: 02
  """


class CommandNotCompletedError(STARModuleError):
  """Command not completed

  Possible cause(s):
    error in previous sequence (not executed)

  Code: 03
  """


class ClotDetectedError(STARModuleError):
  """Clot detected

  Possible cause(s):
    LLD not interrupted

  Code: 04
  """


class BarcodeUnreadableError(STARModuleError):
  """Barcode unreadable

  Possible cause(s):
    bad or missing barcode

  Code: 05
  """


class TipTooLittleVolumeError(STARModuleError):
  """Too little liquid

  Possible cause(s):
    1. liquid surface is not detected,
    2. Aspirate / Dispense conditions could not be fulfilled.

  Code: 06
  """


class TipAlreadyFittedError(STARModuleError):
  """Tip already fitted

  Possible cause(s):
    Repeated attempts to fit a tip or iSwap movement with tips

  Code: 07
  """


class HamiltonNoTipError(STARModuleError):
  """No tips

  Possible cause(s):
    command was started without fitting tip (tip was not fitted or fell off again)

  Code: 08
  """


class NoCarrierError(STARModuleError):
  """No carrier

  Possible cause(s):
    load command without carrier

  Code: 09
  """


class NotCompletedError(STARModuleError):
  """Not completed

  Possible cause(s):
    Command in command buffer was aborted due to an error in a previous command, or command stack
    was deleted.

  Code: 10
  """


class DispenseWithPressureLLDError(STARModuleError):
  """Dispense with  pressure LLD

  Possible cause(s):
    dispense with pressure LLD is not permitted

  Code: 11
  """


class NoTeachInSignalError(STARModuleError):
  """No Teach  In Signal

  Possible cause(s):
    X-Movement to LLD reached maximum allowable position with- out detecting Teach in signal

  Code: 12
  """


class LoadingTrayError(STARModuleError):
  """Loading  Tray error

  Possible cause(s):
    position already occupied

  Code: 13
  """


class SequencedAspirationWithPressureLLDError(STARModuleError):
  """Sequenced aspiration with  pressure LLD

  Possible cause(s):
    sequenced aspiration with pressure LLD is not permitted

  Code: 14
  """


class NotAllowedParameterCombinationError(STARModuleError):
  """Not allowed  parameter combination

  Possible cause(s):
    i.e. PLLD and dispense or wrong X-drive assignment

  Code: 15
  """


class CoverCloseError(STARModuleError):
  """Cover close error

  Possible cause(s):
    cover is not closed and couldn't be locked

  Code: 16
  """


class AspirationError(STARModuleError):
  """Aspiration error

  Possible cause(s):
    aspiration liquid stream error detected

  Code: 17
  """


class WashFluidOrWasteError(STARModuleError):
  """Wash fluid or trash error

  Possible cause(s):
    1. missing wash fluid
    2. trash of particular washer is full

  Code: 18
  """


class IncubationError(STARModuleError):
  """Incubation error

  Possible cause(s):
    incubator temperature out of limit

  Code: 19
  """


class TADMMeasurementError(STARModuleError):
  """TADM measurement error

  Possible cause(s):
    overshoot of limits during aspiration or dispensation

  Code: 20, 26
  """


class NoElementError(STARModuleError):
  """No element

  Possible cause(s):
    expected element not detected

  Code: 21
  """


class ElementStillHoldingError(STARModuleError):
  """Element still holding

  Possible cause(s):
    "Get command" is sent twice or element is not droped expected element is missing (lost)

  Code: 22
  """


class ElementLostError(STARModuleError):
  """Element lost

  Possible cause(s):
    expected element is missing (lost)

  Code: 23
  """


class IllegalTargetPlatePositionError(STARModuleError):
  """Illegal target plate position

  Possible cause(s):
    1. over or underflow of iSWAP positions
    2. iSWAP is not in park position during pipetting activities

  Code: 24
  """


class IllegalUserAccessError(STARModuleError):
  """Illegal user access

  Possible cause(s):
    carrier was manually removed or cover is open (immediate stop is executed)

  Code: 25
  """


class PositionNotReachableError(STARModuleError):
  """Position not reachable

  Possible cause(s):
    position out of mechanical limits using iSWAP, CoRe gripper or PIP-channels

  Code: 27
  """


class UnexpectedLLDError(STARModuleError):
  """unexpected LLD

  Possible cause(s):
    liquid level is reached before LLD scanning is started (using PIP or XL channels)

  Code: 28
  """


class AreaAlreadyOccupiedError(STARModuleError):
  """area already occupied

  Possible cause(s):
    Its impossible to occupy area because this area is already in use

  Code: 29
  """


class ImpossibleToOccupyAreaError(STARModuleError):
  """impossible to occupy area

  Possible cause(s):
    Area cant be occupied because is no solution for arm prepositioning

  Code: 30
  """


class AntiDropControlError(STARModuleError):
  """
  Anti drop controlling out of tolerance. (VENUS only)

  Code: 31
  """


class DecapperError(STARModuleError):
  """
  Decapper lock error while screw / unscrew a cap by twister channels. (VENUS only)

  Code: 32
  """


class DecapperHandlingError(STARModuleError):
  """
  Decapper station error while lock / unlock a cap. (VENUS only)

  Code: 33
  """


class StopError(STARModuleError):
  """
  Hood is open (Not from documentation, but observed)

  Code: 36
  """


class SlaveError(STARModuleError):
  """Slave error

  Possible cause(s):
    This error code indicates an error in one of slaves. (for error handling purpose using service
    software macro code)

  Code: 99
  """


class WrongCarrierError(STARModuleError):
  """
  Wrong carrier barcode detected. (VENUS only)

  Code: 100
  """


class NoCarrierBarcodeError(STARModuleError):
  """
  Carrier barcode could not be read or is missing. (VENUS only)

  Code: 101
  """


class LiquidLevelError(STARModuleError):
  """
  Liquid surface not detected. (VENUS only)

  This error is created from main / slave error 06/70, 06/73 and 06/87.

  Code: 102
  """


class NotDetectedError(STARModuleError):
  """
  Carrier not detected at deck end position. (VENUS only)

  Code: 103
  """


class NotAspiratedError(STARModuleError):
  """
  Dispense volume exceeds the aspirated volume. (VENUS only)

  This error is created from main / slave error 02/54.

  Code: 104
  """


class ImproperDispensationError(STARModuleError):
  """
  The dispensed volume is out of tolerance (may only occur for Nano Pipettor Dispense steps).
  (VENUS only)

  This error is created from main / slave error 02/52 and 02/54.

  Code: 105
  """


class NoLabwareError(STARModuleError):
  """
  The labware to be loaded was not detected by autoload module. (VENUS only)

  Note:

  May only occur on a Reload Carrier step if the labware property 'MlStarCarPosAreRecognizable' is
  set to 1.

  Code: 106
  """


class UnexpectedLabwareError(STARModuleError):
  """
  The labware contains unexpected barcode ( may only occur on a Reload Carrier step ). (VENUS only)

  Code: 107
  """


class WrongLabwareError(STARModuleError):
  """
  The labware to be reloaded contains wrong barcode ( may only occur on a Reload Carrier step ).
  (VENUS only)

  Code: 108
  """


class BarcodeMaskError(STARModuleError):
  """
  The barcode read doesn't match the barcode mask defined. (VENUS only)

  Code: 109
  """


class BarcodeNotUniqueError(STARModuleError):
  """
  The barcode read is not unique. Previously loaded labware with same barcode was loaded without
  unique barcode check. (VENUS only)

  Code: 110
  """


class BarcodeAlreadyUsedError(STARModuleError):
  """
  The barcode read is already loaded as unique barcode ( it's not possible to load the same barcode
  twice ). (VENUS only)

  Code: 111
  """


class KitLotExpiredError(STARModuleError):
  """
  Kit Lot expired. (VENUS only)

  Code: 112
  """


class DelimiterError(STARModuleError):
  """
  Barcode contains character which is used as delimiter in result string. (VENUS only)

  Code: 113
  """


class UnknownHamiltonError(STARModuleError):
  """Unknown error"""


def _module_id_to_module_name(id_):
  """Convert a module ID to a module name."""
  return {
    "C0": "Master",
    "X0": "X-drives",
    "I0": "Auto Load",
    "W1": "Wash station 1-3",
    "W2": "Wash station 4-6",
    "T1": "Temperature carrier 1",
    "T2": "Temperature carrier 2",
    "R0": "ISWAP",
    "P1": "Pipetting channel 1",
    "P2": "Pipetting channel 2",
    "P3": "Pipetting channel 3",
    "P4": "Pipetting channel 4",
    "P5": "Pipetting channel 5",
    "P6": "Pipetting channel 6",
    "P7": "Pipetting channel 7",
    "P8": "Pipetting channel 8",
    "P9": "Pipetting channel 9",
    "PA": "Pipetting channel 10",
    "PB": "Pipetting channel 11",
    "PC": "Pipetting channel 12",
    "PD": "Pipetting channel 13",
    "PE": "Pipetting channel 14",
    "PF": "Pipetting channel 15",
    "PG": "Pipetting channel 16",
    "H0": "CoRe 96 Head",
    "HW": "Pump station 1 station",
    "HU": "Pump station 2 station",
    "HV": "Pump station 3 station",
    "N0": "Nano dispenser",
    "D0": "384 dispensing head",
    "NP": "Nano disp. pressure controller",
    "M1": "Reserved for module 1",
  }.get(id_, "Unknown Module")


def error_code_to_exception(code: int) -> Type[STARModuleError]:
  """Convert an error code to an exception."""
  codes = {
    1: CommandSyntaxError,
    2: HardwareError,
    3: CommandNotCompletedError,
    4: ClotDetectedError,
    5: BarcodeUnreadableError,
    6: TipTooLittleVolumeError,
    7: TipAlreadyFittedError,
    8: HamiltonNoTipError,
    9: NoCarrierError,
    10: NotCompletedError,
    11: DispenseWithPressureLLDError,
    12: NoTeachInSignalError,
    13: LoadingTrayError,
    14: SequencedAspirationWithPressureLLDError,
    15: NotAllowedParameterCombinationError,
    16: CoverCloseError,
    17: AspirationError,
    18: WashFluidOrWasteError,
    19: IncubationError,
    20: TADMMeasurementError,
    21: NoElementError,
    22: ElementStillHoldingError,
    23: ElementLostError,
    24: IllegalTargetPlatePositionError,
    25: IllegalUserAccessError,
    26: TADMMeasurementError,
    27: PositionNotReachableError,
    28: UnexpectedLLDError,
    29: AreaAlreadyOccupiedError,
    30: ImpossibleToOccupyAreaError,
    31: AntiDropControlError,
    32: DecapperError,
    33: DecapperHandlingError,
    99: SlaveError,
    100: WrongCarrierError,
    101: NoCarrierBarcodeError,
    102: LiquidLevelError,
    103: NotDetectedError,
    104: NotAspiratedError,
    105: ImproperDispensationError,
    106: NoLabwareError,
    107: UnexpectedLabwareError,
    108: WrongLabwareError,
    109: BarcodeMaskError,
    110: BarcodeNotUniqueError,
    111: BarcodeAlreadyUsedError,
    112: KitLotExpiredError,
    113: DelimiterError,
  }
  if code in codes:
    return codes[code]
  return UnknownHamiltonError


def trace_information_to_string(module_identifier: str, trace_information: int) -> str:
  """Convert a trace identifier to an error message."""
  table = None

  if module_identifier == "C0":  # master
    table = {
      10: "CAN error",
      11: "Slave command time out",
      20: "E2PROM error",
      30: "Unknown command",
      31: "Unknown parameter",
      32: "Parameter out of range",
      33: "Parameter does not belong to command, or not all parameters were sent",
      34: "Node name unknown",
      35: "id parameter error",
      37: "node name defined twice",
      38: "faulty XL channel settings",
      39: "faulty robotic channel settings",
      40: "PIP task busy",
      41: "Auto load task busy",
      42: "Miscellaneous task busy",
      43: "Incubator task busy",
      44: "Washer task busy",
      45: "iSWAP task busy",
      46: "CoRe 96 head task busy",
      47: "Carrier sensor doesn't work properly",
      48: "CoRe 384 head task busy",
      49: "Nano pipettor task busy",
      50: "XL channel task busy",
      51: "Tube gripper task busy",
      52: "Imaging channel task busy",
      53: "Robotic channel task busy",
    }
  elif module_identifier == "I0":  # autoload
    table = {36: "Hamilton will not run while the hood is open"}
  elif module_identifier in [
    "PX",
    "P1",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "P8",
    "P9",
    "PA",
    "PB",
    "PC",
    "PD",
    "PE",
    "PF",
    "PG",
  ]:
    table = {
      0: "No error",
      20: "No communication to EEPROM",
      30: "Unknown command",
      31: "Unknown parameter",
      32: "Parameter out of range",
      35: "Voltages outside permitted range",
      36: "Stop during execution of command",
      37: "Stop during execution of command",
      40: "No parallel processes permitted (Two or more commands sent for the same control"
      "process)",
      50: "Dispensing drive init. position not found",
      51: "Dispensing drive not initialized",
      52: "Dispensing drive movement error",
      53: "Maximum volume in tip reached",
      54: "Position outside of permitted area",
      55: "Y-drive blocked",
      56: "Y-drive not initialized",
      57: "Y-drive movement error",
      60: "Z-drive blocked",
      61: "Z-drive not initialized",
      62: "Z-drive movement error",
      63: "Z-drive limit stop not found",
      70: "No liquid level found (possibly because no liquid was present)",
      71: "Not enough liquid present (Immersion depth or surface following position possiby"
      "below minimal access range)",
      72: "Auto calibration at pressure (Sensor not possible)",
      73: "No liquid level found with dual LLD",
      74: "Liquid at a not allowed position detected",
      75: "No tip picked up, possibly because no was present at specified position",
      76: "Tip already picked up",
      77: "Tip not droped",
      78: "Wrong tip picked up",
      80: "Liquid not correctly aspirated",
      81: "Clot detected",
      82: "TADM measurement out of lower limit curve",
      83: "TADM measurement out of upper limit curve",
      84: "Not enough memory for TADM measurement",
      85: "No communication to digital potentiometer",
      86: "ADC algorithm error",
      87: "2nd phase of liquid nt found",
      88: "Not enough liquid present (Immersion depth or surface following position possiby"
      "below minimal access range)",
      90: "Limit curve not resetable",
      91: "Limit curve not programmable",
      92: "Limit curve not found",
      93: "Limit curve data incorrect",
      94: "Not enough memory for limit curve",
      95: "Invalid limit curve index",
      96: "Limit curve already stored",
    }
  elif module_identifier == "H0":  # Core 96 head
    table = {
      20: "No communication to EEPROM",
      30: "Unknown command",
      31: "Unknown parameter",
      32: "Parameter out of range",
      35: "Voltage outside permitted range",
      36: "Stop during execution of command",
      37: "The adjustment sensor did not switch",
      40: "No parallel processes permitted",
      50: "Dispensing drive initialization failed",
      51: "Dispensing drive not initialized",
      52: "Dispensing drive movement error",
      53: "Maximum volume in tip reached",
      54: "Position out of permitted area",
      55: "Y drive initialization failed",
      56: "Y drive not initialized",
      57: "Y drive movement error",
      58: "Y drive position outside of permitted area",
      60: "Z drive initialization failed",
      61: "Z drive not initialized",
      62: "Z drive movement error",
      63: "Z drive position outside of permitted area",
      65: "Squeezer drive initialization failed",
      66: "Squeezer drive not initialized",
      67: "Squeezer drive movement error: drive blocked or incremental sensor fault",
      68: "Squeezer drive position outside of permitted area",
      70: "No liquid level found",
      71: "Not enough liquid present",
      75: "No tip picked up",
      76: "Tip already picked up",
      81: "Clot detected",
    }
  elif module_identifier == "R0":  # iswap
    table = {
      20: "No communication to EEPROM",
      30: "Unknown command",
      31: "Unknown parameter",
      32: "Parameter out of range",
      33: "FW doesn't match to HW",
      36: "Stop during execution of command",
      37: "The adjustment sensor did not switch",
      38: "The adjustment sensor cannot be searched",
      40: "No parallel processes permitted",
      41: "No parallel processes permitted",
      42: "No parallel processes permitted",
      50: "Y-drive Initialization failed",
      51: "Y-drive not initialized",
      52: "Y-drive movement error: drive locked or incremental sensor fault",
      53: "Y-drive movement error: position counter over/underflow",
      60: "Z-drive initialization failed",
      61: "Z-drive not initialized",
      62: "Z-drive movement error: drive locked or incremental sensor fault",
      63: "Z-drive movement error: position counter over/underflow",
      70: "Rotation-drive initialization failed",
      71: "Rotation-drive not initialized",
      72: "Rotation-drive movement error: drive locked or incremental sensor fault",
      73: "Rotation-drive movement error: position counter over/underflow",
      80: "Wrist twist drive initialization failed",
      81: "Wrist twist drive not initialized",
      82: "Wrist twist drive movement error: drive locked or incremental sensor fault",
      83: "Wrist twist drive movement error: position counter over/underflow",
      85: "Gripper drive: communication error to gripper DMS digital potentiometer",
      86: "Gripper drive: Auto adjustment of DMS digital potentiometer not possible",
      89: "Gripper drive movement error: drive locked or incremental sensor fault during gripping",
      90: "Gripper drive initialized failed",
      91: "iSWAP not initialized. Call star.initialize_iswap().",
      92: "Gripper drive movement error: drive locked or incremental sensor fault during release",
      93: "Gripper drive movement error: position counter over/underflow",
      94: "Plate not found",
      96: "Plate not available",
      97: "Unexpected object found",
    }

  if table is not None and trace_information in table:
    return table[trace_information]

  return f"Unknown trace information code {trace_information:02}"


class STARFirmwareError(Exception):
  def __init__(self, errors: Dict[str, STARModuleError], raw_response: str):
    self.errors = errors
    self.raw_response = raw_response
    super().__init__(f"{errors}, {raw_response}")


def star_firmware_string_to_error(
  error_code_dict: Dict[str, str],
  raw_response: str,
) -> STARFirmwareError:
  """Convert a firmware string to a STARFirmwareError."""

  errors = {}

  for module_id, error in error_code_dict.items():
    module_name = _module_id_to_module_name(module_id)
    if "/" in error:
      # C0 module: error code / trace information
      error_code_str, trace_information_str = error.split("/")
      error_code, trace_information = (
        int(error_code_str),
        int(trace_information_str),
      )
      if error_code == 0:  # No error
        continue
      error_class = error_code_to_exception(error_code)
    elif module_id == "I0" and error == "36":
      error_class = StopError
      trace_information = int(error)
    else:
      # Slave modules: er## (just trace information)
      error_class = UnknownHamiltonError
      trace_information = int(error)
    error_description = trace_information_to_string(
      module_identifier=module_id, trace_information=trace_information
    )
    errors[module_name] = error_class(
      message=error_description,
      trace_information=trace_information,
      raw_response=error,
      raw_module=module_id,
    )

  # If the master error is a SlaveError, remove it from the errors dict.
  if isinstance(errors.get("Master"), SlaveError):
    errors.pop("Master")

  return STARFirmwareError(errors=errors, raw_response=raw_response)


def convert_star_module_error_to_plr_error(
  error: STARModuleError,
) -> Optional[Exception]:
  """Convert an error returned by a specific STAR module to a Hamilton error."""
  # TipAlreadyFittedError -> HasTipError
  if isinstance(error, TipAlreadyFittedError):
    return HasTipError()

  # HamiltonNoTipError -> NoTipError
  if isinstance(error, HamiltonNoTipError):
    return NoTipError(error.message)

  if error.trace_information == 75:
    return NoTipError(error.message)

  if error.trace_information in {70, 71}:
    return TooLittleLiquidError(error.message)

  if error.trace_information in {54}:
    return TooLittleVolumeError(error.message)

  return None


def convert_star_firmware_error_to_plr_error(
  error: STARFirmwareError,
) -> Optional[Exception]:
  """Check if a STARFirmwareError can be converted to a native PLR error. If so, return it, else
  return `None`."""

  # if all errors are channel errors, return a ChannelizedError
  if all(e.startswith("Pipetting channel ") for e in error.errors):

    def _channel_to_int(channel: str) -> int:
      return int(channel.split(" ")[-1]) - 1  # star is 1-indexed, plr is 0-indexed

    errors = {
      _channel_to_int(module_name): convert_star_module_error_to_plr_error(error) or error
      for module_name, error in error.errors.items()
    }
    return ChannelizedError(errors=errors, raw_response=error.raw_response)

  return None


def _dispensing_mode_for_op(empty: bool, jet: bool, blow_out: bool) -> int:
  """from docs:
  0 = Partial volume in jet mode
  1 = Blow out in jet mode, called "empty" in the VENUS liquid editor
  2 = Partial volume at surface
  3 = Blow out at surface, called "empty" in the VENUS liquid editor
  4 = Empty tip at fix position
  """

  if empty:
    return 4
  if jet:
    return 1 if blow_out else 0
  else:
    return 3 if blow_out else 2


class STAR(HamiltonLiquidHandler):
  """
  Interface for the Hamilton STAR.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    """Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STAR. Only useful if using more than
        one Hamilton machine over USB.
      serial_number: the serial number of the Hamilton STAR. Only useful if using more than one
        Hamilton machine over USB.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
    """

    super().__init__(
      device_address=device_address,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      id_product=0x8000,
      serial_number=serial_number,
    )

    self.iswap_installed: Optional[bool] = None
    self.autoload_installed: Optional[bool] = None
    self.core96_head_installed: Optional[bool] = None

    self._iswap_parked: Optional[bool] = None
    self._num_channels: Optional[int] = None
    self._core_parked: Optional[bool] = None
    self._extended_conf: Optional[dict] = None
    self._channel_traversal_height: float = 245.0
    self._iswap_traversal_height: float = 284.0
    self.core_adjustment = Coordinate.zero()
    self._unsafe = UnSafe(self)

    self._iswap_version: Optional[str] = None  # loaded lazily

  @property
  def unsafe(self) -> "UnSafe":
    """Actions that have a higher risk of damaging the robot. Use with care!"""
    return self._unsafe

  @property
  def num_channels(self) -> int:
    """The number of pipette channels present on the robot."""
    if self._num_channels is None:
      raise RuntimeError("has not loaded num_channels, forgot to call `setup`?")
    return self._num_channels

  def set_minimum_traversal_height(self, traversal_height: float):
    raise NotImplementedError(
      "set_minimum_traversal_height is depricated. use set_minimum_channel_traversal_height or "
      "set_minimum_iswap_traversal_height instead."
    )

  def set_minimum_channel_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the pip channels.

    This refers to the bottom of the pipetting channel when no tip is present, or the bottom of the
    tip when a tip is present. This value will be used as the default value for the
    `minimal_traverse_height_at_begin_of_command` and `minimal_height_at_command_end` parameters
    unless they are explicitly set.
    """

    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"

    self._channel_traversal_height = traversal_height

  def set_minimum_iswap_traversal_height(self, traversal_height: float):
    """Set the minimum traversal height for the iswap."""

    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"

    self._iswap_traversal_height = traversal_height

  @contextmanager
  def iswap_minimum_traversal_height(self, traversal_height: float):
    orig = self._iswap_traversal_height
    self._iswap_traversal_height = traversal_height
    try:
      yield
    except Exception as e:
      self._iswap_traversal_height = orig
      raise e

  @property
  def module_id_length(self):
    return 2

  @property
  def extended_conf(self) -> dict:
    """Extended configuration."""
    if self._extended_conf is None:
      raise RuntimeError("has not loaded extended_conf, forgot to call `setup`?")
    return self._extended_conf

  @property
  def iswap_parked(self) -> bool:
    return self._iswap_parked is True

  @property
  def core_parked(self) -> bool:
    return self._core_parked is True

  async def get_iswap_version(self) -> str:
    """Lazily load the iSWAP version. Use cached value if available."""
    if self._iswap_version is None:
      self._iswap_version = await self.request_iswap_version()
    return self._iswap_version

  async def request_pip_channel_version(self, channel: int) -> str:
    return cast(
      str, (await self.send_command(STAR.channel_id(channel), "RF", fmt="rf" + "&" * 17))["rf"]
    )

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""
    parsed = parse_star_fw_string(resp, "id####")
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str):
    """Raise an error if the firmware response is an error response.

    Raises:
      ValueError: if the format string is incompatible with the response.
      HamiltonException: if the response contains an error.
    """

    # Parse errors.
    module = resp[:2]
    if module == "C0":
      # C0 sends errors as er##/##. P1 raises errors as er## where the first group is the error
      # code, and the second group is the trace information.
      # Beyond that, specific errors may be added for individual channels and modules. These
      # are formatted as P1##/## H0##/##, etc. These items are added programmatically as
      # named capturing groups to the regex.

      exp = r"er(?P<C0>[0-9]{2}/[0-9]{2})"
      for module in [
        "X0",
        "I0",
        "W1",
        "W2",
        "T1",
        "T2",
        "R0",
        "P1",
        "P2",
        "P3",
        "P4",
        "P5",
        "P6",
        "P7",
        "P8",
        "P9",
        "PA",
        "PB",
        "PC",
        "PD",
        "PE",
        "PF",
        "PG",
        "H0",
        "HW",
        "HU",
        "HV",
        "N0",
        "D0",
        "NP",
        "M1",
      ]:
        exp += f" ?(?:{module}(?P<{module}>[0-9]{{2}}/[0-9]{{2}}))?"
      errors = re.search(exp, resp)
    else:
      # Other modules send errors as er##, and do not contain slave errors.
      exp = f"er(?P<{module}>[0-9]{{2}})"
      errors = re.search(exp, resp)

    if errors is not None:
      # filter None elements
      errors_dict = {k: v for k, v in errors.groupdict().items() if v is not None}
      # filter 00 and 00/00 elements, which mean no error.
      errors_dict = {k: v for k, v in errors_dict.items() if v not in ["00", "00/00"]}

    has_error = not (errors is None or len(errors_dict) == 0)
    if has_error:
      he = star_firmware_string_to_error(error_code_dict=errors_dict, raw_response=resp)

      # If there is a faulty parameter error, request which parameter that is.
      for module_name, error in he.errors.items():
        if error.message == "Unknown parameter":
          # temp. disabled until we figure out how to handle async in parse response (the
          # background thread does not have an event loop, and I'm not sure if it should.)
          # vp = await self.send_command(module=error.raw_module, command="VP", fmt="vp&&")["vp"]
          # he[module_name].message += f" ({vp})"

          he.errors[
            module_name
          ].message += " (call lh.backend.request_name_of_last_faulty_parameter)"

      raise he

  def _parse_response(self, resp: str, fmt: str) -> dict:
    """Parse a response from the machine."""
    return parse_star_fw_string(resp, fmt)

  async def setup(
    self,
    skip_autoload=False,
    skip_iswap=False,
    skip_core96_head=False,
  ):
    """Creates a USB connection and finds read/write interfaces.

    Args:
      skip_autoload: if True, skip initializing the autoload module, if applicable.
      skip_iswap: if True, skip initializing the iSWAP module, if applicable.
      skip_core96_head: if True, skip initializing the CoRe 96 head module, if applicable.
    """

    await super().setup()

    self.id_ = 0

    tip_presences = await self.request_tip_presence()
    self._num_channels = len(tip_presences)

    # Request machine information
    conf = await self.request_machine_configuration()
    self._extended_conf = await self.request_extended_configuration()

    left_x_drive_configuration_byte_1 = bin(self.extended_conf["xl"])
    left_x_drive_configuration_byte_1 = left_x_drive_configuration_byte_1 + "0" * (
      16 - len(left_x_drive_configuration_byte_1)
    )
    left_x_drive_configuration_byte_1 = left_x_drive_configuration_byte_1[2:]
    configuration_data1 = bin(conf["kb"]).split("b")[-1].zfill(8)
    autoload_configuration_byte = configuration_data1[-4]
    # Identify installations
    self.autoload_installed = autoload_configuration_byte == "1"
    self.core96_head_installed = left_x_drive_configuration_byte_1[2] == "1"
    self.iswap_installed = left_x_drive_configuration_byte_1[1] == "1"

    initialized = await self.request_instrument_initialization_status()

    if not initialized:
      logger.info("Running backend initialization procedure.")

      await self.pre_initialize_instrument()

    if not initialized or any(tip_presences):
      dy = (4050 - 2175) // (self.num_channels - 1)
      y_positions = [4050 - i * dy for i in range(self.num_channels)]

      await self.initialize_pipetting_channels(
        x_positions=[self.extended_conf["xw"]],  # Tip eject waste X position.
        y_positions=y_positions,
        begin_of_tip_deposit_process=int(self._channel_traversal_height * 10),
        end_of_tip_deposit_process=1220,
        z_position_at_end_of_a_command=3600,
        tip_pattern=[True] * self.num_channels,
        tip_type=4,  # TODO: get from tip types
        discarding_method=0,
      )

    if self.autoload_installed and not skip_autoload:
      autoload_initialized = await self.request_autoload_initialization_status()
      if not autoload_initialized:
        await self.initialize_autoload()

      await self.park_autoload()

    if self.iswap_installed and not skip_iswap:
      iswap_initialized = await self.request_iswap_initialization_status()
      if not iswap_initialized:
        await self.initialize_iswap()

      await self.park_iswap(
        minimum_traverse_height_at_beginning_of_a_command=int(self._iswap_traversal_height * 10)
      )

    if self.core96_head_installed and not skip_core96_head:
      core96_head_initialized = await self.request_core_96_head_initialization_status()
      if not core96_head_initialized:
        await self.initialize_core_96_head(
          trash96=self.deck.get_trash_area96(),
          z_position_at_the_command_end=self._channel_traversal_height,
        )

    # After setup, STAR will have thrown out anything mounted on the pipetting channels, including
    # the core grippers.
    self._core_parked = True

  # ============== LiquidHandlerBackend methods ==============

  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    begin_tip_pick_up_process: Optional[float] = None,
    end_tip_pick_up_process: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    pickup_method: Optional[TipPickupMethod] = None,
  ):
    """Pick up tips from a resource."""

    x_positions, y_positions, channels_involved = self._ops_to_fw_positions(ops, use_channels)

    tip_spots = [op.resource for op in ops]
    tips = set(cast(HamiltonTip, tip_spot.get_tip()) for tip_spot in tip_spots)
    if len(tips) > 1:
      raise ValueError("Cannot mix tips with different tip types.")
    ttti = (await self.get_ttti(list(tips)))[0]

    max_z = max(op.resource.get_absolute_location().z + op.offset.z for op in ops)
    max_total_tip_length = max(op.tip.total_tip_length for op in ops)
    max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)

    # not sure why this is necessary, but it is according to log files and experiments
    if self._get_hamilton_tip([op.resource for op in ops]).tip_size == TipSize.LOW_VOLUME:
      max_tip_length += 2
    elif self._get_hamilton_tip([op.resource for op in ops]).tip_size != TipSize.STANDARD_VOLUME:
      max_tip_length -= 2

    tip = ops[0].tip
    if not isinstance(tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    begin_tip_pick_up_process = (
      round((max_z + max_total_tip_length) * 10)
      if begin_tip_pick_up_process is None
      else int(begin_tip_pick_up_process * 10)
    )
    end_tip_pick_up_process = (
      round((max_z + max_tip_length) * 10)
      if end_tip_pick_up_process is None
      else round(end_tip_pick_up_process * 10)
    )
    minimum_traverse_height_at_beginning_of_a_command = (
      round(self._channel_traversal_height * 10)
      if minimum_traverse_height_at_beginning_of_a_command is None
      else round(minimum_traverse_height_at_beginning_of_a_command * 10)
    )
    pickup_method = pickup_method or tip.pickup_method

    try:
      return await self.pick_up_tip(
        x_positions=x_positions,
        y_positions=y_positions,
        tip_pattern=channels_involved,
        tip_type_idx=ttti,
        begin_tip_pick_up_process=begin_tip_pick_up_process,
        end_tip_pick_up_process=end_tip_pick_up_process,
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
        pickup_method=pickup_method,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

  async def drop_tips(
    self,
    ops: List[Drop],
    use_channels: List[int],
    drop_method: Optional[TipDropMethod] = None,
    begin_tip_deposit_process: Optional[float] = None,
    end_tip_deposit_process: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_end_of_a_command: Optional[float] = None,
  ):
    """Drop tips to a resource.

    Args:
      drop_method: The method to use for dropping tips. If None, the default method for dropping to
        tip spots is `DROP`, and everything else is `PLACE_SHIFT`. Note that `DROP` is only the
        default if *all* tips are being dropped to a tip spot.
    """

    if drop_method is None:
      if any(not isinstance(op.resource, TipSpot) for op in ops):
        drop_method = TipDropMethod.PLACE_SHIFT
      else:
        drop_method = TipDropMethod.DROP

    x_positions, y_positions, channels_involved = self._ops_to_fw_positions(ops, use_channels)

    # get highest z position
    max_z = max(op.resource.get_absolute_location().z + op.offset.z for op in ops)
    if drop_method == TipDropMethod.PLACE_SHIFT:
      # magic values empirically found in https://github.com/PyLabRobot/pylabrobot/pull/63
      begin_tip_deposit_process = (
        round((max_z + 59.9) * 10)
        if begin_tip_deposit_process is None
        else round(begin_tip_deposit_process * 10)
      )
      end_tip_deposit_process = (
        round((max_z + 49.9) * 10)
        if end_tip_deposit_process is None
        else round(end_tip_deposit_process * 10)
      )
    else:
      max_total_tip_length = max(op.tip.total_tip_length for op in ops)
      max_tip_length = max((op.tip.total_tip_length - op.tip.fitting_depth) for op in ops)
      begin_tip_deposit_process = (
        round((max_z + max_total_tip_length) * 10)
        if begin_tip_deposit_process is None
        else round(begin_tip_deposit_process * 10)
      )
      end_tip_deposit_process = (
        round((max_z + max_tip_length) * 10)
        if end_tip_deposit_process is None
        else round(end_tip_deposit_process * 10)
      )

    minimum_traverse_height_at_beginning_of_a_command = (
      round(self._channel_traversal_height * 10)
      if minimum_traverse_height_at_beginning_of_a_command is None
      else round(minimum_traverse_height_at_beginning_of_a_command * 10)
    )
    z_position_at_end_of_a_command = (
      round(self._channel_traversal_height * 10)
      if z_position_at_end_of_a_command is None
      else round(z_position_at_end_of_a_command * 10)
    )

    try:
      return await self.discard_tip(
        x_positions=x_positions,
        y_positions=y_positions,
        tip_pattern=channels_involved,
        begin_tip_deposit_process=begin_tip_deposit_process,
        end_tip_deposit_process=end_tip_deposit_process,
        minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
        z_position_at_end_of_a_command=z_position_at_end_of_a_command,
        discarding_method=drop_method,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

  def _assert_valid_resources(self, resources: Sequence[Resource]) -> None:
    """Assert that resources are in a valid location for pipetting."""
    for resource in resources:
      if resource.get_absolute_location().z < 100:
        raise ValueError(
          f"Resource {resource} is too low: {resource.get_absolute_location().z} < 100"
        )

  class LLDMode(enum.Enum):
    """Liquid level detection mode."""

    OFF = 0
    GAMMA = 1
    PRESSURE = 2
    DUAL = 3
    Z_TOUCH_OFF = 4

  async def aspirate(
    self,
    ops: List[SingleChannelAspiration],
    use_channels: List[int],
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,
    lld_search_height: Optional[List[float]] = None,
    clot_detection_height: Optional[List[float]] = None,
    pull_out_distance_transport_air: Optional[List[float]] = None,
    second_section_height: Optional[List[float]] = None,
    second_section_ratio: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    immersion_depth_direction: Optional[List[int]] = None,
    surface_following_distance: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    pre_wetting_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    aspirate_position_above_z_touch_off: Optional[List[float]] = None,
    detection_height_difference_for_dual_lld: Optional[List[float]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    mix_speed: Optional[List[float]] = None,
    mix_surface_following_distance: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    use_2nd_section_aspiration: Optional[List[bool]] = None,
    retract_height_over_2nd_section_to_empty_tip: Optional[List[float]] = None,
    dispensation_speed_during_emptying_tip: Optional[List[float]] = None,
    dosing_drive_speed_during_2nd_section_search: Optional[List[float]] = None,
    z_drive_speed_during_2nd_section_search: Optional[List[float]] = None,
    cup_upper_edge: Optional[List[float]] = None,
    ratio_liquid_rise_to_tip_deep_in: Optional[List[float]] = None,
    immersion_depth_2nd_section: Optional[List[float]] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    min_z_endpos: Optional[float] = None,
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    liquid_surfaces_no_lld: Optional[List[float]] = None,
  ):
    """Aspirate liquid from the specified channels.

    For all parameters where `None` is the default value, STAR will use the default value, based on
    the aspirations. For all list parameters, the length of the list must be equal to the number of
    operations.

    Args:
      ops: The aspiration operations to perform.
      use_channels: The channels to use for the operations.
      jet: whether to search for a jet liquid class. Only used on dispense. Default is False.
      blow_out: whether to blow out air. Only used on dispense. Note that in the VENUS Liquid
        Editor, this is called "empty". Default is False.

      lld_search_height: The height to start searching for the liquid level when using LLD.
      clot_detection_height: Unknown, but probably the height to search for clots when doing LLD.
      pull_out_distance_transport_air: The distance to pull out when aspirating air, if LLD is
        disabled.
      second_section_height: The height to start the second section of aspiration.
      second_section_ratio: Unknown.
      minimum_height: The minimum height to move to, this is the end of aspiration. The channel
       will move linearly from the liquid surface to this height over the course of the aspiration.
      immersion_depth: The z distance to move after detecting the liquid, can be into or away from
        the liquid surface (dependent on immersion_depth_direction).
      immersion_depth_direction: set to 0, tip will move below the detected liquid surface; set to
        1, tip will move away from the detected surface.
      surface_following_distance: The distance to follow the liquid surface.
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      lld_mode: The liquid level detection mode to use.
      gamma_lld_sensitivity: The sensitivity of the gamma LLD.
      dp_lld_sensitivity: The sensitivity of the DP LLD.
      aspirate_position_above_z_touch_off: If the LLD mode is Z_TOUCH_OFF, this is the height above
        the bottom of the well (presumably) to aspirate from.
      detection_height_difference_for_dual_lld: Difference between the gamma and DP LLD heights if
        the LLD mode is DUAL.
      swap_speed: Swap speed (on leaving liquid) [1mm/s]. Must be between 3 and 1600. Default 100.
      settling_time: The time to wait after mix.
      mix_volume: The volume to aspirate for mix.
      mix_cycles: The number of cycles to perform for mix.
      mix_position_from_liquid_surface: The height to aspirate from for mix
        (LLD or absolute terms).
      mix_speed: The speed to aspirate at for mix.
      mix_surface_following_distance: The distance to follow the liquid surface for
        mix.
      limit_curve_index: The index of the limit curve to use.

      use_2nd_section_aspiration: Whether to use the second section of aspiration.
      retract_height_over_2nd_section_to_empty_tip: Unknown.
      dispensation_speed_during_emptying_tip: Unknown.
      dosing_drive_speed_during_2nd_section_search: Unknown.
      z_drive_speed_during_2nd_section_search: Unknown.
      cup_upper_edge: Unknown.
      ratio_liquid_rise_to_tip_deep_in: Unknown.
      immersion_depth_2nd_section: The depth to move into the liquid for the second section of
        aspiration.

      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting an aspiration.
      min_z_endpos: The minimum height to move to, this is the end of aspiration.

      hamilton_liquid_classes: Override the default liquid classes. See
        pylabrobot/liquid_handling/liquid_classes/hamilton/star.py
      liquid_surface_no_lld: Liquid surface at function without LLD [mm]. Must be between 0
          and 360. Defaults to well bottom + liquid height. Should use absolute z.
    """

    x_positions, y_positions, channels_involved = self._ops_to_fw_positions(ops, use_channels)

    n = len(ops)

    if jet is None:
      jet = [False] * n
    if blow_out is None:
      blow_out = [False] * n

    if hamilton_liquid_classes is None:
      hamilton_liquid_classes = []
      for i, op in enumerate(ops):
        liquid = Liquid.WATER  # default to WATER
        # [-1][0]: get last liquid in well, [0] is indexing into the tuple
        if len(op.liquids) > 0 and op.liquids[-1][0] is not None:
          liquid = op.liquids[-1][0]

        hamilton_liquid_classes.append(
          get_star_liquid_class(
            tip_volume=op.tip.maximal_volume,
            is_core=False,
            is_tip=True,
            has_filter=op.tip.has_filter,
            liquid=liquid,
            jet=jet[i],
            blow_out=blow_out[i],
          )
        )

    self._assert_valid_resources([op.resource for op in ops])

    # correct volumes using the liquid class
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None else op.volume
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]

    well_bottoms = [
      op.resource.get_absolute_location().z + op.offset.z + op.resource.material_z_thickness
      for op in ops
    ]
    liquid_surfaces_no_lld = liquid_surfaces_no_lld or [
      wb + (op.liquid_height or 0) for wb, op in zip(well_bottoms, ops)
    ]
    if lld_search_height is None:
      lld_search_height = [
        (
          wb + op.resource.get_absolute_size_z() + (2.7 if isinstance(op.resource, Well) else 5)
        )  # ?
        for wb, op in zip(well_bottoms, ops)
      ]
    else:
      lld_search_height = [(wb + sh) for wb, sh in zip(well_bottoms, lld_search_height)]
    clot_detection_height = _fill_in_defaults(
      clot_detection_height,
      default=[
        hlc.aspiration_clot_retract_height if hlc is not None else 0
        for hlc in hamilton_liquid_classes
      ],
    )
    pull_out_distance_transport_air = _fill_in_defaults(pull_out_distance_transport_air, [10] * n)
    second_section_height = _fill_in_defaults(second_section_height, [3.2] * n)
    second_section_ratio = _fill_in_defaults(second_section_ratio, [618.0] * n)
    minimum_height = _fill_in_defaults(minimum_height, well_bottoms)
    # TODO: I think minimum height should be the minimum height of the well
    immersion_depth = _fill_in_defaults(immersion_depth, [0] * n)
    immersion_depth_direction = _fill_in_defaults(immersion_depth_direction, [0] * n)
    surface_following_distance = _fill_in_defaults(surface_following_distance, [0] * n)
    flow_rates = [
      op.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 100)
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]
    transport_air_volume = _fill_in_defaults(
      transport_air_volume,
      default=[
        hlc.aspiration_air_transport_volume if hlc is not None else 0
        for hlc in hamilton_liquid_classes
      ],
    )
    blow_out_air_volumes = [
      (op.blow_out_air_volume or (hlc.aspiration_blow_out_volume if hlc is not None else 0))
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]
    pre_wetting_volume = _fill_in_defaults(pre_wetting_volume, [0] * n)
    lld_mode = _fill_in_defaults(lld_mode, [self.__class__.LLDMode.OFF] * n)
    gamma_lld_sensitivity = _fill_in_defaults(gamma_lld_sensitivity, [1] * n)
    dp_lld_sensitivity = _fill_in_defaults(dp_lld_sensitivity, [1] * n)
    aspirate_position_above_z_touch_off = _fill_in_defaults(
      aspirate_position_above_z_touch_off, [0] * n
    )
    detection_height_difference_for_dual_lld = _fill_in_defaults(
      detection_height_difference_for_dual_lld, [0] * n
    )
    swap_speed = _fill_in_defaults(
      swap_speed,
      default=[
        hlc.aspiration_swap_speed if hlc is not None else 100 for hlc in hamilton_liquid_classes
      ],
    )
    settling_time = _fill_in_defaults(
      settling_time,
      default=[
        hlc.aspiration_settling_time if hlc is not None else 0 for hlc in hamilton_liquid_classes
      ],
    )
    mix_volume = _fill_in_defaults(mix_volume, [0] * n)
    mix_cycles = _fill_in_defaults(mix_cycles, [0] * n)
    mix_position_from_liquid_surface = _fill_in_defaults(mix_position_from_liquid_surface, [0] * n)
    mix_speed = _fill_in_defaults(
      mix_speed,
      default=[
        hlc.aspiration_mix_flow_rate if hlc is not None else 50.0 for hlc in hamilton_liquid_classes
      ],
    )
    mix_surface_following_distance = _fill_in_defaults(mix_surface_following_distance, [0] * n)
    limit_curve_index = _fill_in_defaults(limit_curve_index, [0] * n)

    use_2nd_section_aspiration = _fill_in_defaults(use_2nd_section_aspiration, [False] * n)
    retract_height_over_2nd_section_to_empty_tip = _fill_in_defaults(
      retract_height_over_2nd_section_to_empty_tip, [0] * n
    )
    dispensation_speed_during_emptying_tip = _fill_in_defaults(
      dispensation_speed_during_emptying_tip, [50.0] * n
    )
    dosing_drive_speed_during_2nd_section_search = _fill_in_defaults(
      dosing_drive_speed_during_2nd_section_search, [50.0] * n
    )
    z_drive_speed_during_2nd_section_search = _fill_in_defaults(
      z_drive_speed_during_2nd_section_search, [30.0] * n
    )
    cup_upper_edge = _fill_in_defaults(cup_upper_edge, [0] * n)
    ratio_liquid_rise_to_tip_deep_in = _fill_in_defaults(ratio_liquid_rise_to_tip_deep_in, [0] * n)
    immersion_depth_2nd_section = _fill_in_defaults(immersion_depth_2nd_section, [0] * n)

    try:
      return await self.aspirate_pip(
        aspiration_type=[0 for _ in range(n)],
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,
        aspiration_volumes=[round(vol * 10) for vol in volumes],
        lld_search_height=[round(lsh * 10) for lsh in lld_search_height],
        clot_detection_height=[round(cd * 10) for cd in clot_detection_height],
        liquid_surface_no_lld=[round(ls * 10) for ls in liquid_surfaces_no_lld],
        pull_out_distance_transport_air=[round(po * 10) for po in pull_out_distance_transport_air],
        second_section_height=[round(sh * 10) for sh in second_section_height],
        second_section_ratio=[round(sr * 10) for sr in second_section_ratio],
        minimum_height=[round(mh * 10) for mh in minimum_height],
        immersion_depth=[round(id_ * 10) for id_ in immersion_depth],
        immersion_depth_direction=immersion_depth_direction,
        surface_following_distance=[round(sfd * 10) for sfd in surface_following_distance],
        aspiration_speed=[round(fr * 10) for fr in flow_rates],
        transport_air_volume=[round(tav * 10) for tav in transport_air_volume],
        blow_out_air_volume=[round(boa * 10) for boa in blow_out_air_volumes],
        pre_wetting_volume=[round(pwv * 10) for pwv in pre_wetting_volume],
        lld_mode=[mode.value for mode in lld_mode],
        gamma_lld_sensitivity=gamma_lld_sensitivity,
        dp_lld_sensitivity=dp_lld_sensitivity,
        aspirate_position_above_z_touch_off=[
          round(ap * 10) for ap in aspirate_position_above_z_touch_off
        ],
        detection_height_difference_for_dual_lld=[
          round(dh * 10) for dh in detection_height_difference_for_dual_lld
        ],
        swap_speed=[round(ss * 10) for ss in swap_speed],
        settling_time=[round(st * 10) for st in settling_time],
        mix_volume=[round(hv * 10) for hv in mix_volume],
        mix_cycles=mix_cycles,
        mix_position_from_liquid_surface=[
          round(hp * 10) for hp in mix_position_from_liquid_surface
        ],
        mix_speed=[round(hs * 10) for hs in mix_speed],
        mix_surface_following_distance=[round(hsd * 10) for hsd in mix_surface_following_distance],
        limit_curve_index=limit_curve_index,
        use_2nd_section_aspiration=use_2nd_section_aspiration,
        retract_height_over_2nd_section_to_empty_tip=[
          round(rh * 10) for rh in retract_height_over_2nd_section_to_empty_tip
        ],
        dispensation_speed_during_emptying_tip=[
          round(ds * 10) for ds in dispensation_speed_during_emptying_tip
        ],
        dosing_drive_speed_during_2nd_section_search=[
          round(ds * 10) for ds in dosing_drive_speed_during_2nd_section_search
        ],
        z_drive_speed_during_2nd_section_search=[
          round(zs * 10) for zs in z_drive_speed_during_2nd_section_search
        ],
        cup_upper_edge=[round(cue * 10) for cue in cup_upper_edge],
        ratio_liquid_rise_to_tip_deep_in=ratio_liquid_rise_to_tip_deep_in,
        immersion_depth_2nd_section=[round(id_ * 10) for id_ in immersion_depth_2nd_section],
        minimum_traverse_height_at_beginning_of_a_command=round(
          (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
        ),
        min_z_endpos=round((min_z_endpos or self._channel_traversal_height) * 10),
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

  async def dispense(
    self,
    ops: List[SingleChannelDispense],
    use_channels: List[int],
    lld_search_height: Optional[List[float]] = None,
    liquid_surface_no_lld: Optional[List[float]] = None,
    dispensing_mode: Optional[List[int]] = None,
    pull_out_distance_transport_air: Optional[List[float]] = None,
    second_section_height: Optional[List[float]] = None,
    second_section_ratio: Optional[List[float]] = None,
    minimum_height: Optional[List[float]] = None,
    immersion_depth: Optional[List[float]] = None,
    immersion_depth_direction: Optional[List[int]] = None,
    surface_following_distance: Optional[List[float]] = None,
    cut_off_speed: Optional[List[float]] = None,
    stop_back_volume: Optional[List[float]] = None,
    transport_air_volume: Optional[List[float]] = None,
    lld_mode: Optional[List[LLDMode]] = None,
    dispense_position_above_z_touch_off: Optional[List[float]] = None,
    gamma_lld_sensitivity: Optional[List[int]] = None,
    dp_lld_sensitivity: Optional[List[int]] = None,
    swap_speed: Optional[List[float]] = None,
    settling_time: Optional[List[float]] = None,
    mix_volume: Optional[List[float]] = None,
    mix_cycles: Optional[List[int]] = None,
    mix_position_from_liquid_surface: Optional[List[float]] = None,
    mix_speed: Optional[List[float]] = None,
    mix_surface_following_distance: Optional[List[float]] = None,
    limit_curve_index: Optional[List[int]] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[int] = None,
    min_z_endpos: Optional[float] = None,
    side_touch_off_distance: float = 0,
    hamilton_liquid_classes: Optional[List[Optional[HamiltonLiquidClass]]] = None,
    jet: Optional[List[bool]] = None,
    blow_out: Optional[List[bool]] = None,  # "empty" in the VENUS liquid editor
    empty: Optional[List[bool]] = None,  # truly "empty", does not exist in liquid editor, dm4
  ):
    """Dispense liquid from the specified channels.

    For all parameters where `None` is the default value, STAR will use the default value, based on
    the dispenses. For all list parameters, the length of the list must be equal to the number of
    operations.

    Args:
      ops: The dispense operations to perform.
      use_channels: The channels to use for the dispense operations.
      dispensing_mode: The dispensing mode to use for each operation.
      lld_search_height: The height to start searching for the liquid level when using LLD.
      liquid_surface_no_lld: Liquid surface at function without LLD.
      pull_out_distance_transport_air: The distance to pull out the tip for aspirating transport air
        if LLD is disabled.
      second_section_height: Unknown.
      second_section_ratio: Unknown.
      minimum_height: The minimum height at the end of the dispense.
      immersion_depth: The distance above or below to liquid level to start dispensing. See the
        `immersion_depth_direction` parameter.
      immersion_depth_direction: (0 = go deeper, 1 = go up out of liquid)
      surface_following_distance: The distance to follow the liquid surface.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
      transport_air_volume: The volume of air to dispense before dispensing the liquid.
      lld_mode: The liquid level detection mode to use.
      dispense_position_above_z_touch_off: The height to move after LLD mode found the Z touch off
        position.
      gamma_lld_sensitivity: The gamma LLD sensitivity. (1 = high, 4 = low)
      dp_lld_sensitivity: The dp LLD sensitivity. (1 = high, 4 = low)
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600. Default 100.
      settling_time: The settling time.
      mix_volume: The volume to use for mix.
      mix_cycles: The number of mix cycles.
      mix_position_from_liquid_surface: The height to move above the liquid surface for
        mix.
      mix_speed: The mix speed.
      mix_surface_following_distance: The distance to follow the liquid surface for mix.
      limit_curve_index: The limit curve to use for the dispense.
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting a dispense.
      min_z_endpos: The minimum height to move to after a dispense.
      side_touch_off_distance: The distance to move to the side from the well for a dispense.

      hamilton_liquid_classes: Override the default liquid classes. See
        pylabrobot/liquid_handling/liquid_classes/hamilton/star.py

      jet: Whether to use jetting for each dispense. Defaults to `False` for all. Used for
        determining the dispense mode. True for dispense mode 0 or 1.
      blow_out: Whether to use "blow out" dispense mode for each dispense. Defaults to `False` for
        all. This is labelled as "empty" in the VENUS liquid editor, but "blow out" in the firmware
        documentation. True for dispense mode 1 or 3.
      empty: Whether to use "empty" dispense mode for each dispense. Defaults to `False` for all.
        Truly empty the tip, not available in the VENUS liquid editor, but is in the firmware
        documentation. Dispense mode 4.
    """

    x_positions, y_positions, channels_involved = self._ops_to_fw_positions(ops, use_channels)

    n = len(ops)

    if jet is None:
      jet = [False] * n
    if empty is None:
      empty = [False] * n
    if blow_out is None:
      blow_out = [False] * n

    if hamilton_liquid_classes is None:
      hamilton_liquid_classes = []
      for i, op in enumerate(ops):
        liquid = Liquid.WATER  # default to WATER
        # [-1][0]: get last liquid in tip, [0] is indexing into the tuple
        if len(op.liquids) > 0 and op.liquids[-1][0] is not None:
          liquid = op.liquids[-1][0]

        hamilton_liquid_classes.append(
          get_star_liquid_class(
            tip_volume=op.tip.maximal_volume,
            is_core=False,
            is_tip=True,
            has_filter=op.tip.has_filter,
            liquid=liquid,
            jet=jet[i],
            blow_out=blow_out[i],
          )
        )

    # correct volumes using the liquid class
    volumes = [
      hlc.compute_corrected_volume(op.volume) if hlc is not None else op.volume
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]

    well_bottoms = [
      op.resource.get_absolute_location().z + op.offset.z + op.resource.material_z_thickness
      for op in ops
    ]
    liquid_surfaces_no_lld = liquid_surface_no_lld or [
      ls + (op.liquid_height or 0) for ls, op in zip(well_bottoms, ops)
    ]
    if lld_search_height is None:
      lld_search_height = [
        (
          wb + op.resource.get_absolute_size_z() + (2.7 if isinstance(op.resource, Well) else 5)
        )  # ?
        for wb, op in zip(well_bottoms, ops)
      ]
    else:
      lld_search_height = [wb + sh for wb, sh in zip(well_bottoms, lld_search_height)]

    dispensing_modes = dispensing_mode or [
      _dispensing_mode_for_op(empty=empty[i], jet=jet[i], blow_out=blow_out[i])
      for i in range(len(ops))
    ]

    pull_out_distance_transport_air = _fill_in_defaults(pull_out_distance_transport_air, [10.0] * n)
    second_section_height = _fill_in_defaults(second_section_height, [3.2] * n)
    second_section_ratio = _fill_in_defaults(second_section_ratio, [618.0] * n)
    minimum_height = _fill_in_defaults(minimum_height, well_bottoms)
    immersion_depth = _fill_in_defaults(immersion_depth, [0] * n)
    immersion_depth_direction = _fill_in_defaults(immersion_depth_direction, [0] * n)
    surface_following_distance = _fill_in_defaults(surface_following_distance, [0] * n)
    flow_rates = [
      op.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 120)
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]
    cut_off_speed = _fill_in_defaults(cut_off_speed, [5.0] * n)
    stop_back_volume = _fill_in_defaults(
      stop_back_volume,
      default=[
        hlc.dispense_stop_back_volume if hlc is not None else 0 for hlc in hamilton_liquid_classes
      ],
    )
    transport_air_volume = _fill_in_defaults(
      transport_air_volume,
      default=[
        hlc.dispense_air_transport_volume if hlc is not None else 0
        for hlc in hamilton_liquid_classes
      ],
    )
    blow_out_air_volumes = [
      (op.blow_out_air_volume or (hlc.dispense_blow_out_volume if hlc is not None else 0))
      for op, hlc in zip(ops, hamilton_liquid_classes)
    ]
    lld_mode = _fill_in_defaults(lld_mode, [self.__class__.LLDMode.OFF] * n)
    dispense_position_above_z_touch_off = _fill_in_defaults(
      dispense_position_above_z_touch_off, default=[0] * n
    )
    gamma_lld_sensitivity = _fill_in_defaults(gamma_lld_sensitivity, [1] * n)
    dp_lld_sensitivity = _fill_in_defaults(dp_lld_sensitivity, [1] * n)
    swap_speed = _fill_in_defaults(
      swap_speed,
      default=[
        hlc.dispense_swap_speed if hlc is not None else 10.0 for hlc in hamilton_liquid_classes
      ],
    )
    settling_time = _fill_in_defaults(
      settling_time,
      default=[
        hlc.dispense_settling_time if hlc is not None else 0 for hlc in hamilton_liquid_classes
      ],
    )
    mix_volume = _fill_in_defaults(mix_volume, [0] * n)
    mix_cycles = _fill_in_defaults(mix_cycles, [0] * n)
    mix_position_from_liquid_surface = _fill_in_defaults(mix_position_from_liquid_surface, [0] * n)
    mix_speed = _fill_in_defaults(
      mix_speed,
      default=[
        hlc.dispense_mix_flow_rate if hlc is not None else 50.0 for hlc in hamilton_liquid_classes
      ],
    )
    mix_surface_following_distance = _fill_in_defaults(mix_surface_following_distance, [0] * n)
    limit_curve_index = _fill_in_defaults(limit_curve_index, [0] * n)

    try:
      ret = await self.dispense_pip(
        tip_pattern=channels_involved,
        x_positions=x_positions,
        y_positions=y_positions,
        dispensing_mode=dispensing_modes,
        dispense_volumes=[round(vol * 10) for vol in volumes],
        lld_search_height=[round(lsh * 10) for lsh in lld_search_height],
        liquid_surface_no_lld=[round(ls * 10) for ls in liquid_surfaces_no_lld],
        pull_out_distance_transport_air=[round(po * 10) for po in pull_out_distance_transport_air],
        second_section_height=[round(sh * 10) for sh in second_section_height],
        second_section_ratio=[round(sr * 10) for sr in second_section_ratio],
        minimum_height=[round(mh * 10) for mh in minimum_height],
        immersion_depth=[round(id_ * 10) for id_ in immersion_depth],  # [0, 0]
        immersion_depth_direction=immersion_depth_direction,
        surface_following_distance=[round(sfd * 10) for sfd in surface_following_distance],
        dispense_speed=[round(fr * 10) for fr in flow_rates],
        cut_off_speed=[round(cs * 10) for cs in cut_off_speed],
        stop_back_volume=[round(sbv * 10) for sbv in stop_back_volume],
        transport_air_volume=[round(tav * 10) for tav in transport_air_volume],
        blow_out_air_volume=[round(boa * 10) for boa in blow_out_air_volumes],
        lld_mode=[mode.value for mode in lld_mode],
        dispense_position_above_z_touch_off=[
          round(dp * 10) for dp in dispense_position_above_z_touch_off
        ],
        gamma_lld_sensitivity=gamma_lld_sensitivity,
        dp_lld_sensitivity=dp_lld_sensitivity,
        swap_speed=[round(ss * 10) for ss in swap_speed],
        settling_time=[round(st * 10) for st in settling_time],
        mix_volume=[round(mv * 10) for mv in mix_volume],
        mix_cycles=mix_cycles,
        mix_position_from_liquid_surface=[
          round(mp * 10) for mp in mix_position_from_liquid_surface
        ],
        mix_speed=[round(ms * 10) for ms in mix_speed],
        mix_surface_following_distance=[
          round(msfd * 10) for msfd in mix_surface_following_distance
        ],
        limit_curve_index=limit_curve_index,
        minimum_traverse_height_at_beginning_of_a_command=round(
          (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
        ),
        min_z_endpos=round((min_z_endpos or self._channel_traversal_height) * 10),
        side_touch_off_distance=side_touch_off_distance,
      )
    except STARFirmwareError as e:
      if plr_e := convert_star_firmware_error_to_plr_error(e):
        raise plr_e from e
      raise e

    return ret

  async def pick_up_tips96(
    self,
    pickup: PickupTipRack,
    tip_pickup_method: int = 0,
    z_deposit_position: float = 216.4,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    """Pick up tips using the 96 head."""
    assert self.core96_head_installed, "96 head must be installed"
    tip_spot_a1 = pickup.resource.get_item("A1")
    tip_a1 = tip_spot_a1.get_tip()
    assert isinstance(tip_a1, HamiltonTip), "Tip type must be HamiltonTip."
    ttti = await self.get_or_assign_tip_type_index(tip_a1)
    position = tip_spot_a1.get_absolute_location() + tip_spot_a1.center() + pickup.offset
    z_deposit_position += round(pickup.offset.z * 10)

    x_direction = 0 if position.x > 0 else 1
    return await self.pick_up_tips_core96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_position=round(position.y * 10),
      tip_type_idx=ttti,
      tip_pickup_method=tip_pickup_method,
      z_deposit_position=round(z_deposit_position * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      minimum_height_command_end=round(
        (minimum_height_command_end or self._channel_traversal_height) * 10
      ),
    )

  async def drop_tips96(
    self,
    drop: DropTipRack,
    z_deposit_position: float = 216.4,
    minimum_height_command_end: Optional[float] = None,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
  ):
    """Drop tips from the 96 head."""
    assert self.core96_head_installed, "96 head must be installed"
    if isinstance(drop.resource, TipRack):
      tip_a1 = drop.resource.get_item("A1")
      position = tip_a1.get_absolute_location() + tip_a1.center() + drop.offset
    else:
      position = self._position_96_head_in_resource(drop.resource) + drop.offset

    x_direction = 0 if position.x > 0 else 1
    return await self.discard_tips_core96(
      x_position=abs(round(position.x * 10)),
      x_direction=x_direction,
      y_position=round(position.y * 10),
      z_deposit_position=round(z_deposit_position * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      minimum_height_command_end=round(
        (minimum_height_command_end or self._channel_traversal_height) * 10
      ),
    )

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    jet: bool = False,
    blow_out: bool = False,
    use_lld: bool = False,
    liquid_height: float = 0,
    air_transport_retract_dist: float = 10,
    hlc: Optional[HamiltonLiquidClass] = None,
    aspiration_type: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    minimal_end_height: Optional[float] = None,
    lld_search_height: float = 199.9,
    maximum_immersion_depth: Optional[float] = None,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    tube_2nd_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_aspiration: float = 0,
    transport_air_volume: float = 5.0,
    pre_wetting_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 1.0,
    mix_volume: float = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: float = 0,
    surface_following_distance_during_mix: float = 0,
    speed_of_mix: float = 120.0,
    limit_curve_index: int = 0,
  ):
    """Aspirate using the Core96 head.

    Args:
      aspiration: The aspiration to perform.

      jet: Whether to search for a jet liquid class. Only used on dispense.
      blow_out: Whether to use "blow out" dispense mode. Only used on dispense. Note that this is
        labelled as "empty" in the VENUS liquid editor, but "blow out" in the firmware
        documentation.
      hlc: The Hamiltonian liquid class to use. If `None`, the liquid class will be determined
        automatically.

      use_lld: If True, use gamma liquid level detection. If False, use liquid height.
      liquid_height: The height of the liquid above the bottom of the well, in millimeters.
      air_transport_retract_dist: The distance to retract after aspirating, in millimeters.

      aspiration_type: The type of aspiration to perform. (0 = simple; 1 = sequence; 2 = cup emptied
        )
      minimum_traverse_height_at_beginning_of_a_command: The minimum height to move to before
        starting the command.
      minimal_end_height: The minimum height to move to after the command.
      lld_search_height: The height to search for the liquid level.
      maximum_immersion_depth: The maximum immersion depth.
      tube_2nd_section_height_measured_from_zm: Unknown.
      tube_2nd_section_ratio: Unknown.
      immersion_depth: The immersion depth above or below the liquid level. See
       `immersion_depth_direction`.
      immersion_depth_direction: The direction of the immersion depth. (0 = deeper, 1 = out of
        liquid)
      transport_air_volume: The volume of air to aspirate after the liquid.
      pre_wetting_volume: The volume of liquid to use for pre-wetting.
      gamma_lld_sensitivity: The sensitivity of the gamma liquid level detection.
      swap_speed: Swap speed (on leaving liquid) [1mm/s]. Must be between 0.3 and 160. Default 2.
      settling_time: The time to wait after aspirating.
      mix_volume: The volume of liquid to aspirate for mix.
      mix_cycles: The number of cycles to perform for mix.
      mix_position_from_liquid_surface: The position of the mix from the
        liquid surface.
      surface_following_distance_during_mix: The distance to follow the liquid surface
        during mix.
      speed_of_mix: The speed of mix.
      limit_curve_index: The index of the limit curve to use.
    """

    assert self.core96_head_installed, "96 head must be installed"

    # get the first well and tip as representatives
    if isinstance(aspiration, MultiHeadAspirationPlate):
      top_left_well = aspiration.wells[0]
      position = (
        top_left_well.get_absolute_location()
        + top_left_well.center()
        + Coordinate(z=top_left_well.material_z_thickness)
        + aspiration.offset
      )
    else:
      position = aspiration.container.get_absolute_location(y="b") + aspiration.offset

    tip = aspiration.tips[0]

    liquid_height = position.z + liquid_height

    liquid_to_be_aspirated = Liquid.WATER
    if len(aspiration.liquids[0]) > 0 and aspiration.liquids[0][0][0] is not None:
      # [channel][liquid][PyLabRobot.resources.liquid.Liquid]
      liquid_to_be_aspirated = aspiration.liquids[0][0][0]
    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=liquid_to_be_aspirated,
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if hlc is not None:
      volume = hlc.compute_corrected_volume(aspiration.volume)
    else:
      volume = aspiration.volume

    # Get better default values from the HLC if available
    transport_air_volume = transport_air_volume or (
      hlc.aspiration_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = aspiration.blow_out_air_volume or (
      hlc.aspiration_blow_out_volume if hlc is not None else 0
    )
    flow_rate = aspiration.flow_rate or (hlc.aspiration_flow_rate if hlc is not None else 250)
    swap_speed = swap_speed or (hlc.aspiration_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.aspiration_settling_time if hlc is not None else 0.5)
    speed_of_mix = speed_of_mix or (hlc.aspiration_mix_flow_rate if hlc is not None else 10.0)

    channel_pattern = [True] * 12 * 8

    # Was this ever true? Just copied it over from pyhamilton. Could have something to do with
    # the liquid classes and whether blow_out mode is enabled.
    # # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we aspirate air
    # # manually.
    # if blow_out_air_volume is not None and blow_out_air_volume > 0:
    #   await self.aspirate_core_96(
    #     x_position=int(position.x * 10),
    #     y_positions=int(position.y * 10),
    #     lld_mode=0,
    #     liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
    #     aspiration_volumes=int(blow_out_air_volume * 10)
    #   )

    return await self.aspirate_core_96(
      x_position=round(position.x * 10),
      x_direction=0,
      y_positions=round(position.y * 10),
      aspiration_type=aspiration_type,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      minimal_end_height=round((minimal_end_height or self._channel_traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_at_function_without_lld=round(liquid_height * 10),
      pull_out_distance_to_take_transport_air_in_function_without_lld=round(
        air_transport_retract_dist * 10
      ),
      maximum_immersion_depth=round((maximum_immersion_depth or position.z) * 10),
      tube_2nd_section_height_measured_from_zm=round(tube_2nd_section_height_measured_from_zm * 10),
      tube_2nd_section_ratio=round(tube_2nd_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction,
      liquid_surface_sink_distance_at_the_end_of_aspiration=round(
        liquid_surface_sink_distance_at_the_end_of_aspiration * 10
      ),
      aspiration_volumes=round(volume * 10),
      aspiration_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      pre_wetting_volume=round(pre_wetting_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mix_volume=round(mix_volume * 10),
      mix_cycles=mix_cycles,
      mix_position_from_liquid_surface=round(mix_position_from_liquid_surface * 10),
      surface_following_distance_during_mix=round(surface_following_distance_during_mix * 10),
      speed_of_mix=round(speed_of_mix * 10),
      channel_pattern=channel_pattern,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
    )

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    jet: bool = False,
    empty: bool = False,
    blow_out: bool = False,
    hlc: Optional[HamiltonLiquidClass] = None,
    liquid_height: float = 0,
    dispense_mode: Optional[int] = None,
    air_transport_retract_dist=10,
    use_lld: bool = False,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    minimal_end_height: Optional[float] = None,
    lld_search_height: float = 199.9,
    maximum_immersion_depth: Optional[float] = None,
    tube_2nd_section_height_measured_from_zm: float = 3.2,
    tube_2nd_section_ratio: float = 618.0,
    immersion_depth: float = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_dispense: float = 0,
    transport_air_volume: float = 5.0,
    gamma_lld_sensitivity: int = 1,
    swap_speed: float = 2.0,
    settling_time: float = 0,
    mixing_volume: float = 0,
    mixing_cycles: int = 0,
    mixing_position_from_liquid_surface: float = 0,
    surface_following_distance_during_mixing: float = 0,
    speed_of_mixing: float = 120.0,
    limit_curve_index: int = 0,
    cut_off_speed: float = 5.0,
    stop_back_volume: float = 0,
  ):
    """Dispense using the Core96 head.

    Args:
      dispense: The Dispense command to execute.
      jet: Whether to use jet dispense mode.
      blow_out: Whether to blow out after dispensing.
      liquid_height: The height of the liquid in the well, in mm. Used if LLD is not used.
      dispense_mode: The dispense mode to use. 0 = Partial volume in jet mode 1 = Blow out in jet
        mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix position.
        If `None`, the mode will be determined based on the `jet`, `empty`, and `blow_out`
      air_transport_retract_dist: The distance to retract after dispensing, in mm.
      use_lld: Whether to use gamma LLD.

      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command, in mm.
      minimal_end_height: Minimal end height, in mm.
      lld_search_height: LLD search height, in mm.
      maximum_immersion_depth: Maximum immersion depth, in mm. Equals Minimum height during command.
      tube_2nd_section_height_measured_from_zm: Unknown.
      tube_2nd_section_ratio: Unknown.
      immersion_depth: Immersion depth, in mm. See `immersion_depth_direction`.
      immersion_depth_direction: Immersion depth direction. 0 = go deeper, 1 = go up out of liquid.
      liquid_surface_sink_distance_at_the_end_of_dispense: Unknown.
      transport_air_volume: Transport air volume, to dispense before aspiration.
      gamma_lld_sensitivity: Gamma LLD sensitivity.
      swap_speed: Swap speed (on leaving liquid) [mm/s]. Must be between 0.3 and 160. Default 10.
      settling_time: Settling time, in seconds.
      mixing_volume: Mixing volume, in ul.
      mixing_cycles: Mixing cycles.
      mixing_position_from_liquid_surface: Mixing position from liquid surface, in mm.
      surface_following_distance_during_mixing: Surface following distance during mixing, in mm.
      speed_of_mixing: Speed of mixing, in ul/s.
      limit_curve_index: Limit curve index.
      cut_off_speed: Unknown.
      stop_back_volume: Unknown.
    """

    assert self.core96_head_installed, "96 head must be installed"

    # get the first well and tip as representatives
    if isinstance(dispense, MultiHeadDispensePlate):
      top_left_well = dispense.wells[0]
      position = (
        top_left_well.get_absolute_location()
        + top_left_well.center()
        + Coordinate(z=top_left_well.material_z_thickness)
        + dispense.offset
      )
    else:
      position = dispense.container.get_absolute_location(y="b") + dispense.offset
    tip = dispense.tips[0]

    liquid_height = position.z + liquid_height

    dispense_mode = _dispensing_mode_for_op(empty=empty, jet=jet, blow_out=blow_out)

    liquid_to_be_dispensed = Liquid.WATER  # default to water.
    if len(dispense.liquids[0]) > 0 and dispense.liquids[0][-1][0] is not None:
      # [channel][liquid][PyLabRobot.resources.liquid.Liquid]
      liquid_to_be_dispensed = dispense.liquids[0][-1][0]
    hlc = hlc or get_star_liquid_class(
      tip_volume=tip.maximal_volume,
      is_core=True,
      is_tip=True,
      has_filter=tip.has_filter,
      # get last liquid in pipette, first to be dispensed
      liquid=liquid_to_be_dispensed,
      jet=jet,
      blow_out=blow_out,  # see comment in method docstring
    )

    if hlc is not None:
      volume = hlc.compute_corrected_volume(dispense.volume)
    else:
      volume = dispense.volume

    transport_air_volume = transport_air_volume or (
      hlc.dispense_air_transport_volume if hlc is not None else 0
    )
    blow_out_air_volume = dispense.blow_out_air_volume or (
      hlc.dispense_blow_out_volume if hlc is not None else 0
    )
    flow_rate = dispense.flow_rate or (hlc.dispense_flow_rate if hlc is not None else 120)
    swap_speed = swap_speed or (hlc.dispense_swap_speed if hlc is not None else 100)
    settling_time = settling_time or (hlc.dispense_settling_time if hlc is not None else 5)
    speed_of_mixing = speed_of_mixing or (hlc.dispense_mix_flow_rate if hlc is not None else 100)

    channel_pattern = [True] * 12 * 8

    ret = await self.dispense_core_96(
      dispensing_mode=dispense_mode,
      x_position=round(position.x * 10),
      x_direction=0,
      y_position=round(position.y * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._channel_traversal_height) * 10
      ),
      minimal_end_height=round((minimal_end_height or self._channel_traversal_height) * 10),
      lld_search_height=round(lld_search_height * 10),
      liquid_surface_at_function_without_lld=round(liquid_height * 10),
      pull_out_distance_to_take_transport_air_in_function_without_lld=round(
        air_transport_retract_dist * 10
      ),
      maximum_immersion_depth=maximum_immersion_depth or round(position.z * 10),
      tube_2nd_section_height_measured_from_zm=round(tube_2nd_section_height_measured_from_zm * 10),
      tube_2nd_section_ratio=round(tube_2nd_section_ratio * 10),
      immersion_depth=round(immersion_depth * 10),
      immersion_depth_direction=immersion_depth_direction,
      liquid_surface_sink_distance_at_the_end_of_dispense=round(
        liquid_surface_sink_distance_at_the_end_of_dispense * 10
      ),
      dispense_volume=round(volume * 10),
      dispense_speed=round(flow_rate * 10),
      transport_air_volume=round(transport_air_volume * 10),
      blow_out_air_volume=round(blow_out_air_volume * 10),
      lld_mode=int(use_lld),
      gamma_lld_sensitivity=gamma_lld_sensitivity,
      swap_speed=round(swap_speed * 10),
      settling_time=round(settling_time * 10),
      mixing_volume=round(mixing_volume * 10),
      mixing_cycles=mixing_cycles,
      mixing_position_from_liquid_surface=round(mixing_position_from_liquid_surface * 10),
      surface_following_distance_during_mixing=round(surface_following_distance_during_mixing * 10),
      speed_of_mixing=round(speed_of_mixing * 10),
      channel_pattern=channel_pattern,
      limit_curve_index=limit_curve_index,
      tadm_algorithm=False,
      recording_mode=0,
      cut_off_speed=round(cut_off_speed * 10),
      stop_back_volume=round(stop_back_volume * 10),
    )

    # Was this ever true? Just copied it over from pyhamilton. Could have something to do with
    # the liquid classes and whether blow_out mode is enabled.
    # # Unfortunately, `blow_out_air_volume` does not work correctly, so instead we dispense air
    # # manually.
    # if blow_out_air_volume is not None and blow_out_air_volume > 0:
    #   await self.dispense_core_96(
    #     x_position=int(position.x * 10),
    #     y_position=int(position.y * 10),
    #     lld_mode=0,
    #     liquid_surface_at_function_without_lld=int((liquid_height + 30) * 10),
    #     dispense_volume=int(blow_out_air_volume * 10),
    #   )

    return ret

  async def iswap_move_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    grip_direction: GripDirection,
    minimum_traverse_height_at_beginning_of_a_command: float = 284.0,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """After a resource is picked up, move it to a new location but don't release it yet.
    Low level component of :meth:`move_resource`
    """

    assert self.iswap_installed, "iswap must be installed"

    center = location + resource.center()

    await self.move_plate_to_position(
      x_position=round(center.x * 10),
      x_direction=0,
      y_position=round(center.y * 10),
      y_direction=0,
      z_position=round((location.z + resource.get_absolute_size_z() / 2) * 10),
      z_direction=0,
      grip_direction={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      minimum_traverse_height_at_beginning_of_a_command=round(
        minimum_traverse_height_at_beginning_of_a_command * 10
      ),
      collision_control_level=collision_control_level,
      acceleration_index_high_acc=acceleration_index_high_acc,
      acceleration_index_low_acc=acceleration_index_low_acc,
    )

  async def core_pick_up_resource(
    self,
    resource: Resource,
    pickup_distance_from_top: float,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    minimum_z_position_at_the_command_end: Optional[float] = None,
    grip_strength: int = 15,
    z_speed: float = 50.0,
    y_gripping_speed: float = 5.0,
    channel_1: int = 7,
    channel_2: int = 8,
  ):
    """Pick up resource with CoRe gripper tool
    Low level component of :meth:`move_resource`

    Args:
      resource: Resource to pick up.
      offset: Offset from resource position in mm.
      pickup_distance_from_top: Distance from top of resource to pick up.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 360.
      grip_strength: Grip strength (0 = weak, 99 = strong). Must be between 0 and 99. Default 15.
      z_speed: Z speed [mm/s]. Must be between 0.4 and 128.7. Default 50.0.
      y_gripping_speed: Y gripping speed [mm/s]. Must be between 0 and 370.0. Default 5.0.
      channel_1: Channel 1. Must be between 0 and self._num_channels - 1. Default 7.
      channel_2: Channel 2. Must be between 1 and self._num_channels. Default 8.
    """

    # Get center of source plate. Also gripping height and plate width.
    center = resource.get_absolute_location(x="c", y="c", z="b") + offset
    grip_height = center.z + resource.get_absolute_size_z() - pickup_distance_from_top
    grip_width = resource.get_absolute_size_y()  # grip width is y size of resource

    if self.core_parked:
      await self.get_core(p1=channel_1, p2=channel_2)

    await self.core_get_plate(
      x_position=round(center.x * 10),
      x_direction=0,
      y_position=round(center.y * 10),
      y_gripping_speed=round(y_gripping_speed * 10),
      z_position=round(grip_height * 10),
      z_speed=round(z_speed * 10),
      open_gripper_position=round(grip_width * 10) + 30,
      plate_width=round(grip_width * 10) - 30,
      grip_strength=grip_strength,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap_traversal_height) * 10
      ),
      minimum_z_position_at_the_command_end=round(
        (minimum_z_position_at_the_command_end or self._iswap_traversal_height) * 10
      ),
    )

  async def core_move_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    acceleration_index: int = 4,
    z_speed: float = 50.0,
  ):
    """After a ressource is picked up, move it to a new location but don't release it yet.
    Low level component of :meth:`move_resource`

    Args:
      location: Location to move to.
      resource: Resource to move.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 3600. Default 3600.
      acceleration_index: Acceleration index (0 = 0.1 mm/s2, 1 = 0.2 mm/s2, 2 = 0.5 mm/s2,
        3 = 1.0 mm/s2, 4 = 2.0 mm/s2, 5 = 5.0 mm/s2, 6 = 10.0 mm/s2, 7 = 20.0 mm/s2). Must be
        between 0 and 7. Default 4.
      z_speed: Z speed [0.1mm/s]. Must be between 3 and 1600. Default 500.
    """

    center = location + resource.center()

    await self.core_move_plate_to_position(
      x_position=round(center.x * 10),
      x_direction=0,
      x_acceleration_index=acceleration_index,
      y_position=round(center.y * 10),
      z_position=round(center.z * 10),
      z_speed=round(z_speed * 10),
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap_traversal_height) * 10
      ),
    )

  async def core_release_picked_up_resource(
    self,
    location: Coordinate,
    resource: Resource,
    pickup_distance_from_top: float,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    return_tool: bool = True,
  ):
    """Place resource with CoRe gripper tool
    Low level component of :meth:`move_resource`

    Args:
      resource: Location to place.
      pickup_distance_from_top: Distance from top of resource to place.
      offset: Offset from resource position in mm.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command [mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 360.0.
      z_position_at_the_command_end: Minimum z-Position at end of a command [mm] (refers to all
        channels independent of tip pattern parameter 'tm'). Must be between 0 and 360.0
      return_tool: Return tool to wasteblock mount after placing. Default True.
    """

    # Get center of destination location. Also gripping height and plate width.
    grip_height = location.z + resource.get_absolute_size_z() - pickup_distance_from_top
    grip_width = resource.get_absolute_size_y()

    await self.core_put_plate(
      x_position=round(location.x * 10),
      x_direction=0,
      y_position=round(location.y * 10),
      z_position=round(grip_height * 10),
      z_press_on_distance=0,
      z_speed=500,
      open_gripper_position=round(grip_width * 10) + 30,
      minimum_traverse_height_at_beginning_of_a_command=round(
        (minimum_traverse_height_at_beginning_of_a_command or self._iswap_traversal_height) * 10
      ),
      z_position_at_the_command_end=round(
        (z_position_at_the_command_end or self._iswap_traversal_height) * 10
      ),
      return_tool=return_tool,
    )

  async def pick_up_resource(
    self,
    pickup: ResourcePickup,
    use_arm: Literal["iswap", "core"] = "iswap",
    channel_1: int = 7,
    channel_2: int = 8,
    iswap_grip_strength: int = 4,
    core_grip_strength: int = 15,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    plate_width_tolerance: float = 2.0,
    open_gripper_position: Optional[float] = None,
    hotel_depth=160.0,
    hotel_clearance_height=7.5,
    high_speed=False,
    plate_width: Optional[float] = None,
    use_unsafe_hotel: bool = False,
    iswap_collision_control_level: int = 0,
    iswap_fold_up_sequence_at_the_end_of_process: bool = True,
  ):
    if use_arm == "iswap":
      assert (
        pickup.resource.get_absolute_rotation().x == 0
        and pickup.resource.get_absolute_rotation().y == 0
      )
      assert pickup.resource.get_absolute_rotation().z % 90 == 0
      if plate_width is None:
        if pickup.direction in (GripDirection.FRONT, GripDirection.BACK):
          plate_width = pickup.resource.get_absolute_size_x()
        else:
          plate_width = pickup.resource.get_absolute_size_y()

      center_in_absolute_space = pickup.resource.center().rotated(
        pickup.resource.get_absolute_rotation()
      )
      x, y, z = (
        pickup.resource.get_absolute_location("l", "f", "t")
        + center_in_absolute_space
        + pickup.offset
      )
      z -= pickup.pickup_distance_from_top

      traverse_height_at_beginning = (
        minimum_traverse_height_at_beginning_of_a_command or self._iswap_traversal_height
      )
      z_position_at_the_command_end = z_position_at_the_command_end or self._iswap_traversal_height

      if open_gripper_position is None:
        if use_unsafe_hotel:
          open_gripper_position = plate_width + 5
        else:
          open_gripper_position = plate_width + 3

      if use_unsafe_hotel:
        await self.unsafe.get_from_hotel(
          hotel_center_x_coord=round(abs(x) * 10),
          hotel_center_y_coord=round(abs(y) * 10),
          # hotel_center_z_coord=int((z * 10)+0.5), # use sensible rounding (.5 goes up)
          hotel_center_z_coord=round(abs(z) * 10),
          hotel_center_x_direction=0 if x >= 0 else 1,
          hotel_center_y_direction=0 if y >= 0 else 1,
          hotel_center_z_direction=0 if z >= 0 else 1,
          clearance_height=round(hotel_clearance_height * 10),
          hotel_depth=round(hotel_depth * 10),
          grip_direction=pickup.direction,
          open_gripper_position=round(open_gripper_position * 10),
          traverse_height_at_beginning=round(traverse_height_at_beginning * 10),
          z_position_at_end=round(z_position_at_the_command_end * 10),
          high_acceleration_index=4 if high_speed else 1,
          low_acceleration_index=1,
          plate_width=round(plate_width * 10),
          plate_width_tolerance=round(plate_width_tolerance * 10),
        )
      else:
        await self.iswap_get_plate(
          x_position=round(x * 10),
          y_position=round(y * 10),
          z_position=round(z * 10),
          x_direction=0 if x >= 0 else 1,
          y_direction=0 if y >= 0 else 1,
          z_direction=0 if z >= 0 else 1,
          grip_direction={
            GripDirection.FRONT: 1,
            GripDirection.RIGHT: 2,
            GripDirection.BACK: 3,
            GripDirection.LEFT: 4,
          }[pickup.direction],
          minimum_traverse_height_at_beginning_of_a_command=round(
            traverse_height_at_beginning * 10
          ),
          z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
          grip_strength=iswap_grip_strength,
          open_gripper_position=round(open_gripper_position * 10),
          plate_width=round(plate_width * 10) - 33,
          plate_width_tolerance=round(plate_width_tolerance * 10),
          collision_control_level=iswap_collision_control_level,
          acceleration_index_high_acc=4 if high_speed else 1,
          acceleration_index_low_acc=1,
          fold_up_sequence_at_the_end_of_process=iswap_fold_up_sequence_at_the_end_of_process,
        )
    elif use_arm == "core":
      if use_unsafe_hotel:
        raise ValueError("Cannot use iswap hotel mode with core grippers")

      await self.core_pick_up_resource(
        resource=pickup.resource,
        pickup_distance_from_top=pickup.pickup_distance_from_top,
        offset=pickup.offset,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap_traversal_height,
        minimum_z_position_at_the_command_end=self._iswap_traversal_height,
        channel_1=channel_1,
        channel_2=channel_2,
        grip_strength=core_grip_strength,
      )
    else:
      raise ValueError(f"use_arm must be either 'iswap' or 'core', not {use_arm}")

  async def move_picked_up_resource(
    self, move: ResourceMove, use_arm: Literal["iswap", "core"] = "iswap"
  ):
    if use_arm == "iswap":
      await self.iswap_move_picked_up_resource(
        location=move.location,
        resource=move.resource,
        grip_direction=move.gripped_direction,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap_traversal_height,
        collision_control_level=1,
        acceleration_index_high_acc=4,
        acceleration_index_low_acc=1,
      )
    else:
      await self.core_move_picked_up_resource(
        location=move.location,
        resource=move.resource,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap_traversal_height,
        acceleration_index=4,
      )

  async def drop_resource(
    self,
    drop: ResourceDrop,
    use_arm: Literal["iswap", "core"] = "iswap",
    return_core_gripper: bool = True,
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None,
    z_position_at_the_command_end: Optional[float] = None,
    open_gripper_position: Optional[float] = None,
    hotel_depth=160.0,
    hotel_clearance_height=7.5,
    hotel_high_speed=False,
    use_unsafe_hotel: bool = False,
    iswap_collision_control_level: int = 0,
  ):
    # Get center of source plate in absolute space.
    # The computation of the center has to be rotated so that the offset is in absolute space.
    # center_in_absolute_space will be the vector pointing from the destination origin to the
    # center of the moved the resource after drop.
    # This means that the center vector has to be rotated from the child local space by the
    # new child absolute rotation. The moved resource's rotation will be the original child
    # rotation plus the rotation applied by the movement.
    # The resource is moved by drop.rotation
    # The new resource absolute location is
    # drop.resource.get_absolute_rotation().z + drop.rotation
    center_in_absolute_space = drop.resource.center().rotated(
      Rotation(z=drop.resource.get_absolute_rotation().z + drop.rotation)
    )
    x, y, z = drop.destination + center_in_absolute_space + drop.offset

    if use_arm == "iswap":
      traversal_height_start = (
        minimum_traverse_height_at_beginning_of_a_command or self._iswap_traversal_height
      )
      z_position_at_the_command_end = z_position_at_the_command_end or self._iswap_traversal_height
      assert (
        drop.resource.get_absolute_rotation().x == 0
        and drop.resource.get_absolute_rotation().y == 0
      )
      assert drop.resource.get_absolute_rotation().z % 90 == 0

      # Use the pickup direction to determine how wide the plate is gripped.
      # Note that the plate is still in the original orientation at this point,
      # so get_absolute_size_{x,y}() will return the size of the plate in the original orientation.
      if (
        drop.pickup_direction == GripDirection.FRONT or drop.pickup_direction == GripDirection.BACK
      ):
        plate_width = drop.resource.get_absolute_size_x()
      elif (
        drop.pickup_direction == GripDirection.RIGHT or drop.pickup_direction == GripDirection.LEFT
      ):
        plate_width = drop.resource.get_absolute_size_y()
      else:
        raise ValueError("Invalid grip direction")

      z = z + drop.resource.get_absolute_size_z() - drop.pickup_distance_from_top

      if open_gripper_position is None:
        if use_unsafe_hotel:
          open_gripper_position = plate_width + 5
        else:
          open_gripper_position = plate_width + 3

      if use_unsafe_hotel:
        # hotel: down forward down.
        # down to level of the destination + the clearance height (so clearance height can be subtracted)
        # hotel_depth is forward.
        # clearance height is second down.

        await self.unsafe.put_in_hotel(
          hotel_center_x_coord=round(abs(x) * 10),
          hotel_center_y_coord=round(abs(y) * 10),
          hotel_center_z_coord=round(abs(z) * 10),
          hotel_center_x_direction=0 if x >= 0 else 1,
          hotel_center_y_direction=0 if y >= 0 else 1,
          hotel_center_z_direction=0 if z >= 0 else 1,
          clearance_height=round(hotel_clearance_height * 10),
          hotel_depth=round(hotel_depth * 10),
          grip_direction=drop.drop_direction,
          open_gripper_position=round(open_gripper_position * 10),
          traverse_height_at_beginning=round(traversal_height_start * 10),
          z_position_at_end=round(z_position_at_the_command_end * 10),
          high_acceleration_index=4 if hotel_high_speed else 1,
          low_acceleration_index=1,
        )
      else:
        await self.iswap_put_plate(
          x_position=round(abs(x) * 10),
          y_position=round(abs(y) * 10),
          z_position=round(abs(z) * 10),
          x_direction=0 if x >= 0 else 1,
          y_direction=0 if y >= 0 else 1,
          z_direction=0 if z >= 0 else 1,
          grip_direction={
            GripDirection.FRONT: 1,
            GripDirection.RIGHT: 2,
            GripDirection.BACK: 3,
            GripDirection.LEFT: 4,
          }[drop.drop_direction],
          minimum_traverse_height_at_beginning_of_a_command=round(traversal_height_start * 10),
          z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
          open_gripper_position=round(open_gripper_position * 10),
          collision_control_level=iswap_collision_control_level,
        )
    elif use_arm == "core":
      if use_unsafe_hotel:
        raise ValueError("Cannot use iswap hotel mode with core grippers")

      await self.core_release_picked_up_resource(
        location=Coordinate(x, y, z),
        resource=drop.resource,
        pickup_distance_from_top=drop.pickup_distance_from_top,
        minimum_traverse_height_at_beginning_of_a_command=self._iswap_traversal_height,
        z_position_at_the_command_end=self._iswap_traversal_height,
        # int(previous_location.z + move.resource.get_size_z() / 2) * 10,
        return_tool=return_core_gripper,
      )
    else:
      raise ValueError(f"use_arm must be either 'iswap' or 'core', not {use_arm}")

  async def prepare_for_manual_channel_operation(self, channel: int):
    """Prepare for manual operation."""

    await self.position_max_free_y_for_n(pipetting_channel_index=channel)

  async def move_channel_x(self, channel: int, x: float):
    """Move a channel in the x direction."""
    await self.position_left_x_arm_(round(x * 10))

  @need_iswap_parked
  async def move_channel_y(self, channel: int, y: float):
    """Move a channel safely in the y direction."""

    # Anti-channel-crash feature
    if channel > 0:
      max_y_pos = await self.request_y_pos_channel_n(channel - 1)
      if y > max_y_pos:
        raise ValueError(
          f"channel {channel} y-target must be <= {max_y_pos} mm "
          f"(channel {channel - 1} y-position is {round(y, 2)} mm)"
        )
    else:
      # STAR machines appear to lose connection to a channel if y > 635 mm
      max_y_pos = 635
      if y > max_y_pos:
        raise ValueError(f"channel {channel} y-target must be <= {max_y_pos} mm (machine limit)")

    if channel < (self.num_channels - 1):
      min_y_pos = await self.request_y_pos_channel_n(channel + 1)
      if y < min_y_pos:
        raise ValueError(
          f"channel {channel} y-target must be >= {min_y_pos} mm "
          f"(channel {channel + 1} y-position is {round(y, 2)} mm)"
        )
    else:
      # STAR machines appear to lose connection to a channel if y < 6 mm
      min_y_pos = 6
      if y < min_y_pos:
        raise ValueError(f"channel {channel} y-target must be >= {min_y_pos} mm (machine limit)")

    await self.position_single_pipetting_channel_in_y_direction(
      pipetting_channel_index=channel + 1, y_position=round(y * 10)
    )

  async def move_channel_z(self, channel: int, z: float):
    """Move a channel in the z direction."""
    await self.position_single_pipetting_channel_in_z_direction(
      pipetting_channel_index=channel + 1, z_position=round(z * 10)
    )

  async def core_check_resource_exists_at_location_center(
    self,
    location: Coordinate,
    resource: Resource,
    gripper_y_margin: float = 0.5,
    offset: Coordinate = Coordinate.zero(),
    minimum_traverse_height_at_beginning_of_a_command: float = 275.0,
    z_position_at_the_command_end: float = 275.0,
    enable_recovery: bool = True,
    audio_feedback: bool = True,
  ) -> bool:
    """Check existence of resource with CoRe gripper tool
    a "Get plate using CO-RE gripper" + error handling
    Which channels are used for resource check is dependent on which channels have been used for
    `STAR.get_core(p1: int, p2: int)` which is a prerequisite for this check function.

    Args:
      location: Location to check for resource
      resource: Resource to check for.
      gripper_y_margin = Distance between the front / back wall of the resource
        and the grippers during "bumping" / checking
      offset: Offset from resource position in mm.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
        a command [mm] (refers to all channels independent of tip pattern parameter 'tm').
        Must be between 0 and 360.0.
      z_position_at_the_command_end: Minimum z-Position at end of a command [mm] (refers to
        all channels independent of tip pattern parameter 'tm'). Must be between 0 and 360.0.
      enable_recovery: if True will ask for user input if resource was not found
      audio_feedback: enable controlling computer to emit different sounds when
        finding/not finding the resource

    Returns:
      True if resource was found, False if resource was not found
    """

    center = location + resource.centers()[0] + offset
    y_width_to_gripper_bump = resource.get_absolute_size_y() - gripper_y_margin * 2
    assert 9 <= y_width_to_gripper_bump <= round(resource.get_absolute_size_y()), (
      f"width between channels must be between 9 and {resource.get_absolute_size_y()} mm"
      " (i.e. the minimal distance between channels and the max y size of the resource"
    )

    # Check if CoRe gripper currently in use
    cores_used = not self._core_parked
    if not cores_used:
      raise ValueError("CoRe grippers not yet picked up.")

    # Enable recovery of failed checks
    resource_found = False
    try_counter = 0
    while not resource_found:
      try:
        await self.core_get_plate(
          x_position=round(center.x * 10),
          y_position=round(center.y * 10),
          z_position=round(center.z * 10),
          open_gripper_position=round(y_width_to_gripper_bump * 10),
          plate_width=round(y_width_to_gripper_bump * 10),
          # Set default values based on VENUS check_plate commands
          y_gripping_speed=50,
          x_direction=0,
          z_speed=600,
          grip_strength=20,
          # Enable mods of channel z position for check acceleration
          minimum_traverse_height_at_beginning_of_a_command=round(
            minimum_traverse_height_at_beginning_of_a_command * 10
          ),
          minimum_z_position_at_the_command_end=round(z_position_at_the_command_end * 10),
        )
      except STARFirmwareError as exc:
        for module_error in exc.errors.values():
          if module_error.trace_information == 62:
            resource_found = True
          else:
            raise ValueError(f"Unexpected error encountered: {exc}") from exc
      else:
        if audio_feedback:
          audio.play_not_found()
        if enable_recovery:
          print(
            f"\nWARNING: Resource '{resource.name}' not found at center"
            f" location {(center.x, center.y, center.z)} during check no {try_counter}."
          )
          user_prompt = input(
            "Have you checked resource is present?"
            "\n [ yes ] -> machine will check location again"
            "\n [ abort ] -> machine will abort run\n Answer:"
          )
          if user_prompt == "yes":
            try_counter += 1
          elif user_prompt == "abort":
            raise ValueError(
              f"Resource '{resource.name}' not found at center"
              f" location {(center.x,center.y,center.z)}"
              " & error not resolved -> aborted resource movement."
            )
        else:
          # Resource was not found
          return False

    # Resource was found
    if audio_feedback:
      audio.play_got_item()
    return True

  def _position_96_head_in_resource(self, resource: Resource) -> Coordinate:
    """The firmware command expects location of tip A1 of the head. We center the head in the given
    resource."""
    head_size_x = 9 * 11  # 12 channels, 9mm spacing in between
    head_size_y = 9 * 7  #   8 channels, 9mm spacing in between
    channel_size = 9
    loc = resource.get_absolute_location()
    loc.x += (resource.get_size_x() - head_size_x) / 2 + channel_size / 2
    loc.y += (resource.get_size_y() - head_size_y) / 2 + channel_size / 2
    return loc

  # ============== Firmware Commands ==============

  # -------------- 3.2 System general commands --------------

  async def pre_initialize_instrument(self):
    """Pre-initialize instrument"""
    return await self.send_command(module="C0", command="VI")

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
      maximum_tip_volume: Maximum volume of tip [0.1ul]
                          Note! it's automatically limited to max. channel capacity
      tip_type: Type of tip collar (Tip type identification)
      pickup_method: pick up method.
                      Attention! The values set here are temporary and apply only until
                      power OFF or RESET. After power ON the default values apply. (see Table 3)
    """

    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 0 <= tip_type_table_index <= 99, "tip_type_table_index must be between 0 and 99"
    assert 1 <= tip_length <= 1999, "tip_length must be between 1 and 1999"
    assert 1 <= maximum_tip_volume <= 56000, "maximum_tip_volume must be between 1 and 56000"

    return await self.send_command(
      module="C0",
      command="TT",
      tt=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  # -------------- 3.2.1 System query --------------

  async def request_error_code(self):
    """Request error code

    Here the last saved error messages can be retrieved. The error buffer is automatically voided
    when a new command is started. All configured nodes are displayed.

    Returns:
      TODO:
      X0##/##: X0 slave
      ..##/## see node definitions ( chapter 5)
    """

    return await self.send_command(module="C0", command="RE")

  async def request_firmware_version(self):
    """Request firmware version

    Returns: TODO: Rfid0001rf1.0S 2009-06-24 A
    """

    return await self.send_command(module="C0", command="RF")

  async def request_parameter_value(self):
    """Request parameter value

    Returns: TODO: Raid1111er00/00yg1200
    """

    return await self.send_command(module="C0", command="RA")

  class BoardType(enum.Enum):
    C167CR_SINGLE_PROCESSOR_BOARD = 0
    C167CR_DUAL_PROCESSOR_BOARD = 1
    LPC2468_XE167_DUAL_PROCESSOR_BOARD = 2
    LPC2468_SINGLE_PROCESSOR_BOARD = 5
    UNKNOWN = -1

  async def request_electronic_board_type(self):
    """Request electronic board type

    Returns:
      The board type.
    """

    resp = await self.send_command(module="C0", command="QB")
    try:
      return STAR.BoardType(resp["qb"])
    except ValueError:
      return STAR.BoardType.UNKNOWN

  # TODO: parse response.
  async def request_supply_voltage(self):
    """Request supply voltage

    Request supply voltage (for LDPB only)
    """

    return await self.send_command(module="C0", command="MU")

  async def request_instrument_initialization_status(self) -> bool:
    """Request instrument initialization status"""

    resp = await self.send_command(module="C0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  async def request_autoload_initialization_status(self) -> bool:
    """Request autoload initialization status"""

    resp = await self.send_command(module="I0", command="QW", fmt="qw#")
    return resp is not None and resp["qw"] == 1

  async def request_name_of_last_faulty_parameter(self):
    """Request name of last faulty parameter

    Returns: TODO:
      Name of last parameter with syntax error
      (optional) received value separated with blank
      (optional) minimal permitted value separated with blank (optional)
      maximal permitted value separated with blank example with min max data:
      Vpid2233er00/00vpth 00000 03500 example without min max data: Vpid2233er00/00vpcd
    """

    return await self.send_command(module="C0", command="VP", fmt="vp&&")

  async def request_master_status(self):
    """Request master status

    Returns: TODO: see page 19 (SFCO.0036)
    """

    return await self.send_command(module="C0", command="RQ")

  async def request_number_of_presence_sensors_installed(self):
    """Request number of presence sensors installed

    Returns:
      number of sensors installed (1...103)
    """

    resp = await self.send_command(module="C0", command="SR")
    return resp["sr"]

  async def request_eeprom_data_correctness(self):
    """Request EEPROM data correctness

    Returns: TODO: (SFCO.0149)
    """

    return await self.send_command(module="C0", command="QV")

  # -------------- 3.3 Settings --------------

  # -------------- 3.3.1 Volatile Settings --------------

  async def set_single_step_mode(self, single_step_mode: bool = False):
    """Set Single step mode

    Args:
      single_step_mode: Single Step Mode. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AM",
      am=single_step_mode,
    )

  async def trigger_next_step(self):
    """Trigger next step (Single step mode)"""

    # TODO: this command has no reply!!!!
    return await self.send_command(module="C0", command="NS")

  async def halt(self):
    """Halt

    Intermediate sequences not yet carried out and the commands in
    the command stack are discarded. Sequence already in process is
    completed.
    """

    return await self.send_command(module="C0", command="HD")

  async def save_all_cycle_counters(self):
    """Save all cycle counters

    Save all cycle counters of the instrument
    """

    return await self.send_command(module="C0", command="AZ")

  async def set_not_stop(self, non_stop):
    """Set not stop mode

    Args:
      non_stop: True if non stop mode should be turned on after command is sent.
    """

    if non_stop:
      # TODO: this command has no reply!!!!
      return await self.send_command(module="C0", command="AB")
    else:
      return await self.send_command(module="C0", command="AW")

  # -------------- 3.3.2 Non volatile settings (stored in EEPROM) --------------

  async def store_installation_data(
    self,
    date: datetime.datetime = datetime.datetime.now(),
    serial_number: str = "0000",
  ):
    """Store installation data

    Args:
      date: installation date.
    """

    assert len(serial_number) == 4, "serial number must be 4 chars long"

    return await self.send_command(module="C0", command="SI", si=date, sn=serial_number)

  async def store_verification_data(
    self,
    verification_subject: int = 0,
    date: datetime.datetime = datetime.datetime.now(),
    verification_status: bool = False,
  ):
    """Store verification data

    Args:
      verification_subject: verification subject. Default 0. Must be between 0 and 24.
      date: verification date.
      verification_status: verification status.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    return await self.send_command(
      module="C0",
      command="AV",
      vo=verification_subject,
      vd=date,
      vs=verification_status,
    )

  async def additional_time_stamp(self):
    """Additional time stamp"""

    return await self.send_command(module="C0", command="AT")

  async def set_x_offset_x_axis_iswap(self, x_offset: int):
    """Set X-offset X-axis <-> iSWAP

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AG", x_offset=x_offset)

  async def set_x_offset_x_axis_core_96_head(self, x_offset: int):
    """Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def set_x_offset_x_axis_core_nano_pipettor_head(self, x_offset: int):
    """Set X-offset X-axis <-> CoRe 96 head

    Args:
      x_offset: X-offset [0.1mm]
    """

    return await self.send_command(module="C0", command="AF", x_offset=x_offset)

  async def save_download_date(self, date: datetime.datetime = datetime.datetime.now()):
    """Save Download date

    Args:
      date: download date. Default now.
    """

    return await self.send_command(
      module="C0",
      command="AO",
      ao=date,
    )

  async def save_technical_status_of_assemblies(self, processor_board: str, power_supply: str):
    """Save technical status of assemblies

    Args:
      processor_board: Processor board. Art.Nr./Rev./Ser.No. (000000/00/0000)
      power_supply: Power supply. Art.Nr./Rev./Ser.No. (000000/00/0000)
    """

    return await self.send_command(
      module="C0",
      command="BT",
      qt=processor_board + " " + power_supply,
    )

  async def set_instrument_configuration(
    self,
    configuration_data_1: Optional[str] = None,  # TODO: configuration byte
    configuration_data_2: Optional[str] = None,  # TODO: configuration byte
    configuration_data_3: Optional[str] = None,  # TODO: configuration byte
    instrument_size_in_slots_x_range: int = 54,
    auto_load_size_in_slots: int = 54,
    tip_waste_x_position: int = 13400,
    right_x_drive_configuration_byte_1: int = 0,
    right_x_drive_configuration_byte_2: int = 0,
    minimal_iswap_collision_free_position: int = 3500,
    maximal_iswap_collision_free_position: int = 11400,
    left_x_arm_width: int = 3700,
    right_x_arm_width: int = 3700,
    num_pip_channels: int = 0,
    num_xl_channels: int = 0,
    num_robotic_channels: int = 0,
    minimal_raster_pitch_of_pip_channels: int = 90,
    minimal_raster_pitch_of_xl_channels: int = 360,
    minimal_raster_pitch_of_robotic_channels: int = 360,
    pip_maximal_y_position: int = 6065,
    left_arm_minimal_y_position: int = 60,
    right_arm_minimal_y_position: int = 60,
  ):
    """Set instrument configuration

    Args:
      configuration_data_1: configuration data 1.
      configuration_data_2: configuration data 2.
      configuration_data_3: configuration data 3.
      instrument_size_in_slots_x_range: instrument size in slots (X range).
                                          Must be between 10 and 99. Default 54.
      auto_load_size_in_slots: auto load size in slots. Must be between 10
                                and 54. Default 54.
      tip_waste_x_position: tip waste X-position. Must be between 1000 and
                            25000. Default 13400.
      right_x_drive_configuration_byte_1: right X drive configuration byte 1 (see
        xl parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      right_x_drive_configuration_byte_2: right X drive configuration byte 2 (see
        xn parameter bits). Must be between 0 and 1.  Default 0. # TODO: this.
      minimal_iswap_collision_free_position: minimal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 3500.
      maximal_iswap_collision_free_position: maximal iSWAP collision free position for
        direct X access. For explanation of calculation see Fig. 4. Must be between 0 and 30000.
        Default 11400
      left_x_arm_width: width of left X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      right_x_arm_width: width of right X arm [0.1 mm]. Must be between 0 and 9999. Default 3700.
      num_pip_channels: number of PIP channels. Must be between 0 and 16. Default 0.
      num_xl_channels: number of XL channels. Must be between 0 and 8. Default 0.
      num_robotic_channels: number of Robotic channels. Must be between 0 and 8. Default 0.
      minimal_raster_pitch_of_pip_channels: minimal raster pitch of PIP channels [0.1 mm]. Must
                                            be between 0 and 999. Default 90.
      minimal_raster_pitch_of_xl_channels: minimal raster pitch of XL channels [0.1 mm]. Must be
                                            between 0 and 999. Default 360.
      minimal_raster_pitch_of_robotic_channels: minimal raster pitch of Robotic channels [0.1 mm].
                                                Must be between 0 and 999. Default 360.
      pip_maximal_y_position: PIP maximal Y position [0.1 mm]. Must be between 0 and 9999.
                              Default 6065.
      left_arm_minimal_y_position: left arm minimal Y position [0.1 mm]. Must be between 0 and 9999.
                                    Default 60.
      right_arm_minimal_y_position: right arm minimal Y position [0.1 mm]. Must be between 0
                                    and 9999. Default 60.
    """

    assert (
      1 <= instrument_size_in_slots_x_range <= 9
    ), "instrument_size_in_slots_x_range must be between 1 and 99"
    assert 1 <= auto_load_size_in_slots <= 54, "auto_load_size_in_slots must be between 1 and 54"
    assert 1000 <= tip_waste_x_position <= 25000, "tip_waste_x_position must be between 1 and 25000"
    assert (
      0 <= right_x_drive_configuration_byte_1 <= 1
    ), "right_x_drive_configuration_byte_1 must be between 0 and 1"
    assert (
      0 <= right_x_drive_configuration_byte_2 <= 1
    ), "right_x_drive_configuration_byte_2 must be between 0 and  must1"
    assert (
      0 <= minimal_iswap_collision_free_position <= 30000
    ), "minimal_iswap_collision_free_position must be between 0 and 30000"
    assert (
      0 <= maximal_iswap_collision_free_position <= 30000
    ), "maximal_iswap_collision_free_position must be between 0 and 30000"
    assert 0 <= left_x_arm_width <= 9999, "left_x_arm_width must be between 0 and 9999"
    assert 0 <= right_x_arm_width <= 9999, "right_x_arm_width must be between 0 and 9999"
    assert 0 <= num_pip_channels <= 16, "num_pip_channels must be between 0 and 16"
    assert 0 <= num_xl_channels <= 8, "num_xl_channels must be between 0 and 8"
    assert 0 <= num_robotic_channels <= 8, "num_robotic_channels must be between 0 and 8"
    assert (
      0 <= minimal_raster_pitch_of_pip_channels <= 999
    ), "minimal_raster_pitch_of_pip_channels must be between 0 and 999"
    assert (
      0 <= minimal_raster_pitch_of_xl_channels <= 999
    ), "minimal_raster_pitch_of_xl_channels must be between 0 and 999"
    assert (
      0 <= minimal_raster_pitch_of_robotic_channels <= 999
    ), "minimal_raster_pitch_of_robotic_channels must be between 0 and 999"
    assert 0 <= pip_maximal_y_position <= 9999, "pip_maximal_y_position must be between 0 and 9999"
    assert (
      0 <= left_arm_minimal_y_position <= 9999
    ), "left_arm_minimal_y_position must be between 0 and 9999"
    assert (
      0 <= right_arm_minimal_y_position <= 9999
    ), "right_arm_minimal_y_position must be between 0 and 9999"

    return await self.send_command(
      module="C0",
      command="AK",
      kb=configuration_data_1,
      ka=configuration_data_2,
      ke=configuration_data_3,
      xt=instrument_size_in_slots_x_range,
      xa=auto_load_size_in_slots,
      xw=tip_waste_x_position,
      xr=right_x_drive_configuration_byte_1,
      xo=right_x_drive_configuration_byte_2,
      xm=minimal_iswap_collision_free_position,
      xx=maximal_iswap_collision_free_position,
      xu=left_x_arm_width,
      xv=right_x_arm_width,
      kp=num_pip_channels,
      kc=num_xl_channels,
      kr=num_robotic_channels,
      ys=minimal_raster_pitch_of_pip_channels,
      kl=minimal_raster_pitch_of_xl_channels,
      km=minimal_raster_pitch_of_robotic_channels,
      ym=pip_maximal_y_position,
      yu=left_arm_minimal_y_position,
      yx=right_arm_minimal_y_position,
    )

  async def save_pip_channel_validation_status(self, validation_status: bool = False):
    """Save PIP channel validation status

    Args:
      validation_status: PIP channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AJ",
      tq=validation_status,
    )

  async def save_xl_channel_validation_status(self, validation_status: bool = False):
    """Save XL channel validation status

    Args:
      validation_status: XL channel validation status. Default False.
    """

    return await self.send_command(
      module="C0",
      command="AE",
      tx=validation_status,
    )

  # TODO: response
  async def configure_node_names(self):
    """Configure node names"""

    return await self.send_command(module="C0", command="AJ")

  async def set_deck_data(self, data_index: int = 0, data_stream: str = "0"):
    """set deck data

    Args:
      data_index: data index. Must be between 0 and 9. Default 0.
      data_stream: data stream (12 characters). Default <class 'str'>.
    """

    assert 0 <= data_index <= 9, "data_index must be between 0 and 9"
    assert len(data_stream) == 12, "data_stream must be 12 chars"

    return await self.send_command(
      module="C0",
      command="DD",
      vi=data_index,
      vj=data_stream,
    )

  # -------------- 3.3.3 Settings query (stored in EEPROM) --------------

  async def request_technical_status_of_assemblies(self):
    """Request Technical status of assemblies"""

    # TODO: parse res
    return await self.send_command(module="C0", command="QT")

  async def request_installation_data(self):
    """Request installation data"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RI")

  async def request_download_date(self):
    """Request download date"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RO")

  async def request_verification_data(self, verification_subject: int = 0):
    """Request download date

    Args:
      verification_subject: verification subject. Must be between 0 and 24. Default 0.
    """

    assert 0 <= verification_subject <= 24, "verification_subject must be between 0 and 24"

    # TODO: parse results.
    return await self.send_command(module="C0", command="RO", vo=verification_subject)

  async def request_additional_timestamp_data(self):
    """Request additional timestamp data"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RS")

  async def request_pip_channel_validation_status(self):
    """Request PIP channel validation status"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RJ")

  async def request_xl_channel_validation_status(self):
    """Request XL channel validation status"""

    # TODO: parse res
    return await self.send_command(module="C0", command="UJ")

  async def request_machine_configuration(self):
    """Request machine configuration"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RM", fmt="kb**kp**")

  async def request_extended_configuration(self):
    """Request extended configuration"""

    return await self.send_command(
      module="C0",
      command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#ys###"
      + "kl###km###ym####yu####yx####",
    )

  async def request_node_names(self):
    """Request node names"""

    # TODO: parse res
    return await self.send_command(module="C0", command="RK")

  async def request_deck_data(self):
    """Request deck data"""

    # TODO: parse res
    return await self.send_command(module="C0", command="VD")

  # -------------- 3.4 X-Axis control --------------

  # -------------- 3.4.1 Movements --------------

  async def position_left_x_arm_(self, x_position: int = 0):
    """Position left X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position_ must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="JX",
      xs=f"{x_position:05}",
    )

  async def position_right_x_arm_(self, x_position: int = 0):
    """Position right X-Arm

    Collision risk!

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position_ must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="JS",
      xs=f"{x_position:05}",
    )

  async def move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self, x_position: int = 0
  ):
    """Move left X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="KX",
      xs=x_position,
    )

  async def move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position(
    self, x_position: int = 0
  ):
    """Move right X-arm to position with all attached components in Z-safety position

    Args:
      x_position: X-Position [0.1mm]. Must be between 0 and 30000. Default 0.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="KR",
      xs=x_position,
    )

  # -------------- 3.4.2 X-Area reservation for external access --------------

  async def occupy_and_provide_area_for_external_access(
    self,
    taken_area_identification_number: int = 0,
    taken_area_left_margin: int = 0,
    taken_area_left_margin_direction: int = 0,
    taken_area_size: int = 0,
    arm_preposition_mode_related_to_taken_areas: int = 0,
  ):
    """Occupy and provide area for external access

    Args:
      taken_area_identification_number: taken area identification number. Must be between 0 and
        9999. Default 0.
      taken_area_left_margin: taken area left margin. Must be between 0 and 99. Default 0.
      taken_area_left_margin_direction: taken area left margin direction. 1 = negative. Must be
        between 0 and 1. Default 0.
      taken_area_size: taken area size. Must be between 0 and 50000. Default 0.
      arm_preposition_mode_related_to_taken_areas: 0) left arm to left & right arm to right.
        1) all arms left.  2) all arms right.
    """

    assert (
      0 <= taken_area_identification_number <= 9999
    ), "taken_area_identification_number must be between 0 and 9999"
    assert 0 <= taken_area_left_margin <= 99, "taken_area_left_margin must be between 0 and 99"
    assert (
      0 <= taken_area_left_margin_direction <= 1
    ), "taken_area_left_margin_direction must be between 0 and 1"
    assert 0 <= taken_area_size <= 50000, "taken_area_size must be between 0 and 50000"
    assert (
      0 <= arm_preposition_mode_related_to_taken_areas <= 2
    ), "arm_preposition_mode_related_to_taken_areas must be between 0 and 2"

    return await self.send_command(
      module="C0",
      command="BA",
      aq=taken_area_identification_number,
      al=taken_area_left_margin,
      ad=taken_area_left_margin_direction,
      ar=taken_area_size,
      ap=arm_preposition_mode_related_to_taken_areas,
    )

  async def release_occupied_area(self, taken_area_identification_number: int = 0):
    """Release occupied area

    Args:
      taken_area_identification_number: taken area identification number.
                                        Must be between 0 and 9999. Default 0.
    """

    assert (
      0 <= taken_area_identification_number <= 999
    ), "taken_area_identification_number must be between 0 and 9999"

    return await self.send_command(
      module="C0",
      command="BB",
      aq=taken_area_identification_number,
    )

  async def release_all_occupied_areas(self):
    """Release all occupied areas"""

    return await self.send_command(module="C0", command="BC")

  # -------------- 3.4.3 X-query --------------

  async def request_left_x_arm_position(self):
    """Request left X-Arm position"""

    return await self.send_command(module="C0", command="RX", fmt="rx#####")

  async def request_right_x_arm_position(self):
    """Request right X-Arm position"""

    return await self.send_command(module="C0", command="QX", fmt="rx#####")

  async def request_maximal_ranges_of_x_drives(self):
    """Request maximal ranges of X drives"""

    return await self.send_command(module="C0", command="RU")

  async def request_present_wrap_size_of_installed_arms(self):
    """Request present wrap size of installed arms"""

    return await self.send_command(module="C0", command="UA")

  async def request_left_x_arm_last_collision_type(self):
    """Request left X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = await self.send_command(module="C0", command="XX", fmt="xq#")
    return resp["xq"] == 1

  async def request_right_x_arm_last_collision_type(self) -> bool:
    """Request right X-Arm last collision type (after error 27)

    Returns:
      False if present positions collide (not reachable),
      True if position is never reachable.
    """

    resp = await self.send_command(module="C0", command="XR", fmt="xq#")
    return cast(int, resp["xq"]) == 1

  # -------------- 3.5 Pipetting channel commands --------------

  # -------------- 3.5.1 Initialization --------------

  async def initialize_pipetting_channels(
    self,
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    begin_of_tip_deposit_process: int = 0,
    end_of_tip_deposit_process: int = 0,
    z_position_at_end_of_a_command: int = 3600,
    tip_pattern: List[bool] = [True],
    tip_type: int = 16,
    discarding_method: int = 1,
  ):
    """Initialize pipetting channels

    Initialize pipetting channels (discard tips)

    Args:
      x_positions: X-Position [0.1mm] (discard position). Must be between 0 and 25000. Default 0.
      y_positions: y-Position [0.1mm] (discard position). Must be between 0 and 6500. Default 0.
      begin_of_tip_deposit_process: Begin of tip deposit process (Z-discard range) [0.1mm]. Must be
        between 0 and 3600. Default 0.
      end_of_tip_deposit_process: End of tip deposit process (Z-discard range) [0.1mm]. Must be
        between 0 and 3600. Default 0.
      z-position_at_end_of_a_command: Z-Position at end of a command [0.1mm]. Must be between 0 and
        3600. Default 3600.
      tip_pattern: Tip pattern ( channels involved). Default True.
      tip_type: Tip type (recommended is index of longest tip see command 'TT') [0.1mm]. Must be
        between 0 and 99. Default 16.
      discarding_method: discarding method. 0 = place & shift (tp/ tz = tip cone end height), 1 =
        drop (no shift) (tp/ tz = stop disk height). Must be between 0 and 1. Default 1.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert (
      0 <= begin_of_tip_deposit_process <= 3600
    ), "begin_of_tip_deposit_process must be between 0 and 3600"
    assert (
      0 <= end_of_tip_deposit_process <= 3600
    ), "end_of_tip_deposit_process must be between 0 and 3600"
    assert (
      0 <= z_position_at_end_of_a_command <= 3600
    ), "z_position_at_end_of_a_command must be between 0 and 3600"
    assert 0 <= tip_type <= 99, "tip must be between 0 and 99"
    assert 0 <= discarding_method <= 1, "discarding_method must be between 0 and 1"

    return await self.send_command(
      module="C0",
      command="DI",
      read_timeout=120,
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      tp=f"{begin_of_tip_deposit_process:04}",
      tz=f"{end_of_tip_deposit_process:04}",
      te=f"{z_position_at_end_of_a_command:04}",
      tm=[f"{tm:01}" for tm in tip_pattern],
      tt=f"{tip_type:02}",
      ti=discarding_method,
    )

  # -------------- 3.5.2 Tip handling commands using PIP --------------

  @need_iswap_parked
  async def pick_up_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    tip_type_idx: int,
    begin_tip_pick_up_process: int = 0,
    end_tip_pick_up_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    pickup_method: TipPickupMethod = TipPickupMethod.OUT_OF_RACK,
  ):
    """Tip Pick-up

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved).
      tip_type_idx: Tip type.
      begin_tip_pick_up_process: Begin of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      end_tip_pick_up_process: End of tip picking up process (Z- range) [0.1mm]. Must be
          between 0 and 3600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
          of a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      pickup_method: Pick up method.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert (
      0 <= begin_tip_pick_up_process <= 3600
    ), "begin_tip_pick_up_process must be between 0 and 3600"
    assert (
      0 <= end_tip_pick_up_process <= 3600
    ), "end_tip_pick_up_process must be between 0 and 3600"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="TP",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tt=f"{tip_type_idx:02}",
      tp=f"{begin_tip_pick_up_process:04}",
      tz=f"{end_tip_pick_up_process:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      td=pickup_method.value,
    )

  @need_iswap_parked
  async def discard_tip(
    self,
    x_positions: List[int],
    y_positions: List[int],
    tip_pattern: List[bool],
    begin_tip_deposit_process: int = 0,
    end_tip_deposit_process: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_end_of_a_command: int = 3600,
    discarding_method: TipDropMethod = TipDropMethod.DROP,
  ):
    """discard tip

    Args:
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      tip_pattern: Tip pattern (channels involved). Must be between 0 and 1. Default 1.
      begin_tip_deposit_process: Begin of tip deposit process (Z- range) [0.1mm]. Must be between
          0 and 3600. Default 0.
      end_tip_deposit_process: End of tip deposit process (Z- range) [0.1mm]. Must be between 0
          and 3600.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
          command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must
          be between 0 and 3600.
      z-position_at_end_of_a_command: Z-Position at end of a command [0.1mm].
          Must be between 0 and 3600.
      discarding_method: Pick up method Pick up method. 0 = auto selection (see command TT
          parameter tu) 1 = pick up out of rack. 2 = pick up out of wash liquid (slowly). Must be
          between 0 and 2.

    If discarding is PLACE_SHIFT (0), tp/ tz = tip cone end height.
    Otherwise, tp/ tz = stop disk height.
    """

    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert (
      0 <= begin_tip_deposit_process <= 3600
    ), "begin_tip_deposit_process must be between 0 and 3600"
    assert (
      0 <= end_tip_deposit_process <= 3600
    ), "end_tip_deposit_process must be between 0 and 3600"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert (
      0 <= z_position_at_end_of_a_command <= 3600
    ), "z_position_at_end_of_a_command must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="TR",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      xp=[f"{x:05}" for x in x_positions],
      yp=[f"{y:04}" for y in y_positions],
      tm=tip_pattern,
      tp=begin_tip_deposit_process,
      tz=end_tip_deposit_process,
      th=minimum_traverse_height_at_beginning_of_a_command,
      te=z_position_at_end_of_a_command,
      ti=discarding_method.value,
    )

  # TODO:(command:TW) Tip Pick-up for DC wash procedure

  # -------------- 3.5.3 Liquid handling commands using PIP --------------

  # TODO:(command:DC) Set multiple dispense values using PIP

  @need_iswap_parked
  async def aspirate_pip(
    self,
    aspiration_type: List[int] = [0],
    tip_pattern: List[bool] = [True],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,
    lld_search_height: List[int] = [0],
    clot_detection_height: List[int] = [60],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_height: List[int] = [3600],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    aspiration_volumes: List[int] = [0],
    aspiration_speed: List[int] = [500],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    pre_wetting_volume: List[int] = [0],
    lld_mode: List[int] = [1],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    aspirate_position_above_z_touch_off: List[int] = [5],
    detection_height_difference_for_dual_lld: List[int] = [0],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    mix_volume: List[int] = [0],
    mix_cycles: List[int] = [0],
    mix_position_from_liquid_surface: List[int] = [250],
    mix_speed: List[int] = [500],
    mix_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
    # For second section aspiration only
    use_2nd_section_aspiration: List[bool] = [False],
    retract_height_over_2nd_section_to_empty_tip: List[int] = [60],
    dispensation_speed_during_emptying_tip: List[int] = [468],
    dosing_drive_speed_during_2nd_section_search: List[int] = [468],
    z_drive_speed_during_2nd_section_search: List[int] = [215],
    cup_upper_edge: List[int] = [3600],
    ratio_liquid_rise_to_tip_deep_in: List[int] = [16246],
    immersion_depth_2nd_section: List[int] = [30],
  ):
    """aspirate pip

    Aspiration of liquid using PIP.

    It's not really clear what second section aspiration is, but it does not seem to be used
    very often. Probably safe to ignore it.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      aspiration_type: Type of aspiration (0 = simple;1 = sequence; 2 = cup emptied).
                        Must be between 0 and 2. Default 0.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
          independent of tip pattern parameter 'tm'). Must be between 0 and 3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      clot_detection_height: Check height of clot detection above current surface (as computed)
          of the liquid [0.1mm]. Must be between 0 and 500. Default 60.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0
          and 3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function
          without LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be
          between 0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between
          0 and 10000. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
          3600. Default 3600.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out
          of liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must
          be between 0 and 3600. Default 0.
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 12500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 999. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
            between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and
            4. Default 1.
      aspirate_position_above_z_touch_off: aspirate position above Z touch off [0.1mm]. Must
            be between 0 and 100. Default 5.
      detection_height_difference_for_dual_lld: Difference in detection height for dual
            LLD [0.1 mm]. Must be between 0 and 99. Default 0.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
            Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: mix volume [0.1ul]. Must be between 0 and 12500. Default 0
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 900. Default 250.
      mix_speed: Speed of mix [0.1ul/s]. Must be between 4 and 5000.
          Default 500.
      mix_surface_following_distance: Surface following distance during
          mix [0.1mm]. Must be between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
      use_2nd_section_aspiration: 2nd section aspiration. Default False.
      retract_height_over_2nd_section_to_empty_tip: Retract height over 2nd section to empty
          tip [0.1mm]. Must be between 0 and 3600. Default 60.
      dispensation_speed_during_emptying_tip: Dispensation speed during emptying tip [0.1ul/s]
            Must be between 4 and 5000. Default 468.
      dosing_drive_speed_during_2nd_section_search: Dosing drive speed during 2nd section
          search [0.1ul/s]. Must be between 4 and 5000. Default 468.
      z_drive_speed_during_2nd_section_search: Z drive speed during 2nd section search [0.1mm/s].
          Must be between 3 and 1600. Default 215.
      cup_upper_edge: Cup upper edge [0.1mm]. Must be between 0 and 3600. Default 3600.
      ratio_liquid_rise_to_tip_deep_in: Ratio liquid rise to tip deep in [1/100000]. Must be
          between 0 and 50000. Default 16246.
      immersion_depth_2nd_section: Immersion depth 2nd section [0.1mm]. Must be between 0 and
          3600. Default 30.
    """

    assert all(0 <= x <= 2 for x in aspiration_type), "aspiration_type must be between 0 and 2"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert all(
      0 <= x <= 3600 for x in lld_search_height
    ), "lld_search_height must be between 0 and 3600"
    assert all(
      0 <= x <= 500 for x in clot_detection_height
    ), "clot_detection_height must be between 0 and 500"
    assert all(
      0 <= x <= 3600 for x in liquid_surface_no_lld
    ), "liquid_surface_no_lld must be between 0 and 3600"
    assert all(
      0 <= x <= 3600 for x in pull_out_distance_transport_air
    ), "pull_out_distance_transport_air must be between 0 and 3600"
    assert all(
      0 <= x <= 3600 for x in second_section_height
    ), "second_section_height must be between 0 and 3600"
    assert all(
      0 <= x <= 10000 for x in second_section_ratio
    ), "second_section_ratio must be between 0 and 10000"
    assert all(0 <= x <= 3600 for x in minimum_height), "minimum_height must be between 0 and 3600"
    assert all(
      0 <= x <= 3600 for x in immersion_depth
    ), "immersion_depth must be between 0 and 3600"
    assert all(
      0 <= x <= 1 for x in immersion_depth_direction
    ), "immersion_depth_direction must be between 0 and 1"
    assert all(
      0 <= x <= 3600 for x in surface_following_distance
    ), "surface_following_distance must be between 0 and 3600"
    assert all(
      0 <= x <= 12500 for x in aspiration_volumes
    ), "aspiration_volumes must be between 0 and 12500"
    assert all(
      4 <= x <= 5000 for x in aspiration_speed
    ), "aspiration_speed must be between 4 and 5000"
    assert all(
      0 <= x <= 500 for x in transport_air_volume
    ), "transport_air_volume must be between 0 and 500"
    assert all(
      0 <= x <= 9999 for x in blow_out_air_volume
    ), "blow_out_air_volume must be between 0 and 9999"
    assert all(
      0 <= x <= 999 for x in pre_wetting_volume
    ), "pre_wetting_volume must be between 0 and 999"
    assert all(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert all(
      1 <= x <= 4 for x in gamma_lld_sensitivity
    ), "gamma_lld_sensitivity must be between 1 and 4"
    assert all(
      1 <= x <= 4 for x in dp_lld_sensitivity
    ), "dp_lld_sensitivity must be between 1 and 4"
    assert all(
      0 <= x <= 100 for x in aspirate_position_above_z_touch_off
    ), "aspirate_position_above_z_touch_off must be between 0 and 100"
    assert all(
      0 <= x <= 99 for x in detection_height_difference_for_dual_lld
    ), "detection_height_difference_for_dual_lld must be between 0 and 99"
    assert all(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert all(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert all(0 <= x <= 12500 for x in mix_volume), "mix_volume must be between 0 and 12500"
    assert all(0 <= x <= 99 for x in mix_cycles), "mix_cycles must be between 0 and 99"
    assert all(
      0 <= x <= 900 for x in mix_position_from_liquid_surface
    ), "mix_position_from_liquid_surface must be between 0 and 900"
    assert all(4 <= x <= 5000 for x in mix_speed), "mix_speed must be between 4 and 5000"
    assert all(
      0 <= x <= 3600 for x in mix_surface_following_distance
    ), "mix_surface_following_distance must be between 0 and 3600"
    assert all(
      0 <= x <= 999 for x in limit_curve_index
    ), "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"
    assert all(
      0 <= x <= 3600 for x in retract_height_over_2nd_section_to_empty_tip
    ), "retract_height_over_2nd_section_to_empty_tip must be between 0 and 3600"
    assert all(
      4 <= x <= 5000 for x in dispensation_speed_during_emptying_tip
    ), "dispensation_speed_during_emptying_tip must be between 4 and 5000"
    assert all(
      4 <= x <= 5000 for x in dosing_drive_speed_during_2nd_section_search
    ), "dosing_drive_speed_during_2nd_section_search must be between 4 and 5000"
    assert all(
      3 <= x <= 1600 for x in z_drive_speed_during_2nd_section_search
    ), "z_drive_speed_during_2nd_section_search must be between 3 and 1600"
    assert all(0 <= x <= 3600 for x in cup_upper_edge), "cup_upper_edge must be between 0 and 3600"
    assert all(
      0 <= x <= 5000 for x in ratio_liquid_rise_to_tip_deep_in
    ), "ratio_liquid_rise_to_tip_deep_in must be between 0 and 50000"
    assert all(
      0 <= x <= 3600 for x in immersion_depth_2nd_section
    ), "immersion_depth_2nd_section must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="AS",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      at=[f"{at:01}" for at in aspiration_type],
      tm=tip_pattern,
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      lp=[f"{lp:04}" for lp in lld_search_height],
      ch=[f"{ch:03}" for ch in clot_detection_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      zx=[f"{zx:04}" for zx in minimum_height],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it}" for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      av=[f"{av:05}" for av in aspiration_volumes],
      as_=[f"{as_:04}" for as_ in aspiration_speed],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      oa=[f"{oa:03}" for oa in pre_wetting_volume],
      lm=[f"{lm}" for lm in lld_mode],
      ll=[f"{ll}" for ll in gamma_lld_sensitivity],
      lv=[f"{lv}" for lv in dp_lld_sensitivity],
      zo=[f"{zo:03}" for zo in aspirate_position_above_z_touch_off],
      ld=[f"{ld:02}" for ld in detection_height_difference_for_dual_lld],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in mix_volume],
      mc=[f"{mc:02}" for mc in mix_cycles],
      mp=[f"{mp:03}" for mp in mix_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in mix_speed],
      mh=[f"{mh:04}" for mh in mix_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm,
      gk=recording_mode,
      lk=[1 if lk else 0 for lk in use_2nd_section_aspiration],
      ik=[f"{ik:04}" for ik in retract_height_over_2nd_section_to_empty_tip],
      sd=[f"{sd:04}" for sd in dispensation_speed_during_emptying_tip],
      se=[f"{se:04}" for se in dosing_drive_speed_during_2nd_section_search],
      sz=[f"{sz:04}" for sz in z_drive_speed_during_2nd_section_search],
      io=[f"{io:04}" for io in cup_upper_edge],
      il=[f"{il:05}" for il in ratio_liquid_rise_to_tip_deep_in],
      in_=[f"{in_:04}" for in_ in immersion_depth_2nd_section],
    )

  @need_iswap_parked
  async def dispense_pip(
    self,
    tip_pattern: List[bool],
    dispensing_mode: List[int] = [0],
    x_positions: List[int] = [0],
    y_positions: List[int] = [0],
    minimum_height: List[int] = [3600],
    lld_search_height: List[int] = [0],
    liquid_surface_no_lld: List[int] = [3600],
    pull_out_distance_transport_air: List[int] = [50],
    immersion_depth: List[int] = [0],
    immersion_depth_direction: List[int] = [0],
    surface_following_distance: List[int] = [0],
    second_section_height: List[int] = [0],
    second_section_ratio: List[int] = [0],
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    min_z_endpos: int = 3600,  #
    dispense_volumes: List[int] = [0],
    dispense_speed: List[int] = [500],
    cut_off_speed: List[int] = [250],
    stop_back_volume: List[int] = [0],
    transport_air_volume: List[int] = [0],
    blow_out_air_volume: List[int] = [200],
    lld_mode: List[int] = [1],
    side_touch_off_distance: int = 1,
    dispense_position_above_z_touch_off: List[int] = [5],
    gamma_lld_sensitivity: List[int] = [1],
    dp_lld_sensitivity: List[int] = [1],
    swap_speed: List[int] = [100],
    settling_time: List[int] = [5],
    mix_volume: List[int] = [0],
    mix_cycles: List[int] = [0],
    mix_position_from_liquid_surface: List[int] = [250],
    mix_speed: List[int] = [500],
    mix_surface_following_distance: List[int] = [0],
    limit_curve_index: List[int] = [0],
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
  ):
    """dispense pip

    Dispensing of liquid using PIP.

    LLD restrictions!
      - "dP and Dual LLD" are used in aspiration only. During dispensation all pressure-based
        LLD is set to OFF.
      - "side touch off" turns LLD & "Z touch off" to OFF , is not available for simultaneous
        Asp/Disp. command

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode
        1 = Blow out in jet mode 2 = Partial volume at surface
        3 = Blow out at surface 4 = Empty tip at fix position.
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_height: Minimum height (maximum immersion depth) [0.1 mm]. Must be between 0 and
        3600. Default 3600.
      lld_search_height: LLD search height [0.1 mm]. Must be between 0 and 3600. Default 0.
      liquid_surface_no_lld: Liquid surface at function without LLD [0.1mm]. Must be between 0 and
        3600. Default 3600.
      pull_out_distance_transport_air: pull out distance to take transport air in function without
        LLD [0.1mm]. Must be between 0 and 3600. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
        liquid). Must be between 0 and 1. Default 0.
      surface_following_distance: Surface following distance during aspiration [0.1mm]. Must be
        between 0 and 3600. Default 0.
      second_section_height: Tube 2nd section height measured from "zx" [0.1mm]. Must be between
        0 and 3600. Default 0.
      second_section_ratio: Tube 2nd section ratio (see Fig. 2 in fw guide). Must be between 0 and
        10000. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
        command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm'). Must be
        between 0 and 3600. Default 3600.
      min_z_endpos: Minimum z-Position at end of a command [0.1 mm] (refers to all channels
        independent of tip pattern parameter 'tm'). Must be between 0 and 3600.  Default 3600.
      dispense_volumes: Dispense volume [0.1ul]. Must be between 0 and 12500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 4 and 5000. Default 500.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 4 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul]. Must be between 0 and 180. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 9999. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between 0
        and 4. Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] (0 = OFF). Must be between 0 and 45.
        Default 1.
      dispense_position_above_z_touch_off: dispense position above Z touch off [0.1 s] (0 = OFF)
        Turns LLD & Z touch off to OFF if ON!. Must be between 0 and 100. Default 5.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      dp_lld_sensitivity: delta p LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
        Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1600.
        Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: Mix volume [0.1ul]. Must be between 0 and 12500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: Mix position in Z- direction from liquid surface (LLD or
        absolute terms) [0.1mm]. Must be between 0 and 900.  Default 250.
      mix_speed: Speed of mixing [0.1ul/s]. Must be between 4 and 5000. Default 500.
      mix_surface_following_distance: Surface following distance during mixing [0.1mm]. Must be
        between 0 and 3600. Default 0.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
        be between 0 and 2. Default 0.
    """

    assert all(0 <= x <= 4 for x in dispensing_mode), "dispensing_mode must be between 0 and 4"
    assert all(0 <= xp <= 25000 for xp in x_positions), "x_positions must be between 0 and 25000"
    assert all(0 <= yp <= 6500 for yp in y_positions), "y_positions must be between 0 and 6500"
    assert any(0 <= x <= 3600 for x in minimum_height), "minimum_height must be between 0 and 3600"
    assert any(
      0 <= x <= 3600 for x in lld_search_height
    ), "lld_search_height must be between 0 and 3600"
    assert any(
      0 <= x <= 3600 for x in liquid_surface_no_lld
    ), "liquid_surface_no_lld must be between 0 and 3600"
    assert any(
      0 <= x <= 3600 for x in pull_out_distance_transport_air
    ), "pull_out_distance_transport_air must be between 0 and 3600"
    assert any(
      0 <= x <= 3600 for x in immersion_depth
    ), "immersion_depth must be between 0 and 3600"
    assert any(
      0 <= x <= 1 for x in immersion_depth_direction
    ), "immersion_depth_direction must be between 0 and 1"
    assert any(
      0 <= x <= 3600 for x in surface_following_distance
    ), "surface_following_distance must be between 0 and 3600"
    assert any(
      0 <= x <= 3600 for x in second_section_height
    ), "second_section_height must be between 0 and 3600"
    assert any(
      0 <= x <= 10000 for x in second_section_ratio
    ), "second_section_ratio must be between 0 and 10000"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= min_z_endpos <= 3600, "min_z_endpos must be between 0 and 3600"
    assert any(
      0 <= x <= 12500 for x in dispense_volumes
    ), "dispense_volume must be between 0 and 12500"
    assert any(4 <= x <= 5000 for x in dispense_speed), "dispense_speed must be between 4 and 5000"
    assert any(4 <= x <= 5000 for x in cut_off_speed), "cut_off_speed must be between 4 and 5000"
    assert any(
      0 <= x <= 180 for x in stop_back_volume
    ), "stop_back_volume must be between 0 and 180"
    assert any(
      0 <= x <= 500 for x in transport_air_volume
    ), "transport_air_volume must be between 0 and 500"
    assert any(
      0 <= x <= 9999 for x in blow_out_air_volume
    ), "blow_out_air_volume must be between 0 and 9999"
    assert any(0 <= x <= 4 for x in lld_mode), "lld_mode must be between 0 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert any(
      0 <= x <= 100 for x in dispense_position_above_z_touch_off
    ), "dispense_position_above_z_touch_off must be between 0 and 100"
    assert any(
      1 <= x <= 4 for x in gamma_lld_sensitivity
    ), "gamma_lld_sensitivity must be between 1 and 4"
    assert any(
      1 <= x <= 4 for x in dp_lld_sensitivity
    ), "dp_lld_sensitivity must be between 1 and 4"
    assert any(3 <= x <= 1600 for x in swap_speed), "swap_speed must be between 3 and 1600"
    assert any(0 <= x <= 99 for x in settling_time), "settling_time must be between 0 and 99"
    assert any(0 <= x <= 12500 for x in mix_volume), "mix_volume must be between 0 and 12500"
    assert any(0 <= x <= 99 for x in mix_cycles), "mix_cycles must be between 0 and 99"
    assert any(
      0 <= x <= 900 for x in mix_position_from_liquid_surface
    ), "mix_position_from_liquid_surface must be between 0 and 900"
    assert any(4 <= x <= 5000 for x in mix_speed), "mix_speed must be between 4 and 5000"
    assert any(
      0 <= x <= 3600 for x in mix_surface_following_distance
    ), "mix_surface_following_distance must be between 0 and 3600"
    assert any(
      0 <= x <= 999 for x in limit_curve_index
    ), "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    return await self.send_command(
      module="C0",
      command="DS",
      tip_pattern=tip_pattern,
      read_timeout=max(120, self.read_timeout),
      dm=[f"{dm:01}" for dm in dispensing_mode],
      tm=[f"{tm:01}" for tm in tip_pattern],
      xp=[f"{xp:05}" for xp in x_positions],
      yp=[f"{yp:04}" for yp in y_positions],
      zx=[f"{zx:04}" for zx in minimum_height],
      lp=[f"{lp:04}" for lp in lld_search_height],
      zl=[f"{zl:04}" for zl in liquid_surface_no_lld],
      po=[f"{po:04}" for po in pull_out_distance_transport_air],
      ip=[f"{ip:04}" for ip in immersion_depth],
      it=[f"{it:01}" for it in immersion_depth_direction],
      fp=[f"{fp:04}" for fp in surface_following_distance],
      zu=[f"{zu:04}" for zu in second_section_height],
      zr=[f"{zr:05}" for zr in second_section_ratio],
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{min_z_endpos:04}",
      dv=[f"{dv:05}" for dv in dispense_volumes],
      ds=[f"{ds:04}" for ds in dispense_speed],
      ss=[f"{ss:04}" for ss in cut_off_speed],
      rv=[f"{rv:03}" for rv in stop_back_volume],
      ta=[f"{ta:03}" for ta in transport_air_volume],
      ba=[f"{ba:04}" for ba in blow_out_air_volume],
      lm=[f"{lm:01}" for lm in lld_mode],
      dj=f"{side_touch_off_distance:02}",  #
      zo=[f"{zo:03}" for zo in dispense_position_above_z_touch_off],
      ll=[f"{ll:01}" for ll in gamma_lld_sensitivity],
      lv=[f"{lv:01}" for lv in dp_lld_sensitivity],
      de=[f"{de:04}" for de in swap_speed],
      wt=[f"{wt:02}" for wt in settling_time],
      mv=[f"{mv:05}" for mv in mix_volume],
      mc=[f"{mc:02}" for mc in mix_cycles],
      mp=[f"{mp:03}" for mp in mix_position_from_liquid_surface],
      ms=[f"{ms:04}" for ms in mix_speed],
      mh=[f"{mh:04}" for mh in mix_surface_following_distance],
      gi=[f"{gi:03}" for gi in limit_curve_index],
      gj=tadm_algorithm,  #
      gk=recording_mode,  #
    )

  # TODO:(command:DA) Simultaneous aspiration & dispensation of liquid

  # TODO:(command:DF) Dispense on fly using PIP (Partial volume in jet mode)

  # TODO:(command:LW) DC Wash procedure using PIP

  # -------------- 3.5.5 CoRe gripper commands --------------

  @need_iswap_parked
  async def get_core(self, p1: int, p2: int):
    """Get CoRe gripper tool from wasteblock mount."""
    if not 0 <= p1 < self.num_channels:
      raise ValueError(f"channel_1 must be between 0 and {self.num_channels - 1}")
    if not 1 <= p2 <= self.num_channels:
      raise ValueError(f"channel_2 must be between 1 and {self.num_channels}")

    # This appears to be deck.get_size_x() - 562.5, but let's keep an explicit check so that we
    # can catch unknown deck sizes. Can the grippers exist at another location? If so, define it as
    # a resource on the robot deck and use deck.get_resource().get_absolute_location().
    deck_size = self.deck.get_absolute_size_x()
    if deck_size == STARLET_SIZE_X:
      xs = 7975  # 1360-797.5 = 562.5
    elif deck_size == STAR_SIZE_X:
      xs = 13385  # 1900-1337.5 = 562.5, plus a manual adjustment of + 10
    else:
      raise ValueError(f"Deck size {deck_size} not supported")

    command_output = await self.send_command(
      module="C0",
      command="ZT",
      xs=f"{xs + self.core_adjustment.x:05}",
      xd="0",
      ya=f"{1240 + self.core_adjustment.y:04}",
      yb=f"{1065 + self.core_adjustment.y:04}",
      pa=f"{p1:02}",
      pb=f"{p2:02}",
      tp=f"{2350 + self.core_adjustment.z:04}",
      tz=f"{2250 + self.core_adjustment.z:04}",
      th=round(self._iswap_traversal_height * 10),
      tt="14",
    )
    self._core_parked = False
    return command_output

  @need_iswap_parked
  async def put_core(self):
    """Put CoRe gripper tool at wasteblock mount."""
    assert self.deck is not None, "must have deck defined to access CoRe grippers"
    deck_size = self.deck.get_absolute_size_x()
    if deck_size == STARLET_SIZE_X:
      xs = 7975
    elif deck_size == STAR_SIZE_X:
      xs = 13385
    else:
      raise ValueError(f"Deck size {deck_size} not supported")
    command_output = await self.send_command(
      module="C0",
      command="ZS",
      xs=f"{xs + self.core_adjustment.x:05}",
      xd="0",
      ya=f"{1240 + self.core_adjustment.y:04}",
      yb=f"{1065 + self.core_adjustment.y:04}",
      tp=f"{2150 + self.core_adjustment.z:04}",
      tz=f"{2050 + self.core_adjustment.z:04}",
      th=round(self._iswap_traversal_height * 10),
      te=round(self._iswap_traversal_height * 10),
    )
    self._core_parked = True
    return command_output

  async def core_open_gripper(self):
    """Open CoRe gripper tool."""
    return await self.send_command(module="C0", command="ZO")

  @need_iswap_parked
  async def core_get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_gripping_speed: int = 50,
    z_position: int = 0,
    z_speed: int = 500,
    open_gripper_position: int = 0,
    plate_width: int = 0,
    grip_strength: int = 15,
    minimum_traverse_height_at_beginning_of_a_command: int = 2750,
    minimum_z_position_at_the_command_end: int = 2750,
  ):
    """Get plate with CoRe gripper tool from wasteblock mount."""

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_gripping_speed <= 3700, "y_gripping_speed must be between 0 and 3700"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_speed <= 1287, "z_speed must be between 0 and 1287"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= plate_width <= 9999, "plate_width must be between 0 and 9999"
    assert 0 <= grip_strength <= 99, "grip_strength must be between 0 and 99"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert (
      0 <= minimum_z_position_at_the_command_end <= 3600
    ), "minimum_z_position_at_the_command_end must be between 0 and 3600"

    command_output = await self.send_command(
      module="C0",
      command="ZP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yv=f"{y_gripping_speed:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      yg=f"{plate_width:04}",
      yw=f"{grip_strength:02}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{minimum_z_position_at_the_command_end:04}",
    )

    return command_output

  @need_iswap_parked
  async def core_put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    z_press_on_distance: int = 0,
    z_speed: int = 500,
    open_gripper_position: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 2750,
    z_position_at_the_command_end: int = 2750,
    return_tool: bool = True,
  ):
    """Put plate with CoRe gripper tool and return to wasteblock mount."""

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_press_on_distance <= 50, "z_press_on_distance must be between 0 and 999"
    assert 0 <= z_speed <= 1600, "z_speed must be between 0 and 1600"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert (
      0 <= z_position_at_the_command_end <= 3600
    ), "z_position_at_the_command_end must be between 0 and 3600"

    command_output = await self.send_command(
      module="C0",
      command="ZR",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zi=f"{z_press_on_distance:03}",
      zy=f"{z_speed:04}",
      yo=f"{open_gripper_position:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
    )

    if return_tool:
      await self.put_core()

    return command_output

  @need_iswap_parked
  async def core_move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    x_acceleration_index: int = 4,
    y_position: int = 0,
    z_position: int = 0,
    z_speed: int = 500,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
  ):
    """Move a plate with CoRe gripper tool."""

    command_output = await self.send_command(
      module="C0",
      command="ZM",
      xs=f"{x_position:05}",
      xd=x_direction,
      xg=x_acceleration_index,
      yj=f"{y_position:04}",
      zj=f"{z_position:04}",
      zy=f"{z_speed:04}",
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
    )

    return command_output

  # TODO:(command:ZB)

  # -------------- 3.5.6 Adjustment & movement commands --------------

  async def position_single_pipetting_channel_in_y_direction(
    self, pipetting_channel_index: int, y_position: int
  ):
    """Position single pipetting channel in Y-direction.

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16.
      y_position: y position [0.1mm]. Must be between 0 and 6500.
    """

    assert (
      1 <= pipetting_channel_index <= self.num_channels
    ), "pipetting_channel_index must be between 1 and self"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"

    return await self.send_command(
      module="C0",
      command="KY",
      pn=f"{pipetting_channel_index:02}",
      yj=f"{y_position:04}",
    )

  async def position_single_pipetting_channel_in_z_direction(
    self, pipetting_channel_index: int, z_position: int
  ):
    """Position single pipetting channel in Z-direction.

    Note that this refers to the point of the tip if a tip is mounted!

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and 16.
      z_position: y position [0.1mm]. Must be between 0 and 3347. The docs say 3600,but empirically
        3347 is the max.
    """

    assert (
      1 <= pipetting_channel_index <= self.num_channels
    ), "pipetting_channel_index must be between 1 and self.num_channels"
    # docs say 3600, but empirically 3347 is the max
    assert 0 <= z_position <= 3347, "z_position must be between 0 and 3347"

    return await self.send_command(
      module="C0",
      command="KZ",
      pn=f"{pipetting_channel_index:02}",
      zj=f"{z_position:04}",
    )

  async def search_for_teach_in_signal_using_pipetting_channel_n_in_x_direction(
    self, pipetting_channel_index: int, x_position: int
  ):
    """Search for Teach in signal using pipetting channel n in X-direction.

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 1 and self.num_channels.
      x_position: x position [0.1mm]. Must be between 0 and 30000.
    """

    assert (
      1 <= pipetting_channel_index <= self.num_channels
    ), "pipetting_channel_index must be between 1 and self.num_channels"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"

    return await self.send_command(
      module="C0",
      command="XL",
      pn=f"{pipetting_channel_index:02}",
      xs=f"{x_position:05}",
    )

  async def spread_pip_channels(self):
    """Spread PIP channels"""

    return await self.send_command(module="C0", command="JE")

  @need_iswap_parked
  async def move_all_pipetting_channels_to_defined_position(
    self,
    tip_pattern: bool = True,
    x_positions: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_command: int = 3600,
    z_endpos: int = 0,
  ):
    """Move all pipetting channels to defined position

    Args:
      tip_pattern: Tip pattern (channels involved). Default True.
      x_positions: x positions [0.1mm]. Must be between 0 and 25000. Default 0.
      y_positions: y positions [0.1mm]. Must be between 0 and 6500. Default 0.
      minimum_traverse_height_at_beginning_of_command: Minimum traverse height at beginning of a
        command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').  Must be
        between 0 and 3600. Default 3600.
      z_endpos: Z-Position at end of a command [0.1 mm] (refers to all channels independent of tip
        pattern parameter 'tm'). Must be between 0 and 3600. Default 0.
    """

    assert 0 <= x_positions <= 25000, "x_positions must be between 0 and 25000"
    assert 0 <= y_positions <= 6500, "y_positions must be between 0 and 6500"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_command must be between 0 and 3600"
    assert 0 <= z_endpos <= 3600, "z_endpos must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="JM",
      tm=tip_pattern,
      xp=x_positions,
      yp=y_positions,
      th=minimum_traverse_height_at_beginning_of_command,
      zp=z_endpos,
    )

  # TODO:(command:JR): teach rack using pipetting channel n

  @need_iswap_parked
  async def position_max_free_y_for_n(self, pipetting_channel_index: int):
    """Position all pipetting channels so that there is maximum free Y range for channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 0 and self.num_channels.
    """

    assert (
      0 <= pipetting_channel_index < self.num_channels
    ), "pipetting_channel_index must be between 1 and self.num_channels"
    # convert Python's 0-based indexing to Hamilton firmware's 1-based indexing
    pipetting_channel_index = pipetting_channel_index + 1

    return await self.send_command(
      module="C0",
      command="JP",
      pn=f"{pipetting_channel_index:02}",
    )

  async def move_all_channels_in_z_safety(self):
    """Move all pipetting channels in Z-safety position"""

    return await self.send_command(module="C0", command="ZA")

  # -------------- 3.5.7 PIP query --------------

  # TODO:(command:RY): Request Y-Positions of all pipetting channels

  async def request_y_pos_channel_n(self, pipetting_channel_index: int) -> float:
    """Request Y-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 0 and 15.
        0 is the backmost channel.
    """

    assert (
      0 <= pipetting_channel_index < self.num_channels
    ), "pipetting_channel_index must be between 0 and self.num_channels"
    # convert Python's 0-based indexing to Hamilton firmware's 1-based indexing
    pipetting_channel_index = pipetting_channel_index + 1

    y_pos_query = await self.send_command(
      module="C0",
      command="RB",
      fmt="rb####",
      pn=f"{pipetting_channel_index:02}",
    )
    # Extract y-coordinate and convert to mm
    return float(y_pos_query["rb"] / 10)

  # TODO:(command:RZ): Request Z-Positions of all pipetting channels

  async def request_z_pos_channel_n(self, pipetting_channel_index: int) -> float:
    """Request Z-Position of Pipetting channel n

    Args:
      pipetting_channel_index: Index of pipetting channel. Must be between 0 and 15.
        0 is the backmost channel.

    Returns:
      Z-Position of channel n [0.1mm]. Taking into account tip presence and length.
    """

    assert 0 <= pipetting_channel_index <= 15, "pipetting_channel_index must be between 0 and 15"
    # convert Python's 0-based indexing to Hamilton firmware's 1-based indexing
    pipetting_channel_index = pipetting_channel_index + 1

    z_pos_query = await self.send_command(
      module="C0",
      command="RD",
      fmt="rd####",
      pn=f"{pipetting_channel_index:02}",
    )
    # Extract z-coordinate and convert to mm
    return float(z_pos_query["rd"] / 10)

  async def request_tip_presence(self) -> List[int]:
    """Request query tip presence on each channel

    Returns:
      0 = no tip, 1 = Tip in gripper (for each channel)
    """

    resp = await self.send_command(module="C0", command="RT", fmt="rt# (n)")
    return cast(List[int], resp.get("rt"))

  async def request_pip_height_last_lld(self):
    """Request PIP height of last LLD

    Returns:
      LLD height of all channels
    """

    return await self.send_command(module="C0", command="RL", fmt="lh#### (n)")

  async def request_tadm_status(self):
    """Request PIP height of last LLD

    Returns:
      TADM channel status 0 = off, 1 = on
    """

    return await self.send_command(module="C0", command="QS", fmt="qs# (n)")

  # TODO:(command:FS) Request PIP channel dispense on fly status
  # TODO:(command:VE) Request PIP channel 2nd section aspiration data

  # -------------- 3.6 XL channel commands --------------

  # TODO: all XL channel commands

  # -------------- 3.6.1 Initialization XL --------------

  # TODO:(command:LI)

  # -------------- 3.6.2 Tip handling commands using XL --------------

  # TODO:(command:LP)
  # TODO:(command:LR)

  # -------------- 3.6.3 Liquid handling commands using XL --------------

  # TODO:(command:LA)
  # TODO:(command:LD)
  # TODO:(command:LB)
  # TODO:(command:LC)

  # -------------- 3.6.4 Wash commands using XL channel --------------

  # TODO:(command:LE)
  # TODO:(command:LF)

  # -------------- 3.6.5 XL CoRe gripper commands --------------

  # TODO:(command:LT)
  # TODO:(command:LS)
  # TODO:(command:LU)
  # TODO:(command:LV)
  # TODO:(command:LM)
  # TODO:(command:LO)
  # TODO:(command:LG)

  # -------------- 3.6.6 Adjustment & movement commands CP --------------

  # TODO:(command:LY)
  # TODO:(command:LZ)
  # TODO:(command:LH)
  # TODO:(command:LJ)
  # TODO:(command:XM)
  # TODO:(command:LL)
  # TODO:(command:LQ)
  # TODO:(command:LK)
  # TODO:(command:UE)

  # -------------- 3.6.7 XL channel query --------------

  # TODO:(command:UY)
  # TODO:(command:UB)
  # TODO:(command:UZ)
  # TODO:(command:UD)
  # TODO:(command:UT)
  # TODO:(command:UL)
  # TODO:(command:US)
  # TODO:(command:UF)

  # -------------- 3.7 Tube gripper commands --------------

  # TODO: all tube gripper commands

  # -------------- 3.7.1 Movements --------------

  # TODO:(command:FC)
  # TODO:(command:FD)
  # TODO:(command:FO)
  # TODO:(command:FT)
  # TODO:(command:FU)
  # TODO:(command:FJ)
  # TODO:(command:FM)
  # TODO:(command:FW)

  # -------------- 3.7.2 Tube gripper query --------------

  # TODO:(command:FQ)
  # TODO:(command:FN)

  # -------------- 3.8 Imaging channel commands --------------

  # TODO: all imaging commands

  # -------------- 3.8.1 Movements --------------

  # TODO:(command:IC)
  # TODO:(command:ID)
  # TODO:(command:IM)
  # TODO:(command:IJ)

  # -------------- 3.8.2 Imaging channel query --------------

  # TODO:(command:IN)

  # -------------- 3.9 Robotic channel commands --------------

  # -------------- 3.9.1 Initialization --------------

  # TODO:(command:OI)

  # -------------- 3.9.2 Cap handling commands --------------

  # TODO:(command:OP)
  # TODO:(command:OQ)

  # -------------- 3.9.3 Adjustment & movement commands --------------

  # TODO:(command:OY)
  # TODO:(command:OZ)
  # TODO:(command:OH)
  # TODO:(command:OJ)
  # TODO:(command:OX)
  # TODO:(command:OM)
  # TODO:(command:OF)
  # TODO:(command:OG)

  # -------------- 3.9.4 Robotic channel query --------------

  # TODO:(command:OA)
  # TODO:(command:OB)
  # TODO:(command:OC)
  # TODO:(command:OD)
  # TODO:(command:OT)

  # -------------- 3.10 CoRe 96 Head commands --------------

  # -------------- 3.10.1 Initialization --------------

  async def initialize_core_96_head(
    self, trash96: Trash, z_position_at_the_command_end: float = 245.0
  ):
    """Initialize CoRe 96 Head

    Args:
      trash96: Trash object where tips should be disposed. The 96 head will be positioned in the
        center of the trash.
      z_position_at_the_command_end: Z position at the end of the command [mm].
    """

    # The firmware command expects location of tip A1 of the head.
    loc = self._position_96_head_in_resource(trash96)

    return await self.send_command(
      module="C0",
      command="EI",
      read_timeout=60,
      xs=f"{abs(round(loc.x * 10)):05}",
      xd=0 if loc.x >= 0 else 1,
      yh=f"{abs(round(loc.y * 10)):04}",
      za=f"{round(loc.z * 10):04}",
      ze=f"{round(z_position_at_the_command_end*10):04}",
    )

  async def move_core_96_to_safe_position(self):
    """Move CoRe 96 Head to Z save position"""

    return await self.send_command(module="C0", command="EV")

  async def request_core_96_head_initialization_status(self) -> bool:
    # not available in the C0 docs, so get from module H0 itself instead
    response = await self.send_command(module="H0", command="QW", fmt="qw#")
    return bool(response.get("qw", 0) == 1)  # type?

  # -------------- 3.10.2 Tip handling using CoRe 96 Head --------------

  @need_iswap_parked
  async def pick_up_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    tip_type_idx: int,
    tip_pickup_method: int = 2,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Pick up tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_size: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96 tip
        wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert (
      0 <= minimum_height_command_end <= 3425
    ), "minimum_height_command_end must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="EP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      tt=f"{tip_type_idx:02}",
      wu=tip_pickup_method,
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  @need_iswap_parked
  async def discard_tips_core96(
    self,
    x_position: int,
    x_direction: int,
    y_position: int,
    z_deposit_position: int = 3425,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimum_height_command_end: int = 3425,
  ):
    """Drop tips with CoRe 96 head

    Args:
      x_position: x position [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: y position [0.1mm]. Must be between 1080 and 5600. Default 5600.
      tip_type: Tip type.
      tip_pickup_method: Tip pick up method. 0 = pick up from rack. 1 = pick up from C0Re 96
        tip wash station. 2 = pick up with " full volume blow out"
      z_deposit_position: Z- deposit position [0.1mm] (collar bearing position) Must bet between
        0 and 3425. Default 3425.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
        of a command [0.1mm]. Must be between 0 and 3425.
      minimum_height_command_end: Minimal height at command end [0.1 mm] Must be between 0 and 3425
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= z_deposit_position <= 3425, "z_deposit_position must be between 0 and 3425"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert (
      0 <= minimum_height_command_end <= 3425
    ), "minimum_height_command_end must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="ER",
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      za=f"{z_deposit_position:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimum_height_command_end:04}",
    )

  # -------------- 3.10.3 Liquid handling using CoRe 96 Head --------------

  @need_iswap_parked
  async def aspirate_core_96(
    self,
    aspiration_type: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_positions: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimal_end_height: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_aspiration: int = 0,
    aspiration_volumes: int = 0,
    aspiration_speed: int = 1000,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    pre_wetting_volume: int = 0,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    swap_speed: int = 100,
    settling_time: int = 5,
    mix_volume: int = 0,
    mix_cycles: int = 0,
    mix_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mix: int = 0,
    speed_of_mix: int = 1000,
    channel_pattern: List[bool] = [True] * 96,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
  ):
    """aspirate CoRe 96

    Aspiration of liquid using CoRe 96

    Args:
      aspiration_type: Type of aspiration (0 = simple; 1 = sequence; 2 = cup emptied). Must be
          between 0 and 2. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_positions: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
          a command 0.1mm] (refers to all channels independent of tip pattern parameter 'tm').
          Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from "zm" [0.1mm]
           Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_distance_at_the_end_of_aspiration: Liquid surface sink distance at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      aspiration_volumes: Aspiration volume [0.1ul]. Must be between 0 and 11500. Default 0.
      aspiration_speed: Aspiration speed [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      pre_wetting_volume: Pre-wetting volume. Must be between 0 and 11500. Default 0.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be between
          0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mix_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mix_cycles: Number of mix cycles. Must be between 0 and 99. Default 0.
      mix_position_from_liquid_surface: mix position in Z- direction from
          liquid surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_mix: surface following distance during
          mix [0.1mm]. Must be between 0 and 990. Default 0.
      speed_of_mix: Speed of mix [0.1ul/s]. Must be between 3 and 5000.
          Default 1000.
      todo: TODO: 24 hex chars. Must be between 4 and 5000.
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement.
          Must be between 0 and 2. Default 0.
    """

    assert 0 <= aspiration_type <= 2, "aspiration_type must be between 0 and 2"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_positions <= 5600, "y_positions must be between 1080 and 5600"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimal_end_height <= 3425, "minimal_end_height must be between 0 and 3425"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert (
      0 <= liquid_surface_at_function_without_lld <= 3425
    ), "liquid_surface_at_function_without_lld must be between 0 and 3425"
    assert (
      0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3425
    ), "pull_out_distance_to_take_transport_air_in_function_without_lld must be between 0 and 3425"
    assert (
      0 <= maximum_immersion_depth <= 3425
    ), "maximum_immersion_depth must be between 0 and 3425"
    assert (
      0 <= tube_2nd_section_height_measured_from_zm <= 3425
    ), "tube_2nd_section_height_measured_from_zm must be between 0 and 3425"
    assert (
      0 <= tube_2nd_section_ratio <= 10000
    ), "tube_2nd_section_ratio must be between 0 and 10000"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert (
      0 <= liquid_surface_sink_distance_at_the_end_of_aspiration <= 990
    ), "liquid_surface_sink_distance_at_the_end_of_aspiration must be between 0 and 990"
    assert 0 <= aspiration_volumes <= 11500, "aspiration_volumes must be between 0 and 11500"
    assert 3 <= aspiration_speed <= 5000, "aspiration_speed must be between 3 and 5000"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= pre_wetting_volume <= 11500, "pre_wetting_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mix_volume <= 11500, "mix_volume must be between 0 and 11500"
    assert 0 <= mix_cycles <= 99, "mix_cycles must be between 0 and 99"
    assert (
      0 <= mix_position_from_liquid_surface <= 990
    ), "mix_position_from_liquid_surface must be between 0 and 990"
    assert (
      0 <= surface_following_distance_during_mix <= 990
    ), "surface_following_distance_during_mix must be between 0 and 990"
    assert 3 <= speed_of_mix <= 5000, "speed_of_mix must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"

    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="EA",
      aa=aspiration_type,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_positions:04}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimal_end_height:04}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_at_function_without_lld:04}",
      pp=f"{pull_out_distance_to_take_transport_air_in_function_without_lld:04}",
      zm=f"{maximum_immersion_depth:04}",
      zv=f"{tube_2nd_section_height_measured_from_zm:04}",
      zq=f"{tube_2nd_section_ratio:05}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{liquid_surface_sink_distance_at_the_end_of_aspiration:03}",
      af=f"{aspiration_volumes:05}",
      ag=f"{aspiration_speed:04}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      wv=f"{pre_wetting_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mix_volume:05}",
      hc=f"{mix_cycles:02}",
      hp=f"{mix_position_from_liquid_surface:03}",
      mj=f"{surface_following_distance_during_mix:03}",
      hs=f"{speed_of_mix:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  @need_iswap_parked
  async def dispense_core_96(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    tube_2nd_section_height_measured_from_zm: int = 0,
    tube_2nd_section_ratio: int = 3425,
    lld_search_height: int = 3425,
    liquid_surface_at_function_without_lld: int = 3425,
    pull_out_distance_to_take_transport_air_in_function_without_lld: int = 50,
    maximum_immersion_depth: int = 3425,
    immersion_depth: int = 0,
    immersion_depth_direction: int = 0,
    liquid_surface_sink_distance_at_the_end_of_dispense: int = 0,
    minimum_traverse_height_at_beginning_of_a_command: int = 3425,
    minimal_end_height: int = 3425,
    dispense_volume: int = 0,
    dispense_speed: int = 5000,
    cut_off_speed: int = 250,
    stop_back_volume: int = 0,
    transport_air_volume: int = 0,
    blow_out_air_volume: int = 200,
    lld_mode: int = 1,
    gamma_lld_sensitivity: int = 1,
    side_touch_off_distance: int = 0,
    swap_speed: int = 100,
    settling_time: int = 5,
    mixing_volume: int = 0,
    mixing_cycles: int = 0,
    mixing_position_from_liquid_surface: int = 250,
    surface_following_distance_during_mixing: int = 0,
    speed_of_mixing: int = 1000,
    channel_pattern: List[bool] = [True] * 12 * 8,
    limit_curve_index: int = 0,
    tadm_algorithm: bool = False,
    recording_mode: int = 0,
  ):
    """dispense CoRe 96

    Dispensing of liquid using CoRe 96

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
          in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
          position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm] of well A1. Must be between 1080 and 5600. Default 0.
      maximum_immersion_depth: Minimum height (maximum immersion depth) [0.1mm]. Must be between
          0 and 3425. Default 3425.
      tube_2nd_section_height_measured_from_zm: Tube 2nd section height measured from
          "zm" [0.1mm]. Must be between 0 and 3425. Default 0.
      tube_2nd_section_ratio: Tube 2nd section ratio (See Fig 2.). Must be between 0 and 10000.
          Default 3425.
      lld_search_height: LLD search height [0.1mm]. Must be between 0 and 3425. Default 3425.
      liquid_surface_at_function_without_lld: Liquid surface at function without LLD [0.1mm].
          Must be between 0 and 3425. Default 3425.
      pull_out_distance_to_take_transport_air_in_function_without_lld: pull out distance to take
          transport air in function without LLD [0.1mm]. Must be between 0 and 3425. Default 50.
      immersion_depth: Immersion depth [0.1mm]. Must be between 0 and 3600. Default 0.
      immersion_depth_direction: Direction of immersion depth (0 = go deeper, 1 = go up out of
          liquid). Must be between 0 and 1. Default 0.
      liquid_surface_sink_distance_at_the_end_of_dispense: Liquid surface sink elevation at
          the end of aspiration [0.1mm]. Must be between 0 and 990. Default 0.
      minimum_traverse_height_at_beginning_of_a_command: Minimal traverse height at begin of
          command [0.1mm]. Must be between 0 and 3425. Default 3425.
      minimal_end_height: Minimal height at command end [0.1mm]. Must be between 0 and 3425.
          Default 3425.
      dispense_volume: Dispense volume [0.1ul]. Must be between 0 and 11500. Default 0.
      dispense_speed: Dispense speed [0.1ul/s]. Must be between 3 and 5000. Default 5000.
      cut_off_speed: Cut-off speed [0.1ul/s]. Must be between 3 and 5000. Default 250.
      stop_back_volume: Stop back volume [0.1ul/s]. Must be between 0 and 999. Default 0.
      transport_air_volume: Transport air volume [0.1ul]. Must be between 0 and 500. Default 0.
      blow_out_air_volume: Blow-out air volume [0.1ul]. Must be between 0 and 11500. Default 200.
      lld_mode: LLD mode (0 = off, 1 = gamma, 2 = dP, 3 = dual, 4 = Z touch off). Must be
          between 0 and 4. Default 1.
      gamma_lld_sensitivity: gamma LLD sensitivity (1= high, 4=low). Must be between 1 and 4.
          Default 1.
      side_touch_off_distance: side touch off distance [0.1 mm] 0 = OFF ( > 0 = ON & turns LLD off)
        Must be between 0 and 45. Default 1.
      swap_speed: Swap speed (on leaving liquid) [0.1mm/s]. Must be between 3 and 1000. Default 100.
      settling_time: Settling time [0.1s]. Must be between 0 and 99. Default 5.
      mixing_volume: mix volume [0.1ul]. Must be between 0 and 11500. Default 0.
      mixing_cycles: Number of mixing cycles. Must be between 0 and 99. Default 0.
      mixing_position_from_liquid_surface: mix position in Z- direction from liquid
          surface (LLD or absolute terms) [0.1mm]. Must be between 0 and 990. Default 250.
      surface_following_distance_during_mixing: surface following distance during mixing [0.1mm].
          Must be between 0 and 990. Default 0.
      speed_of_mixing: Speed of mixing [0.1ul/s]. Must be between 3 and 5000. Default 1000.
      channel_pattern: list of 96 boolean values
      limit_curve_index: limit curve index. Must be between 0 and 999. Default 0.
      tadm_algorithm: TADM algorithm. Default False.
      recording_mode: Recording mode 0 : no 1 : TADM errors only 2 : all TADM measurement. Must
          be between 0 and 2. Default 0.
    """

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert (
      0 <= maximum_immersion_depth <= 3425
    ), "maximum_immersion_depth must be between 0 and 3425"
    assert (
      0 <= tube_2nd_section_height_measured_from_zm <= 3425
    ), "tube_2nd_section_height_measured_from_zm must be between 0 and 3425"
    assert (
      0 <= tube_2nd_section_ratio <= 10000
    ), "tube_2nd_section_ratio must be between 0 and 10000"
    assert 0 <= lld_search_height <= 3425, "lld_search_height must be between 0 and 3425"
    assert (
      0 <= liquid_surface_at_function_without_lld <= 3425
    ), "liquid_surface_at_function_without_lld must be between 0 and 3425"
    assert (
      0 <= pull_out_distance_to_take_transport_air_in_function_without_lld <= 3425
    ), "pull_out_distance_to_take_transport_air_in_function_without_lld must be between 0 and 3425"
    assert 0 <= immersion_depth <= 3600, "immersion_depth must be between 0 and 3600"
    assert 0 <= immersion_depth_direction <= 1, "immersion_depth_direction must be between 0 and 1"
    assert (
      0 <= liquid_surface_sink_distance_at_the_end_of_dispense <= 990
    ), "liquid_surface_sink_distance_at_the_end_of_dispense must be between 0 and 990"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3425
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3425"
    assert 0 <= minimal_end_height <= 3425, "minimal_end_height must be between 0 and 3425"
    assert 0 <= dispense_volume <= 11500, "dispense_volume must be between 0 and 11500"
    assert 3 <= dispense_speed <= 5000, "dispense_speed must be between 3 and 5000"
    assert 3 <= cut_off_speed <= 5000, "cut_off_speed must be between 3 and 5000"
    assert 0 <= stop_back_volume <= 999, "stop_back_volume must be between 0 and 999"
    assert 0 <= transport_air_volume <= 500, "transport_air_volume must be between 0 and 500"
    assert 0 <= blow_out_air_volume <= 11500, "blow_out_air_volume must be between 0 and 11500"
    assert 0 <= lld_mode <= 4, "lld_mode must be between 0 and 4"
    assert 1 <= gamma_lld_sensitivity <= 4, "gamma_lld_sensitivity must be between 1 and 4"
    assert 0 <= side_touch_off_distance <= 45, "side_touch_off_distance must be between 0 and 45"
    assert 3 <= swap_speed <= 1000, "swap_speed must be between 3 and 1000"
    assert 0 <= settling_time <= 99, "settling_time must be between 0 and 99"
    assert 0 <= mixing_volume <= 11500, "mixing_volume must be between 0 and 11500"
    assert 0 <= mixing_cycles <= 99, "mixing_cycles must be between 0 and 99"
    assert (
      0 <= mixing_position_from_liquid_surface <= 990
    ), "mixing_position_from_liquid_surface must be between 0 and 990"
    assert (
      0 <= surface_following_distance_during_mixing <= 990
    ), "surface_following_distance_during_mixing must be between 0 and 990"
    assert 3 <= speed_of_mixing <= 5000, "speed_of_mixing must be between 3 and 5000"
    assert 0 <= limit_curve_index <= 999, "limit_curve_index must be between 0 and 999"
    assert 0 <= recording_mode <= 2, "recording_mode must be between 0 and 2"

    # Convert bool list to hex string
    assert len(channel_pattern) == 96, "channel_pattern must be a list of 96 boolean values"
    channel_pattern_bin_str = reversed(["1" if x else "0" for x in channel_pattern])
    channel_pattern_hex = hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]

    return await self.send_command(
      module="C0",
      command="ED",
      da=dispensing_mode,
      xs=f"{x_position:05}",
      xd=x_direction,
      yh=f"{y_position:04}",
      zm=f"{maximum_immersion_depth:04}",
      zv=f"{tube_2nd_section_height_measured_from_zm:04}",
      zq=f"{tube_2nd_section_ratio:05}",
      lz=f"{lld_search_height:04}",
      zt=f"{liquid_surface_at_function_without_lld:04}",
      pp=f"{pull_out_distance_to_take_transport_air_in_function_without_lld:04}",
      iw=f"{immersion_depth:03}",
      ix=immersion_depth_direction,
      fh=f"{liquid_surface_sink_distance_at_the_end_of_dispense:03}",
      zh=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ze=f"{minimal_end_height:04}",
      df=f"{dispense_volume:05}",
      dg=f"{dispense_speed:04}",
      es=f"{cut_off_speed:04}",
      ev=f"{stop_back_volume:03}",
      vt=f"{transport_air_volume:03}",
      bv=f"{blow_out_air_volume:05}",
      cm=lld_mode,
      cs=gamma_lld_sensitivity,
      ej=f"{side_touch_off_distance:02}",
      bs=f"{swap_speed:04}",
      wh=f"{settling_time:02}",
      hv=f"{mixing_volume:05}",
      hc=f"{mixing_cycles:02}",
      hp=f"{mixing_position_from_liquid_surface:03}",
      mj=f"{surface_following_distance_during_mixing:03}",
      hs=f"{speed_of_mixing:04}",
      cw=channel_pattern_hex,
      cr=f"{limit_curve_index:03}",
      cj=tadm_algorithm,
      cx=recording_mode,
    )

  # -------------- 3.10.4 Adjustment & movement commands --------------

  async def move_core_96_head_to_defined_position(
    self,
    dispensing_mode: int = 0,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    z_position: int = 0,
    minimum_height_at_beginning_of_a_command: int = 3425,
  ):
    """Move CoRe 96 Head to defined position

    Args:
      dispensing_mode: Type of dispensing mode 0 = Partial volume in jet mode 1 = Blow out
        in jet mode 2 = Partial volume at surface 3 = Blow out at surface 4 = Empty tip at fix
        position. Must be between 0 and 4. Default 0.
      x_position: X-Position [0.1mm] of well A1. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Y-Position [0.1mm]. Must be between 1080 and 5600. Default 0.
      z_position: Z-Position [0.1mm]. Must be between 0 and 5600. Default 0.
      minimum_height_at_beginning_of_a_command: Minimum height at beginning of a command 0.1mm]
        (refers to all channels independent of tip pattern parameter 'tm'). Must be between 0 and
        3425. Default 3425.
    """

    assert 0 <= dispensing_mode <= 4, "dispensing_mode must be between 0 and 4"
    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 1080 <= y_position <= 5600, "y_position must be between 1080 and 5600"
    assert 0 <= y_position <= 5600, "z_position must be between 0 and 5600"
    assert (
      0 <= minimum_height_at_beginning_of_a_command <= 3425
    ), "minimum_height_at_beginning_of_a_command must be between 0 and 3425"

    return await self.send_command(
      module="C0",
      command="EM",
      dm=dispensing_mode,
      xs=x_position,
      xd=x_direction,
      yh=y_position,
      za=z_position,
      zh=minimum_height_at_beginning_of_a_command,
    )

  # -------------- 3.10.5 Wash procedure commands using CoRe 96 Head --------------

  # TODO:(command:EG) Washing tips using CoRe 96 Head
  # TODO:(command:EU) Empty washed tips (end of wash procedure only)

  # -------------- 3.10.6 Query CoRe 96 Head --------------

  async def request_tip_presence_in_core_96_head(self):
    """Request Tip presence in CoRe 96 Head

    Returns:
      qh: 0 = no tips, 1 = TipRack are picked up
    """

    return await self.send_command(module="C0", command="QH", fmt="qh#")

  async def request_position_of_core_96_head(self):
    """Request position of CoRe 96 Head (A1 considered to tip length)

    Returns:
      xs: A1 X direction [0.1mm]
      xd: X direction 0 = positive 1 = negative
      yh: A1 Y direction [0.1mm]
      za: Z height [0.1mm]
    """

    return await self.send_command(module="C0", command="QI", fmt="xs#####xd#hy####za####")

  async def request_core_96_head_channel_tadm_status(self):
    """Request CoRe 96 Head channel TADM Status

    Returns:
      qx: TADM channel status 0 = off 1 = on
    """

    return await self.send_command(module="C0", command="VC", fmt="qx#")

  async def request_core_96_head_channel_tadm_error_status(self):
    """Request CoRe 96 Head channel TADM error status

    Returns:
      vb: error pattern 0 = no error
    """

    return await self.send_command(module="C0", command="VB", fmt="vb" + "&" * 24)

  # -------------- 3.11 384 Head commands --------------

  # -------------- 3.11.1 Initialization --------------

  # -------------- 3.11.2 Tip handling using 384 Head --------------

  # -------------- 3.11.3 Liquid handling using 384 Head --------------

  # -------------- 3.11.4 Adjustment & movement commands --------------

  # -------------- 3.11.5 Wash procedure commands using 384 Head --------------

  # -------------- 3.11.6 Query 384 Head --------------

  # -------------- 3.12 Nano pipettor commands --------------

  # TODO: all nano pipettor commands

  # -------------- 3.12.1 Initialization --------------

  # TODO:(command:NI)
  # TODO:(command:NV)
  # TODO:(command:NP)

  # -------------- 3.12.2 Nano pipettor liquid handling commands --------------

  # TODO:(command:NA)
  # TODO:(command:ND)
  # TODO:(command:NF)

  # -------------- 3.12.3 Nano pipettor wash & clean commands --------------

  # TODO:(command:NW)
  # TODO:(command:NU)

  # -------------- 3.12.4 Nano pipettor adjustment & movements --------------

  # TODO:(command:NM)
  # TODO:(command:NT)

  # -------------- 3.12.5 Nano pipettor query --------------

  # TODO:(command:QL)
  # TODO:(command:QN)
  # TODO:(command:RN)
  # TODO:(command:QQ)
  # TODO:(command:QR)
  # TODO:(command:QO)
  # TODO:(command:RR)
  # TODO:(command:QU)

  # -------------- 3.13 Auto load commands --------------

  # -------------- 3.13.1 Initialization --------------

  async def initialize_auto_load(self):
    """Initialize Auto load module"""

    return await self.send_command(module="C0", command="II")

  async def move_auto_load_to_z_save_position(self):
    """Move auto load to Z save position"""

    return await self.send_command(module="C0", command="IV")

  # -------------- 3.13.2 Carrier handling --------------

  # TODO:(command:CI) Identify carrier (determine carrier type)

  async def request_single_carrier_presence(self, carrier_position: int):
    """Request single carrier presence

    Args:
      carrier_position: Carrier position (slot number)

    Returns:
      True if present, False otherwise
    """

    assert 1 <= carrier_position <= 54, "carrier_position must be between 1 and 54"
    carrier_position_str = str(carrier_position).zfill(2)
    resp = await self.send_command(
      module="C0",
      command="CT",
      fmt="ct#",
      cp=carrier_position_str,
    )
    assert resp is not None
    return resp["ct"] == 1

  # Move autoload/scanner X-drive into slot number
  async def move_autoload_to_slot(self, slot_number: int):
    """Move autoload to specific slot/track position"""

    assert 1 <= slot_number <= 54, "slot_number must be between 1 and 54"
    slot_no_as_safe_str = str(slot_number).zfill(2)

    return await self.send_command(module="I0", command="XP", xp=slot_no_as_safe_str)

  # Park autoload
  async def park_autoload(self):
    """Park autoload"""

    # Identify max number of x positions for your liquid handler
    max_x_pos = str(self.extended_conf["xt"]).zfill(2)

    # Park autoload to max x position available
    return await self.send_command(module="I0", command="XP", xp=max_x_pos)

  # TODO:(command:CA) Push out carrier to loading tray (after identification CI)

  async def unload_carrier(self, carrier: Carrier):
    """Use autoload to unload carrier."""
    # Identify carrier end rail
    track_width = 22.5
    carrier_width = carrier.get_absolute_location().x - 100 + carrier.get_absolute_size_x()
    carrier_end_rail = int(carrier_width / track_width)
    assert 1 <= carrier_end_rail <= 54, "carrier loading rail must be between 1 and 54"

    carrier_end_rail_str = str(carrier_end_rail).zfill(2)

    # Unload and read out barcodes
    resp = await self.send_command(
      module="C0",
      command="CR",
      cp=carrier_end_rail_str,
    )
    # Park autoload
    await self.park_autoload()
    return resp

  async def load_carrier(
    self,
    carrier: Carrier,
    barcode_reading: bool = False,
    barcode_reading_direction: Literal["horizontal", "vertical"] = "horizontal",
    barcode_symbology: Literal[
      "ISBT Standard",
      "Code 128 (Subset B and C)",
      "Code 39",
      "Codebar",
      "Code 2of5 Interleaved",
      "UPC A/E",
      "YESN/EAN 8",
      "Code 93",
    ] = "Code 128 (Subset B and C)",
    no_container_per_carrier: int = 5,
    park_autoload_after: bool = True,
  ):
    """
    Use autoload to load carrier.

    Args:
      carrier: Carrier to load
      barcode_reading: Whether to read barcodes. Default False.
      barcode_reading_direction: Barcode reading direction. Either "vertical" or "horizontal",
        default "horizontal".
      barcode_symbology: Barcode symbology. Default "Code 128 (Subset B and C)".
      no_container_per_carrier: Number of containers per carrier. Default 5.
      park_autoload_after: Whether to park autoload after loading. Default True.
    """

    barcode_reading_direction_dict = {
      "vertical": "0",
      "horizontal": "1",
    }
    barcode_symbology_dict = {
      "ISBT Standard": "70",
      "Code 128 (Subset B and C)": "71",
      "Code 39": "72",
      "Codebar": "73",
      "Code 2of5 Interleaved": "74",
      "UPC A/E": "75",
      "YESN/EAN 8": "76",
      "Code 93": "",
    }
    # Identify carrier end rail
    track_width = 22.5
    carrier_width = carrier.get_absolute_location().x - 100 + carrier.get_absolute_size_x()
    carrier_end_rail = int(carrier_width / track_width)
    assert 1 <= carrier_end_rail <= 54, "carrier loading rail must be between 1 and 54"

    # Determine presence of carrier at defined position
    presence_check = await self.request_single_carrier_presence(carrier_end_rail)
    carrier_end_rail_str = str(carrier_end_rail).zfill(2)

    if presence_check != 1:
      raise ValueError(
        f"""No carrier found at position {carrier_end_rail},
                       have you placed the carrier onto the correct autoload tray position?"""
      )

    # Set carrier type for identification purposes
    await self.send_command(module="C0", command="CI", cp=carrier_end_rail_str)

    # Load carrier
    # with barcoding
    if barcode_reading:
      # Choose barcode symbology
      await self.send_command(
        module="C0",
        command="CB",
        bt=barcode_symbology_dict[barcode_symbology],
      )
      # Load and read out barcodes
      resp = await self.send_command(
        module="C0",
        command="CL",
        bd=barcode_reading_direction_dict[barcode_reading_direction],
        bp="0616",  # Barcode reading direction (0 = vertical 1 = horizontal)
        co="0960",  # Distance between containers (pattern) [0.1 mm]
        cf="380",  # Width of reading window [0.1 mm]
        cv="1281",  # Carrier reading speed [0.1 mm]/s
        cn=str(no_container_per_carrier).zfill(2),  # No of containers (cups, plates) in a carrier
      )
    else:  # without barcoding
      resp = await self.send_command(module="C0", command="CL", cn="00")

    if park_autoload_after:
      await self.park_autoload()
    return resp

  async def set_loading_indicators(self, bit_pattern: List[bool], blink_pattern: List[bool]):
    """Set loading indicators (LEDs)

    The docs here are a little weird because 2^54 < 7FFFFFFFFFFFFF.

    Args:
      bit_pattern: On if True, off otherwise
      blink_pattern: Blinking if True, steady otherwise
    """

    assert len(bit_pattern) == 54, "bit pattern must be length 54"
    assert len(blink_pattern) == 54, "bit pattern must be length 54"

    def pattern2hex(pattern: List[bool]) -> str:
      bit_string = "".join(["1" if x else "0" for x in pattern])
      return hex(int(bit_string, base=2))[2:].upper().zfill(14)

    bit_pattern_hex = pattern2hex(bit_pattern)
    blink_pattern_hex = pattern2hex(blink_pattern)

    return await self.send_command(
      module="C0",
      command="CP",
      cl=bit_pattern_hex,
      cb=blink_pattern_hex,
    )

  # TODO:(command:CS) Check for presence of carriers on loading tray

  async def set_barcode_type(
    self,
    ISBT_Standard: bool = True,
    code128: bool = True,
    code39: bool = True,
    codebar: bool = True,
    code2_5: bool = True,
    UPC_AE: bool = True,
    EAN8: bool = True,
  ):
    """Set bar code type: which types of barcodes will be scanned for.

    Args:
      ISBT_Standard: ISBT_Standard. Default True.
      code128: Code128. Default True.
      code39: Code39. Default True.
      codebar: Codebar. Default True.
      code2_5: Code2_5. Default True.
      UPC_AE: UPC_AE. Default True.
      EAN8: EAN8. Default True.
    """

    # Encode values into bit pattern. Last bit is always one.
    bt = ""
    for t in [
      ISBT_Standard,
      code128,
      code39,
      codebar,
      code2_5,
      UPC_AE,
      EAN8,
      True,
    ]:
      bt += "1" if t else "0"

    # Convert bit pattern to hex.
    bt_hex = hex(int(bt, base=2))

    return await self.send_command(module="C0", command="CB", bt=bt_hex)

  # TODO:(command:CW) Unload carrier finally

  async def set_carrier_monitoring(self, should_monitor: bool = False):
    """Set carrier monitoring

    Args:
      should_monitor: whether carrier should be monitored.

    Returns:
      True if present, False otherwise
    """

    return await self.send_command(module="C0", command="CU", cu=should_monitor)

  # TODO:(command:CN) Take out the carrier to identification position

  # -------------- 3.13.3 Auto load query --------------

  # TODO:(command:RC) Query presence of carrier on deck

  async def request_auto_load_slot_position(self):
    """Request auto load slot position.

    Returns:
      slot position (0..54)
    """

    return await self.send_command(module="C0", command="QA", fmt="qa##")

  # TODO:(command:CQ) Request auto load module type

  # -------------- 3.14 G1-3/ CR Needle Washer commands --------------

  # TODO: All needle washer commands

  # TODO:(command:WI)
  # TODO:(command:WI)
  # TODO:(command:WS)
  # TODO:(command:WW)
  # TODO:(command:WR)
  # TODO:(command:WC)
  # TODO:(command:QF)

  # -------------- 3.15 Pump unit commands --------------

  async def request_pump_settings(self, pump_station: int = 1):
    """Set carrier monitoring

    Args:
      carrier_position: pump station number (1..3)

    Returns:
      0 = CoRe 96 wash station (single chamber)
      1 = DC wash station (single chamber rev 02 ) 2 = ReReRe (single chamber)
      3 = CoRe 96 wash station (dual chamber)
      4 = DC wash station (dual chamber)
      5 = ReReRe (dual chamber)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(module="C0", command="ET", fmt="et#", ep=pump_station)

  # -------------- 3.15.1 DC Wash commands (only for revision up to 01) --------------

  # TODO:(command:FA) Start DC wash procedure
  # TODO:(command:FB) Stop DC wash procedure
  # TODO:(command:FP) Prime DC wash station

  # -------------- 3.15.2 Single chamber pump unit only --------------

  # TODO:(command:EW) Start circulation (single chamber only)
  # TODO:(command:EC) Check circulation (single chamber only)
  # TODO:(command:ES) Stop circulation (single chamber only)
  # TODO:(command:EF) Prime (single chamber only)
  # TODO:(command:EE) Drain & refill (single chamber only)
  # TODO:(command:EB) Fill (single chamber only)
  # TODO:(command:QE) Request single chamber pump station prime status

  # -------------- 3.15.3 Dual chamber pump unit only --------------

  async def initialize_dual_pump_station_valves(self, pump_station: int = 1):
    """Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(module="C0", command="EJ", ep=pump_station)

  async def fill_selected_dual_chamber(
    self,
    pump_station: int = 1,
    drain_before_refill: bool = False,
    wash_fluid: int = 1,
    chamber: int = 2,
    waste_chamber_suck_time_after_sensor_change: int = 0,
  ):
    """Initialize pump station valves (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
      drain_before_refill: drain chamber before refill. Default False.
      wash_fluid: wash fluid (1 or 2)
      chamber: chamber (1 or 2)
      drain_before_refill: waste chamber suck time after sensor change [s] (for error handling only)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"
    assert 1 <= wash_fluid <= 2, "wash_fluid must be between 1 and 2"
    assert 1 <= chamber <= 2, "chamber must be between 1 and 2"

    # wash fluid <-> chamber connection
    # 0 = wash fluid 1 <-> chamber 2
    # 1 = wash fluid 1 <-> chamber 1
    # 2 = wash fluid 2 <-> chamber 1
    # 3 = wash fluid 2 <-> chamber 2
    connection = {(1, 2): 0, (1, 1): 1, (2, 1): 2, (2, 2): 3}[wash_fluid, chamber]

    return await self.send_command(
      module="C0",
      command="EH",
      ep=pump_station,
      ed=drain_before_refill,
      ek=connection,
      eu=f"{waste_chamber_suck_time_after_sensor_change:02}",
      wait=False,
    )

  # TODO:(command:EK) Drain selected chamber

  async def drain_dual_chamber_system(self, pump_station: int = 1):
    """Drain system (dual chamber only)

    Args:
      carrier_position: pump station number (1..3)
    """

    assert 1 <= pump_station <= 3, "pump_station must be between 1 and 3"

    return await self.send_command(module="C0", command="EL", ep=pump_station)

  # TODO:(command:QD) Request dual chamber pump station prime status

  # -------------- 3.16 Incubator commands --------------

  # TODO: all incubator commands
  # TODO:(command:HC)
  # TODO:(command:HI)
  # TODO:(command:HF)
  # TODO:(command:RP)

  # -------------- 3.17 iSWAP commands --------------

  # -------------- 3.17.1 Pre & Initialization commands --------------

  async def initialize_iswap(self):
    """Initialize iSWAP (for standalone configuration only)"""

    return await self.send_command(module="C0", command="FI")

  async def initialize_autoload(self):
    """Initialize autoload (for standalone configuration only)"""

    return await self.send_command(module="C0", command="II")

  async def position_components_for_free_iswap_y_range(self):
    """Position all components so that there is maximum free Y range for iSWAP"""

    return await self.send_command(module="C0", command="FY")

  async def move_iswap_x_relative(self, step_size: float, allow_splitting: bool = False):
    """
    Args:
      step_size: X Step size [1mm] Between -99.9 and 99.9 if allow_splitting is False.
      allow_splitting: Allow splitting of the movement into multiple steps. Default False.
    """

    direction = 0 if step_size >= 0 else 1
    max_step_size = 99.9
    if abs(step_size) > max_step_size:
      if not allow_splitting:
        raise ValueError("step_size must be less than 99.9")
      await self.move_iswap_x_relative(
        step_size=max_step_size if step_size > 0 else -max_step_size, allow_splitting=True
      )
      remaining_steps = step_size - max_step_size if step_size > 0 else step_size + max_step_size
      return await self.move_iswap_x_relative(remaining_steps, allow_splitting)

    return await self.send_command(
      module="C0", command="GX", gx=str(round(abs(step_size) * 10)).zfill(3), xd=direction
    )

  async def move_iswap_y_relative(self, step_size: float, allow_splitting: bool = False):
    """
    Args:
      step_size: Y Step size [1mm] Between -99.9 and 99.9 if allow_splitting is False.
      allow_splitting: Allow splitting of the movement into multiple steps. Default False.
    """

    direction = 0 if step_size >= 0 else 1
    max_step_size = 99.9
    if abs(step_size) > max_step_size:
      if not allow_splitting:
        raise ValueError("step_size must be less than 99.9")
      await self.move_iswap_y_relative(
        step_size=max_step_size if step_size > 0 else -max_step_size, allow_splitting=True
      )
      remaining_steps = step_size - max_step_size if step_size > 0 else step_size + max_step_size
      return await self.move_iswap_y_relative(remaining_steps, allow_splitting)

    return await self.send_command(
      module="C0", command="GY", gy=str(round(abs(step_size) * 10)).zfill(3), yd=direction
    )

  async def move_iswap_z_relative(self, step_size: float, allow_splitting: bool = False):
    """
    Args:
      step_size: Z Step size [1mm] Between -99.9 and 99.9 if allow_splitting is False.
      allow_splitting: Allow splitting of the movement into multiple steps. Default False.
    """

    direction = 0 if step_size >= 0 else 1
    max_step_size = 99.9
    if abs(step_size) > max_step_size:
      if not allow_splitting:
        raise ValueError("step_size must be less than 99.9")
      await self.move_iswap_z_relative(
        step_size=max_step_size if step_size > 0 else -max_step_size, allow_splitting=True
      )
      remaining_steps = step_size - max_step_size if step_size > 0 else step_size + max_step_size
      return await self.move_iswap_z_relative(remaining_steps, allow_splitting)

    return await self.send_command(
      module="C0", command="GZ", gz=str(round(abs(step_size) * 10)).zfill(3), zd=direction
    )

  async def open_not_initialized_gripper(self):
    return await self.send_command(module="C0", command="GI")

  async def iswap_open_gripper(self, open_position: Optional[int] = None):
    """Open gripper

    Args:
      open_position: Open position [0.1mm] (0.1 mm = 16 increments) The gripper moves to pos + 20.
                     Must be between 0 and 9999. Default 1320 for iSWAP 4.0 (landscape). Default to
                     910 for iSWAP 3 (portrait).
    """

    if open_position is None:
      open_position = 910 if (await self.get_iswap_version()).startswith("3") else 1320

    assert 0 <= open_position <= 9999, "open_position must be between 0 and 9999"

    return await self.send_command(module="C0", command="GF", go=f"{open_position:04}")

  async def iswap_close_gripper(
    self,
    grip_strength: int = 5,
    plate_width: int = 0,
    plate_width_tolerance: int = 0,
  ):
    """Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      grip_strength: Grip strength. 0 = low . 9 = high. Default 5.
      plate_width: Plate width [0.1mm]
                   (gb should be > min. Pos. + stop ramp + gt -> gb > 760 + 5 + g )
      plate_width_tolerance: Plate width tolerance [0.1mm]. Must be between 0 and 99. Default 20.
    """

    return await self.send_command(
      module="C0",
      command="GC",
      gw=grip_strength,
      gb=plate_width,
      gt=plate_width_tolerance,
    )

  # -------------- 3.17.2 Stack handling commands CP --------------

  async def park_iswap(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 2840,
  ):
    """Close gripper

    The gripper should be at the position gb+gt+20 before sending this command.

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning
                of a command [0.1mm]. Must be between 0 and 3600. Default 3600.
    """

    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    command_output = await self.send_command(
      module="C0",
      command="PG",
      th=minimum_traverse_height_at_beginning_of_a_command,
    )

    # Once the command has completed successfully, set _iswap_parked to True
    self._iswap_parked = True
    return command_output

  async def iswap_get_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    grip_strength: int = 5,
    open_gripper_position: int = 860,
    plate_width: int = 860,
    plate_width_tolerance: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
    fold_up_sequence_at_the_end_of_process: bool = True,
  ):
    """Get plate using iswap.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
            4 =negative X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
            a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0
            and 3600. Default 3600.
      grip_strength: Grip strength 0 = low .. 9 = high. Must be between 1 and 9. Default 5.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999.
            Default 860.
      plate_width: plate width [0.1mm]. Must be between 0 and 9999. Default 860.
      plate_width_tolerance: plate width tolerance [0.1mm]. Must be between 0 and 99. Default 860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert (
      0 <= z_position_at_the_command_end <= 3600
    ), "z_position_at_the_command_end must be between 0 and 3600"
    assert 1 <= grip_strength <= 9, "grip_strength must be between 1 and 9"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= plate_width <= 9999, "plate_width must be between 0 and 9999"
    assert 0 <= plate_width_tolerance <= 99, "plate_width_tolerance must be between 0 and 99"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert (
      0 <= acceleration_index_high_acc <= 4
    ), "acceleration_index_high_acc must be between 0 and 4"
    assert (
      0 <= acceleration_index_low_acc <= 4
    ), "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PP",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      gb=f"{plate_width:04}",
      gt=f"{plate_width_tolerance:02}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
      gc=fold_up_sequence_at_the_end_of_process,
    )

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def iswap_put_plate(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    z_position_at_the_command_end: int = 3600,
    open_gripper_position: int = 860,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """put plate

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      z_position_at_the_command_end: Z-Position at the command end [0.1mm]. Must be between 0 and
            3600. Default 3600.
      open_gripper_position: Open gripper position [0.1mm]. Must be between 0 and 9999. Default
            860.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4.
            Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4.
            Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert (
      0 <= z_position_at_the_command_end <= 3600
    ), "z_position_at_the_command_end must be between 0 and 3600"
    assert 0 <= open_gripper_position <= 9999, "open_gripper_position must be between 0 and 9999"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert (
      0 <= acceleration_index_high_acc <= 4
    ), "acceleration_index_high_acc must be between 0 and 4"
    assert (
      0 <= acceleration_index_low_acc <= 4
    ), "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PR",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      te=f"{z_position_at_the_command_end:04}",
      gr=grip_direction,
      go=f"{open_gripper_position:04}",
      ga=collision_control_level,
      # xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}"
    )

    # Once the command has completed successfully, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def iswap_rotate(
    self,
    position: int = 33,
    gripper_velocity: int = 55_000,
    gripper_acceleration: int = 170,
    gripper_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
    wrist_velocity: int = 48_000,
    wrist_acceleration: int = 145,
    wrist_protection: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 5,
  ):
    """
    Rotate the iswap to a predifined position.
    Velocity units are "incr/sec"
    Acceleration units are "1_000 incr/sec**2"
    For a list of the possible positions see the pylabrobot documentation on the R0 module.
    """
    assert 20 <= gripper_velocity <= 75_000
    assert 5 <= gripper_acceleration <= 200
    assert 20 <= wrist_velocity <= 65_000
    assert 20 <= wrist_acceleration <= 200

    return await self.send_command(
      module="R0",
      command="PD",
      pd=position,
      wv=f"{gripper_velocity:05}",
      wr=f"{gripper_acceleration:03}",
      ww=gripper_protection,
      tv=f"{wrist_velocity:05}",
      tr=f"{wrist_acceleration:03}",
      tw=wrist_protection,
    )

  async def move_plate_to_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """Move plate to position.

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y, 4 = negative
            X. Must be between 1 and 4. Default 1.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
            command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
            Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index low acc. Must be between 0 and 4. Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert (
      0 <= acceleration_index_high_acc <= 4
    ), "acceleration_index_high_acc must be between 0 and 4"
    assert (
      0 <= acceleration_index_low_acc <= 4
    ), "acceleration_index_low_acc must be between 0 and 4"

    command_output = await self.send_command(
      module="C0",
      command="PM",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
    )
    # Once the command has completed successfuly, set _iswap_parked to false
    self._iswap_parked = False
    return command_output

  async def collapse_gripper_arm(
    self,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    fold_up_sequence_at_the_end_of_process: bool = True,
  ):
    """Collapse gripper arm

    Args:
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of a
                                                         command 0.1mm]. Must be between 0 and 3600.
                                                         Default 3600.
      fold_up_sequence_at_the_end_of_process: fold up sequence at the end of process. Default True.
    """

    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"

    return await self.send_command(
      module="C0",
      command="PN",
      th=minimum_traverse_height_at_beginning_of_a_command,
      gc=fold_up_sequence_at_the_end_of_process,
    )

  # -------------- 3.17.3 Hotel handling commands --------------

  # implemented in UnSafe class

  # -------------- 3.17.4 Barcode commands --------------

  # TODO:(command:PB) Read barcode using iSWAP

  # -------------- 3.17.5 Teach in commands --------------

  async def prepare_iswap_teaching(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    minimum_traverse_height_at_beginning_of_a_command: int = 3600,
    collision_control_level: int = 1,
    acceleration_index_high_acc: int = 4,
    acceleration_index_low_acc: int = 1,
  ):
    """Prepare iSWAP teaching

    Prepare for teaching with iSWAP

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 1300.
      minimum_traverse_height_at_beginning_of_a_command: Minimum traverse height at beginning of
        a command 0.1mm]. Must be between 0 and 3600. Default 3600.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
        Default 1.
      acceleration_index_high_acc: acceleration index high acc. Must be between 0 and 4. Default 4.
      acceleration_index_low_acc: acceleration index high acc. Must be between 0 and 4. Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 0 <= location <= 1, "location must be between 0 and 1"
    assert 0 <= hotel_depth <= 3000, "hotel_depth must be between 0 and 3000"
    assert (
      0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
    ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"
    assert (
      0 <= acceleration_index_high_acc <= 4
    ), "acceleration_index_high_acc must be between 0 and 4"
    assert (
      0 <= acceleration_index_low_acc <= 4
    ), "acceleration_index_low_acc must be between 0 and 4"

    return await self.send_command(
      module="C0",
      command="PT",
      xs=f"{x_position:05}",
      xd=x_direction,
      yj=f"{y_position:04}",
      yd=y_direction,
      zj=f"{z_position:04}",
      zd=z_direction,
      hh=location,
      hd=f"{hotel_depth:04}",
      gr=grip_direction,
      th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
      ga=collision_control_level,
      xe=f"{acceleration_index_high_acc} {acceleration_index_low_acc}",
    )

  async def get_logic_iswap_position(
    self,
    x_position: int = 0,
    x_direction: int = 0,
    y_position: int = 0,
    y_direction: int = 0,
    z_position: int = 0,
    z_direction: int = 0,
    location: int = 0,
    hotel_depth: int = 1300,
    grip_direction: int = 1,
    collision_control_level: int = 1,
  ):
    """Get logic iSWAP position

    Args:
      x_position: Plate center in X direction  [0.1mm]. Must be between 0 and 30000. Default 0.
      x_direction: X-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      y_position: Plate center in Y direction [0.1mm]. Must be between 0 and 6500. Default 0.
      y_direction: Y-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      z_position: Plate gripping height in Z direction. Must be between 0 and 3600. Default 0.
      z_direction: Z-direction. 0 = positive 1 = negative. Must be between 0 and 1. Default 0.
      location: location. 0 = Stack 1 = Hotel. Must be between 0 and 1. Default 0.
      hotel_depth: Hotel depth [0.1mm]. Must be between 0 and 3000. Default 1300.
      grip_direction: Grip direction. 1 = negative Y, 2 = positive X, 3 = positive Y,
                      4 = negative X. Must be between 1 and 4. Default 1.
      collision_control_level: collision control level 1 = high 0 = low. Must be between 0 and 1.
                               Default 1.
    """

    assert 0 <= x_position <= 30000, "x_position must be between 0 and 30000"
    assert 0 <= x_direction <= 1, "x_direction must be between 0 and 1"
    assert 0 <= y_position <= 6500, "y_position must be between 0 and 6500"
    assert 0 <= y_direction <= 1, "y_direction must be between 0 and 1"
    assert 0 <= z_position <= 3600, "z_position must be between 0 and 3600"
    assert 0 <= z_direction <= 1, "z_direction must be between 0 and 1"
    assert 0 <= location <= 1, "location must be between 0 and 1"
    assert 0 <= hotel_depth <= 3000, "hotel_depth must be between 0 and 3000"
    assert 1 <= grip_direction <= 4, "grip_direction must be between 1 and 4"
    assert 0 <= collision_control_level <= 1, "collision_control_level must be between 0 and 1"

    return await self.send_command(
      module="C0",
      command="PC",
      xs=x_position,
      xd=x_direction,
      yj=y_position,
      yd=y_direction,
      zj=z_position,
      zd=z_direction,
      hh=location,
      hd=hotel_depth,
      gr=grip_direction,
      ga=collision_control_level,
    )

  # -------------- 3.17.6 iSWAP query --------------

  async def request_iswap_in_parking_position(self):
    """Request iSWAP in parking position

    Returns:
      0 = gripper is not in parking position
      1 = gripper is in parking position
    """

    return await self.send_command(module="C0", command="RG", fmt="rg#")

  async def request_plate_in_iswap(self) -> bool:
    """Request plate in iSWAP

    Returns:
      True if holding a plate, False otherwise.
    """

    resp = await self.send_command(module="C0", command="QP", fmt="ph#")
    return resp is not None and resp["ph"] == 1

  async def request_iswap_position(self):
    """Request iSWAP position ( grip center )

    Returns:
      xs: Hotel center in X direction [1mm]
      xd: X direction 0 = positive 1 = negative
      yj: Gripper center in Y direction [1mm]
      yd: Y direction 0 = positive 1 = negative
      zj: Gripper Z height (gripping height) [1mm]
      zd: Z direction 0 = positive 1 = negative
    """

    resp = await self.send_command(module="C0", command="QG", fmt="xs#####xd#yj####yd#zj####zd#")
    resp["xs"] = resp["xs"] / 10
    resp["yj"] = resp["yj"] / 10
    resp["zj"] = resp["zj"] / 10
    return resp

  async def request_iswap_initialization_status(self) -> bool:
    """Request iSWAP initialization status

    Returns:
      True if iSWAP is fully initialized
    """

    resp = await self.send_command(module="R0", command="QW", fmt="qw#")
    return cast(int, resp["qw"]) == 1

  async def request_iswap_version(self) -> str:
    """Firmware command for getting iswap version"""
    return cast(str, (await self.send_command("R0", "RF", fmt="rf" + "&" * 15))["rf"])

  # -------------- 3.18 Cover and port control --------------

  async def lock_cover(self):
    """Lock cover"""

    return await self.send_command(module="C0", command="CO")

  async def unlock_cover(self):
    """Unlock cover"""

    return await self.send_command(module="C0", command="HO")

  async def disable_cover_control(self):
    """Disable cover control"""

    return await self.send_command(module="C0", command="CD")

  async def enable_cover_control(self):
    """Enable cover control"""

    return await self.send_command(module="C0", command="CE")

  async def set_cover_output(self, output: int = 0):
    """Set cover output

    Args:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """

    assert 1 <= output <= 3, "output must be between 1 and 3"
    return await self.send_command(module="C0", command="OS", on=output)

  async def reset_output(self, output: int = 0):
    """Reset output

    Returns:
      output: 1 = cover lock; 2 = reserve out; 3 = reserve out.
    """

    assert 1 <= output <= 3, "output must be between 1 and 3"
    return await self.send_command(module="C0", command="QS", on=output, fmt="#")

  async def request_cover_open(self) -> bool:
    """Request cover open

    Returns: True if the cover is open
    """

    resp = await self.send_command(module="C0", command="QC", fmt="qc#")
    return bool(resp["qc"])

  # -------------- 4.0 Direct Device Integration --------------
  # Communication occurs directly through STAR "TCC" connections,
  # i.e. firmware commands. This means devices can be seen as part
  # of the STAR machine directly (if number of devices =< 2).

  # -------------- 4.1 Hamilton Heater Shaker (HHS) --------------

  async def check_type_is_hhs(self, device_number: int):
    """
    Convenience method to check that connected device is an HHS.
    Executed through firmware query
    """

    firmware_version = await self.send_command(module=f"T{device_number}", command="RF")
    if "Heater Shaker" not in firmware_version:
      raise ValueError(
        f"Device number {device_number} does not connect to a Hamilton"
        f" Heater Shaker, found {firmware_version} instead."
        f"Have you called the wrong device number?"
      )

  async def initialize_hhs(self, device_number: int) -> str:
    """Initialize Hamilton Heater Shaker (HHS) at specified TCC port

    Args:
      device_number: TCC connect number to the HHS

    Returns:
      Information string about the initialization status
    """

    module_pointer = f"T{device_number}"

    # Request module configuration
    try:
      await self.send_command(module=module_pointer, command="QU")
    except TimeoutError as exc:
      error_message = (
        f"No Hamilton Heater Shaker found at device_number {device_number}"
        f", have you checked your connections? Original error: {exc}"
      )
      raise ValueError(error_message) from exc

    await self.check_type_is_hhs(device_number)

    # Request module configuration
    hhs_init_status = await self.send_command(module=module_pointer, command="QW", fmt="qw#")
    hhs_init_status = hhs_init_status["qw"]

    # Initializing HHS if necessary
    info = "HHS already initialized"
    if hhs_init_status != 1:
      await self.send_command(module=module_pointer, command="LI")
      info = f"HHS at device number {device_number} initialized."

    return info

  # -------------- 4.1.1 HHS Plate Lock --------------

  async def open_plate_lock(self, device_number: int):
    """Open HHS plate lock"""

    await self.check_type_is_hhs(device_number)

    return await self.send_command(
      module=f"T{device_number}",
      command="LP",
      lp="0",  # => open plate lock
    )

  async def close_plate_lock(self, device_number: int):
    """Close HHS plate lock"""

    await self.check_type_is_hhs(device_number)

    return await self.send_command(
      module=f"T{device_number}",
      command="LP",
      lp="1",  # => close plate lock
    )

  # -------------- 4.1.2 HHS Shaking --------------
  async def start_shaking_at_hhs(
    self,
    device_number: int,
    rpm: int,
    rotation: int = 0,
    plate_locked_during_shaking: bool = True,
  ):
    """Start shaking of specified HHS

    Args:
      rpm: round per minute
      rotation: 0: clockwise rotation, 1: counter-clockwise rotation
      plate_locked_during_shaking: True if plate is locked during shaking
    """

    await self.check_type_is_hhs(device_number)

    # Ensure plate is locked before shaking starts
    # allow over-writing of default (perhaps special holder system)
    if plate_locked_during_shaking:
      await self.close_plate_lock(device_number)

    return await self.send_command(
      module=f"T{device_number}",
      command="SB",
      st=str(rotation),
      sv=str(rpm).zfill(4),
      sr="00500",  # ??? maybe shakingAccRamp rate?
    )

  async def stop_shaking_at_hhs(self, device_number: int):
    """Close HHS plate lock"""

    await self.check_type_is_hhs(device_number)

    return await self.send_command(module="T1", command="SC")

  # -------------- 4.1.3 HHS Heating/Temperature Control --------------

  async def start_temperature_control_at_hhs(
    self,
    device_number: int,
    temp: Union[float, int],
  ):
    """Start temperature regulation of specified HHS"""

    await self.check_type_is_hhs(device_number)
    assert 0 < temp <= 105

    # Ensure proper temperature input handling
    if isinstance(temp, (float, int)):
      safe_temp_str = f"{int(temp * 10):04d}"
    else:
      safe_temp_str = str(temp)

    return await self.send_command(
      module=f"T{device_number}",
      command="TA",  # temperature adjustment
      ta=safe_temp_str,
    )

  async def get_temperature_at_hhs(self, device_number: int) -> dict:
    """Query current temperatures of both sensors of specified HHS

    Returns:
      Dictionary with keys "middle_T" and "edge_T" for the middle and edge temperature
    """

    await self.check_type_is_hhs(device_number)

    request_temperature = await self.send_command(module=f"T{device_number}", command="RT")
    processed_t_info = [int(x) / 10 for x in request_temperature.split("+")[-2:]]

    return {
      "middle_T": processed_t_info[0],
      "edge_T": processed_t_info[-1],
    }

  async def stop_temperature_control_at_hhs(self, device_number: int):
    """Stop temperature regulation of specified HHS"""

    await self.check_type_is_hhs(device_number)

    return await self.send_command(module=f"T{device_number}", command="TO")

  # -------------- 4.2 Hamilton Heater Cooler (HHS) --------------

  async def check_type_is_hhc(self, device_number: int):
    """
    Convenience method to check that connected device is an HHC.
    Executed through firmware query
    """

    firmware_version = await self.send_command(module=f"T{device_number}", command="RF")
    if "Hamilton Heater Cooler" not in firmware_version:
      raise ValueError(
        f"Device number {device_number} does not connect to a Hamilton"
        f" Heater-Cooler, found {firmware_version} instead."
        f"Have you called the wrong device number?"
      )

  async def initialize_hhc(self, device_number: int) -> str:
    """Initialize Hamilton Heater Cooler (HHC) at specified TCC port

    Args:
      device_number: TCC connect number to the HHC
    """

    module_pointer = f"T{device_number}"

    # Request module configuration
    try:
      await self.send_command(module=module_pointer, command="QU")
    except TimeoutError as exc:
      error_message = (
        f"No Hamilton Heater Cooler found at device_number {device_number}"
        f", have you checked your connections? Original error: {exc}"
      )
      raise ValueError(error_message) from exc

    await self.check_type_is_hhc(device_number)

    # Request module configuration
    hhc_init_status = await self.send_command(module=module_pointer, command="QW", fmt="qw#")
    hhc_init_status = hhc_init_status["qw"]

    info = "HHC already initialized"
    # Initializing HHS if necessary
    if hhc_init_status != 1:
      # Initialize device
      await self.send_command(module=module_pointer, command="LI")
      info = f"HHS at device number {device_number} initialized."

    return info

  async def start_temperature_control_at_hhc(
    self,
    device_number: int,
    temp: Union[float, int],
  ):
    """Start temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)
    assert 0 < temp <= 105

    # Ensure proper temperature input handling
    if isinstance(temp, (float, int)):
      safe_temp_str = f"{round(temp * 10):04d}"
    else:
      safe_temp_str = str(temp)

    return await self.send_command(
      module=f"T{device_number}",
      command="TA",  # temperature adjustment
      ta=safe_temp_str,
      tb="1800",  # TODO: identify precise purpose?
      tc="0020",  # TODO: identify precise purpose?
    )

  async def get_temperature_at_hhc(self, device_number: int) -> dict:
    """Query current temperatures of both sensors of specified HHC"""

    await self.check_type_is_hhc(device_number)

    request_temperature = await self.send_command(module=f"T{device_number}", command="RT")
    processed_t_info = [int(x) / 10 for x in request_temperature.split("+")[-2:]]

    return {
      "middle_T": processed_t_info[0],
      "edge_T": processed_t_info[-1],
    }

  async def query_whether_temperature_reached_at_hhc(self, device_number: int):
    """Stop temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)
    query_current_control_status = await self.send_command(
      module=f"T{device_number}", command="QD", fmt="qd#"
    )

    return query_current_control_status["qd"] == 0

  async def stop_temperature_control_at_hhc(self, device_number: int):
    """Stop temperature regulation of specified HHC"""

    await self.check_type_is_hhc(device_number)

    return await self.send_command(module=f"T{device_number}", command="TO")

  # -------------- Extra - Probing labware with STAR - making STAR into a CMM --------------

  y_drive_mm_per_increment = 0.046302082
  z_drive_mm_per_increment = 0.01072765

  @staticmethod
  def mm_to_y_drive_increment(value_mm: float) -> int:
    return round(value_mm / STAR.y_drive_mm_per_increment)

  @staticmethod
  def y_drive_increment_to_mm(value_mm: int) -> float:
    return round(value_mm * STAR.y_drive_mm_per_increment, 2)

  @staticmethod
  def mm_to_z_drive_increment(value_mm: float) -> int:
    return round(value_mm / STAR.z_drive_mm_per_increment)

  @staticmethod
  def z_drive_increment_to_mm(value_increments: int) -> float:
    return round(value_increments * STAR.z_drive_mm_per_increment, 2)

  async def clld_probe_z_height_using_channel(
    self,
    channel_idx: int,  # 0-based indexing of channels!
    lowest_immers_pos: float = 99.98,  # mm
    start_pos_search: float = 330.0,  # mm
    channel_speed: float = 10.0,  # mm
    channel_acceleration: float = 800.0,  # mm/sec**2
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,  # mm
    move_channels_to_save_pos_after: bool = False,
  ) -> float:
    """Probes the Z-height below the specified channel on a Hamilton STAR liquid handling machine
    using the channels 'capacitive Liquid Level Detection' (cLLD) capabilities.
    N.B.: this means only conductive materials can be probed!

    Args:
      channel_idx: The index of the channel to use for probing. Backmost channel = 0.
      lowest_immers_pos: The lowest immersion position in mm.
      start_pos_lld_search: The start position for z-touch search in mm.
      channel_speed: The speed of channel movement in mm/sec.
      channel_acceleration: The acceleration of the channel in mm/sec**2.
      detection_edge: The edge steepness at capacitive LLD detection.
      detection_drop: The offset after capacitive LLD edge detection.
      post_detection_trajectory (0, 1): Movement of the channel up (1) or down (0) after
        contacting the surface.
      post_detection_dist: Distance to move into the trajectory after detection in mm.
      move_channels_to_save_pos_after: Flag to move channels to a safe position after
       operation.

    Returns:
      The detected Z-height in mm.
    """

    lowest_immers_pos_increments = STAR.mm_to_z_drive_increment(lowest_immers_pos)
    start_pos_search_increments = STAR.mm_to_z_drive_increment(start_pos_search)
    channel_speed_increments = STAR.mm_to_z_drive_increment(channel_speed)
    channel_acceleration_thousand_increments = STAR.mm_to_z_drive_increment(
      channel_acceleration / 1000
    )
    post_detection_dist_increments = STAR.mm_to_z_drive_increment(post_detection_dist)

    assert 9_320 <= lowest_immers_pos_increments <= 31_200, (
      f"Lowest immersion position must be between \n{STAR.z_drive_increment_to_mm(9_320)}"
      + f" and {STAR.z_drive_increment_to_mm(31_200)} mm, is {lowest_immers_pos} mm"
    )
    assert 9_320 <= start_pos_search_increments <= 31_200, (
      f"Start position of LLD search must be between \n{STAR.z_drive_increment_to_mm(9_320)}"
      + f" and {STAR.z_drive_increment_to_mm(31_200)} mm, is {start_pos_search} mm"
    )
    assert 20 <= channel_speed_increments <= 15_000, (
      f"LLD search speed must be between \n{STAR.z_drive_increment_to_mm(20)}"
      + f"and {STAR.z_drive_increment_to_mm(15_000)} mm/sec, is {channel_speed} mm/sec"
    )
    assert 5 <= channel_acceleration_thousand_increments <= 150, (
      f"Channel acceleration must be between \n{STAR.z_drive_increment_to_mm(5*1_000)} "
      + f" and {STAR.z_drive_increment_to_mm(150*1_000)} mm/sec**2, is {channel_acceleration} mm/sec**2"
    )
    assert (
      0 <= detection_edge <= 1_023
    ), "Edge steepness at capacitive LLD detection must be between 0 and 1023"
    assert (
      0 <= detection_drop <= 1_023
    ), "Offset after capacitive LLD edge detection must be between 0 and 1023"
    assert 0 <= post_detection_dist_increments <= 9_999, (
      "Post cLLD-detection movement distance must be between \n0"
      + f" and {STAR.z_drive_increment_to_mm(9_999)} mm, is {post_detection_dist} mm"
    )

    lowest_immers_pos_str = f"{lowest_immers_pos_increments:05}"
    start_pos_search_str = f"{start_pos_search_increments:05}"
    channel_speed_str = f"{channel_speed_increments:05}"
    channel_acc_str = f"{channel_acceleration_thousand_increments:03}"
    detection_edge_str = f"{detection_edge:04}"
    detection_drop_str = f"{detection_drop:04}"
    post_detection_dist_str = f"{post_detection_dist_increments:04}"

    await self.send_command(
      module=STAR.channel_id(channel_idx),
      command="ZL",
      zh=lowest_immers_pos_str,  # Lowest immersion position [increment]
      zc=start_pos_search_str,  # Start position of LLD search [increment]
      zl=channel_speed_str,  # Speed of channel movement
      zr=channel_acc_str,  # Acceleration [1000 increment/second^2]
      gt=detection_edge_str,  # Edge steepness at capacitive LLD detection
      gl=detection_drop_str,  # Offset after capacitive LLD edge detection
      zj=post_detection_trajectory,  # Movement of the channel after contacting surface
      zi=post_detection_dist_str,  # Distance to move up after detection [increment]
    )
    if move_channels_to_save_pos_after:
      await self.move_all_channels_in_z_safety()

    get_llds = await self.request_pip_height_last_lld()
    result_in_mm = float(get_llds["lh"][channel_idx] / 10)

    return result_in_mm

  async def request_tip_len_on_channel(
    self,
    channel_idx: int,  # 0-based indexing of channels!
  ) -> float:
    """
    Measures the length of the tip attached to the specified pipetting channel.
    Checks if a tip is present on the given channel. If present, moves all channels
    to THE safe Z position, 334.3 mm, measures the tip bottom Z-coordinate, and calculates
    the total tip length. Supports tips of lengths 50.4 mm, 59.9 mm, and 95.1 mm.
    Raises an error if the tip length is unsupported or if no tip is present.
    Parameters:
      channel_idx: Index of the pipetting channel (0-based).
    Returns:
      The measured tip length in millimeters.
    Raises:
      ValueError: If no tip is present on the channel or if the tip length is unsupported.
    """

    # Check there is a tip on the channel
    all_channel_occupancy = await self.request_tip_presence()
    if not all_channel_occupancy[channel_idx]:
      raise ValueError(f"No tip present on channel {channel_idx}")

    # Level all channels
    await self.move_all_channels_in_z_safety()
    known_top_position_channel_head = 334.3  # mm
    fitting_depth_of_all_standard_channel_tips = 8  # mm
    unknown_offset_for_all_tips = 0.4  # mm

    # Request z-coordinate of channel+tip bottom
    tip_bottom_z_coordinate = await self.request_z_pos_channel_n(
      pipetting_channel_index=channel_idx
    )

    total_tip_len = round(
      known_top_position_channel_head
      - (
        tip_bottom_z_coordinate
        - fitting_depth_of_all_standard_channel_tips
        - unknown_offset_for_all_tips
      ),
      1,
    )

    if total_tip_len in [50.4, 59.9, 95.1]:  # 50ul, 300ul, 1000ul
      return total_tip_len
    raise ValueError(f"Tip of length {total_tip_len} not yet supported")

  async def ztouch_probe_z_height_using_channel(
    self,
    channel_idx: int,  # 0-based indexing of channels!
    tip_len: Optional[float] = None,  # mm
    lowest_immers_pos: float = 99.98,  # mm
    start_pos_search: Optional[float] = None,  # mm
    channel_speed: float = 10.0,  # mm/sec
    channel_acceleration: float = 800.0,  # mm/sec**2
    channel_speed_upwards: float = 125.0,  # mm
    detection_limiter_in_PWM: int = 1,
    push_down_force_in_PWM: int = 0,
    post_detection_dist: float = 2.0,  # mm
    move_channels_to_save_pos_after: bool = False,
  ) -> float:
    """Probes the Z-height below the specified channel on a Hamilton STAR liquid handling machine
    using the channels 'z-touchoff' capabilities, i.e. a controlled triggering of the z-drive,
    aka a controlled 'crash'.

    Args:
      channel_idx: The index of the channel to use for probing. Backmost channel = 0.
      tip_len: override the tip length (of tip on channel `channel_idx`). Default is the tip length
        of the tip that was picked up.
      lowest_immers_pos: The lowest immersion position in mm.
      start_pos_lld_search: The start position for z-touch search in mm.
      channel_speed: The speed of channel movement in mm/sec.
      channel_acceleration: The acceleration of the channel in mm/sec**2.
      detection_limiter_in_PWM: Offset PWM limiter value for searching
      push_down_force_in_PWM: Offset PWM value for push down force.
        cf000 = No push down force, drive is switched off.
      post_detection_dist: Distance to move into the trajectory after detection in mm.
      move_channels_to_save_pos_after: Flag to move channels to a safe position after
        operation.

    Returns:
      The detected Z-height in mm.
    """

    version = await self.request_pip_channel_version(channel_idx)
    year_matches = re.search(r"\b\d{4}\b", version)
    if year_matches is not None:
      year = int(year_matches.group())
      if year < 2022:
        raise ValueError(
          "Z-touch probing is not supported for PIP versions predating 2022, "
          f"found version '{version}'"
        )

    if tip_len is None:
      # currently a bug, will be fixed in the future
      # reverted to previous implementation
      # tip_len = self.head[channel_idx].get_tip().total_tip_length
      tip_len = await self.request_tip_len_on_channel(channel_idx)

    # fitting_depth = 8 mm for 10, 50, 300, 1000 ul Hamilton tips
    # fitting_depth = self.head[channel_idx].get_tip().fitting_depth
    fitting_depth = 8  # mm, for 10, 50, 300, 1000 ul Hamilton tips

    if start_pos_search is None:
      start_pos_search = 334.7 - tip_len + fitting_depth

    tip_len_used_in_increments = (tip_len - fitting_depth) / STAR.z_drive_mm_per_increment
    channel_head_start_pos = (
      start_pos_search + tip_len - fitting_depth
    )  # start_pos of the head itself!
    safe_head_bottom_z_pos = (
      99.98 + tip_len - fitting_depth
    )  # 99.98 == STAR.z_drive_increment_to_mm(9_320)
    safe_head_top_z_pos = 334.7  # 334.7 == STAR.z_drive_increment_to_mm(31_200)

    lowest_immers_pos_increments = STAR.mm_to_z_drive_increment(lowest_immers_pos)
    start_pos_search_increments = STAR.mm_to_z_drive_increment(channel_head_start_pos)
    channel_speed_increments = STAR.mm_to_z_drive_increment(channel_speed)
    channel_acceleration_thousand_increments = STAR.mm_to_z_drive_increment(
      channel_acceleration / 1000
    )
    channel_speed_upwards_increments = STAR.mm_to_z_drive_increment(channel_speed_upwards)

    assert 0 <= channel_idx <= 15, f"channel_idx must be between 0 and 15, is {channel_idx}"
    assert 20 <= tip_len <= 120, "Total tip length must be between 20 and 120"

    assert 9320 <= lowest_immers_pos_increments <= 31_200, (
      "Lowest immersion position must be between \n99.98"
      + f" and 334.7 mm, is {lowest_immers_pos} mm"
    )
    assert safe_head_bottom_z_pos <= channel_head_start_pos <= safe_head_top_z_pos, (
      f"Start position of LLD search must be between \n{safe_head_bottom_z_pos}"
      + f" and {safe_head_top_z_pos} mm, is {channel_head_start_pos} mm"
    )
    assert 20 <= channel_speed_increments <= 15_000, (
      f"Z-touch search speed must be between \n{STAR.z_drive_increment_to_mm(20)}"
      + f" and {STAR.z_drive_increment_to_mm(15_000)} mm/sec, is {channel_speed} mm/sec"
    )
    assert 5 <= channel_acceleration_thousand_increments <= 150, (
      f"Channel acceleration must be between \n{STAR.z_drive_increment_to_mm(5*1_000)}"
      + f" and {STAR.z_drive_increment_to_mm(150*1_000)} mm/sec**2, is {channel_speed} mm/sec**2"
    )
    assert 20 <= channel_speed_upwards_increments <= 15_000, (
      f"Channel retraction speed must be between \n{STAR.z_drive_increment_to_mm(20)}"
      + f" and {STAR.z_drive_increment_to_mm(15_000)} mm/sec, is {channel_speed_upwards} mm/sec"
    )
    assert (
      0 <= detection_limiter_in_PWM <= 125
    ), "Detection limiter value must be between 0 and 125 PWM."
    assert 0 <= push_down_force_in_PWM <= 125, "Push down force between 0 and 125 PWM values"
    assert (
      0 <= post_detection_dist <= 245
    ), f"Post detection distance must be between 0 and 245 mm, is {post_detection_dist}"

    lowest_immers_pos_str = f"{lowest_immers_pos_increments:05}"
    start_pos_search_str = f"{start_pos_search_increments:05}"
    channel_speed_str = f"{channel_speed_increments:05}"
    channel_acc_str = f"{channel_acceleration_thousand_increments:03}"
    channel_speed_up_str = f"{channel_speed_upwards_increments:05}"
    detection_limiter_in_PWM_str = f"{detection_limiter_in_PWM:03}"
    push_down_force_in_PWM_str = f"{push_down_force_in_PWM:03}"

    ztouch_probed_z_height = await self.send_command(
      module=STAR.channel_id(channel_idx),
      command="ZH",
      zb=start_pos_search_str,  # begin of searching range [increment]
      za=lowest_immers_pos_str,  # end of searching range [increment]
      zv=channel_speed_up_str,  # speed z-drive upper section [increment/second]
      zr=channel_acc_str,  # acceleration z-drive [1000 increment/second]
      zu=channel_speed_str,  # speed z-drive lower section [increment/second]
      cg=detection_limiter_in_PWM_str,  # offset PWM limiter value for searching
      cf=push_down_force_in_PWM_str,  # offset PWM value for push down force
      fmt="rz#####",
    )
    # Subtract tip_length from measurement in increment, and convert to mm
    result_in_mm = STAR.z_drive_increment_to_mm(
      ztouch_probed_z_height["rz"] - tip_len_used_in_increments
    )
    if post_detection_dist != 0:  # Safety first
      await self.move_channel_z(z=result_in_mm + post_detection_dist, channel=channel_idx)
    if move_channels_to_save_pos_after:
      await self.move_all_channels_in_z_safety()

    return float(result_in_mm)

  class RotationDriveOrientation(enum.Enum):
    LEFT = 1
    FRONT = 2
    RIGHT = 3

  async def rotate_iswap_rotation_drive(self, orientation: RotationDriveOrientation):
    return await self.send_command(
      module="R0",
      command="WP",
      auto_id=False,
      wp=orientation.value,
    )

  class WristOrientation(enum.Enum):
    RIGHT = 1
    STRAIGHT = 2
    LEFT = 3
    REVERSE = 4

  async def rotate_iswap_wrist(self, orientation: WristOrientation):
    return await self.send_command(
      module="R0",
      command="TP",
      auto_id=False,
      tp=orientation.value,
    )

  @staticmethod
  def channel_id(channel_idx: int) -> str:
    """channel_idx: plr style, 0-indexed from the back"""
    channel_ids = "123456789ABCDEFG"
    return "P" + channel_ids[channel_idx]

  async def get_channels_y_positions(self) -> Dict[int, float]:
    """Get the Y position of all channels in mm"""
    resp = await self.send_command(
      module="C0",
      command="RY",
      fmt="ry#### (n)",
    )
    y_positions = [round(y / 10, 2) for y in resp["ry"]]

    # sometimes there is (likely) a floating point error and channels are reported to be
    # less than 9mm apart. (When you set channels using position_channels_in_y_direction,
    # it will raise an error.) The minimum y is 6mm, so we fix that first (in case that
    # values is misreported). Then, we traverse the list in reverse and set the min_diff.
    if y_positions[-1] < 5.8:
      raise RuntimeError(
        "Channels are reported to be too close to the front of the machine. "
        "The known minimum is 6, which will be fixed automatically for 5.8<y<6. "
        f"Reported values: {y_positions}."
      )
    elif 5.8 <= y_positions[-1] < 6:
      y_positions[-1] = 6.0

    min_diff = 9.0
    for i in range(len(y_positions) - 2, -1, -1):
      if y_positions[i] - y_positions[i + 1] < min_diff:
        y_positions[i] = y_positions[i + 1] + min_diff

    return {channel_idx: y for channel_idx, y in enumerate(y_positions)}

  @need_iswap_parked
  async def position_channels_in_y_direction(self, ys: Dict[int, float], make_space=True):
    """position all channels simultaneously in the Y direction.

    Args:
      ys: A dictionary mapping channel index to the desired Y position in mm. The channel index is
        0-indexed from the back.
      make_space: If True, the channels will be moved to ensure they are at least 9mm apart and in
        descending order, after the channels in `ys` have been put at the desired locations. Note
        that an error may still be raised, if there is insufficient space to move the channels or
        if the requested locations are not valid. Set this to False if you wan to aviod inadvertently
        moving other channels.
    """

    # check that the locations of channels after the move will be at least 9mm apart, and in
    # descending order
    channel_locations = await self.get_channels_y_positions()

    for channel_idx, y in ys.items():
      channel_locations[channel_idx] = y

    if make_space:
      # For the channels to the back of `back_channel`, make sure the space between them is
      # >=9mm. We start with the channel closest to `back_channel`, and make sure the
      # channel behind it is at least 9mm, updating if needed. Iterating from the front (closest
      # to `back_channel`) to the back (channel 0), all channels are put at the correct location.
      # This order matters because the channel in front of any channel may have been moved in the
      # previous iteration.
      # Note that if a channel is already spaced at >=9mm, it is not moved.
      use_channels = list(ys.keys())
      back_channel = min(use_channels)
      for channel_idx in range(back_channel, 0, -1):
        if (channel_locations[channel_idx - 1] - channel_locations[channel_idx]) < 9:
          channel_locations[channel_idx - 1] = channel_locations[channel_idx] + 9

      # Similarly for the channels to the front of `front_channel`, make sure they are all
      # spaced >=9mm apart. This time, we iterate from back (closest to `front_channel`)
      # to the front (lh.backend.num_channels - 1), and put each channel >=9mm before the
      # one behind it.
      front_channel = max(use_channels)
      for channel_idx in range(front_channel, self.num_channels - 1):
        if (channel_locations[channel_idx] - channel_locations[channel_idx + 1]) < 9:
          channel_locations[channel_idx + 1] = channel_locations[channel_idx] - 9

    # Quick checks before movement.
    if channel_locations[0] > 650:
      raise ValueError("Channel 0 would hit the back of the robot")

    if channel_locations[self.num_channels - 1] < 6:
      raise ValueError("Channel N would hit the front of the robot")

    if not all(
      int((channel_locations[i] - channel_locations[i + 1]) * 1000) >= 8_999  # float fixing
      for i in range(len(channel_locations) - 1)
    ):
      raise ValueError("Channels must be at least 9mm apart and in descending order")

    yp = " ".join([f"{round(y*10):04}" for y in channel_locations.values()])
    return await self.send_command(
      module="C0",
      command="JY",
      yp=yp,
    )

  async def get_channels_z_positions(self) -> Dict[int, float]:
    """Get the Y position of all channels in mm"""
    resp = await self.send_command(
      module="C0",
      command="RZ",
      fmt="rz#### (n)",
    )
    return {channel_idx: round(y / 10, 2) for channel_idx, y in enumerate(resp["rz"])}

  async def position_channels_in_z_direction(self, zs: Dict[int, float]):
    channel_locations = await self.get_channels_z_positions()

    for channel_idx, z in zs.items():
      channel_locations[channel_idx] = z

    return await self.send_command(
      module="C0", command="JZ", zp=[f"{round(z*10):04}" for z in channel_locations.values()]
    )

  async def pierce_foil(
    self,
    wells: Union[Well, List[Well]],
    piercing_channels: List[int],
    hold_down_channels: List[int],
    move_inwards: float,
    spread: Literal["wide", "tight"] = "wide",
    one_by_one: bool = False,
    distance_from_bottom: float = 20.0,
  ):
    """Pierce the foil of the media source plate at the specified column. Throw away the tips
    after piercing because there will be a bit of foil stuck to the tips. Use this method
    before aspirating from a foil-sealed plate to make sure the tips are clean and the
    aspirations are accurate.

    Args:
      wells: Well or wells in the plate to pierce the foil. If multiple wells, they must be on one
        column.
      piercing_channels: The channels to use for piercing the foil.
      hold_down_channels: The channels to use for holding down the plate when moving up the
        piercing channels.
      spread: The spread of the piercing channels in the well.
      one_by_one: If True, the channels will pierce the foil one by one. If False, all channels
        will pierce the foil simultaneously.
    """

    x: float
    ys: List[float]
    z: float

    # if only one well is give, but in a list, convert to Well so we fall into single-well logic.
    if isinstance(wells, list) and len(wells) == 1:
      wells = wells[0]

    if isinstance(wells, Well):
      well = wells
      x, y, z = well.get_absolute_location("c", "c", "cavity_bottom")

      if spread == "wide":
        offsets = get_wide_single_resource_liquid_op_offsets(
          well, num_channels=len(piercing_channels)
        )
      else:
        offsets = get_tight_single_resource_liquid_op_offsets(
          well, num_channels=len(piercing_channels)
        )
      ys = [y + offset.y for offset in offsets]
    else:
      assert (
        len(set(w.get_absolute_location().x for w in wells)) == 1
      ), "Wells must be on the same column"
      absolute_center = wells[0].get_absolute_location("c", "c", "cavity_bottom")
      x = absolute_center.x
      ys = [well.get_absolute_location(x="c", y="c").y for well in wells]
      z = absolute_center.z

    await self.move_channel_x(0, x=x)

    await self.position_channels_in_y_direction(
      {channel: y for channel, y in zip(piercing_channels, ys)}
    )

    zs = [z + distance_from_bottom for _ in range(len(piercing_channels))]
    if one_by_one:
      for channel in piercing_channels:
        await self.move_channel_z(channel, z)
    else:
      await self.position_channels_in_z_direction(
        {channel: z for channel, z in zip(piercing_channels, zs)}
      )

    await self.step_off_foil(
      [wells] if isinstance(wells, Well) else wells,
      back_channel=hold_down_channels[0],
      front_channel=hold_down_channels[1],
      move_inwards=move_inwards,
    )

  async def step_off_foil(
    self,
    wells: Union[Well, List[Well]],
    front_channel: int,
    back_channel: int,
    move_inwards: float = 2,
    move_height: float = 15,
  ):
    """
    Hold down a plate by placing two channels on the edges of a plate that is sealed with foil
    while moving up the channels that are still within the foil. This is useful when, for
    example, aspirating from a plate that is sealed: without holding it down, the tips might get
    stuck in the plate and move it up when retracting. Putting plates on the edge prevents this.

    When aspirating or dispensing in the foil, be sure to set the `min_z_endpos` parameter in
    `lh.aspirate` or `lh.dispense` to a value in the foil. You might want to use something like

    .. code-block:: python

        well = plate.get_well("A3")
        await wc.lh.aspirate(
          [well]*4, vols=[100]*4, use_channels=[7,8,9,10],
          min_z_endpos=well.get_absolute_location(z="cavity_bottom").z,
          surface_following_distance=0,
          pull_out_distance_transport_air=[0] * 4)
        await step_off_foil(lh.backend, [well], front_channel=11, back_channel=6, move_inwards=3)

    Args:
      wells: Wells in the plate to hold down. (x-coordinate of channels will be at center of wells).
        Must be sorted from back to front.
      front_channel: The channel to place on the front of the plate.
      back_channel: The channel to place on the back of the plate.
      move_inwards: mm to move inwards (backward on the front channel; frontward on the back).
      move_height: mm to move upwards after piercing the foil. front_channel and back_channel will hold the plate down.
    """

    if front_channel <= back_channel:
      raise ValueError(
        "front_channel should be in front of back_channel. " "Channels are 0-indexed from the back."
      )

    if isinstance(wells, Well):
      wells = [wells]

    plates = set(well.parent for well in wells)
    assert len(plates) == 1, "All wells must be in the same plate"
    plate = plates.pop()
    assert plate is not None

    z_location = plate.get_absolute_location(z="top").z

    if plate.get_absolute_rotation().z % 360 == 0:
      back_location = plate.get_absolute_location(y="b")
      front_location = plate.get_absolute_location(y="f")
    elif plate.get_absolute_rotation().z % 360 == 90:
      back_location = plate.get_absolute_location(x="r")
      front_location = plate.get_absolute_location(x="l")
    elif plate.get_absolute_rotation().z % 360 == 180:
      back_location = plate.get_absolute_location(y="f")
      front_location = plate.get_absolute_location(y="b")
    elif plate.get_absolute_rotation().z % 360 == 270:
      back_location = plate.get_absolute_location(x="l")
      front_location = plate.get_absolute_location(x="r")
    else:
      raise ValueError("Plate rotation must be a multiple of 90 degrees")

    try:
      # Then move all channels in the y-space simultaneously.
      await self.position_channels_in_y_direction(
        {
          front_channel: front_location.y + move_inwards,
          back_channel: back_location.y - move_inwards,
        }
      )

      await self.move_channel_z(front_channel, z_location)
      await self.move_channel_z(back_channel, z_location)
    finally:
      # Move channels that are lower than the `front_channel` and `back_channel` to
      # the just above the foil, in case the foil pops up.
      zs = await self.get_channels_z_positions()
      indices = [channel_idx for channel_idx, z in zs.items() if z < z_location]
      idx = {
        idx: z_location + move_height for idx in indices if idx not in (front_channel, back_channel)
      }
      await self.position_channels_in_z_direction(idx)

      # After that, all channels are clear to move up.
      await self.move_all_channels_in_z_safety()

  async def request_volume_in_tip(self, channel: int) -> float:
    resp = await self.send_command(STAR.channel_id(channel), "QC", fmt="qc##### (n)")
    _, current_volume = resp["qc"]  # first is max volume
    return float(current_volume) / 10

  @asynccontextmanager
  async def slow_iswap(self, wrist_velocity: int = 20_000, gripper_velocity: int = 20_000):
    """A context manager that sets the iSWAP to slow speed during the context"""
    assert 20 <= gripper_velocity <= 75_000
    assert 20 <= wrist_velocity <= 65_000

    original_wv = (await self.send_command("R0", "RA", ra="wv", fmt="wv#####"))["wv"]
    original_tv = (await self.send_command("R0", "RA", ra="tv", fmt="tv#####"))["tv"]

    await self.send_command("R0", "AA", wv=gripper_velocity)  # wrist velocity
    await self.send_command("R0", "AA", tv=wrist_velocity)  # gripper velocity
    try:
      yield
    finally:
      await self.send_command("R0", "AA", wv=original_wv)
      await self.send_command("R0", "AA", tv=original_tv)


class UnSafe:
  """
  Namespace for actions that are unsafe to perfom.
  For example, actions that send the iSWAP outside of the Hamilton Deck
  """

  def __init__(self, star: "STAR"):
    self.star = star

  async def put_in_hotel(
    self,
    hotel_center_x_coord: int = 0,
    hotel_center_y_coord: int = 0,
    hotel_center_z_coord: int = 0,
    hotel_center_x_direction: Literal[0, 1] = 0,
    hotel_center_y_direction: Literal[0, 1] = 0,
    hotel_center_z_direction: Literal[0, 1] = 0,
    clearance_height: int = 50,
    hotel_depth: int = 1_300,
    grip_direction: GripDirection = GripDirection.FRONT,
    traverse_height_at_beginning: int = 3_600,
    z_position_at_end: int = 3_600,
    grip_strength: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] = 5,
    open_gripper_position: int = 860,
    collision_control: Literal[0, 1] = 1,
    high_acceleration_index: Literal[1, 2, 3, 4] = 4,
    low_acceleration_index: Literal[1, 2, 3, 4] = 1,
    fold_up_at_end: bool = True,
  ):
    """
    A hotel is a location to store a plate. This can be a loading
    dock for an external machine such as a cytomat or a centrifuge.

    Take care when using this command to interact with hotels located
    outside of the hamilton deck area. Ensure that rotations of the
    iSWAP arm don't collide with anything.

    tip: set the hotel depth big enough so that the boundary is inside the
    hamilton deck. The iSWAP rotations will happen before it enters the hotel.

    The units of all relevant variables are in 0.1mm
    """

    assert 0 <= hotel_center_x_coord <= 99_999
    assert 0 <= hotel_center_y_coord <= 6_500
    assert 0 <= hotel_center_z_coord <= 3_500
    assert 0 <= clearance_height <= 999
    assert 0 <= hotel_depth <= 3_000
    assert 0 <= traverse_height_at_beginning <= 3_600
    assert 0 <= z_position_at_end <= 3_600
    assert 0 <= open_gripper_position <= 9_999

    return await self.star.send_command(
      module="C0",
      command="PI",
      xs=f"{hotel_center_x_coord:05}",
      xd=hotel_center_x_direction,
      yj=f"{hotel_center_y_coord:04}",
      yd=hotel_center_y_direction,
      zj=f"{hotel_center_z_coord:04}",
      zd=hotel_center_z_direction,
      zc=f"{clearance_height:03}",
      hd=f"{hotel_depth:04}",
      gr={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      th=f"{traverse_height_at_beginning:04}",
      te=f"{z_position_at_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      ga=collision_control,
      xe=f"{high_acceleration_index} {low_acceleration_index}",
      gc=int(fold_up_at_end),
    )

  async def get_from_hotel(
    self,
    hotel_center_x_coord: int = 0,
    hotel_center_y_coord: int = 0,
    hotel_center_z_coord: int = 0,
    # for direction, 0 is positive, 1 is negative
    hotel_center_x_direction: Literal[0, 1] = 0,
    hotel_center_y_direction: Literal[0, 1] = 0,
    hotel_center_z_direction: Literal[0, 1] = 0,
    clearance_height: int = 50,
    hotel_depth: int = 1_300,
    grip_direction: GripDirection = GripDirection.FRONT,
    traverse_height_at_beginning: int = 3_600,
    z_position_at_end: int = 3_600,
    grip_strength: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] = 5,
    open_gripper_position: int = 860,
    plate_width: int = 800,
    plate_width_tolerance: int = 20,
    collision_control: Literal[0, 1] = 1,
    high_acceleration_index: Literal[1, 2, 3, 4] = 4,
    low_acceleration_index: Literal[1, 2, 3, 4] = 1,
    fold_up_at_end: bool = True,
  ):
    """
    A hotel is a location to store a plate. This can be a loading
    dock for an external machine such as a cytomat or a centrifuge.

    Take care when using this command to interact with hotels located
    outside of the hamilton deck area. Ensure that rotations of the
    iSWAP arm don't collide with anything.

    tip: set the hotel depth big enough so that the boundary is inside the
    hamilton deck. The iSWAP rotations will happen before it enters the hotel.

    The units of all relevant variables are in 0.1mm
    """

    assert 0 <= hotel_center_x_coord <= 99_999
    assert 0 <= hotel_center_y_coord <= 6_500
    assert 0 <= hotel_center_z_coord <= 3_500
    assert 0 <= clearance_height <= 999
    assert 0 <= hotel_depth <= 3_000
    assert 0 <= traverse_height_at_beginning <= 3_600
    assert 0 <= z_position_at_end <= 3_600
    assert 0 <= open_gripper_position <= 9_999
    assert 0 <= plate_width <= 9_999
    assert 0 <= plate_width_tolerance <= 99

    return await self.star.send_command(
      module="C0",
      command="PO",
      xs=f"{hotel_center_x_coord:05}",
      xd=hotel_center_x_direction,
      yj=f"{hotel_center_y_coord:04}",
      yd=hotel_center_y_direction,
      zj=f"{hotel_center_z_coord:04}",
      zd=hotel_center_z_direction,
      zc=f"{clearance_height:03}",
      hd=f"{hotel_depth:04}",
      gr={
        GripDirection.FRONT: 1,
        GripDirection.RIGHT: 2,
        GripDirection.BACK: 3,
        GripDirection.LEFT: 4,
      }[grip_direction],
      th=f"{traverse_height_at_beginning:04}",
      te=f"{z_position_at_end:04}",
      gw=grip_strength,
      go=f"{open_gripper_position:04}",
      gb=f"{plate_width:04}",
      gt=f"{plate_width_tolerance:02}",
      ga=collision_control,
      xe=f"{high_acceleration_index} {low_acceleration_index}",
      gc=int(fold_up_at_end),
    )

  async def violently_shoot_down_tip(self, channel_idx: int):
    """Shoot down the tip on the specified channel by releasing the drive that holds the spring. The
    tips will shoot down in place at an acceleration bigger than g. This is done by initializing
    the squeezer drive wihile a tip is mounted.

    Safe to do when above a tip rack, for example directly after a tip pickup.

    .. warning::

      Consider this method an easter egg. Not for serious use.
    """
    await self.star.send_command(module=STAR.channel_id(channel_idx), command="SI")
