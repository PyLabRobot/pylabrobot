from typing import Dict


class CytomatBusyError(Exception):
  """Exception raised when the device is still busy and a new command is not accepted."""

  pass


class CytomatCommandUnknownError(Exception):
  """Exception raised for an unknown command."""

  pass


class CytomatTelegramStructureError(Exception):
  """Exception raised for telegram structure errors."""

  pass


class CytomatIncorrectParameterError(Exception):
  """Exception raised for incorrect parameters in telegram."""

  pass


class CytomatUnknownLocationError(Exception):
  """Exception raised for unknown location numbers specified."""

  pass


class CytomatIncorrectHandlerPositionError(Exception):
  """Exception raised for incorrect handler start positions."""

  pass


class CytomatShovelExtendedError(Exception):
  """Exception raised when a command cannot be executed as the shovel is extended."""

  pass


class CytomatHandlerOccupiedError(Exception):
  """Exception raised when the handler is already occupied."""

  pass


class CytomatHandlerEmptyError(Exception):
  """Exception raised when the handler is empty."""

  pass


class CytomatTransferStationEmptyError(Exception):
  """Exception raised when the transfer station is empty."""

  pass


class CytomatTransferStationOccupiedError(Exception):
  """Exception raised when the transfer station is occupied."""

  pass


class CytomatTransferStationPositionError(Exception):
  """Exception raised when the transfer station is not in position."""

  pass


class CytomatLiftDoorNotConfiguredError(Exception):
  """Exception raised when the automatic lift door is not configured."""

  pass


class CytomatLiftDoorNotOpenError(Exception):
  """Exception raised when the automatic lift door is not open."""

  pass


class CytomatMemoryAccessError(Exception):
  """Exception raised for errors while accessing internal memory."""

  pass


class CytomatUnauthorizedAccessError(Exception):
  """Exception raised for incorrect password or unauthorized access."""

  pass


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
