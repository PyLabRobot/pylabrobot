""" Hamilton backend errors """

from abc import ABCMeta


class HamiltonModuleError(Exception, metaclass=ABCMeta):
  """ Base class for all Hamilton backend errors, raised by a single module. """

  def __init__(self, message, raw_response=None, raw_module=None):
    super().__init__(message)
    self.message = message
    self.raw_response = raw_response
    self.raw_module = raw_module


class CommandSyntaxError(HamiltonModuleError):
  """ Command syntax error

  Code: 01
  """

  pass


class HardwareError(HamiltonModuleError):
  """ Hardware error

  Possible cause(s):
    drive blocked, low power etc.

  Code: 02
  """

  pass


class CommandNotCompletedError(HamiltonModuleError):
  """ Command not completed

  Possible cause(s):
    error in previous sequence (not executed)

  Code: 03
  """

  pass


class ClotDetectedError(HamiltonModuleError):
  """ Clot detected

  Possible cause(s):
    LLD not interrupted

  Code: 04
  """

  pass


class BarcodeUnreadableError(HamiltonModuleError):
  """ Barcode unreadable

  Possible cause(s):
    bad or missing barcode

  Code: 05
  """

  pass


class TooLittleLiquidError(HamiltonModuleError):
  """ Too little liquid

  Possible cause(s):
    1. liquid surface is not detected,
    2. Aspirate / Dispense conditions could not be fulfilled.

  Code: 06
  """

  pass


class TipAlreadyFittedError(HamiltonModuleError):
  """ Tip already fitted

  Possible cause(s):
    Repeated attempts to fit a tip or iSwap movement with tips

  Code: 07
  """

  pass


class NoTipError(HamiltonModuleError):
  """ No tips

  Possible cause(s):
    command was started without fitting tip (tip was not fitted or fell off again)

  Code: 08
  """

  pass


class NoCarrierError(HamiltonModuleError):
  """ No carrier

  Possible cause(s):
    load command without carrier

  Code: 09
  """

  pass


class NotCompletedError(HamiltonModuleError):
  """ Not completed

  Possible cause(s):
    Command in command buffer was aborted due to an error in a previous command, or command stack
    was deleted.

  Code: 10
  """

  pass


class DispenseWithPressureLLDError(HamiltonModuleError):
  """ Dispense with  pressure LLD

  Possible cause(s):
    dispense with pressure LLD is not permitted

  Code: 11
  """

  pass


class NoTeachInSignalError(HamiltonModuleError):
  """ No Teach  In Signal

  Possible cause(s):
    X-Movement to LLD reached maximum allowable position with- out detecting Teach in signal

  Code: 12
  """

  pass


class LoadingTrayError(HamiltonModuleError):
  """ Loading  Tray error

  Possible cause(s):
    position already occupied

  Code: 13
  """

  pass


class SequencedAspirationWithPressureLLDError(HamiltonModuleError):
  """ Sequenced aspiration with  pressure LLD

  Possible cause(s):
    sequenced aspiration with pressure LLD is not permitted

  Code: 14
  """

  pass


class NotAllowedParameterCombinationError(HamiltonModuleError):
  """ Not allowed  parameter combination

  Possible cause(s):
    i.e. PLLD and dispense or wrong X-drive assignment

  Code: 15
  """

  pass


class CoverCloseError(HamiltonModuleError):
  """Cover close error

  Possible cause(s):
    cover is not closed and couldn't be locked

  Code: 16
  """

  pass


class AspirationError(HamiltonModuleError):
  """ Aspiration error

  Possible cause(s):
    aspiration liquid stream error detected


  Code: 17
  """

  pass


class WashFluidOrWasteError(HamiltonModuleError):
  """Wash fluid or waste error

  Possible cause(s):
    1. missing wash fluid
    2. waste of particular washer is full

  Code: 18
  """

  pass


class IncubationError(HamiltonModuleError):
  """ Incubation error

  Possible cause(s):
    incubator temperature out of limit

  Code: 19
  """

  pass


class TADMMeasurementError(HamiltonModuleError):
  """TADM measurement error

  Possible cause(s):
    overshoot of limits during aspiration or dispensation

  Code: 20, 26
  """

  pass


class NoElementError(HamiltonModuleError):
  """ No element

  Possible cause(s):
    expected element not detected

  Code: 21
  """

  pass


class ElementStillHoldingError(HamiltonModuleError):
  """Element still holding

  Possible cause(s):
    "Get command" is sent twice or element is not discarded expected element is missing (lost)

  Code: 22
  """

  pass


class ElementLostError(HamiltonModuleError):
  """ Element lost

  Possible cause(s):
    expected element is missing (lost)

  Code: 23
  """

  pass


class IllegalTargetPlatePositionError(HamiltonModuleError):
  """Illegal target plate position

  Possible cause(s):
    1. over or underflow of iSWAP positions
    2. iSWAP is not in park position during pipetting activities

  Code: 24
  """

  pass


class IllegalUserAccessError(HamiltonModuleError):
  """Illegal user access

  Possible cause(s):
    carrier was manually removed or cover is open (immediate stop is executed)

  Code: 25
  """

  pass


class PositionNotReachableError(HamiltonModuleError):
  """Position not reachable

  Possible cause(s):
    position out of mechanical limits using iSWAP, CoRe gripper or PIP-channels

  Code: 27
  """

  pass


class UnexpectedLLDError(HamiltonModuleError):
  """ unexpected LLD

  Possible cause(s):
    liquid level is reached before LLD scanning is started (using PIP or XL channels)

  Code: 28
  """

  pass


