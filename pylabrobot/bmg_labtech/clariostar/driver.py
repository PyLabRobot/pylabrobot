import asyncio
import logging
import time
from typing import Optional, Union

from pylabrobot.device import Driver
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources.plate import Plate

logger = logging.getLogger("pylabrobot")


class CLARIOstarDriver(Driver):
  """FTDI-based driver for the BMG Labtech CLARIOstar plate reader.

  Owns the USB connection, low-level protocol framing, and device-level
  operations (initialize, open/close tray).  Communicates over FTDI USB
  (VID 0x0403, PID 0xBB68) at 125000 baud.
  """

  def __init__(self, device_id: Optional[str] = None):
    super().__init__()
    self.io = FTDI(
      human_readable_device_name="BMG CLARIOstar", device_id=device_id, vid=0x0403, pid=0xBB68
    )

  async def setup(self) -> None:
    await self.io.setup()
    await self.io.set_baudrate(125000)
    await self.io.set_line_property(8, 0, 0)  # 8N1
    await self.io.set_latency_timer(2)

    await self._initialize()
    await self._request_eeprom_data()

  async def stop(self) -> None:
    await self.io.stop()

  # -- Low-level protocol ---------------------------------------------------

  async def read_resp(self, timeout: float = 20) -> bytes:
    """Read a response terminated by 0x0D."""
    d = b""
    last_read = b""
    end_byte_found = False
    t = time.time()

    while True:
      last_read = await self.io.read(25)
      if len(last_read) > 0:
        d += last_read
        end_byte_found = d[-1] == 0x0D
        if len(last_read) < 25 and end_byte_found:
          break
      else:
        if end_byte_found:
          break
        if time.time() - t > timeout:
          logger.warning("timed out reading response")
          break
        await asyncio.sleep(0.0001)

    logger.debug("read %s", d.hex())
    return d

  async def send(self, cmd: Union[bytearray, bytes], read_timeout: float = 20) -> bytes:
    """Send a command with 16-bit checksum + 0x0D terminator and return the response."""
    checksum = (sum(cmd) & 0xFFFF).to_bytes(2, byteorder="big")
    cmd = cmd + checksum + b"\x0d"
    logger.debug("sending %s", cmd.hex())
    w = await self.io.write(cmd)
    logger.debug("wrote %s bytes", w)
    assert w == len(cmd)
    return await self.read_resp(timeout=read_timeout)

  async def _wait_for_ready_and_return(self, ret: bytes, timeout: float = 150) -> bytes:
    """Poll command status until the device reports ready."""
    last_status = None
    t = time.time()
    while time.time() - t < timeout:
      await asyncio.sleep(0.1)
      command_status = await self._read_command_status()

      if len(command_status) != 24:
        logger.warning(
          "unexpected response %s. Expected 24 bytes for command status.", command_status
        )
        continue

      if command_status != last_status:
        logger.info("status changed %s", command_status.hex())
        last_status = command_status
      else:
        continue

      if command_status[2] != 0x18 or command_status[3] != 0x0C or command_status[4] != 0x01:
        logger.warning("unexpected response header %s", command_status)

      if command_status[5] not in {0x25, 0x05}:
        logger.warning("unexpected status byte %s", command_status)

      if command_status[5] == 0x05:
        logger.debug("status is ready")
        return ret

    raise TimeoutError("CLARIOstar did not become ready within timeout.")

  async def _read_command_status(self) -> bytes:
    return await self.send(b"\x02\x00\x09\x0c\x80\x00")

  async def _initialize(self) -> None:
    command_response = await self.send(b"\x02\x00\x0d\x0c\x01\x00\x00\x10\x02\x00")
    await self._wait_for_ready_and_return(command_response)

  async def _request_eeprom_data(self) -> None:
    eeprom_response = await self.send(b"\x02\x00\x0f\x0c\x05\x07\x00\x00\x00\x00\x00\x00")
    await self._wait_for_ready_and_return(eeprom_response)

  # -- Tray control ---------------------------------------------------------

  async def open(self) -> None:
    """Open the plate tray."""
    open_response = await self.send(b"\x02\x00\x0e\x0c\x03\x01\x00\x00\x00\x00\x00")
    await self._wait_for_ready_and_return(open_response)

  async def close(self) -> None:
    """Close the plate tray."""
    close_response = await self.send(b"\x02\x00\x0e\x0c\x03\x00\x00\x00\x00\x00\x00")
    await self._wait_for_ready_and_return(close_response)

  # -- Helpers used by capability backends ----------------------------------

  async def mp_and_focus_height_value(self) -> None:
    resp = await self.send(b"\x02\x00\x0f\x0c\x05\17\x00\x00\x00\x00\x00\x00")
    await self._wait_for_ready_and_return(resp)

  async def read_order_values(self) -> bytes:
    return await self.send(b"\x02\x00\x0f\x0c\x05\x1d\x00\x00\x00\x00\x00\x00")

  async def status_hw(self) -> bytes:
    resp = await self.send(b"\x02\x00\x09\x0c\x81\x00")
    return await self._wait_for_ready_and_return(resp)

  async def request_measurement_values(self) -> bytes:
    return await self.send(b"\x02\x00\x0f\x0c\x05\x02\x00\x00\x00\x00\x00\x00")

  def plate_bytes(self, plate: Plate) -> bytes:
    """Encode plate geometry into the 62-byte binary format."""

    def float_to_bytes(f: float) -> bytes:
      return round(f * 100).to_bytes(2, byteorder="big")

    plate_length = plate.get_absolute_size_x()
    plate_width = plate.get_absolute_size_y()

    well_0 = plate.get_well(0)
    assert well_0.location is not None, "Well 0 must be assigned to a plate"
    plate_x1 = well_0.location.x + well_0.center().x
    plate_y1 = plate_width - (well_0.location.y + well_0.center().y)
    plate_xn = plate_length - plate_x1
    plate_yn = plate_width - plate_y1

    plate_cols = plate.num_items_x
    plate_rows = plate.num_items_y

    wells = ([1] * plate.num_items) + ([0] * (384 - plate.num_items))
    well_mask: int = sum(b << i for i, b in enumerate(wells[::-1]))
    wells_bytes = well_mask.to_bytes(48, "big")

    return (
      float_to_bytes(plate_length)
      + float_to_bytes(plate_width)
      + float_to_bytes(plate_x1)
      + float_to_bytes(plate_y1)
      + float_to_bytes(plate_xn)
      + float_to_bytes(plate_yn)
      + plate_cols.to_bytes(1, byteorder="big")
      + plate_rows.to_bytes(1, byteorder="big")
      + wells_bytes
    )

  async def run_measurement(self, payload: bytes) -> bytes:
    """Execute a measurement run and poll until complete."""
    message_size = (len(payload) + 7).to_bytes(2, byteorder="big")
    cmd = b"\x02" + message_size + b"\x0c" + payload
    run_response = await self.send(cmd)

    last_status = None
    while True:
      await asyncio.sleep(0.1)
      command_status = await self._read_command_status()
      if command_status != last_status:
        last_status = command_status
        logger.info("status changed %s", command_status)
        continue
      if command_status == bytes(
        b"\x02\x00\x18\x0c\x01\x25\x04\x2e\x00\x00\x04\x01\x00\x00\x03\x00"
        b"\x00\x00\x00\xc0\x00\x01\x46\x0d"
      ):
        return run_response
