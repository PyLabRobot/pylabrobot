import asyncio
import logging
import re
import time
import warnings
from typing import List, Optional, Tuple, Union

import serial

from pylabrobot.capabilities.automated_retrieval.backend import AutomatedRetrievalBackend
from pylabrobot.capabilities.humidity_controlling.backend import HumidityControllerBackend
from pylabrobot.capabilities.shaking.backend import ShakerBackend
from pylabrobot.capabilities.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.io.serial import Serial
from pylabrobot.machines.backend import MachineBackend
from pylabrobot.capabilities.barcode_scanning import BarcodeScannerBackend
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.carrier import PlateCarrier

from .constants import ControllerError, HandlingError, LiconicType
from .errors import controller_error_map, handler_error_map

logger = logging.getLogger(__name__)

# Mapping site_height to motor steps for Liconic cassettes
LICONIC_SITE_HEIGHT_TO_STEPS = {
  5: 377,  # pitch=11, site_height=5
  11: 582,  # pitch=17, site_height=11
  12: 617,  # pitch=18, site_height=12
  17: 788,  # pitch=23, site_height=17
  22: 959,  # pitch=28, site_height=22
  23: 994,  # pitch=29, site_height=23
  24: 1028,  # pitch=30, site_height=24
  27: 1131,  # pitch=33, site_height=27
  44: 1713,  # pitch=50, site_height=44
  53: 2021,  # pitch=59, site_height=53
  66: 2467,  # pitch=72, site_height=66
  104: 3563,  # pitch=110, site_height=104
}