class AreaAlreadyOccupiedError(HamiltonModuleError):
  """ area already occupied

  Possible cause(s):
    Its impossible to occupy area because this area is already in use

  Code: 29
  """

  pass


class ImpossibleToOccupyAreaError(HamiltonModuleError):
  """ impossible to occupy area

  Possible cause(s):
    Area cant be occupied because is no solution for arm prepositioning

  Code: 30
  """

  pass


class SlaveError(HamiltonModuleError):
  """ Slave error

  Possible cause(s):
    This error code indicates an error in one of slaves. (for error handling purpose using service
    software macro code)

  Code: 99
  """

  pass


class UnknownHamiltonError(HamiltonModuleError):
  """ Unknown error """

  pass


def _module_id_to_module_name(id_):
  """ Convert a module ID to a module name. """
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
    "M1": "Reserved for module 1"
  }[id_]


class HamiltonError(Exception):
  """
  All Hamilton machine errors.

  Example:
    >>> try:
    ...   lh.pickup_tips([True, True, True])
    ... except HamiltonError as e:
    ...   print(e)
    HamiltonError({
      'Pipetting Channel 1': NoTipError('Tip already picked up'),
      'Pipetting Channel 3': NoTipError('Tip already picked up'),
    })

    >>> try:
    ...   lh.pickup_tips([True, False, True])
    ... except HamiltonError as e:
    ...   if 'Pipetting Channel 1' in e:
    ...     print('Pipetting Channel 1 error: ', e['Pipetting Channel 1'], e.error_code)
    Pipetting Channel 1 error:  NoTipError('Tip already picked up'), '08/76'
  """

  def __init__(self, errors, raw_response=None):
    self.raw_response = raw_response

    # Convert error codes to error objects
    self.errors = {}
    for module_id, error in errors.items():
      module_name = _module_id_to_module_name(module_id)
      if "/" in error:
        # C0 module: error code / trace information
        error_code, trace_information = error.split("/")
        error_code, trace_information = int(error_code), int(trace_information)
        if error_code == 0: # No error
          continue
        error_class = HamiltonError.error_code_to_exception(error_code)
      else:
        # Slave modules: er## (just trace information)
        error_class = UnknownHamiltonError
        trace_information = int(error)
      error_description = HamiltonError.trace_information_to_string(
        module_identifier=module_id, trace_information=trace_information)
      self.errors[module_name] = error_class(error_description,
                                             raw_response=error,
                                             raw_module=module_id)

    # If the master error is a SlaveError, remove it from the errors dict.
    if "Master" in self.errors:
      if isinstance(self.errors["Master"], SlaveError):
        self.errors.pop("Master")

    super().__init__(str(self.errors))

  def __len__(self):
    return len(self.errors)

  def __getitem__(self, key):
    return self.errors[key]

  def __setitem__(self, key, value):
    self.errors[key] = value

  def __contains__(self, key):
    return key in self.errors

  def items(self):
    return self.errors.items()

  @staticmethod
  def error_code_to_exception(code: int) -> HamiltonModuleError:
    """ Convert an error code to an exception. """
    codes = {
      1: CommandSyntaxError,
      2: HardwareError,
      3: CommandNotCompletedError,
      4: ClotDetectedError,
      5: BarcodeUnreadableError,
      6: TooLittleLiquidError,
      7: TipAlreadyFittedError,
      8: NoTipError,
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
      26: TADMMeasurementError,
      21: NoElementError,
      22: ElementStillHoldingError,
      23: ElementLostError,
      24: IllegalTargetPlatePositionError,
      25: IllegalUserAccessError,
      27: PositionNotReachableError,
      28: UnexpectedLLDError,
      29: AreaAlreadyOccupiedError,
      30: ImpossibleToOccupyAreaError,
      99: SlaveError
    }
    if code in codes:
      return codes[code]
    return UnknownHamiltonError

  @staticmethod
  def trace_information_to_string(module_identifier: str, trace_information: int) -> str:
    """ Convert a trace identifier to an error message. """
    table = None

    if module_identifier == "C0":
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
        53: "Robotic channel task busy"
      }
    elif module_identifier in ["PX", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "PA",
                               "PB", "PC", "PD", "PE", "PF", "PG"]:
      table = {
        0: "No error",
        20: "No communication to EEPROM",
        30: "Unknown command",
        31: "Unknown parameter",
        32: "Parameter out of range",
        35: "Voltages outside permitted range",
        36: "Stop during execution of command",
        37: "Stop during execution of command",
        40: "No paralell processes permitted (Two or more commands sent for the same control"
            "process)",
        50: "Dispensing drive init. position not found",
        51: "Dispensing drive not initialized",
        52: "Dispensing drive movement error",
        53: "Maximum volume in tip reached",
        54: "Position outside of permitted area",
        55: "Y-drive blocked",
        56: "Y-drive not initialized",
        57: "Y-drive movement error",
        60: "X-drive blocked",
        61: "X-drive not initialized",
        62: "X-drive movement error",
        63: "X-drive limit stop not found",
        70: "No liquid level found (possibly because no liquid was present)",
        71: "Not enough liquid present (Immersion depth or surface following position possiby"
            "below minimal access range)",
        75: "No tip picked up, possibly because no was present at specified position",
        76: "Tip already picked up",
        77: "Tip not discarded",
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
        96: "Limit curve already stored"
      }

    if table is not None and trace_information in table:
      return table[trace_information]

    return f"Unknown trace information code {trace_information:02}"
