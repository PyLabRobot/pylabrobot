from abc import ABCMeta
from typing import Dict, Optional, Type

from pylabrobot.capabilities.liquid_handling.errors import ChannelizedError
from pylabrobot.resources.errors import (
  HasTipError,
  NoTipError,
  TooLittleLiquidError,
  TooLittleVolumeError,
)


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
    "Get command" is sent twice or element is not dropped expected element is missing (lost)

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
      40: "No parallel processes permitted (Two or more commands sent for the same controlprocess)",
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
      65: "Squeezer drive blocked. Can you manually unblock the squeezer drive by turning its screw?",
      66: "Squeezer drive not initialized",
      67: "Squeezer drive movement error: Step loss",
      68: "Init position adjustment error",
      70: "No liquid level found (possibly because no liquid was present, or too little liquid was present to trigger cLLD)",
      71: "Not enough liquid present (Immersion depth or surface following position possibly"
      "below minimal access range)",
      72: "Auto calibration at pressure (Sensor not possible)",
      73: "No liquid level found with dual LLD",
      74: "Liquid at a not allowed position detected",
      75: "No tip picked up, possibly because no was present at specified position",
      76: "Tip already picked up",
      77: "Tip not dropped",
      78: "Wrong tip picked up",
      80: "Liquid not correctly aspirated",
      81: "Clot detected",
      82: "TADM measurement out of lower limit curve",
      83: "TADM measurement out of upper limit curve",
      84: "Not enough memory for TADM measurement",
      85: "No communication to digital potentiometer",
      86: "ADC algorithm error",
      87: "2nd phase of liquid nt found",
      88: "Not enough liquid present (Immersion depth or surface following position possibly"
      "below minimal access range)",
      90: "Limit curve not resettable",
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
      91: "iSWAP not initialized. Call STARBackend.initialize_iswap().",
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
