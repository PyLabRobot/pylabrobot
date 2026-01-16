import asyncio
import logging
import time
import warnings
from typing import List, Tuple, Optional

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.storage.backend import IncubatorBackend
from pylabrobot.barcode_scanners.keyence import KeyenceBarcodeScannerBackend

logger = logging.getLogger(__name__)

class LiconicBackend(IncubatorBackend):
  """
  Backend for Liconic incubators.
  Written to connect with internal barcode reader and gas control.
  Barcode reader tested is the Keyence BL-1300
  """

  default_baud = 9600
  serial_message_encoding = "ascii"
  init_timeout = 1.0
  start_timeout = 15.0
  poll_interval = 0.2

  def __init__(self, port: str, barcode_installed: Optional[bool] = None, barcode_port: Optional[str] = None):
    super().__init__()

    self.barcode_installed: Optional[bool] = barcode_installed
    self.barcode_port: Optional[str] = barcode_port

    self.io_plc = Serial(
      port=port,
      baudrate=self.default_baud,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_EVEN,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=1,
      timeout=1,
      rtscts=True,
    )

    if barcode_installed:
      if not barcode_port:
        raise ValueError("barcode_port must also be provided if barcode is installed")
      self.io_bcr = KeyenceBarcodeScannerBackend(serial_port=barcode_port)

    self.co2_installed: Optional[bool] = None
    self.n2_installed: Optional[bool] = None

  # Function to setup serial connection with Liconic PLC
  async def setup(self):
    """
    1. Open serial port (9600 8E1, RTS/CTS) via the Serial wrapper.
    2. Send >200 ms break, wait 150 ms, flush buffers.
    3. Handshake: CR → wait for CC<CR><LF>
    4. Activate handling: ST 1801 → expect OK<CR><LF>
    5. Poll ready-flag: RD 1915 → wait for "1"<CR><LF>
    """
    try:
      await self.io_plc.setup()
    except serial.SerialException as e:
      raise RuntimeError(f"Could not open {self.io_plc.port}: {e}")

    await self.io_plc.send_break(duration=0.2)  # >100 ms required
    await asyncio.sleep(0.15)
    await self.io_plc.reset_input_buffer()
    await self.io_plc.reset_output_buffer()

    await self.io_plc.write(b"CR\r")
    deadline = time.time() + self.init_timeout
    while time.time() < deadline:
      resp = await self.io_plc.readline()  # reads through LF
      if resp.strip() == b"CC":
        break
    else:
      await self.io_plc.stop()
      raise TimeoutError(f"No CC response from Liconic PLC within {self.init_timeout} seconds")

    await self.io_plc.write(b"ST 1801\r")
    resp = await self.io_plc.readline()
    if resp.strip() != b"OK":
      await self.io_plc.stop()
      raise RuntimeError(f"Unexpected reply to ST 1801: {resp!r}")

    deadline = time.time() + self.start_timeout
    while time.time() < deadline:
      await self.io_plc.write(b"RD 1915\r")
      flag = await self.io_plc.readline()
      if flag.strip() == b"1":
        break
      await asyncio.sleep(self.poll_interval)
    else:
      await self.io_plc.stop()
      raise TimeoutError(f"PLC did not signal ready within {self.start_timeout} seconds")

    if self.io_bcr is not None:
      try:
        await self.io_bcr.setup()
      except Exception as e:
        await self.io_bcr.stop()
        raise RuntimeError(f"Could not setup barcode reader on {self.barcode_port}: {e}")

  async def stop(self):
    await self.io_plc.stop()
    if self.io_bcr is not None:
      await self.io_bcr.stop()

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    warnings.warn("Liconic racks need to be configured manually on each setup")

  async def initialize(self):
    await self._send_command_plc("ST 1900")
    await self._send_command_plc("ST 1801")
    await self._wait_ready()

  async def open_door(self):
    await self._send_command_plc("ST 1901")
    await self._wait_ready()

  async def close_door(self):
    await self._send_command_plc("ST 1902")
    await self._wait_ready()

  async def fetch_plate_to_loading_tray(self, plate: Plate, site=PlateHolder, read_barcode: Optional[bool]=False):
    """ Fetch a plate from the incubator to the loading tray."""
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"
    m, n = self._site_to_m_n(site)
    await self._send_command_plc(f"WR DM0 {m}") # carousel number
    await self._send_command_plc(f"WR DM5 {n}") # plate position in carousel

    if self.barcode_installed and read_barcode:
      await self._send_command_plc("ST 1910")  # move shovel to barcode reading position
      await self._wait_ready()
      barcode = await self._send_command_bcr("LON") # read barcode, need to check if this needs a timeout level signal trigger vs. one-shot read
      if barcode is None:
        raise RuntimeError("Failed to read barcode from plate")
      elif barcode == "ERROR":
        logger.info(f"No barcode found when reading plate at cassette {m}, position {n}")
      else:
        logger.info(f"Read barcode from plate at cassette {m}, position {n}: {barcode}")
        reset = await self._send_command_plc("RS 1910")  # move shovel back to normal position
      if reset != "OK":
        raise RuntimeError("Failed to reset shovel position after barcode reading")
      await self._wait_ready()
    elif read_barcode and not self.barcode_installed:
      logger.info(" Barcode reading requested during export but instance not configured with barcode reader.")

    await self._send_command_plc("ST 1905")  # plate to transfer station
    await self._wait_ready()
    await self._send_command_plc("ST 1903")  # terminate access

  async def take_in_plate(self, plate: Plate, site: PlateHolder, read_barcode: Optional[bool]=False):
    """ Take in a plate from the loading tray to the incubator."""
    m, n = self._site_to_m_n(site)
    await self._send_command_plc(f"WR DM0 {m}") # carousel number
    await self._send_command_plc(f"WR DM5 {n}") # plate position in carousel
    await self._send_command_plc("ST 1904")  # plate from transfer station
    await self._wait_ready()

    if self.barcode_installed and read_barcode:
      await self._send_command_plc("ST 1910")  # move shovel to barcode reading position
      await self._wait_ready()
      barcode = await self._send_command_bcr("LON") # read barcode
      if barcode is None:
        raise RuntimeError("Failed to read barcode from plate")
      elif barcode == "ERROR":
        logger.info(f"No barcode found when reading plate at cassette {m}, position {n}")
      else:
        logger.info(f"Read barcode from plate at cassette {m}, position {n}: {barcode}")
        reset = await self._send_command_plc("RS 1910")  # move shovel back to normal position
      if reset != "OK":
        raise RuntimeError("Failed to reset shovel position after barcode reading")
      await self._wait_ready()
    elif read_barcode and not self.barcode_installed:
      logger.info(" Barcode reading requested during import but instance not configured with barcode reader.")

    await self._send_command_plc("ST 1903")  # terminate access

  async def _send_command_plc(self, command: str) -> str:
    """
    Send an ASCII command to the Liconic PLC over serial and return the response.
    """
    cmd = command.strip() + "\r"
    logger.debug(f"Sending command to Liconic PLC: {cmd!r}")
    await self.io_plc.write(cmd.encode(self.serial_message_encoding))
    resp = (await self.io_plc.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError(f"No response from Liconic PLC for command {command!r}")
    resp = resp.strip()
    if resp.startswith("E"):
      # add Liconic error handling message decoding here
      raise RuntimeError(f"Error response from Liconic PLC for command {command!r}: {resp!r}")
    return resp

  async def _send_command_bcr(self, command: str) -> str:
    """
    Send an ASCII command to the barcode reader over serial and return the response.
    """
    cmd = command.strip() + "\r"
    logger.debug(f"Sending command to Barcode Reader: {cmd!r}")
    await self.io_bcr.write(cmd.encode(self.serial_message_encoding))
    resp = (await self.io_bcr.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError(f"No response from Barcode Reader for command {command!r}")
    resp = resp.strip()
    if resp.startswith("NG"):
      raise RuntimeError("Barcode reader is off: cannot read barcode")
    elif resp.startswith("ERR99"):
      raise RuntimeError(f"Error response from Barcode Reader for command {command!r}: {resp!r}")
    return resp

  async def _wait_ready(self, timeout: int = 60):
    """
    Poll the ready-flag (RD 1915) until it is set, or timeout is reached.
    """
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
      resp = await self._send_command_plc("RD 1915")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
    raise TimeoutError(f"Incubator did not become ready within {timeout} seconds")

  async def set_temperature(self, temperature: float):
    """ Set the temperature of the incubator in degrees Celsius. Using command WR DM890 ttttt
    where ttttt is temperature in 0.1 degrees Celsius (e.g. 37.0C = 370) """
    temp_value = int(temperature * 10)
    temp_str = str(temp_value).zfill(5)
    await self._send_command_plc(f"WR DM890 {temp_str}")
    await self._wait_ready()

  async def get_temperature(self) -> float:
    """ Get the temperature of the incubator in degrees Celsius. Using command RD DM982 """
    resp = await self._send_command_plc("RD DM982")
    try:
      temp_value = int(resp)
      temperature = temp_value / 10.0
      return temperature
    except ValueError:
      raise RuntimeError(f"Invalid temperature value received from incubator: {resp!r}")

  async def start_shaking(self, frequency: float = 10.0):
    """ Start shaking. Frequency by default is 10 Hz. Using command ST 1913. This functionality is
    not currently able to be tested. """
    if frequency < 1.0 or frequency > 50.0:
      raise ValueError("Shaking frequency must be between 1.0 and 50.0 Hz")
    else:
      frequency_value = int(frequency)  # assuming incubator expects frequency in 0.1 Hz units
      await self._send_command_plc(f"WR DM39 {frequency_value}")
    await self._send_command_plc("ST 1913")
    await self._wait_ready()

  async def stop_shaking(self):
    """ Stop shaking. Using command RS 1913 """
    await self._send_command_plc("RS 1913")
    await self._wait_ready()
