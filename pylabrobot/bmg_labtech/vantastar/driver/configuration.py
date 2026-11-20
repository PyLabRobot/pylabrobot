"""VANTAstar device configuration, parsed from the HardwareInfoResponse (INIT reply, id 0x13).

The HardwareInfoResponse payload is a sequence of modules, each a module number (u8), the module's
length (u16) and its fields.  Module 2 describes the mainboard and its firmware; the others describe
the fitted hardware (photomultipliers, temperature controller, pumps, spectrometer, monochromator,
...).  The layouts below hold for firmware 1.11 and 1.12 (version 1110 and 1120, firmware package 8
and 11).
"""

import dataclasses
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from .responses import split_reply

logger = logging.getLogger(__name__)

HARDWARE_INFO_ID = 0x13

_MAINBOARD_MODULE = 2
# Module 2 field offsets, from the start of the module's fields.
_OFF_FW_VERSION = 0  # u16, e.g. 1120 for firmware 1.12
_OFF_FW_BUILD = 2  # u16
_OFF_PROTOCOL_VERSION = 4  # u8
_OFF_MAINBOARD_VERSION = 5  # u8
_OFF_CPU_BOARD_VERSION = 6  # u8
_OFF_SERIAL_STRING = 12  # null-terminated ASCII; the firmware package number (u16) follows a byte
# after the terminator

# The modules of the fitted hardware, by number.
_DETECTORS_MODULE = 9
_TEMPERATURE_CONTROLLER_MODULE = 14
_PUMPS_MODULE = 15
_STACKER_MODULE = 17
_BARCODE_READERS_MODULE = 18
_SPECTROMETER_MODULE = 31
_MONOCHROMATOR_MODULE = 47
_ATMOSPHERIC_CONTROL_UNIT_MODULE = 49
_STIRRER_MODULE = 98

# Module 9's photomultiplier configuration (bits 1-2), module 15's pump types (two bits each) and
# module 18's barcode reader types (two bits each), as the reader names them.
_PHOTOMULTIPLIERS = {0: "single", 1: "single extended infrared", 2: "dual"}
_PUMP_TYPES = {1: "XP3000", 2: "XCalibur"}
_BARCODE_READER_TYPES = {0: "SE 923", 1: "CSE 600", 2: "SE 955", 3: "SE 965"}


def _bit(value: int, bit: int) -> bool:
  return bool(value >> bit & 1)


def _parse_monochromator_slides(text: str) -> Dict[str, Dict[int, str]]:
  """Parse `NAME:1=open,2=Filter 1,...` lines into {slide: {position: name}}.

  An entry without a whole position number (the slide's `LV_Range`) is left out.
  """
  slides: Dict[str, Dict[int, str]] = {}
  for line in text.splitlines():
    name, _, positions = line.partition(":")
    if positions:
      entries = (entry.partition("=") for entry in positions.split(","))
      slides[name.strip()] = {
        int(number): label for number, _, label in entries if number.strip().isdigit()
      }
  return slides