class LiconicBackend(
  AutomatedRetrievalBackend,
  TemperatureControllerBackend,
  HumidityControllerBackend,
  ShakerBackend,
):
  """Backend for Liconic incubators."""

  default_baud = 9600
  serial_message_encoding = "ascii"
  init_timeout = 1.0
  start_timeout = 15.0
  poll_interval = 0.2

  def __init__(
    self,
    model: Union[LiconicType, str],
    port: str,
  ):
    super().__init__()

    if isinstance(model, str):
      try:
        model = LiconicType(model)
      except ValueError:
        raise ValueError(f"Unsupported Liconic model: '{model}'")

    self.model = model
    self._racks: List[PlateCarrier] = []

    self.io = Serial(
      human_readable_device_name=f"Liconic {model.value}",
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=True,
    )

    self.co2_installed: Optional[bool] = None
    self.n2_installed: Optional[bool] = None

  async def setup(self):
    await MachineBackend.setup(self)
    try:
      await self.io.setup()
    except serial.SerialException as e:
      raise RuntimeError(f"Could not open {self.io.port}: {e}") from e

    await self.io.send_break(duration=0.2)
    await asyncio.sleep(0.15)
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    await self.io.write(b"CR\r")
    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      resp = await self.io.readline()
      if resp.strip() == b"CC":
        break
    else:
      await self.io.stop()
      raise TimeoutError(f"No CC response from Liconic PLC within {self.init_timeout} seconds")

    await self.io.write(b"ST 1801\r")
    resp = await self.io.readline()
    if resp.strip() != b"OK":
      await self.io.stop()
      raise RuntimeError(f"Unexpected reply to ST 1801: {resp!r}")

    deadline = time.time() + self.start_timeout
    while time.time() < deadline:
      await self.io.write(b"RD 1915\r")
      flag = await self.io.readline()
      if flag.strip() == b"1":
        return
      await asyncio.sleep(self.poll_interval)

    await self.io.stop()
    raise TimeoutError(f"PLC did not signal ready within {self.start_timeout} seconds")

  async def stop(self):
    await self.io.stop()
    await MachineBackend.stop(self)

  async def set_racks(self, racks: List[PlateCarrier]):
    self._racks = racks
    warnings.warn("Liconic racks need to be configured manually on each setup")

  # -- AutomatedRetrievalBackend --

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"

    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)

    await self._send_command(f"WR DM0 {m}")
    await self._send_command(f"WR DM23 {step_size}")
    await self._send_command(f"WR DM25 {pos_num}")
    await self._send_command(f"WR DM5 {n}")

    await self._send_command("ST 1905")
    await self._wait_ready()
    await self._send_command("ST 1903")

  async def store_plate(self, plate: Plate, site: PlateHolder):
    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)

    await self._send_command(f"WR DM0 {m}")
    await self._send_command(f"WR DM23 {step_size}")
    await self._send_command(f"WR DM25 {pos_num}")
    await self._send_command(f"WR DM5 {n}")
    await self._send_command("ST 1904")
    await self._wait_ready()
    await self._send_command("ST 1903")

  # -- TemperatureControllerBackend --

  @property
  def supports_active_cooling(self) -> bool:
    return self.model.has_active_cooling

  async def set_temperature(self, temperature: float):
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    temp_value = int(temperature * 10)
    temp_str = str(temp_value).zfill(5)
    await self._send_command(f"WR DM890 {temp_str}")
    await self._wait_ready()

  async def get_current_temperature(self) -> float:
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    resp = await self._send_command("RD DM982")
    try:
      return int(resp) / 10.0
    except ValueError:
      raise RuntimeError(f"Invalid temperature value received from incubator: {resp!r}")

  async def deactivate(self):
    pass  # no-op

  # -- HumidityControllerBackend --

  @property
  def supports_humidity_control(self) -> bool:
    return self.model.has_humidity_control

  async def set_humidity(self, humidity: float):
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    humidity_val = int(humidity * 1000)
    await self._send_command(f"WR DM893 {str(humidity_val).zfill(5)}")
    await self._wait_ready()

  async def get_current_humidity(self) -> float:
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    resp = await self._send_command("RD DM983")
    try:
      return int(resp) / 1000.0
    except ValueError:
      raise RuntimeError(f"Invalid humidity value received from incubator: {resp!r}")

  # -- ShakerBackend --

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    raise NotImplementedError("Liconic does not support plate locking")

  async def unlock_plate(self):
    raise NotImplementedError("Liconic does not support plate locking")

  async def start_shaking(self, speed: float):
    if speed < 1.0 or speed > 50.0:
      raise ValueError("Shaking frequency must be between 1.0 and 50.0 Hz")
    frequency_value = int(speed * 10)
    await self._send_command(f"WR DM39 {str(frequency_value).zfill(5)}")
    await self._send_command("ST 1913")
    await self._wait_ready()

  async def stop_shaking(self):
    await self._send_command("RS 1913")
    await self._wait_ready()

  # -- Device-specific methods --

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1
    return rack_idx, site_idx

  def _carrier_to_steps_pos(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    if rack.model is None or not rack.model.startswith("liconic"):
      raise ValueError(f"The plate carrier used: {rack.model} is not compatible with the Liconic")
    match = re.search(r"_(\d+)mm", rack.model)
    if match:
      site_height = int(match.group(1))
      site_num = int(rack.model.split("_")[-1])
      if site_height not in LICONIC_SITE_HEIGHT_TO_STEPS:
        raise ValueError(
          f"Unknown site height {site_height}mm - not in LICONIC_SITE_HEIGHT_TO_STEPS"
        )
      return LICONIC_SITE_HEIGHT_TO_STEPS[site_height], site_num
    raise ValueError(
      f"Could not parse site height and pos num from PlateCarrier model: {rack.model}"
    )

  async def _send_command(self, command: str) -> str:
    cmd = command.strip() + "\r"
    logger.debug("Sending command to Liconic PLC: %r", cmd)
    await self.io.write(cmd.encode(self.serial_message_encoding))
    resp = (await self.io.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError(f"No response from Liconic PLC for command {command!r}")
    resp = resp.strip()
    if resp.startswith("E"):
      logger.error("Command %s failed with %s", command, resp)
      for member in ControllerError:
        if resp == member.value:
          cls, msg = controller_error_map[member]
          raise cls(msg)
      raise RuntimeError(f"Unknown error {resp} when sending command {command}")
    return resp

  async def _wait_plate_ready(self, timeout: int = 60):
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
      resp = await self._send_command("RD 1914")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
    raise TimeoutError(f"Plate did not become ready within {timeout} seconds")

  async def _wait_ready(self, timeout: int = 60):
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
      resp = await self._send_command("RD 1915")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
    err_flag = await self._send_command("RD 1814")
    if err_flag == "1":
      error = await self._send_command("RD DM200")
      for member in HandlingError:
        if error == member.value:
          cls, msg = handler_error_map[member]
          raise cls(msg)
      raise RuntimeError(f"Liconic Handler in unknown error state with memory showing {error}")
    raise TimeoutError(f"Incubator did not become ready within {timeout} seconds")

  async def initialize(self):
    await self._send_command("ST 1900")
    await self._send_command("ST 1801")
    await self._wait_ready()

  async def open_door(self):
    await self._send_command("ST 1901")
    await self._wait_ready()

  async def close_door(self):
    await self._send_command("ST 1902")
    await self._wait_ready()

  async def move_position_to_position(self, plate: Plate, dest_site: PlateHolder):
    orig_site = plate.parent
    assert isinstance(orig_site, PlateHolder)
    assert isinstance(dest_site, PlateHolder)

    if dest_site.resource is not None:
      raise RuntimeError(f"Position {dest_site} already has a plate assigned!")

    orig_m, orig_n = self._site_to_m_n(orig_site)
    dest_m, dest_n = self._site_to_m_n(dest_site)
    orig_step_size, orig_pos_num = self._carrier_to_steps_pos(orig_site)
    dest_step_size, dest_pos_num = self._carrier_to_steps_pos(dest_site)

    await self._send_command(f"WR DM0 {orig_m}")
    await self._send_command(f"WR DM23 {orig_step_size}")
    await self._send_command(f"WR DM25 {orig_pos_num}")
    await self._send_command(f"WR DM5 {orig_n}")
    await self._send_command("ST 1908")
    await self._wait_ready()

    if orig_m != dest_m:
      await self._send_command(f"WR DM0 {dest_m}")
    await self._send_command(f"WR DM23 {dest_step_size}")
    await self._send_command(f"WR DM25 {dest_pos_num}")
    await self._send_command(f"WR DM5 {dest_n}")
    await self._send_command("ST 1909")
    await self._wait_ready()
    await self._send_command("ST 1903")

  async def read_barcode_inline(
    self, cassette: int, plt_position: int, barcode_scanner: BarcodeScannerBackend
  ) -> Barcode:
    await self._send_command("ST 1910")
    await self._wait_ready()
    barcode = await barcode_scanner.scan_barcode()
    logger.info(
      "Read barcode from plate at cassette %d, position %d: %s",
      cassette, plt_position, barcode.data,
    )
    reset = await self._send_command("RS 1910")
    if reset != "OK":
      raise RuntimeError("Failed to reset shovel position after barcode reading")
    await self._wait_ready()
    return barcode

  async def scan_barcode(self, site: PlateHolder, barcode_scanner: BarcodeScannerBackend) -> Barcode:
    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)
    await self._send_command(f"WR DM0 {m}")
    await self._send_command(f"WR DM23 {step_size}")
    await self._send_command(f"WR DM25 {pos_num}")
    await self._send_command(f"WR DM5 {n}")
    await self._send_command("ST 1910")
    barcode = await barcode_scanner.scan_barcode()
    logger.info("Scanned barcode: %s", barcode.data)
    return barcode

  async def get_target_temperature(self) -> float:
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    resp = await self._send_command("RD DM890")
    try:
      return int(resp) / 10.0
    except ValueError:
      raise RuntimeError(f"Invalid set temperature value received from incubator: {resp!r}")

  async def get_target_humidity(self) -> float:
    if not self.model.has_temperature_control:
      raise NotImplementedError("Climate control is not supported on this model")
    resp = await self._send_command("RD DM893")
    try:
      return int(resp) / 1000.0
    except ValueError:
      raise RuntimeError(f"Invalid set humidity value received from incubator: {resp!r}")

  async def get_shaker_speed(self) -> float:
    speed_val = await self._send_command("RD DM39")
    speed = int(speed_val) / 10.0
    await self._wait_ready()
    return speed

  async def set_co2_level(self, co2_level: float):
    co2_val = int(co2_level * 10000)
    await self._send_command(f"WR DM894 {str(co2_val).zfill(5)}")
    await self._wait_ready()

  async def get_co2_level(self) -> float:
    resp = await self._send_command("RD DM984")
    try:
      return int(resp) / 10000.0
    except ValueError:
      raise RuntimeError(f"Invalid co2 value received from incubator: {resp!r}")

  async def get_target_co2_level(self) -> float:
    resp = await self._send_command("RD DM894")
    try:
      return int(resp) / 10000.0
    except ValueError:
      raise RuntimeError(f"Invalid co2 set value received from incubator: {resp!r}")

  async def set_n2_level(self, n2_level: float):
    n2_val = int(n2_level * 10000)
    await self._send_command(f"WR DM895 {str(n2_val).zfill(5)}")
    await self._wait_ready()

  async def get_n2_level(self) -> float:
    resp = await self._send_command("RD DM985")
    try:
      return int(resp) / 10000.0
    except ValueError:
      raise RuntimeError(f"Invalid N2 value received from incubator: {resp!r}")

  async def get_target_n2_level(self) -> float:
    resp = await self._send_command("RD DM895")
    try:
      return int(resp) / 10000.0
    except ValueError:
      raise RuntimeError(f"Invalid N2 set value received from incubator: {resp!r}")

  async def turn_swap_station(self, home: bool):
    resp = await self._send_command("RD 1912")
    if home and resp == "1":
      await self._send_command("RS 1912")
    else:
      await self._send_command("ST 1912")

  async def check_shovel_sensor(self) -> bool:
    await self._send_command("ST 1911")
    await asyncio.sleep(0.1)
    resp = await self._send_command("RD 1812")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from incubator read shovel sensor: {resp!r}")

  async def check_transfer_sensor(self) -> bool:
    resp = await self._send_command("RD 1813")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read transfer station sensor: {resp!r}")

  async def check_second_transfer_sensor(self) -> bool:
    resp = await self._send_command("RD 1807")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read 2nd transfer station sensor: {resp!r}")

  def serialize(self) -> dict:
    return {
      **MachineBackend.serialize(self),
      "port": self.io.port,
      "model": self.model.value,
    }

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"], model=data["model"])
