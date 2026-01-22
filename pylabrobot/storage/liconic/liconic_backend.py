import asyncio
import logging
import time
import warnings
from typing import List, Tuple, Optional, Union

import serial

from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.storage.backend import IncubatorBackend
from pylabrobot.barcode_scanners.keyence import KeyenceBarcodeScannerBackend
from pylabrobot.storage.liconic.constants import LiconicType

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

  def __init__(self, model: Union[LiconicType, str], port: str, barcode_installed: Optional[bool] = None, barcode_port: Optional[str] = None):
    super().__init__()

    self.barcode_installed: Optional[bool] = barcode_installed
    self.barcode_port: Optional[str] = barcode_port

    if isinstance(model, str):
      try:
        model = LiconicType(model)
      except ValueError:
        raise ValueError(f"Unsupported Liconic model: '{model}")

    self.model = model
    self._racks: List[PlateCarrier] = []

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

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1  # plr is 0-indexed, cytomat is 1-indexed
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1  # 1-indexed
    return rack_idx, site_idx

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

    if read_barcode:
      await self.read_barcode_inline(m,n)

    await self._send_command_plc("ST 1905")  # plate to transfer station
    await self._wait_ready()
    await self._send_command_plc("ST 1903")  # terminate access

  async def take_in_plate(self, plate: Plate, site: PlateHolder, read_barcode: Optional[bool]=False):
    """ Take in a plate from the loading tray to the incubator."""
    m, n = self._site_to_m_n(site)
    await self._send_command_plc(f"WR DM0 {m}") # cassette number
    await self._send_command_plc(f"WR DM5 {n}") # plate position in cassette
    await self._send_command_plc("ST 1904")  # plate from transfer station
    await self._wait_ready()

    if read_barcode:
      await self.read_barcode_inline(m,n)

    await self._send_command_plc("ST 1903")  # terminate access

  async def move_position_to_position(self,
                                      plate: Plate,
                                      orig_site: PlateHolder,
                                      dest_site: PlateHolder,
                                      read_barcode: Optional[bool]=False):
    """ Move plate from one internal position to another"""
    orig_m, orig_n = self._site_to_m_n(orig_site) # origin cassette # and plate position #
    dest_m, dest_n = self._site_to_m_n(dest_site) # destination cassette # and plate position #

    await self._send_command_plc(f"WR DM 0 {orig_m}") # origin cassette #
    await self._send_command_plc(f"WR DM 5 {orig_n}") # origin plate position #

    if read_barcode:
      await self.read_barcode_inline(orig_m,orig_n)

    await self._send_command_plc("ST 1908") # pick plate from origin position

    await self._wait_ready()

    if orig_m != dest_m:
      await self._send_command_plc(f"WR DM0 {dest_m}") # destination cassette # if different
    await self._send_command_plc(f"WR DM5 {dest_n}") # destination plate position #
    await self._send_command_plc("ST 1909") # place plate in destination position

    await self._wait_ready()
    await self._send_command_plc("ST 1903") # terminate access

  async def read_barcode_inline(self, cassette: int, plt_position: int) -> str:
    if self.barcode_installed:
      await self._send_command_plc("ST 1910")  # move shovel to barcode reading position
      await self._wait_ready()
      barcode = await self._send_command_bcr("LON") # read barcode
      if barcode is None:
        raise RuntimeError("Failed to read barcode from plate")
      elif barcode == "ERROR":
        logger.info(f"No barcode found when reading plate at cassette {cassette}, position {plt_position}")
      else:
        logger.info(f"Read barcode from plate at cassette {cassette}, position {plt_position}: {barcode}")
        reset = await self._send_command_plc("RS 1910")  # move shovel back to normal position
        if reset != "OK":
          raise RuntimeError("Failed to reset shovel position after barcode reading")
      await self._wait_ready()
      return barcode
    else:
      logger.info(" Barcode reading requested but instance not configured with barcode reader.")
      return "No barcode"


  async def scan_cassette(self,):
    pass

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
    resp = await self.io_bcr.send_command(cmd)
    #resp = (await self.io_bcr.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError(f"No response from Barcode Reader for command {command!r}")
    resp = resp.strip()
    if resp.startswith("NG"):
      raise RuntimeError("Barcode reader is off: cannot read barcode")
    elif resp.startswith("ERR99"):
      raise RuntimeError(f"Error response from Barcode Reader for command {command!r}: {resp!r}")
    return resp

  async def _wait_plate_ready(self, timeout: int = 60):
    """
    Poll the plate-ready flag (RD 1914) until it is set, or timeout is reached.
    """
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline:
      resp = await self._send_command_plc("RD 1914")
      if resp == "1":
        return
      await asyncio.sleep(0.1)
    raise TimeoutError(f"Plate did not become ready within {timeout} seconds")

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
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    temp_value = int(temperature * 10)
    temp_str = str(temp_value).zfill(5)
    await self._send_command_plc(f"WR DM890 {temp_str}")
    await self._wait_ready()

  async def get_temperature(self) -> float:
    """ Get the temperature of the incubator in degrees Celsius. Using command RD DM982 """
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command_plc("RD DM982")
    try:
      temp_value = int(resp)
      temperature = temp_value / 10.0
      return temperature
    except ValueError:
      raise RuntimeError(f"Invalid temperature value received from incubator: {resp!r}")

  # UNTESTED
  # Unsure if 1 means ON and 0 means OFF, needs to be confirmed.
  async def shaker_status(self) -> int:
    """ Determines whether the shaker is ON (1) or OFF (0)"""
    value = await self._send_command_plc()
    await self._wait_ready()
    return value

  # UNTESTED
  # Unsure if a liconic will return 00250 for 25 or 00025. Assuming former.
  # Should be in Hz
  async def get_shaker_speed(self) -> float:
    """ Gets the current shaker speed default = 25"""
    speed_val = await self._send_command_plc("RD DM39")
    speed = speed_val / 10.0
    await self._wait_ready()
    return speed

  # UNTESTED
  # Unsure if setting WR DM39 00250 will set it at 25 Hz or if WR DM39 00025 will. Assuming former
  async def start_shaking(self, frequency):
    """ Start shaking. Must be between 1 and 50 Hz. Frequency by default is 10 Hz. Using command
    ST 1913. This functionality is not currently able to be tested. """
    if frequency < 1.0 or frequency > 50.0:
      raise ValueError("Shaking frequency must be between 1.0 and 50.0 Hz")
    else:
      frequency_value = int(frequency)  # assuming incubator expects frequency in 0.1 Hz units
      frequency = frequency_value * 10
      await self._send_command_plc(f"WR DM39 {str(frequency).zfill(5)}")
    await self._send_command_plc("ST 1913")
    await self._wait_ready()

  # UNTESTED
  async def stop_shaking(self):
    """ Stop shaking. Using command RS 1913 """
    await self._send_command_plc("RS 1913")
    await self._wait_ready()

  async def get_set_temperature(self) -> float:
    """ Get the set value temperature of the incubator in degrees Celsius."""
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command_plc("RD DM890")
    try:
      temp_value = int(resp)
      temperature = temp_value / 10.0
      return temperature
    except ValueError:
      raise RuntimeError(f"Invalid set temperature value received from incubator: {resp!r}")

  async def set_humidity(self, humidity: float):
    """ Set the humidity of the incubator in percentage (%)."""
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    humidity_val = int(humidity * 10)
    await self._send_command_plc(f"WR DM893 {str(humidity_val).zfill(5)}")
    await self._wait_ready()

  async def get_humidity(self) -> float:
    """ Get the actual humidity of the incubator in percentage (%)."""
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command_plc("RD DM983")
    try:
      humidity_value = int(resp)
      humidity = humidity_value / 10.0
      return humidity
    except ValueError:
      raise RuntimeError(f"Invalid humidity value received from incubator: {resp!r}")

  async def get_set_humidity(self) -> float:
    """ Get the set value humidity of the incubator in percentage (%)."""
    if self.model.value.split('_')[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command_plc("RD DM893")
    try:
      humidity_value = int(resp)
      humidity = humidity_value / 10.0
      return humidity
    except ValueError:
      raise RuntimeError(f"Invalid set humidity value received from incubator: {resp!r}")

  # UNTESTED
  async def set_co2_level(self, co2_level: float):
    """ Set the CO2 level of the incubator in 1/100% vol. percentage (%) 500 = 5.0 % ."""
    co2_val = int(co2_level * 100)
    await self._send_command_plc(f"WR DM894 {str(co2_val).zfill(5)}")
    await self._wait_ready()

  # UNTESTED
  async def get_co2_level(self) -> float:
    """ Get the CO2 level of the incubator in percentage (%)."""
    resp = await self._send_command_plc("RD DM984")
    try:
      co2_value = int(resp)
      co2 = co2_value / 100.0
      return co2
    except ValueError:
      raise RuntimeError(f"Invalid co2 value received from incubator: {resp!r}")

  # UNTESTED
  async def get_set_co2_level(self) -> float:
    """ Get the set value CO2 level of the incubator in percentage (%)."""
    resp = await self._send_command_plc("RD DM894")
    try:
      co2_set_value = int(resp)
      co2 = co2_set_value / 100.0
      return co2
    except ValueError:
      raise RuntimeError(f"Invalid co2 set value received from incubator: {resp!r}")

  # UNTESTED
  async def set_n2_level(self, n2_level: float):
    """ Set the N2 level of the incubator in percentage (%)."""
    n2_val = int(n2_level * 100)
    await self._send_command_plc(f"WR DM895 {str(n2_val).zfill(5)}")

  # UNTESTED
  async def get_n2_level(self) -> float:
    """ Get the N2 level of the incubator in percentage (%)."""
    resp = await self._send_command_plc("RD DM985")
    try:
      n2_value = int(resp)
      n2 = n2_value / 100.0
      return n2
    except ValueError:
      raise RuntimeError(f"Invalid N2 value received from incubator: {resp!r}")

  # UNTESTED
  async def get_set_n2_level(self) -> float:
    """ Get the set value N2 level of the incubator in percentage (%)."""
    resp = await self._send_command_plc("RD DM895")
    try:
      n2_set_value = int(resp)
      n2 = n2_set_value / 100.0
      return n2
    except ValueError:
      raise RuntimeError(f"Invalid N2 set value received from incubator: {resp!r}")

  # UNTESTED
  # Unsure what RD 1912 returns (is 1 home or swapped?)
  # Another avenue is to read the first byte of T16 or T17 but don't have ability to test
  async def turn_swap_station(self, home: bool):
    """ Turn the swap station of the incubator. If home is True, turn to home position."""
    resp = await self._send_command_plc("RD 1912")
    if home and resp == "1":
      await self._send_command_plc("RS 1912")
    else:
      await self._send_command_plc("ST 1912")

  # UNTESTED
  # Activate plate sensor (ST 1911) used in HT units only because it is off by default
  async def check_shovel_sensor(self) -> bool:
    """ First need to activate shovel transfer sensor deactivated by default, wait 0.1 seconds
      and then Check if the shovel plate sensor is activated."""
    await self._send_command_plc("ST 1911")
    asyncio.sleep(0.1)
    resp = await self._send_command_plc("RD 1812")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from incubator read shovel sensor: {resp!r}")

  # UNTESTED
  async def check_transfer_sensor(self) -> bool:
    """ Check if the transfer plate sensor is activated."""
    resp = await self._send_command_plc("RD 1813")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read transfer station sensor: {resp!r}")

  # UNTESTED
  async def check_second_transfer_sensor(self) -> bool:
    """ Check if the second transfer plate sensor is activated."""
    resp = await self._send_command_plc("RD 1807")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read 2nd transfer station sensor: {resp!r}")

  async def scan_barcode(self, cassette: int, position: int, pitch: int, plate_count: int) -> str:
    """ Scan a barcode using the internal barcode reader. Using command LON """
    if not self.barcode_installed:
      raise RuntimeError("Barcode reader not installed in this incubator instance")

    await self._send_command_plc(f"WR DM0 {cassette}") # carousel number
    await self._send_command_plc(f"WR DM23 {pitch}")   # pitch of plate in mm
    await self._send_command_plc(f"WR DM25 {plate_count}") # plate
    await self._send_command_plc(f"WR DM5 {position}") # plate position in carousel
    await self._send_command_plc("ST 1910")  # move shovel to barcode reading position

    barcode = await self._send_command_bcr("LON")
    print(f"Scanned barcode: {barcode}")
