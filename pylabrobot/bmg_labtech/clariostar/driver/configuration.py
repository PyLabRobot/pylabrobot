"""What the CLARIOstar answered about itself."""

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

INIT_REPLY_TYPE = 0x13

# The firmware whose replies are decoded, as (lowest, highest) firmware number x 1000: the 1.x line.
# Older firmware is untested; 2.x (the line with native USB) lays its replies out unseen.
DECODED_FIRMWARE = (1300, 1999)

_BARCODE_READER_TYPES = {0: "SE 923", 1: "CSE 600", 2: "SE 955", 3: "SE 965"}
_BARCODE_READER_POSITIONS = ("front", "right", "optional 1", "optional 2")


def _bit(value: int, bit: int) -> bool:
  return bool(value >> bit & 1)


def _subsystems(payload: bytes) -> Iterator[Tuple[int, bytes]]:
  """Yield the INIT reply's blocks, one per subsystem: `[id][length u16 BE][data]`, from byte 6.

  Raises:
    ValueError: if the payload is not an INIT reply or its subsystems do not end with it.
  """
  if not payload or payload[0] != INIT_REPLY_TYPE:
    raise ValueError(f"not an INIT reply: {payload[:1].hex()}")
  offset = 6
  while offset < len(payload):
    length = int.from_bytes(payload[offset + 1 : offset + 3], "big")
    data = payload[offset + 3 : offset + 3 + length]
    if offset + 3 > len(payload) or len(data) != length:
      raise ValueError(f"INIT reply cut short in the subsystem at byte {offset}")
    yield payload[offset], data
    offset += 3 + length


def _subsystem(subsystems: Dict[int, bytes], subsystem_id: int, length: int) -> bytes:
  """Return a subsystem's data, at least `length` bytes long.

  Raises:
    ValueError: if the reply has no such subsystem, or a shorter one.
  """
  data = subsystems.get(subsystem_id, b"")
  if len(data) < length:
    raise ValueError(f"INIT reply has no subsystem 0x{subsystem_id:02x} of {length} bytes")
  return data


def _pump_type(types: int, pump: int) -> str:
  """The type of pump 0-2, from its two bits in the pump types byte."""
  if _bit(types, 2 * pump + 1):
    return "NextGen"
  return "XCalibur" if _bit(types, 2 * pump) else "XP3000"


def _parse_monochromator_slides(text: str) -> Dict[str, Dict[int, str]]:
  """Parse `NAME:1=open,2=Filter 1,...` lines into {slide: {position: name}}."""
  slides: Dict[str, Dict[int, str]] = {}
  for line in text.splitlines():
    name, _, positions = line.partition(":")
    if positions:
      slides[name.strip()] = {
        int(number): label
        for number, _, label in (entry.partition("=") for entry in positions.split(","))
      }
  return slides


