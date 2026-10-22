"""What the CLARIOstar answered about itself."""

import dataclasses
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

INIT_REPLY_TYPE = 0x13

# The firmware whose replies are decoded, as (lowest, highest) firmware number x 1000: the 1.x line.
# Older firmware is untested; 2.x (the line with native USB) lays its replies out unseen.
DECODED_FIRMWARE = (1300, 1999)

_BARCODE_READER_TYPES = {0: "SE 923", 1: "CSE 600", 2: "SE 955", 3: "SE 965"}
BARCODE_READER_POSITIONS = ("front", "right", "optional 1", "optional 2")


# The INIT reply's blocks by id, named after the module each describes.
_BLOCK_NAMES = {
  0x01: "main board",
  0x09: "detectors",
  0x0E: "temperature controller",
  0x0F: "pumps",
  0x11: "stacker",
  0x12: "barcode readers",
  0x1F: "spectrometer",
  0x2F: "slides",
  0x31: "atmospheric control unit",
}


def _bit(value: int, bit: int) -> bool:
  return bool(value >> bit & 1)


def _blocks(payload: bytes) -> Iterator[Tuple[int, bytes]]:
  """Yield the INIT reply's blocks, one per module: `[id][length u16 BE][data]`, from byte 6.

  Raises:
    ValueError: if the payload is not an INIT reply or its blocks do not end with it.
  """
  if not payload or payload[0] != INIT_REPLY_TYPE:
    raise ValueError(f"not an INIT reply: {payload[:1].hex()}")
  offset = 6
  while offset < len(payload):
    length = int.from_bytes(payload[offset + 1 : offset + 3], "big")
    data = payload[offset + 3 : offset + 3 + length]
    if offset + 3 > len(payload) or len(data) != length:
      raise ValueError(f"INIT reply cut short in the block at byte {offset}")
    yield payload[offset], data
    offset += 3 + length


def _block(blocks: Dict[int, bytes], block_id: int, length: int) -> bytes:
  """Return a block's data, at least `length` bytes long.

  Raises:
    ValueError: if the reply has no such block, or a shorter one.
  """
  data = blocks.get(block_id, b"")
  if len(data) < length:
    name = _BLOCK_NAMES.get(block_id, "unknown")
    raise ValueError(f"INIT reply has no {name} block (0x{block_id:02x}) of {length} bytes")
  return data


def _pump_type(types: int, pump: int) -> str:
  """The type of pump 0-2, from its two bits in the pump types byte."""
  if _bit(types, 2 * pump + 1):
    return "NextGen"
  return "XCalibur" if _bit(types, 2 * pump) else "XP3000"


