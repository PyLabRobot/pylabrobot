import asyncio
import ctypes
import logging
import time
import struct
from typing import List, Optional

try:
  from pylibftdi import Device
  USE_FTDI = True
except ImportError:
  USE_FTDI = False

from .backend import PlateReaderBackend


logger = logging.getLogger("pylabrobot")


class CLARIOStar(PlateReaderBackend):
  """ A plate reader backend for the Clario star. Note that this is not a complete implementation
  and many commands and parameters are not implemented yet. """

  def __init__(self):
    self.dev: Optional[Device] = None

  async def setup(self):
    if not USE_FTDI:
      raise RuntimeError("pylibftdi is not installed. Run `pip install pylabrobot[plate_reading]`.")

    self.dev = Device()
    self.dev.open()
    self.dev.baudrate = 125000
    self.dev.ftdi_fn.ftdi_set_line_property(8, 0, 0) # 8N1
    self.dev.ftdi_fn.ftdi_set_latency_timer(2)

    await self.initialize()
    await self.request_eeprom_data()

  async def stop(self):
    if self.dev is not None:
      self.dev.close()

  def get_stat(self):
    if self.dev is None:
      raise RuntimeError("device not initialized")
    stat = ctypes.c_ushort(0)
    self.dev.ftdi_fn.ftdi_poll_modem_status(ctypes.byref(stat))
    return hex(stat.value)

  async def read_resp(self, timeout=20) -> bytes:
    """ Read a response from the plate reader. If the timeout is reached, return the data that has
    been read so far. """

    if self.dev is None:
      raise RuntimeError("device not initialized")

    d = b""
    t = time.time()
    while (len(d) == 0 or not d[-1] == 0x0d) and time.time() - t < timeout: #
      r = self.dev.read(25) # 25 is max length observed in pcap
      if len(r) != 0:
        d += r
      else:
        # If we read data, immediately try again. if we didn't read data, wait a bit before trying
        # again.
        await asyncio.sleep(0.0001)

    logger.debug("read %s", d.hex())

    return d

  async def send(self, cmd: bytearray, read_timeout=20):
    """ Send a command to the plate reader and return the response. """

    if self.dev is None:
      raise RuntimeError("device not initialized")

    logger.debug("sending %s", cmd.hex())

    w = self.dev.write(cmd)

    logger.debug("wrote %s bytes", w)

    assert w == len(cmd)

    return await self.read_resp(timeout=read_timeout)

  async def read_command_status(self):
    status = await self.send(bytearray([0x02, 0x00, 0x09, 0x0c, 0x80, 0x00, 0x00, 0x97, 0x0d]))
    return status

  async def wait_for_ready_and_return(self, ret, timeout=150):
    last_status = None
    t = time.time()
    while time.time() - t < timeout:
      await asyncio.sleep(0.1)

      command_status = await self.read_command_status()

      if len(command_status) != 24:
        logger.warning("unexpected response %s. I think a command status response is always 24 "
                       "bytes", command_status)
        continue

      if command_status != last_status:
        logger.info("status changed %s", command_status)
        last_status = command_status
      else:
        continue

      if command_status[2] != b"\x18" or command_status[3] != b"\x0c" or \
        command_status[4] != b"\x01":
        logger.warning("unexpected response %s. I think 18 0c 01 indicates a command status "
                        "response", command_status)

      if command_status[5] not in {b"\x25", b"\x05"}: # 25 is busy, 05 is ready. probably.
        logger.warning("unexpected response %s.", command_status)

      if command_status[5] == 0x05:
        logger.debug("status is ready")
        return ret

  async def initialize(self):
    command_response = await self.send(
      bytearray([0x02, 0x00, 0x0D, 0x0C, 0x01, 0x00, 0x00, 0x10, 0x02, 0x00, 0x00, 0x2E, 0x0D]))
    return await self.wait_for_ready_and_return(command_response)

  async def request_eeprom_data(self):
    eeprom_response = await self.send(
      bytearray([0x02, 0x00, 0x0F, 0x0C, 0x05, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29,
        0x0D]))
    return await self.wait_for_ready_and_return(eeprom_response)

  async def open(self):
    open_response = await self.send(bytearray([0x02, 0x00, 0x0E, 0x0C, 0x03, 0x01, 0x00, 0x00, 0x00,
      0x00, 0x00, 0x00, 0x20, 0x0D]))
    return await self.wait_for_ready_and_return(open_response)

  async def close(self):
    close_response = await self.send(bytearray([0x02, 0x00, 0x0E, 0x0C, 0x03, 0x00, 0x00, 0x00,
      0x00, 0x00, 0x00, 0x00, 0x1F, 0x0D]))
    return await self.wait_for_ready_and_return(close_response)

  async def _mp_and_focus_height_value(self):
    mp_and_focus_height_value_response = await self.send(bytearray([0x02, 0x00, 0x0F, 0x0C, 0x05,
      0x17, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x39, 0x0D]))
    return await self.wait_for_ready_and_return(mp_and_focus_height_value_response)

  async def _run(self):
    run_response = await self.send(bytearray([0x02, 0x00, 0x86, 0x0C, 0x04, 0x31, 0xEC, 0x21, 0x66,
      0x05, 0x96, 0x04, 0x60, 0x2C, 0x56, 0x1D, 0x06, 0x0C, 0x08, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
      0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x01,
      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x04, 0x00, 0x1E, 0x27, 0x0F, 0x27, 0x0F,
      0x01, 0x05, 0x14, 0x00, 0x00, 0x01, 0x00, 0x00, 0x0E, 0x10, 0x00, 0x01, 0x00, 0x01, 0x00,
      0x01, 0x00, 0x01, 0x00, 0x01, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
      0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x64, 0x00, 0x20,
      0x00, 0x00, 0x11, 0x65, 0x0D]))

    # TODO: find a prettier way to do this. It's essentially copied from wait_for_ready_and_return.
    last_status = None
    while True:
      await asyncio.sleep(0.1)

      command_status = await self.read_command_status()

      if command_status != last_status:
        last_status = command_status
        logger.info("status changed %s", command_status)
        continue

      if command_status == bytes(b"\x02\x00\x18\x0c\x01\x25\x04\x2e\x00\x00\x04\x01\x00\x00\x03\x00"
                                 b"\x00\x00\x00\xc0\x00\x01\x46\x0d"):
        return run_response

  async def _read_order_values(self):
    return await self.send(
      bytearray([0x02, 0x00, 0x0F, 0x0C, 0x05, 0x1D, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3F,
      0x0D]))

  async def _status_hw(self):
    status_hw_response = await self.send(bytearray([0x02, 0x00, 0x09, 0x0C, 0x81, 0x00, 0x00, 0x98,
      0x0D]))
    return await self.wait_for_ready_and_return(status_hw_response)

  async def _get_measurement_values(self):
    return await self.send(bytearray([0x02, 0x00, 0x0F, 0x0C, 0x05, 0x02, 0x00, 0x00, 0x00, 0x00,
      0x00, 0x00, 0x00, 0x24, 0x0D]))

  async def read_luminescence(self) -> List[List[float]]:
    await self._mp_and_focus_height_value()

    await self._run()

    await self._read_order_values()

    await self._status_hw()

    vals = await self._get_measurement_values()

    # All 96 values are 32 bit integers. The header is variable length, so we need to find the
    # start of the data. In the future, when we understand the protocol better, this can be
    # replaced with a more robust solution.
    start_idx = vals.index(b"\x00\x00\x00\x00\x00\x00") + len(b"\x00\x00\x00\x00\x00\x00")
    data = list(vals)[start_idx:start_idx+96*4]

    # group bytes by 4
    int_bytes = [data[i:i+4] for i in range(0, len(data), 4)]

    # convert to int
    ints = [struct.unpack(">i", bytes(int_data))[0] for int_data in int_bytes]

    # for backend conformity, convert to float, and reshape to 2d array
    floats = [[float(int_) for int_ in ints[i:i+12]] for i in range(0, len(ints), 12)]

    return floats
