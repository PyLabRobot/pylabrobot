from typing import Dict, Tuple


class SpectraMaxError(Exception):
  """Exceptions raised by a SpectraMax plate reader."""


class SpectraMaxUnrecognizedCommandError(SpectraMaxError):
  """Unrecognized command errors sent from the computer."""


class SpectraMaxFirmwareError(SpectraMaxError):
  """Firmware errors."""


class SpectraMaxHardwareError(SpectraMaxError):
  """Hardware errors."""


class SpectraMaxMotionError(SpectraMaxError):
  """Motion errors."""


class SpectraMaxNVRAMError(SpectraMaxError):
  """NVRAM errors."""


ERROR_CODES: Dict[int, Tuple[str, type]] = {
  100: ("command not found", SpectraMaxUnrecognizedCommandError),
  101: ("invalid argument", SpectraMaxUnrecognizedCommandError),
  102: ("too many arguments", SpectraMaxUnrecognizedCommandError),
  103: ("not enough arguments", SpectraMaxUnrecognizedCommandError),
  104: ("input line too long", SpectraMaxUnrecognizedCommandError),
  105: ("command invalid, system busy", SpectraMaxUnrecognizedCommandError),
  106: ("command invalid, measurement in progress", SpectraMaxUnrecognizedCommandError),
  107: ("no data to transfer", SpectraMaxUnrecognizedCommandError),
  108: ("data buffer full", SpectraMaxUnrecognizedCommandError),
  109: ("error buffer overflow", SpectraMaxUnrecognizedCommandError),
  110: ("stray light cuvette, door open?", SpectraMaxUnrecognizedCommandError),
  111: ("invalid read settings", SpectraMaxUnrecognizedCommandError),
  200: ("assert failed", SpectraMaxFirmwareError),
  201: ("bad error number", SpectraMaxFirmwareError),
  202: ("receive queue overflow", SpectraMaxFirmwareError),
  203: ("serial port parity error", SpectraMaxFirmwareError),
  204: ("serial port overrun error", SpectraMaxFirmwareError),
  205: ("serial port framing error", SpectraMaxFirmwareError),
  206: ("cmd generated too much output", SpectraMaxFirmwareError),
  207: ("fatal trap", SpectraMaxFirmwareError),
  208: ("RTOS error", SpectraMaxFirmwareError),
  209: ("stack overflow", SpectraMaxFirmwareError),
  210: ("unknown interrupt", SpectraMaxFirmwareError),
  300: ("thermistor faulty", SpectraMaxHardwareError),
  301: ("safe temperature limit exceeded", SpectraMaxHardwareError),
  302: ("low light", SpectraMaxHardwareError),
  303: ("unable to cal dark current", SpectraMaxHardwareError),
  304: ("signal level saturation", SpectraMaxHardwareError),
  305: ("reference level saturation", SpectraMaxHardwareError),
  306: ("plate air cal fail, low light", SpectraMaxHardwareError),
  307: ("cuv air ref fail", SpectraMaxHardwareError),
  308: ("stray light", SpectraMaxHardwareError),
  312: ("gain calibration failed", SpectraMaxHardwareError),
  313: ("reference gain check fail", SpectraMaxHardwareError),
  314: ("low lamp level warning", SpectraMaxHardwareError),
  315: ("can't find zero order", SpectraMaxHardwareError),
  316: ("grating motor driver faulty", SpectraMaxHardwareError),
  317: ("monitor ADC faulty", SpectraMaxHardwareError),
  400: ("carriage motion error", SpectraMaxMotionError),
  401: ("filter wheel error", SpectraMaxMotionError),
  402: ("grating error", SpectraMaxMotionError),
  403: ("stage error", SpectraMaxMotionError),
  500: ("NVRAM CRC corrupt", SpectraMaxNVRAMError),
  501: ("NVRAM Grating cal data bad", SpectraMaxNVRAMError),
  502: ("NVRAM Cuvette air cal data error", SpectraMaxNVRAMError),
  503: ("NVRAM Plate air cal data error", SpectraMaxNVRAMError),
  504: ("NVRAM Carriage offset error", SpectraMaxNVRAMError),
  505: ("NVRAM Stage offset error", SpectraMaxNVRAMError),
}