@dataclass
class VANTAstarConfiguration:
  """What the reader reported about itself.

  The firmware and the serial number come from module 2; the fitted hardware from the other
  modules, one by one (``update_from_modules``).  A fitted-hardware field is None until read, or
  when its module is missing from the reply: the tested reader sends no module for the stacker and
  the barcode readers.  The tested reader reported a single standard photomultiplier, a
  temperature controller of 3 channels, no pumps, a spectrometer up to 1050 nm, 7 monochromator
  slides with linear variable filters, no atmospheric control unit and no stirrer; the other
  values of these fields are decoded from the layout the reader uses, not seen on a reader.

  Attributes:
    firmware_version: the firmware version number, e.g. 1120 for firmware 1.12.
    firmware_build: the firmware build number.
    firmware_package: the firmware package number (Voyager shows it as "FWP").
    protocol_version: the communication protocol version the reader supports.
    mainboard_version: the mainboard version.
    cpu_board_version: the CPU board version.
    serial_number: the reader's serial number; a saved file holds ``SERIAL_NUMBER_PLACEHOLDER``.
    modules: every module's fields, by module number; not saved.
    photomultipliers: "single", "single extended infrared" or "dual".
    temperature_controller: whether a temperature controller (incubator) is built in.
    temperature_controller_channels: its channels.
    pumps: the type of injector pumps 1 and 2, "XP3000" or "XCalibur"; None where not built in.
    spectrometer: whether the absorbance spectrometer is built in.
    spectrometer_maximum_wavelength: its longest wavelength, nm.
    monochromator_slides: the monochromator's slides by name, each with its positions:
      {slide: {position: name}}.
    linear_variable_filters: whether the monochromator holds linear variable filters.
    atmospheric_control_unit_connected: whether an atmospheric control unit is connected.
    stirrer: whether a stirrer is connected.
    stacker_connected: whether a stacker is connected.
    barcode_readers: {reader 1-4: scanner type} for the barcode readers built in.
  """

  firmware_version: Optional[int] = None
  firmware_build: Optional[int] = None
  firmware_package: Optional[int] = None
  protocol_version: Optional[int] = None
  mainboard_version: Optional[int] = None
  cpu_board_version: Optional[int] = None
  serial_number: Optional[str] = None
  modules: Optional[Dict[int, bytes]] = None
  photomultipliers: Optional[str] = None
  temperature_controller: Optional[bool] = None
  temperature_controller_channels: Optional[int] = None
  pumps: Optional[Tuple[Optional[str], Optional[str]]] = None
  spectrometer: Optional[bool] = None
  spectrometer_maximum_wavelength: Optional[int] = None
  monochromator_slides: Optional[Dict[str, Dict[int, str]]] = None
  linear_variable_filters: Optional[bool] = None
  atmospheric_control_unit_connected: Optional[bool] = None
  stirrer: Optional[bool] = None
  stacker_connected: Optional[bool] = None
  barcode_readers: Optional[Dict[int, str]] = None

  def _update_from_detectors(self, data: bytes) -> None:
    """The photomultipliers, from module 9's last byte, bits 1-2."""
    configuration = data[-1] >> 1 & 0x03
    if configuration not in _PHOTOMULTIPLIERS:
      raise ValueError(f"photomultiplier configuration {configuration} is not known")
    self.photomultipliers = _PHOTOMULTIPLIERS[configuration]

  def _update_from_temperature_controller(self, data: bytes) -> None:
    """The temperature controller and its channels, from module 14's first byte."""
    self.temperature_controller = _bit(data[0], 0)
    self.temperature_controller_channels = data[0] >> 1 & 0x07

  def _update_from_pumps(self, data: bytes) -> None:
    """The injector pumps 1 and 2 and their types, from module 15's first byte."""
    pump_1, pump_2 = (_PUMP_TYPES.get(data[0] >> (2 * pump) & 0x03) for pump in range(2))
    self.pumps = (pump_1, pump_2)

  def _update_from_stacker(self, data: bytes) -> None:
    """The stacker, from module 17's third byte from the end, bit 0."""
    self.stacker_connected = _bit(data[-3], 0)

  def _update_from_barcode_readers(self, data: bytes) -> None:
    """The barcode readers and their types, from module 18's last two bytes."""
    readers, types = data[-2], data[-1]
    self.barcode_readers = {
      reader + 1: _BARCODE_READER_TYPES[types >> (2 * reader) & 0x03]
      for reader in range(4)
      if _bit(readers, reader)
    }

  def _update_from_spectrometer(self, data: bytes) -> None:
    """The spectrometer and its longest wavelength, from module 31's last 11 bytes."""
    self.spectrometer = _bit(data[-11], 0)
    self.spectrometer_maximum_wavelength = int.from_bytes(data[-6:-4], "big")

  def _update_from_monochromator(self, data: bytes) -> None:
    """The slides' named positions and the linear variable filters, from module 47: the slides'
    text, then a byte with the linear variable filters in bit 0."""
    text, separator, rest = data.partition(b"\x00")
    if not separator or not rest:
      raise ValueError("module 47 has no slides byte after its text")
    self.monochromator_slides = _parse_monochromator_slides(text.decode("ascii", errors="replace"))
    self.linear_variable_filters = _bit(rest[0], 0)

  def _update_from_atmospheric_control_unit(self, data: bytes) -> None:
    """The atmospheric control unit, from module 49's seventh byte from the end, bit 0."""
    self.atmospheric_control_unit_connected = _bit(data[-7], 0)

  def _update_from_stirrer(self, data: bytes) -> None:
    """The stirrer, from module 98's second byte from the end, bit 0."""
    self.stirrer = _bit(data[-2], 0)

  def update_from_modules(self, modules: Dict[int, bytes]) -> None:
    """Fill the fitted-hardware fields from the reply's modules.

    Each module is decoded on its own: one missing leaves its fields None; one shorter than its
    fields leaves them None and logs a warning.
    """
    decoders: Tuple[Tuple[int, int, Callable[[bytes], None]], ...] = (
      (_DETECTORS_MODULE, 1, self._update_from_detectors),
      (_TEMPERATURE_CONTROLLER_MODULE, 1, self._update_from_temperature_controller),
      (_PUMPS_MODULE, 1, self._update_from_pumps),
      (_STACKER_MODULE, 3, self._update_from_stacker),
      (_BARCODE_READERS_MODULE, 2, self._update_from_barcode_readers),
      (_SPECTROMETER_MODULE, 11, self._update_from_spectrometer),
      (_MONOCHROMATOR_MODULE, 2, self._update_from_monochromator),
      (_ATMOSPHERIC_CONTROL_UNIT_MODULE, 7, self._update_from_atmospheric_control_unit),
      (_STIRRER_MODULE, 2, self._update_from_stirrer),
    )
    for number, length, update in decoders:
      data = modules.get(number)
      if data is None:
        continue
      try:
        if len(data) < length:
          raise ValueError(f"module {number} is {len(data)} bytes, shorter than {length}")
        update(data)
      except ValueError as error:
        logger.warning("%s; its fields stay unknown", error)


