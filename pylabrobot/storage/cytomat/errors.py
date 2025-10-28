from typing import Dict

from pylabrobot.storage.cytomat.constants import ErrorRegister


class CytomatBusyError(Exception):
  """Exception raised when the device is still busy and a new command is not accepted."""


class CytomatCommandUnknownError(Exception):
  """Exception raised for an unknown command."""


class CytomatTelegramStructureError(Exception):
  """Exception raised for telegram structure errors."""


class CytomatIncorrectParameterError(Exception):
  """Exception raised for incorrect parameters in telegram."""


class CytomatUnknownLocationError(Exception):
  """Exception raised for unknown location numbers specified."""


class CytomatIncorrectHandlerPositionError(Exception):
  """Exception raised for incorrect handler start positions."""


class CytomatShovelExtendedError(Exception):
  """Exception raised when a command cannot be executed as the shovel is extended."""


class CytomatHandlerOccupiedError(Exception):
  """Exception raised when the handler is already occupied."""


class CytomatHandlerEmptyError(Exception):
  """Exception raised when the handler is empty."""


class CytomatTransferStationEmptyError(Exception):
  """Exception raised when the transfer station is empty."""


class CytomatTransferStationOccupiedError(Exception):
  """Exception raised when the transfer station is occupied."""


class CytomatTransferStationPositionError(Exception):
  """Exception raised when the transfer station is not in position."""


class CytomatLiftDoorNotConfiguredError(Exception):
  """Exception raised when the automatic lift door is not configured."""


class CytomatLiftDoorNotOpenError(Exception):
  """Exception raised when the automatic lift door is not open."""


class CytomatMemoryAccessError(Exception):
  """Exception raised for errors while accessing internal memory."""


class CytomatUnauthorizedAccessError(Exception):
  """Exception raised for incorrect password or unauthorized access."""


error_map: Dict[int, Exception] = {
  0x1: CytomatBusyError("Device still busy, new command not accepted"),
  0x2: CytomatCommandUnknownError("Command unknown"),
  0x3: CytomatTelegramStructureError("Telegram structure error"),
  0x4: CytomatIncorrectParameterError("Incorrect parameter in telegram"),
  0x5: CytomatUnknownLocationError("Unknown location number specified"),
  0x11: CytomatIncorrectHandlerPositionError("Incorrect handler (start) position"),
  0x12: CytomatShovelExtendedError("Command cannot be executed as shovel is extended"),
  0x21: CytomatHandlerOccupiedError("Handler already occupied"),
  0x22: CytomatHandlerEmptyError("Handler empty"),
  0x31: CytomatTransferStationEmptyError("Transfer station empty"),
  0x32: CytomatTransferStationOccupiedError("Transfer station occupied"),
  0x33: CytomatTransferStationPositionError("Transfer station not in position"),
  0x41: CytomatLiftDoorNotConfiguredError("Automatic lift door not configured"),
  0x42: CytomatLiftDoorNotOpenError("Automatic lift door not open"),
  0x51: CytomatMemoryAccessError("Error while accessing internal memory"),
  0x52: CytomatUnauthorizedAccessError("Incorrect password / unauthorized access"),
}


class CytomatCommunicationWithMotorControllersInterruptedError(Exception):
  pass


class CytomatNoMtpLoadedOnHandlerShovelError(Exception):
  pass


class CytomatNoMtpUnloadedFromHandlerShovelError(Exception):
  pass


class CytomatShovelNotExtendedAutomaticUnitPositionError(Exception):
  pass


class CytomatProcessTimeoutError(Exception):
  pass


class CytomatAutomaticLiftDoorNotOpenError(Exception):
  pass


class CytomatAutomaticLiftDoorNotClosedError(Exception):
  pass


class CytomatShovelNotRetractedError(Exception):
  pass


class CytomatStepperMotorControllerTemperatureTooHighError(Exception):
  pass


class CytomatOtherStepperMotorControllerError(Exception):
  pass


class CytomatTransferStationNotRotatedError(Exception):
  pass


class CytomatCommunicationWithHeatingControllersAndGasSupplyDisturbedError(Exception):
  pass


class CytomatFatalErrorOccurredDuringErrorRoutineError(Exception):
  pass


error_register_map: Dict[ErrorRegister, Exception] = {
  ErrorRegister.COMMUNICATION_WITH_MOTOR_CONTROLLERS_INTERRUPTED: CytomatCommunicationWithMotorControllersInterruptedError(
    "Communication with motor controllers interrupted"
  ),
  ErrorRegister.NO_MTP_LOADED_ON_HANDLER_SHOVEL: CytomatNoMtpLoadedOnHandlerShovelError(
    "MTP not loaded on handler shovel"
  ),
  ErrorRegister.NO_MTP_UNLOADED_FROM_HANDLER_SHOVEL: CytomatNoMtpUnloadedFromHandlerShovelError(
    "MTP not unloaded from handler shovel"
  ),
  ErrorRegister.SHOVEL_NOT_EXTENDED_AUTOMATIC_UNIT_POSITION_ERROR: CytomatShovelNotExtendedAutomaticUnitPositionError(
    "Shovel not extended, automatic unit position error"
  ),
  ErrorRegister.PROCESS_TIMEOUT: CytomatProcessTimeoutError("Process timeout"),
  ErrorRegister.AUTOMATIC_LIFT_DOOR_NOT_OPEN: CytomatAutomaticLiftDoorNotOpenError(
    "Automatic lift door not open"
  ),
  ErrorRegister.AUTOMATIC_LIFT_DOOR_NOT_CLOSED: CytomatAutomaticLiftDoorNotClosedError(
    "Automatic lift door not closed"
  ),
  ErrorRegister.SHOVEL_NOT_RETRACTED: CytomatShovelNotRetractedError("Shovel not retracted"),
  ErrorRegister.STEPPER_MOTOR_CONTROLLER_TEMPERATURE_TOO_HIGH: CytomatStepperMotorControllerTemperatureTooHighError(
    "Stepper motor controller temperature too high"
  ),
  ErrorRegister.OTHER_STEPPER_MOTOR_CONTROLLER_ERROR: CytomatOtherStepperMotorControllerError(
    "Other stepper motor controller error"
  ),
  ErrorRegister.TRANSFER_STATION_NOT_ROTATED: CytomatTransferStationNotRotatedError(
    "Transfer station not rotated"
  ),
  ErrorRegister.COMMUNICATION_WITH_HEATING_CONTROLLERS_AND_GAS_SUPPLY_DISTURBED: CytomatCommunicationWithHeatingControllersAndGasSupplyDisturbedError(
    "Communication with heating controllers and gas supply disturbed"
  ),
  ErrorRegister.FATAL_ERROR_OCCURRED_DURING_ERROR_ROUTINE: CytomatFatalErrorOccurredDuringErrorRoutineError(
    "Fatal error occurred during error routine"
  ),
}
