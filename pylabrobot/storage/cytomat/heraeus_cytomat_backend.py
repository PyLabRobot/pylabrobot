import asyncio
import logging
import time
import warnings
from typing import List, Tuple

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.storage.backend import IncubatorBackend

logger = logging.getLogger(__name__)


class HeraeusCytomatBackend(IncubatorBackend):
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
    super().__init__()
    self.io = Serial(
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=True,
    )

  async def setup(self) -> Serial:
    """
    1. Open serial port (9600 8E1, RTS/CTS) via the Serial wrapper.
    2. Send >200 ms break, wait 150 ms, flush buffers.
    3. Handshake: CR → wait for CC<CR><LF>
    4. Activate handling: ST 1801 → expect OK<CR><LF>
    5. Poll ready-flag: RD 1915 → wait for "1"<CR><LF>
    """
    try:
      await self.io.setup()
    except serial.SerialException as e:
      raise RuntimeError(f"Could not open {self.io.port}: {e}")

    await self.io.send_break(duration=0.2)  # >100 ms required
    await asyncio.sleep(0.15)
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    await self.io.write(b"CR\r")
    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      resp = await self.io.readline()  # reads through LF
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
        return self.io
      await asyncio.sleep(self.poll_interval)

    await self.io.stop()
    raise TimeoutError(f"PLC did not signal ready within {self.start_timeout} seconds")

  async def stop(self):
    await self.io.stop()

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    warnings.warn("Cytomat racks need to be configured manually on each setup")

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

  async def fetch_plate_to_loading_tray(self, plate: Plate, site=PlateHolder):
    """Fetch a plate from storage onto the transfer station, with gate open/close."""
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")  # carousel pos
    await self._send_command(f"WR DM5 {n}")  # handler level
    await self._send_command("ST 1905")  # plate to transfer station
    await self._wait_ready()
    await self._send_command("ST 1903")  # terminate access

  async def take_in_plate(self, plate: Plate, site: PlateHolder):
    """Place a plate from the transfer station into storage at the given site."""
    m, n = self._site_to_m_n(site)
    await self._send_command(f"WR DM0 {m}")  # carousel pos
    await self._send_command(f"WR DM5 {n}")  # handler level
    await self._send_command("ST 1904")  # plate to storage
    await self._wait_ready()
    await self._send_command("ST 1903")  # terminate access

  async def set_temperature(self, temperature: float):
    raise NotImplementedError("Temperature control not implemented yet")

  async def get_temperature(self) -> float:
    raise NotImplementedError("Temperature query not implemented yet")

  async def start_shaking(self, frequency: float = 1.0):
    await self._send_command("ST 1607")
    await self._wait_ready()

  async def stop_shaking(self):
    await self._send_command("RS 1607")
    await self._wait_ready()

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1  # plr is 0-indexed, cytomat is 1-indexed
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1  # 1-indexed
    return rack_idx, site_idx

  async def _send_command(self, command: str) -> str:
    """
    Send an ASCII command (without CR) and return the raw response string.
    """
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
    """Read Plate Detection Transfer Station (RD 1813)."""
    resp = await self._send_command("RD 1813")
    return resp == "1"

  async def _wait_ready(self, timeout: int = 60):
    """
    Poll the ready flag (RD 1915) until it becomes '1' or timeout.
    """
    start = time.time()
    while True:
      resp = await self._send_command("RD 1915")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
      if time.time() - start > timeout:
        raise TimeoutError("Legacy Cytomat did not become ready in time")

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.io.port,
    }

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"])