@dataclass
class DeviceConfiguration:
  """What the reader reported about itself. None until `initialize` and `discover` have read it.

  The fitted hardware comes from the INIT reply, the rest from the stored parameters.
  """

  model: Optional[str] = None
  # Never read from the reader; a saved file holds `SERIAL_NUMBER_PLACEHOLDER`.
  serial_number: Optional[str] = None
  mainboard_revision: Optional[int] = None
  firmware_version: Optional[str] = None
  extended_red_photomultiplier_tube: Optional[bool] = None
  dual_photomultiplier_tubes: Optional[bool] = None
  excitation_slide_positions: Optional[int] = None
  spectrometer: Optional[bool] = None
  monochromator_slides: Optional[Dict[str, Dict[int, str]]] = None
  linear_variable_filters: Optional[bool] = None
  temperature_controller: Optional[bool] = None
  temperature_controller_channels: Optional[int] = None
  # The type of injector pumps 1-3, None where not built in.
  pumps: Optional[Tuple[Optional[str], Optional[str], Optional[str]]] = None
  stacker_connected: Optional[bool] = None
  # {position: type} for the barcode readers built in.
  barcode_readers: Optional[Dict[str, str]] = None
  atmospheric_control_unit_connected: Optional[bool] = None
  # From the stored parameters.
  fluorescence_channels: Optional[int] = None
  luminescence_channels: Optional[int] = None
  time_resolved_fluorescence_channels: Optional[int] = None
  absorbance_available: Optional[bool] = None
  alpha_screen_available: Optional[bool] = None
  fluorescence_polarization_available: Optional[bool] = None
  fluorescence_polarization_ultraviolet_available: Optional[bool] = None
  bottom_measurement_available: Optional[bool] = None
  extended_temperature_range: Optional[bool] = None
  high_speed_shaking_allowed: Optional[bool] = None
  well_1536_plates_supported: Optional[bool] = None

  def update_from_parameters(self, parameters: Dict[str, int]) -> None:
    """Fill the fields the stored parameters carry, from `CLARIOstarDriver.request_parameters`."""
    self.fluorescence_channels = parameters["No. of fluorescence measurement channels"]
    self.luminescence_channels = parameters["No. of luminescence measurement channels"]
    self.time_resolved_fluorescence_channels = parameters[
      "No. of time-resolved fluorescence meas. channels"
    ]
    self.absorbance_available = parameters["Absorbance measurement option"] != 0
    self.alpha_screen_available = parameters["AlphaScreen measurement option"] != 0
    # 0 none, 1 visible range, 2 ultraviolet and visible range.
    polarization = parameters["Fluorescence polarization measurement option"]
    self.fluorescence_polarization_available = polarization != 0
    self.fluorescence_polarization_ultraviolet_available = polarization == 2
    self.bottom_measurement_available = parameters["Bottom measurement option"] != 0
    self.extended_temperature_range = parameters["Extended incubator"] != 0
    self.high_speed_shaking_allowed = parameters["High speed shaking allowed"] != 0
    self.well_1536_plates_supported = parameters["1536 well measurements enabled"] != 0

  def update_from_init_reply(self, payload: bytes) -> None:
    """Fill the fields the INIT reply carries.

    Raises:
      ValueError: if the payload is not a whole INIT reply, or comes from firmware outside
        `DECODED_FIRMWARE`; the main board fields are filled either way.
    """
    subsystems = dict(_subsystems(payload))
    main_board = _subsystem(subsystems, 0x01, 5)
    firmware = int.from_bytes(main_board[0:2], "big")
    self.firmware_version = f"{firmware / 1000:.2f}"
    self.mainboard_revision = main_board[4]
    self.model = "CLARIOstarPlus" if main_board[4] >= 2 else "CLARIOstar"
    if not DECODED_FIRMWARE[0] <= firmware <= DECODED_FIRMWARE[1]:
      raise ValueError(f"INIT reply of firmware {self.firmware_version} is not decoded here")

    measurement = _subsystem(subsystems, 0x09, 1)[0]
    self.dual_photomultiplier_tubes = _bit(measurement, 2)
    self.extended_red_photomultiplier_tube = _bit(measurement, 1) or _bit(measurement, 2)
    self.excitation_slide_positions = 4 if _bit(measurement, 3) else 3

    self.spectrometer = _bit(_subsystem(subsystems, 0x1F, 1)[0], 0)
    text, _, slide_info = _subsystem(subsystems, 0x2F, 1).partition(b"\x00")
    self.monochromator_slides = _parse_monochromator_slides(text.decode("ascii", errors="replace"))
    self.linear_variable_filters = bool(slide_info) and _bit(slide_info[0], 0)

    temperature_controller = _subsystem(subsystems, 0x0E, 1)[0]
    self.temperature_controller = _bit(temperature_controller, 0)
    self.temperature_controller_channels = (temperature_controller & 0x70) >> 4

    pumps, pump_types = _subsystem(subsystems, 0x0F, 2)[:2]
    pump_1, pump_2, pump_3 = (
      _pump_type(pump_types, pump) if _bit(pumps, pump) else None for pump in range(3)
    )
    self.pumps = (pump_1, pump_2, pump_3)

    self.stacker_connected = _bit(_subsystem(subsystems, 0x11, 1)[0], 0)
    readers, reader_types = _subsystem(subsystems, 0x12, 2)[:2]
    self.barcode_readers = {
      position: _BARCODE_READER_TYPES[reader_types >> (2 * index) & 0x03]
      for index, position in enumerate(_BARCODE_READER_POSITIONS)
      if _bit(readers, index)
    }
    self.atmospheric_control_unit_connected = _bit(_subsystem(subsystems, 0x31, 1)[0], 0)


