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


error_map = {
    "01": CytomatBusyError("Device still busy, new command not accepted"),
    "02": CytomatCommandUnknownError("Command unknown"),
    "03": CytomatTelegramStructureError("Telegram structure error"),
    "04": CytomatIncorrectParameterError("Incorrect parameter in telegram"),
    "05": CytomatUnknownLocationError("Unknown location number specified"),
    "11": CytomatIncorrectHandlerPositionError("Incorrect handler (start) position"),
    "12": CytomatShovelExtendedError("Command cannot be executed as shovel is extended"),
    "21": CytomatHandlerOccupiedError("Handler already occupied"),
    "22": CytomatHandlerEmptyError("Handler empty"),
    "31": CytomatTransferStationEmptyError("Transfer station empty"),
    "32": CytomatTransferStationOccupiedError("Transfer station occupied"),
    "33": CytomatTransferStationPositionError("Transfer station not in position"),
    "41": CytomatLiftDoorNotConfiguredError("Automatic lift door not configured"),
    "42": CytomatLiftDoorNotOpenError("Automatic lift door not open"),
    "51": CytomatMemoryAccessError("Error while accessing internal memory"),
    "52": CytomatUnauthorizedAccessError("Incorrect password / unauthorized access"),
}