def get_modules(payload: bytes) -> Dict[int, bytes]:
  """The modules of a HardwareInfoResponse payload, by module number.

  Raises:
    ValueError: if a module runs past the end of the payload.
  """
  modules: Dict[int, bytes] = {}
  i = 0
  while i + 3 <= len(payload):
    number = payload[i]
    length = int.from_bytes(payload[i + 1 : i + 3], "big")
    if i + 3 + length > len(payload):
      raise ValueError(f"module {number} runs past the end of the HardwareInfoResponse")
    modules[number] = payload[i + 3 : i + 3 + length]
    i += 3 + length
  return modules


def parse_hardware_info(reply: bytes) -> VANTAstarConfiguration:
  """Parse a HardwareInfoResponse into the reader's configuration.

  Raises:
    ValueError: if ``reply`` is not a HardwareInfoResponse, is malformed, or has no module 2.
  """
  if not reply or reply[0] != HARDWARE_INFO_ID:
    raise ValueError(f"not a HardwareInfoResponse: {reply[:16].hex()}")
  _, _, payload = split_reply(reply)
  modules = get_modules(payload)
  if _MAINBOARD_MODULE not in modules:
    raise ValueError("HardwareInfoResponse has no mainboard module")
  m = modules[_MAINBOARD_MODULE]
  if len(m) <= _OFF_SERIAL_STRING:
    raise ValueError("HardwareInfoResponse mainboard module too short")
  end = m.find(b"\x00", _OFF_SERIAL_STRING)
  if end == -1:
    end = len(m)
  serial = m[_OFF_SERIAL_STRING:end].decode("ascii", errors="replace").strip()
  package_offset = end + 2
  configuration = VANTAstarConfiguration(
    firmware_version=int.from_bytes(m[_OFF_FW_VERSION : _OFF_FW_VERSION + 2], "big"),
    firmware_build=int.from_bytes(m[_OFF_FW_BUILD : _OFF_FW_BUILD + 2], "big"),
    firmware_package=(
      int.from_bytes(m[package_offset : package_offset + 2], "big")
      if len(m) >= package_offset + 2
      else None
    ),
    protocol_version=m[_OFF_PROTOCOL_VERSION],
    mainboard_version=m[_OFF_MAINBOARD_VERSION],
    cpu_board_version=m[_OFF_CPU_BOARD_VERSION],
    serial_number=serial or None,
    modules=modules,
  )
  configuration.update_from_modules(modules)
  return configuration