# What is fitted: compared between a declared configuration and the reader at setup. Firmware is
# not, so a configuration taken off one reader still describes another of its build.
FITTED_FIELDS = (
  "model",
  "extended_red_photomultiplier_tube",
  "dual_photomultiplier_tubes",
  "excitation_slide_positions",
  "spectrometer",
  "monochromator_slides",
  "linear_variable_filters",
  "temperature_controller",
  "temperature_controller_channels",
  "pumps",
  "stacker_connected",
  "barcode_readers",
  "atmospheric_control_unit_connected",
  "fluorescence_channels",
  "luminescence_channels",
  "time_resolved_fluorescence_channels",
  "absorbance_available",
  "alpha_screen_available",
  "fluorescence_polarization_available",
  "fluorescence_polarization_ultraviolet_available",
  "bottom_measurement_available",
  "extended_temperature_range",
  "high_speed_shaking_allowed",
  "well_1536_plates_supported",
)


SERIAL_NUMBER_PLACEHOLDER = "000-0000"


def configuration_to_json(configuration: DeviceConfiguration) -> Dict[str, Any]:
  """The fields as JSON values, as saved: the serial number a placeholder."""
  fields = dataclasses.asdict(configuration)
  fields["serial_number"] = SERIAL_NUMBER_PLACEHOLDER
  return dict(json.loads(json.dumps(fields)))


def configuration_from_json(fields: Dict[str, Any]) -> DeviceConfiguration:
  """A configuration from saved fields, with JSON's lists and string keys turned back."""
  configuration = DeviceConfiguration(**fields)
  if configuration.pumps is not None:
    pump_1, pump_2, pump_3 = configuration.pumps
    configuration.pumps = (pump_1, pump_2, pump_3)
  if configuration.monochromator_slides is not None:
    configuration.monochromator_slides = {
      slide: {int(position): name for position, name in positions.items()}
      for slide, positions in configuration.monochromator_slides.items()
    }
  return configuration


def read_configuration(path: str) -> Dict[str, Any]:
  """Read a configuration written by `CLARIOstarDriver.save_configuration`."""
  with open(path) as f:
    return dict(json.load(f))


def compute_recording_name(configuration: DeviceConfiguration) -> str:
  """The file name a saved configuration goes by: model, firmware and the fitted modules.

  `<model>_<firmware>[_temperature_controller|_extended_temperature_range][_pumps_N][_stacker]
  [_atmospheric_control][_1536].json`, e.g. `clariostar_plus_1_35_temperature_controller.json`.
  """
  if configuration.model is None or configuration.firmware_version is None:
    raise ValueError("model and firmware version are not read yet")
  parts = [
    "clariostar_plus" if configuration.model == "CLARIOstarPlus" else "clariostar",
    configuration.firmware_version.replace(".", "_"),
  ]
  if configuration.temperature_controller:
    parts.append(
      "extended_temperature_range"
      if configuration.extended_temperature_range
      else "temperature_controller"
    )
  pumps = sum(pump is not None for pump in configuration.pumps or ())
  if pumps:
    parts.append(f"pumps_{pumps}")
  if configuration.stacker_connected:
    parts.append("stacker")
  if configuration.atmospheric_control_unit_connected:
    parts.append("atmospheric_control")
  if configuration.well_1536_plates_supported:
    parts.append("1536")
  return "_".join(parts) + ".json"