def _parse_monochromator_slides(text: str) -> Dict[str, Dict[int, str]]:
  """Parse `NAME:1=open,2=Filter 1,...` lines into {slide: {position: name}}.

  An entry without a whole position number is left out.
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
class DeviceConfiguration:
  """What the reader reported about itself.

  The fitted hardware comes from the INIT reply (`update_from_init_reply`), block by block; the
  rest from the stored parameters (`update_from_parameters`). A field is None until read,
  or when its block is missing from the reply.
  """

  model: Optional[str] = None
  # The reader's serial, from its stored parameters; a saved file holds `SERIAL_NUMBER_PLACEHOLDER`.
  serial_number: Optional[str] = None
  mainboard_revision: Optional[int] = None
  firmware_version: Optional[str] = None
  firmware_build: Optional[int] = None
  # The day the firmware was compiled, ISO "YYYY-MM-DD": the same on every reader running it.
  firmware_build_date: Optional[str] = None
  extended_red_photomultiplier_tube: Optional[bool] = None
  dual_photomultiplier_tubes: Optional[bool] = None
  # The dual photomultipliers swapped (cross-over): fluorescence on the standard one.
  photomultiplier_tube_crossover: Optional[bool] = None
  # What it counts is not verified: each slide reports 3 positions.
  excitation_slide_positions: Optional[int] = None
  spectrometer: Optional[bool] = None
  # The reader's slides by name, each with its positions: {slide: {position: name}}.
  monochromator_slides: Optional[Dict[str, Dict[int, str]]] = None
  # The monochromators: one bit for excitation, dichroic and emission, read as all or none.
  linear_variable_filters: Optional[bool] = None
  temperature_controller: Optional[bool] = None
  # What they count is not verified.
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
  # Polarization option 2: ultraviolet as well.
  fluorescence_polarization_ultraviolet_available: Optional[bool] = None
  bottom_measurement_available: Optional[bool] = None
  # The temperature controller from 10 instead of 25 C, up to 65 instead of 45 C.
  extended_temperature_range: Optional[bool] = None
  high_speed_shaking_allowed: Optional[bool] = None
  well_1536_plates_supported: Optional[bool] = None

  def update_from_parameters(self, parameters: Dict[str, int]) -> None:
    """Fill the fields the stored parameters carry, from
    `CLARIOstarDriver.request_parameters`."""
    self.fluorescence_channels = parameters["fluorescence_measurement_channel_count"]
    self.luminescence_channels = parameters["luminescence_measurement_channel_count"]
    self.time_resolved_fluorescence_channels = parameters[
      "time_resolved_fluorescence_measurement_channel_count"
    ]
    self.absorbance_available = parameters["absorbance_measurement_option"] != 0
    self.alpha_screen_available = parameters["alpha_screen_measurement_option"] != 0
    # 0 none, 1 visible range, 2 ultraviolet and visible range.
    polarization = parameters["fluorescence_polarization_measurement_option"]
    self.fluorescence_polarization_available = polarization != 0
    self.fluorescence_polarization_ultraviolet_available = polarization == 2
    self.bottom_measurement_available = parameters["bottom_measurement_option"] != 0
    self.extended_temperature_range = parameters["extended_incubator"] != 0
    self.high_speed_shaking_allowed = parameters["high_speed_shaking_allowed"] != 0
    self.well_1536_plates_supported = parameters["well_1536_measurements_enabled"] != 0

  def _update_from_detectors(self, blocks: Dict[int, bytes]) -> None:
    """The photomultipliers and the excitation slide, from block `09` byte 0."""
    measurement = _block(blocks, 0x09, 1)[0]
    self.dual_photomultiplier_tubes = _bit(measurement, 2)
    self.extended_red_photomultiplier_tube = _bit(measurement, 1) or _bit(measurement, 2)
    self.photomultiplier_tube_crossover = _bit(measurement, 1) and _bit(measurement, 2)
    self.excitation_slide_positions = 4 if _bit(measurement, 3) else 3

  def _update_from_spectrometer(self, blocks: Dict[int, bytes]) -> None:
    """The absorbance spectrometer, from block `1F` bit 0."""
    self.spectrometer = _bit(_block(blocks, 0x1F, 1)[0], 0)

  def _update_from_monochromator_slides(self, blocks: Dict[int, bytes]) -> None:
    """The slides' named positions and the linear variable filters, from block `2F`."""
    text, _, slide_info = _block(blocks, 0x2F, 1).partition(b"\x00")
    self.monochromator_slides = _parse_monochromator_slides(text.decode("ascii", errors="replace"))
    self.linear_variable_filters = bool(slide_info) and _bit(slide_info[0], 0)

  def _update_from_temperature_controller(self, blocks: Dict[int, bytes]) -> None:
    """The temperature controller and its channels, from block `0E`."""
    temperature_controller = _block(blocks, 0x0E, 1)[0]
    self.temperature_controller = _bit(temperature_controller, 0)
    self.temperature_controller_channels = (temperature_controller & 0x70) >> 4

  def _update_from_pumps(self, blocks: Dict[int, bytes]) -> None:
    """The injector pumps 1-3 and their types, from block `0F`."""
    pumps, pump_types = _block(blocks, 0x0F, 2)[:2]
    pump_1, pump_2, pump_3 = (
      _pump_type(pump_types, pump) if _bit(pumps, pump) else None for pump in range(3)
    )
    self.pumps = (pump_1, pump_2, pump_3)

  def _update_from_stacker(self, blocks: Dict[int, bytes]) -> None:
    """The stacker, from block `11` bit 0."""
    self.stacker_connected = _bit(_block(blocks, 0x11, 1)[0], 0)

  def _update_from_barcode_readers(self, blocks: Dict[int, bytes]) -> None:
    """The barcode readers by position and type, from block `12`."""
    readers, reader_types = _block(blocks, 0x12, 2)[:2]
    self.barcode_readers = {
      position: _BARCODE_READER_TYPES[reader_types >> (2 * index) & 0x03]
      for index, position in enumerate(BARCODE_READER_POSITIONS)
      if _bit(readers, index)
    }

  def _update_from_atmospheric_control_unit(self, blocks: Dict[int, bytes]) -> None:
    """The atmospheric control unit, from block `31` bit 0."""
    self.atmospheric_control_unit_connected = _bit(_block(blocks, 0x31, 1)[0], 0)

  def update_from_init_reply(self, payload: bytes) -> None:
    """Fill the fields the INIT reply carries.

    Each block is decoded on its own: one missing or shorter than its fields leaves them None
    and logs a warning; the others are filled.

    Raises:
      ValueError: if the payload is not a whole INIT reply, or comes from firmware outside
        `DECODED_FIRMWARE`; the main board fields are filled either way.
    """
    blocks = dict(_blocks(payload))
    main_board = _block(blocks, 0x01, 5)
    firmware = int.from_bytes(main_board[0:2], "big")
    self.firmware_version = f"{firmware / 1000:.2f}"
    self.firmware_build = main_board[2]
    self.mainboard_revision = main_board[4]
    self.model = "CLARIOstarPlus" if main_board[4] >= 2 else "CLARIOstar"
    if not DECODED_FIRMWARE[0] <= firmware <= DECODED_FIRMWARE[1]:
      raise ValueError(f"INIT reply of firmware {self.firmware_version} is not decoded here")
    for update in (
      self._update_from_detectors,
      self._update_from_spectrometer,
      self._update_from_monochromator_slides,
      self._update_from_temperature_controller,
      self._update_from_pumps,
      self._update_from_stacker,
      self._update_from_barcode_readers,
      self._update_from_atmospheric_control_unit,
    ):
      try:
        update(blocks)
      except ValueError as error:
        logger.warning("%s; its fields stay unknown", error)


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
