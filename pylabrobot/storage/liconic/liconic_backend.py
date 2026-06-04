import logging
import re
import warnings
from typing import List, Optional, Tuple, Union

import anyio

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

from pylabrobot.barcode_scanners import BarcodeScanner
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.carrier import PlateCarrier
from pylabrobot.storage.backend import IncubatorBackend
from pylabrobot.storage.liconic.constants import ControllerError, HandlingError, LiconicType
from pylabrobot.storage.liconic.errors import controller_error_map, handler_error_map

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


class ExperimentalLiconicBackend(IncubatorBackend):
  """Backend for Liconic incubators.

  Optionally accepts a BarcodeScanner instance for internal barcode reading.
  """

  default_baud = 9600
  serial_message_encoding = "ascii"
  init_timeout = 1.0
  start_timeout = 15.0
  poll_interval = 0.2

  def __init__(
    self,
    model: Union[LiconicType, str],
    port: str,
    barcode_scanner: Optional[BarcodeScanner] = None,
  ):
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )
    super().__init__()

    self.barcode_scanner = barcode_scanner

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

  # Function to setup serial connection with Liconic PLC
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding):
    """
    1. Open serial port (9600 8E1, RTS/CTS) via the Serial wrapper.
    2. Send >200 ms break, wait 150 ms, flush buffers.
    3. Handshake: CR → wait for CC<CR><LF>
    4. Activate handling: ST 1801 → expect OK<CR><LF>
    5. Poll ready-flag: RD 1915 → wait for "1"<CR><LF>
    """
    await super()._enter_lifespan(stack)
    try:
      await stack.enter_async_context(self.io)
    except serial.SerialException as e:
      raise RuntimeError(f"Could not open {self.io.port}: {e}") from e

    await self.io.send_break(duration=0.2)  # >100 ms required
    await anyio.sleep(0.15)
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    await self.io.write(b"CR\r")
    try:
      with anyio.fail_after(self.init_timeout):
        while True:
          resp = await self.io.readline()  # reads through LF
          if resp.strip() == b"CC":
            break
    except TimeoutError as e:
      raise TimeoutError(
        f"No CC response from Liconic PLC within {self.init_timeout} seconds"
      ) from e

    await self.io.write(b"ST 1801\r")
    resp = await self.io.readline()
    if resp.strip() != b"OK":
      raise RuntimeError(f"Unexpected reply to ST 1801: {resp!r}")

    try:
      with anyio.fail_after(self.start_timeout):
        while True:
          await self.io.write(b"RD 1915\r")
          flag = await self.io.readline()
          if flag.strip() == b"1":
            break
          await anyio.sleep(self.poll_interval)
    except TimeoutError as e:
      raise TimeoutError(f"PLC did not signal ready within {self.start_timeout} seconds") from e

  def _site_to_m_n(self, site: PlateHolder) -> Tuple[int, int]:
    rack = site.parent
    assert isinstance(rack, PlateCarrier), "Site not in rack"
    assert self._racks is not None, "Racks not set"
    rack_idx = self._racks.index(rack) + 1  # plr is 0-indexed, liconic is 1-indexed
    site_idx = next(idx for idx, s in rack.sites.items() if s == site) + 1  # 1-indexed
    return rack_idx, site_idx

  # Wrote this function to return motor step size and plate position number from PlateCarrier model name
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

  async def set_racks(self, racks: List[PlateCarrier]):
    await super().set_racks(racks)
    warnings.warn("Liconic racks need to be configured manually on each setup")

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

  async def fetch_plate_to_loading_tray(
    self, plate: Plate, read_barcode: bool = False, **backend_kwargs
  ):
    """Fetch a plate from the incubator to the loading tray."""
    site = plate.parent
    assert isinstance(site, PlateHolder), "Plate not in storage"

    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)

    await self._send_command(f"WR DM0 {m}")  # carousel number
    await self._send_command(f"WR DM23 {step_size}")  # motor step size
    await self._send_command(f"WR DM25 {pos_num}")  # number of positions in cassette
    await self._send_command(f"WR DM5 {n}")  # plate position in carousel

    if read_barcode:
      plate.barcode = await self.read_barcode_inline(m, n)

    await self._send_command("ST 1905")  # plate to transfer station
    await self._wait_ready()
    await self._send_command("ST 1903")  # terminate access

  async def take_in_plate(
    self, plate: Plate, site: PlateHolder, read_barcode: bool = False, **backend_kwargs
  ):
    """Take in a plate from the loading tray to the incubator."""
    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)

    await self._send_command(f"WR DM0 {m}")  # carousel number
    await self._send_command(f"WR DM23 {step_size}")  # motor step size
    await self._send_command(f"WR DM25 {pos_num}")  # number of positions in cassette
    await self._send_command(f"WR DM5 {n}")  # plate position in cassette
    await self._send_command("ST 1904")  # plate from transfer station
    await self._wait_ready()

    if read_barcode:
      plate.barcode = await self.read_barcode_inline(m, n)

    await self._send_command("ST 1903")  # terminate access

  async def move_position_to_position(
    self, plate: Plate, dest_site: PlateHolder, read_barcode: bool = False
  ):
    """Move plate from one internal position to another"""
    orig_site = plate.parent
    assert isinstance(orig_site, PlateHolder)
    assert isinstance(dest_site, PlateHolder)

    if dest_site.resource is not None:
      raise RuntimeError(f"Position {dest_site} already has a plate assigned!")

    orig_m, orig_n = self._site_to_m_n(orig_site)  # origin cassette # and plate position #
    dest_m, dest_n = self._site_to_m_n(dest_site)  # destination cassette # and plate position #

    await self._send_command(f"WR DM0 {orig_m}")  # origin cassette #
    orig_step_size, orig_pos_num = self._carrier_to_steps_pos(orig_site)
    dest_step_size, dest_pos_num = self._carrier_to_steps_pos(dest_site)

    await self._send_command(f"WR DM0 {orig_m}")  # carousel number
    await self._send_command(f"WR DM23 {orig_step_size}")  # motor step size
    await self._send_command(f"WR DM25 {orig_pos_num}")  # number of positions in cassette
    await self._send_command(f"WR DM5 {orig_n}")  # origin plate position #

    if read_barcode:
      plate.barcode = await self.read_barcode_inline(orig_m, orig_n)

    await self._send_command("ST 1908")  # pick plate from origin position

    await self._wait_ready()

    if orig_m != dest_m:
      await self._send_command(f"WR DM0 {dest_m}")  # destination cassette # if different
    await self._send_command(f"WR DM23 {dest_step_size}")  # motor step size
    await self._send_command(f"WR DM25 {dest_pos_num}")  # number of positions in cassette
    await self._send_command(f"WR DM5 {dest_n}")  # destination plate position #
    await self._send_command("ST 1909")  # place plate in destination position

    await self._wait_ready()
    await self._send_command("ST 1903")  # terminate access

  async def read_barcode_inline(self, cassette: int, plt_position: int) -> Barcode:
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")

    await self._send_command("ST 1910")  # move shovel to barcode reading position
    await self._wait_ready()
    barcode = await self.barcode_scanner.scan()
    logger.info(
      f"Read barcode from plate at cassette {cassette}, position {plt_position}: {barcode.data}"
    )
    reset = await self._send_command("RS 1910")  # move shovel back to normal position
    if reset != "OK":
      raise RuntimeError("Failed to reset shovel position after barcode reading")
    await self._wait_ready()
    return barcode

  async def _send_command(self, command: str) -> str:
    """
    Send an ASCII command to the Liconic PLC over serial and return the response.
    """
    cmd = command.strip() + "\r"
    logger.debug(f"Sending command to Liconic PLC: {cmd!r}")
    await self.io.write(cmd.encode(self.serial_message_encoding))
    resp = (await self.io.read(128)).decode(self.serial_message_encoding)
    if not resp:
      raise RuntimeError(f"No response from Liconic PLC for command {command!r}")
    resp = resp.strip()
    if resp.startswith("E"):
      logger.error(f"Command {command} failed with {resp}")
      for member in ControllerError:
        if resp == member.value:
          cls, msg = controller_error_map[member]
          raise cls(msg)
      raise RuntimeError(f"Unknown error {resp} when sending command {command}")
    return resp

  async def _wait_plate_ready(self, timeout: int = 60):
    """
    Poll the plate-ready flag (RD 1914) until it is set, or timeout is reached.
    """
    try:
      with anyio.fail_after(timeout):
        while True:
          resp = await self._send_command("RD 1914")
          if resp == "1":
            return
          await anyio.sleep(0.1)
    except TimeoutError:
      raise TimeoutError(f"Plate was not ready within {timeout} seconds") from None

  async def _wait_ready(self, timeout: int = 60):
    """
    Poll the ready-flag (RD 1915) until it is set. If timeout is reached
    the error flag is read and if true aka "1" then the error register is read.
    """
    try:
      with anyio.fail_after(timeout):
        while True:
          resp = await self._send_command("RD 1915")
          if resp == "1":
            return
          await anyio.sleep(0.1)
    except TimeoutError:
      err_flag = await self._send_command("RD 1814")
      if err_flag == "1":
        error = await self._send_command("RD DM200")
        for member in HandlingError:
          if error == member.value:
            cls, msg = handler_error_map[member]
            raise cls(msg)
        raise RuntimeError(f"Liconic Handler in unknown error state with memory showing {error}")
      raise TimeoutError(f"Incubator did not become ready within {timeout} seconds")

  async def set_temperature(self, temperature: float):
    """Set the temperature of the incubator in degrees Celsius. Using command WR DM890 ttttt
    where ttttt is temperature in 0.1 degrees Celsius (e.g. 37.0C = 370)"""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    temp_value = int(temperature * 10)
    temp_str = str(temp_value).zfill(5)
    await self._send_command(f"WR DM890 {temp_str}")
    await self._wait_ready()

  async def get_temperature(self) -> float:
    """Get the temperature of the incubator in degrees Celsius. Using command RD DM982"""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command("RD DM982")
    try:
      temp_value = int(resp)
      temperature = temp_value / 10.0
      return temperature
    except ValueError:
      raise RuntimeError(f"Invalid temperature value received from incubator: {resp!r}")

  async def shaker_status(self) -> int:
    """Determines whether the shaker is ON (1) or OFF (0).

    UNTESTED. Unsure if 1 means ON and 0 means OFF, needs to be confirmed."""
    # TODO: Missing PLC command - need to determine correct command from Liconic documentation
    raise NotImplementedError("shaker_status command not yet implemented")

  async def get_shaker_speed(self) -> float:
    """Gets the current shaker speed in Hz, default = 25.

    UNTESTED. Unsure if Liconic returns 00250 for 25 or 00025. Assuming former."""
    speed_val = await self._send_command("RD DM39")
    speed = int(speed_val) / 10.0
    await self._wait_ready()
    return speed

  async def start_shaking(self, frequency):
    """Start shaking. Must be between 1 and 50 Hz. Frequency by default is 10 Hz. Uses command
    ST 1913.

    UNTESTED. Unsure if WR DM39 00250 sets 25 Hz or if WR DM39 00025 does. Assuming former."""
    if frequency < 1.0 or frequency > 50.0:
      raise ValueError("Shaking frequency must be between 1.0 and 50.0 Hz")
    frequency_value = int(frequency * 10)  # PLC expects 0.1 Hz units: 25 Hz -> 250
    await self._send_command(f"WR DM39 {str(frequency_value).zfill(5)}")
    await self._send_command("ST 1913")
    await self._wait_ready()

  async def stop_shaking(self):
    """Stop shaking. Uses command RS 1913.

    UNTESTED."""
    await self._send_command("RS 1913")
    await self._wait_ready()

  async def get_target_temperature(self) -> float:
    """Get the set value temperature of the incubator in degrees Celsius."""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command("RD DM890")
    try:
      temp_value = int(resp)
      temperature = temp_value / 10.0
      return temperature
    except ValueError:
      raise RuntimeError(f"Invalid set temperature value received from incubator: {resp!r}")

  async def set_humidity(self, humidity: float):
    """Set the humidity of the incubator as a fraction (0.0 to 1.0)."""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    humidity_val = int(humidity * 1000)  # PLC uses 0.1% units: 0.9 fraction -> 900 -> 90.0%
    await self._send_command(f"WR DM893 {str(humidity_val).zfill(5)}")
    await self._wait_ready()

  async def get_humidity(self) -> float:
    """Get the actual humidity of the incubator as a fraction (0.0 to 1.0)."""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command("RD DM983")
    try:
      humidity_value = int(resp)
      humidity = humidity_value / 1000.0  # PLC uses 0.1% units: 900 -> 0.9 fraction
      return humidity
    except ValueError:
      raise RuntimeError(f"Invalid humidity value received from incubator: {resp!r}")

  async def get_target_humidity(self) -> float:
    """Get the set value humidity of the incubator as a fraction (0.0 to 1.0)."""
    if self.model.value.split("_")[-1] == "NC":
      raise NotImplementedError("Climate control is not supported on this model")

    resp = await self._send_command("RD DM893")
    try:
      humidity_value = int(resp)
      humidity = humidity_value / 1000.0  # PLC uses 0.1% units: 900 -> 0.9 fraction
      return humidity
    except ValueError:
      raise RuntimeError(f"Invalid set humidity value received from incubator: {resp!r}")

  async def set_co2_level(self, co2_level: float):
    """Set the CO2 level of the incubator as a fraction (0.0 to 1.0). PLC uses 1/100% vol units
    (e.g. 500 = 5.0%), so 0.05 fraction -> 500.

    UNTESTED."""
    co2_val = int(co2_level * 10000)  # PLC uses 0.01% units: 0.05 fraction -> 500 -> 5.0%
    await self._send_command(f"WR DM894 {str(co2_val).zfill(5)}")
    await self._wait_ready()

  async def get_co2_level(self) -> float:
    """Get the CO2 level of the incubator as a fraction (0.0 to 1.0).

    UNTESTED."""
    resp = await self._send_command("RD DM984")
    try:
      co2_value = int(resp)
      co2 = co2_value / 10000.0  # PLC uses 0.01% units: 500 -> 0.05 fraction
      return co2
    except ValueError:
      raise RuntimeError(f"Invalid co2 value received from incubator: {resp!r}")

  async def get_target_co2_level(self) -> float:
    """Get the set value CO2 level of the incubator as a fraction (0.0 to 1.0).

    UNTESTED."""
    resp = await self._send_command("RD DM894")
    try:
      co2_set_value = int(resp)
      co2 = co2_set_value / 10000.0  # PLC uses 0.01% units: 500 -> 0.05 fraction
      return co2
    except ValueError:
      raise RuntimeError(f"Invalid co2 set value received from incubator: {resp!r}")

  async def set_n2_level(self, n2_level: float):
    """Set the N2 level of the incubator as a fraction (0.0 to 1.0).

    UNTESTED."""
    n2_val = int(n2_level * 10000)  # PLC uses 0.01% units: 0.9 fraction -> 9000 -> 90.0%
    await self._send_command(f"WR DM895 {str(n2_val).zfill(5)}")
    await self._wait_ready()

  async def get_n2_level(self) -> float:
    """Get the N2 level of the incubator as a fraction (0.0 to 1.0).

    UNTESTED."""
    resp = await self._send_command("RD DM985")
    try:
      n2_value = int(resp)
      n2 = n2_value / 10000.0  # PLC uses 0.01% units: 9000 -> 0.9 fraction
      return n2
    except ValueError:
      raise RuntimeError(f"Invalid N2 value received from incubator: {resp!r}")

  async def get_target_n2_level(self) -> float:
    """Get the set value N2 level of the incubator as a fraction (0.0 to 1.0).

    UNTESTED."""
    resp = await self._send_command("RD DM895")
    try:
      n2_set_value = int(resp)
      n2 = n2_set_value / 10000.0  # PLC uses 0.01% units: 9000 -> 0.9 fraction
      return n2
    except ValueError:
      raise RuntimeError(f"Invalid N2 set value received from incubator: {resp!r}")

  async def turn_swap_station(self, home: bool):
    """Turn the swap station of the incubator. If home is True, turn to home position.

    UNTESTED. Unsure what RD 1912 returns (is 1 home or swapped?). Another avenue is to read the
    first byte of T16 or T17 but don't have ability to test."""
    resp = await self._send_command("RD 1912")
    if home and resp == "1":
      await self._send_command("RS 1912")
    else:
      await self._send_command("ST 1912")

  async def check_shovel_sensor(self) -> bool:
    """Activate shovel transfer sensor (ST 1911, off by default on HT units), wait 0.1 seconds,
    then check if the shovel plate sensor is activated.

    UNTESTED."""
    await self._send_command("ST 1911")
    await anyio.sleep(0.1)
    resp = await self._send_command("RD 1812")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from incubator read shovel sensor: {resp!r}")

  async def check_transfer_sensor(self) -> bool:
    """Check if the transfer plate sensor is activated.

    UNTESTED."""
    resp = await self._send_command("RD 1813")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read transfer station sensor: {resp!r}")

  async def check_second_transfer_sensor(self) -> bool:
    """Check if the second transfer plate sensor is activated.

    UNTESTED."""
    resp = await self._send_command("RD 1807")
    if resp == "1":
      return True
    elif resp == "0":
      return False
    else:
      raise RuntimeError(f"Unexpected response from read 2nd transfer station sensor: {resp!r}")

  async def scan_barcode(self, site: PlateHolder) -> Barcode:
    """Scan a barcode using the internal barcode reader."""
    if self.barcode_scanner is None:
      raise RuntimeError("Barcode scanner not configured for this incubator instance")

    m, n = self._site_to_m_n(site)
    step_size, pos_num = self._carrier_to_steps_pos(site)

    await self._send_command(f"WR DM0 {m}")  # carousel number
    await self._send_command(f"WR DM23 {step_size}")  # pitch of plate in mm
    await self._send_command(f"WR DM25 {pos_num}")  # plate
    await self._send_command(f"WR DM5 {n}")  # plate position in carousel
    await self._send_command("ST 1910")  # move shovel to barcode reading position

    barcode = await self.barcode_scanner.scan()
    logger.info(f"Scanned barcode: {barcode.data}")
    return barcode

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.io.port,
      "model": self.model.value,
    }

  @classmethod
  def deserialize(cls, data: dict):
    return cls(port=data["port"], model=data["model"])