# What is fitted: compared between a declared configuration and the reader at setup.  Firmware is
# not, so a configuration taken off one reader still describes another of its build.
FITTED_FIELDS = (
  "photomultipliers",
  "temperature_controller",
  "temperature_controller_channels",
  "pumps",
  "spectrometer",
  "spectrometer_maximum_wavelength",
  "monochromator_slides",
  "linear_variable_filters",
  "atmospheric_control_unit_connected",
  "stirrer",
  "stacker_connected",
  "barcode_readers",
)


SERIAL_NUMBER_PLACEHOLDER = "0000-000000"


def configuration_to_json(configuration: VANTAstarConfiguration) -> Dict[str, Any]:
  """The fields as JSON values, as saved: the serial number a placeholder, the modules left out
  (they hold the serial numbers of the reader's boards)."""
  fields = dataclasses.asdict(configuration)
  fields["serial_number"] = SERIAL_NUMBER_PLACEHOLDER
  del fields["modules"]
  return dict(json.loads(json.dumps(fields)))


def configuration_from_json(fields: Dict[str, Any]) -> VANTAstarConfiguration:
  """A configuration from saved fields, with JSON's lists and string keys turned back."""
  configuration = VANTAstarConfiguration(**fields)
  if configuration.pumps is not None:
    pump_1, pump_2 = configuration.pumps
    configuration.pumps = (pump_1, pump_2)
  if configuration.monochromator_slides is not None:
    configuration.monochromator_slides = {
      slide: {int(position): name for position, name in positions.items()}
      for slide, positions in configuration.monochromator_slides.items()
    }
  if configuration.barcode_readers is not None:
    configuration.barcode_readers = {
      int(reader): scanner for reader, scanner in configuration.barcode_readers.items()
    }
  return configuration


def read_configuration(path: str) -> Dict[str, Any]:
  """Read a configuration written by ``VANTAstarDriver.save_configuration``."""
  with open(path, encoding="utf-8") as f:
    return dict(json.load(f))


def compute_recording_name(configuration: VANTAstarConfiguration) -> str:
  """The file name a saved configuration goes by: firmware and the fitted modules.

  ``vantastar_<firmware>[_temperature_controller][_pumps_N][_stacker][_atmospheric_control]
  [_stirrer].json``, e.g. ``vantastar_1_12_temperature_controller.json``.

  Raises:
    ValueError: if the firmware version is not read yet.
  """
  if configuration.firmware_version is None:
    raise ValueError("the firmware version is not read yet")
  parts = ["vantastar", f"{configuration.firmware_version / 1000:.2f}".replace(".", "_")]
  if configuration.temperature_controller:
    parts.append("temperature_controller")
  pumps = sum(pump is not None for pump in configuration.pumps or ())
  if pumps:
    parts.append(f"pumps_{pumps}")
  if configuration.stacker_connected:
    parts.append("stacker")
  if configuration.atmospheric_control_unit_connected:
    parts.append("atmospheric_control")
  if configuration.stirrer:
    parts.append("stirrer")
  return "_".join(parts) + ".json"
