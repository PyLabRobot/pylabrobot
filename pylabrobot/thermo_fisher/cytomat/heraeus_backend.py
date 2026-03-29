import asyncio
import logging
import time
import warnings
from typing import List, Tuple

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.capabilities.automated_retrieval.backend import AutomatedRetrievalBackend
from pylabrobot.capabilities.humidity_controlling.backend import HumidityControllerBackend
from pylabrobot.capabilities.shaking.backend import ShakerBackend
from pylabrobot.capabilities.temperature_controlling.backend import TemperatureControllerBackend
from pylabrobot.device import Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier

logger = logging.getLogger(__name__)


class HeraeusCytomatBackend(
  AutomatedRetrievalBackend,
  TemperatureControllerBackend,
  HumidityControllerBackend,
  ShakerBackend,
  Driver,
):
  """
  Backend for legacy (Heraeus) Cytomats.
  Perhaps identical to Liconic backend...
  to configure stackers:
      WR DM25 00007 for setting a stacker config to 10 slots
      WR DM23 06900 for setting pitch to 69 mm
  ** stacker config must be reset after every restart of cytomat **
  """

  default_baud = 9600
  serial_message_encoding = "ascii"
  init_timeout = 1.0
  start_timeout = 15.0
  poll_interval = 0.2

  def __init__(self, port: str):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    super().__init__()
    self._racks: List[PlateCarrier] = []
    self.io = Serial(
      human_readable_device_name="Heraeus Cytomat",
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=True,
    )

  async def setup(self):
    await Driver.setup(self)
    try:
      await self.io.setup()
    except serial.SerialException as e:
      raise RuntimeError(f"Could not open {self.io.port}: {e}")

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
      raise TimeoutError(f"No CC response from PLC within {self.init_timeout} seconds")

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
    await Driver.stop(self)

  async def set_racks(self, racks: List[PlateCarrier]):
    self._racks = racks
    warnings.warn("Cytomat racks need to be configured manually on each setup")

  # -- AutomatedRetrievalBackend --

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")
    await self._send_command(f"WR DM5 {n}")
    await self._send_command("ST 1905")
    await self._wait_ready()
    await self._send_command("ST 1903")

  async def store_plate(self, plate: Plate, site: PlateHolder):
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")
    await self._send_command(f"WR DM5 {n}")
    await self._send_command("ST 1904")
    await self._wait_ready()
    await self._send_command("ST 1903")

  # -- TemperatureControllerBackend --

  @property
  def supports_active_cooling(self) -> bool:
    return False

  async def set_temperature(self, temperature: float):
    raise NotImplementedError("Temperature control not implemented yet")

  async def request_current_temperature(self) -> float:
    raise NotImplementedError("Temperature query not implemented yet")

  async def deactivate(self):
    pass

  # -- HumidityControllerBackend --

  @property
  def supports_humidity_control(self) -> bool:
    return False

  async def set_humidity(self, humidity: float):
    raise NotImplementedError("Humidity control not implemented yet")

  async def request_current_humidity(self) -> float:
    raise NotImplementedError("Humidity query not implemented yet")

  # -- ShakerBackend --

  @property
  def supports_locking(self) -> bool:
    return False

  async def lock_plate(self):
    raise NotImplementedError("Heraeus Cytomat does not support plate locking")

  async def unlock_plate(self):
    raise NotImplementedError("Heraeus Cytomat does not support plate locking")

  async def start_shaking(self, speed: float):
    await self._send_command("ST 1607")
    await self._wait_ready()

  async def stop_shaking(self):
    await self._send_command("RS 1607")
    await self._wait_ready()

  # -- Device-specific methods --

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1
    return rack_idx, site_idx

  async def _send_command(self, command: str) -> str:
    cmd = command.strip() + "\r"
    logger.debug("Sending Cytomat command: %r", cmd)
    await self.io.write(cmd.encode(self.serial_message_encoding))
    resp = (await self.io.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError("No response from Cytomat controller")
    resp = resp.strip()
    if resp.startswith("E"):
      raise RuntimeError(f"Cytomat controller error: {resp}")
    return resp

  async def wait_for_transfer_station(self, occupied: bool = False):
    while (await self.read_plate_detection_xfer()) != occupied:
      await asyncio.sleep(1)

  async def read_plate_detection_xfer(self) -> bool:
    resp = await self._send_command("RD 1813")
    return resp == "1"

  async def _wait_ready(self, timeout: int = 60):
    start = time.time()
    while True:
      resp = await self._send_command("RD 1915")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
      if time.time() - start > timeout:
        raise TimeoutError("Legacy Cytomat did not become ready in time")

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

  def serialize(self) -> dict:
    return {
      **Driver.serialize(self),
      "port": self.io.port,
    }

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"])
