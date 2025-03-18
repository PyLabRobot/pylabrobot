import asyncio
import logging
import math
import struct
import sys
import time
from typing import List, Optional, Union

from pylabrobot import utils
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources.plate import Plate

from .backend import PlateReaderBackend

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal

logger = logging.getLogger("pylabrobot")


class CLARIOStar(PlateReaderBackend):
  """A plate reader backend for the Clario star. Note that this is not a complete implementation
  and many commands and parameters are not implemented yet."""

  def __init__(self, device_id: Optional[str] = None):
    self.io = FTDI(device_id=device_id)

  async def setup(self):
    await self.io.setup()
    self.io.set_baudrate(125000)
    self.io.set_line_property(8, 0, 0)  # 8N1
    self.io.set_latency_timer(2)

    await self.initialize()
    await self.request_eeprom_data()

  async def stop(self):
    await self.io.stop()

  def get_stat(self):
    stat = self.io.poll_modem_status()
    return hex(stat)

  async def read_resp(self, timeout=20) -> bytes:
    """Read a response from the plate reader. If the timeout is reached, return the data that has
    been read so far."""

    d = b""
    last_read = b""
    end_byte_found = False
    t = time.time()

    # Commands are terminated with 0x0d, but this value may also occur as a part of the response.
    # Therefore, we read until we read a 0x0d, but if that's the last byte we read in a full packet,
    # we keep reading for at least one more cycle. We only check the timeout if the last read was
    # unsuccessful (i.e. keep reading if we are still getting data).
    while True:
      last_read = self.io.read(25)  # 25 is max length observed in pcap
      if len(last_read) > 0:
        d += last_read
        end_byte_found = d[-1] == 0x0D
        if (
          len(last_read) < 25 and end_byte_found
        ):  # if we read less than 25 bytes, we're at the end
          break
      else:
        # If we didn't read any data, check if the last read ended in an end byte. If so, we're done
        if end_byte_found:
          break

        # Check if we've timed out.
        if time.time() - t > timeout:
          logger.warning("timed out reading response")
          break

        # If we read data, we don't wait and immediately try to read more.
        await asyncio.sleep(0.0001)

    logger.debug("read %s", d.hex())

    return d

  async def send(self, cmd: Union[bytearray, bytes], read_timeout=20):
    """Send a command to the plate reader and return the response."""

    checksum = (sum(cmd) & 0xFFFF).to_bytes(2, byteorder="big")
    cmd = cmd + checksum + b"\x0d"

    logger.debug("sending %s", cmd.hex())

    w = self.io.write(cmd)

    logger.debug("wrote %s bytes", w)

    assert w == len(cmd)

    resp = await self.read_resp(timeout=read_timeout)
    return resp

  async def _wait_for_ready_and_return(self, ret, timeout=150):
    """Wait for the plate reader to be ready and return the response."""
    last_status = None
    t = time.time()
    while time.time() - t < timeout:
      await asyncio.sleep(0.1)

      command_status = await self.read_command_status()

      if len(command_status) != 24:
        logger.warning(
          "unexpected response %s. I think a command status response is always 24 " "bytes",
          command_status,
        )
        continue

      if command_status != last_status:
        logger.info("status changed %s", command_status.hex())
        last_status = command_status
      else:
        continue

      if command_status[2] != 0x18 or command_status[3] != 0x0C or command_status[4] != 0x01:
        logger.warning(
          "unexpected response %s. I think 18 0c 01 indicates a command status " "response",
          command_status,
        )

      if command_status[5] not in {
        0x25,
        0x05,
      }:  # 25 is busy, 05 is ready. probably.
        logger.warning("unexpected response %s.", command_status)

      if command_status[5] == 0x05:
        logger.debug("status is ready")
        return ret

  async def read_command_status(self):
    status = await self.send(b"\x02\x00\x09\x0c\x80\x00")
    return status

  async def initialize(self):
    command_response = await self.send(b"\x02\x00\x0d\x0c\x01\x00\x00\x10\x02\x00")
    return await self._wait_for_ready_and_return(command_response)

  async def request_eeprom_data(self):
    eeprom_response = await self.send(b"\x02\x00\x0f\x0c\x05\x07\x00\x00\x00\x00\x00\x00")
    return await self._wait_for_ready_and_return(eeprom_response)

  async def open(self):
    open_response = await self.send(b"\x02\x00\x0e\x0c\x03\x01\x00\x00\x00\x00\x00")
    return await self._wait_for_ready_and_return(open_response)

  async def close(self, plate: Optional[Plate] = None):
    close_response = await self.send(b"\x02\x00\x0e\x0c\x03\x00\x00\x00\x00\x00\x00")
    return await self._wait_for_ready_and_return(close_response)

  async def _mp_and_focus_height_value(self):
    mp_and_focus_height_value_response = await self.send(
      b"\x02\x00\x0f\x0c\x05\17\x00\x00\x00\x00" + b"\x00\x00"
    )
    return await self._wait_for_ready_and_return(mp_and_focus_height_value_response)

  async def _run_luminescence(self, focal_height: float):
    """Run a plate reader luminescence run."""

    assert 0 <= focal_height <= 25, "focal height must be between 0 and 25 mm"

    focal_height_data = int(focal_height * 100).to_bytes(2, byteorder="big")

    run_response = await self.send(
      b"\x02\x00\x86\x0c\x04\x31\xec\x21\x66\x05\x96\x04\x60\x2c\x56"
      b"\x1d\x06\x0c\x08\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00"
      b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
      b"\x00\x00\x00\x00\x00\x00\x00\x00\x02\x01\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27"
      b"\x0f\x27\x0f\x01" + focal_height_data + b"\x00\x00\x01\x00\x00\x0e\x10\x00\x01\x00\x01\x00"
      b"\x01\x00\x01\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x00\x01"
      b"\x00\x00\x00\x01\x00\x64\x00\x20\x00\x00"
    )

    # TODO: find a prettier way to do this. It's essentially copied from _wait_for_ready_and_return.
    last_status = None
    while True:
      await asyncio.sleep(0.1)

      command_status = await self.read_command_status()

      if command_status != last_status:
        last_status = command_status
        logger.info("status changed %s", command_status)
        continue

      if command_status == bytes(
        b"\x02\x00\x18\x0c\x01\x25\x04\x2e\x00\x00\x04\x01\x00\x00\x03\x00"
        b"\x00\x00\x00\xc0\x00\x01\x46\x0d"
      ):
        return run_response

  async def _run_absorbance(self, wavelength: float):
    """Run a plate reader absorbance run."""
    wavelength_data = int(wavelength * 10).to_bytes(2, byteorder="big")

    absorbance_command = (
      b"\x02\x00\x7c\x0c\x04\x31\xec\x21\x66\x05\x96\x04\x60\x2c\x56\x1d\x06"
      b"\x0c\x08\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00\x00"
      b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
      b"\x00\x00\x00\x00\x00\x00\x82\x02\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27\x0f\x27"
      b"\x0f\x19\x01" + wavelength_data + b"\x00\x00\x00\x64\x00\x00\x00\x00\x00\x00\x00\x64\x00"
      b"\x00\x00\x00\x00\x02\x00\x00\x00\x00\x01\x00\x00\x00\x01\x00\x16\x00\x01\x00\x00"
    )
    run_response = await self.send(absorbance_command)

    # TODO: find a prettier way to do this. It's essentially copied from _wait_for_ready_and_return.
    last_status = None
    while True:
      await asyncio.sleep(0.1)

      command_status = await self.read_command_status()

      if command_status != last_status:
        last_status = command_status
        logger.info("status changed %s", command_status)
        continue

      if command_status == bytes(
        b"\x02\x00\x18\x0c\x01\x25\x04\x2e\x00\x00\x04\x01\x00\x00\x03\x00"
        b"\x00\x00\x00\xc0\x00\x01\x46\x0d"
      ):
        return run_response

  async def _read_order_values(self):
    return await self.send(b"\x02\x00\x0f\x0c\x05\x1d\x00\x00\x00\x00\x00\x00")

  async def _status_hw(self):
    status_hw_response = await self.send(b"\x02\x00\x09\x0c\x81\x00")
    return await self._wait_for_ready_and_return(status_hw_response)

  async def _get_measurement_values(self):
    return await self.send(b"\x02\x00\x0f\x0c\x05\x02\x00\x00\x00\x00\x00\x00")

  async def read_luminescence(self, plate: Plate, focal_height: float = 13) -> List[List[float]]:
    """Read luminescence values from the plate reader."""
    await self._mp_and_focus_height_value()

    await self._run_luminescence(focal_height=focal_height)

    await self._read_order_values()

    await self._status_hw()

    vals = await self._get_measurement_values()

    # All 96 values are 32 bit integers. The header is variable length, so we need to find the
    # start of the data. In the future, when we understand the protocol better, this can be
    # replaced with a more robust solution.
    start_idx = vals.index(b"\x00\x00\x00\x00\x00\x00") + len(b"\x00\x00\x00\x00\x00\x00")
    data = list(vals)[start_idx : start_idx + 96 * 4]

    # group bytes by 4
    int_bytes = [data[i : i + 4] for i in range(0, len(data), 4)]

    # convert to int
    ints = [struct.unpack(">i", bytes(int_data))[0] for int_data in int_bytes]

    # for backend conformity, convert to float, and reshape to 2d array
    floats = [[float(int_) for int_ in ints[i : i + 12]] for i in range(0, len(ints), 12)]

    return floats

  async def read_absorbance(
    self,
    plate: Plate,
    wavelength: int,
    report: Literal["OD", "transmittance"] = "OD",
  ) -> List[List[float]]:
    """Read absorbance values from the device.

    Args:
      wavelength: wavelength to read absorbance at, in nanometers.
      report: whether to report absorbance as optical depth (OD) or transmittance. Transmittance is
        used interchangeably with "transmission" in the CLARIOStar software and documentation.

    Returns:
      A 2d array of absorbance values, as transmission percentage (values between 0 and 100).
    """

    await self._mp_and_focus_height_value()

    await self._run_absorbance(wavelength=wavelength)

    await self._read_order_values()

    await self._status_hw()

    vals = await self._get_measurement_values()
    div = b"\x00" * 6
    start_idx = vals.index(div) + len(div)
    chromatic_data = vals[start_idx : start_idx + 96 * 4]
    ref_data = vals[start_idx + 96 * 4 : start_idx + (96 * 2) * 4]
    chromatic_bytes = [bytes(chromatic_data[i : i + 4]) for i in range(0, len(chromatic_data), 4)]
    ref_bytes = [bytes(ref_data[i : i + 4]) for i in range(0, len(ref_data), 4)]
    chromatic_reading = [struct.unpack(">i", x)[0] for x in chromatic_bytes]
    reference_reading = [struct.unpack(">i", x)[0] for x in ref_bytes]

    # c100 is the value of the chromatic at 100% intensity
    # c0 is the value of the chromatic at 0% intensity (black reading)
    # r100 is the value of the reference at 100% intensity
    # r0 is the value of the reference at 0% intensity (black reading)
    after_values_idx = start_idx + (96 * 2) * 4
    c100, c0, r100, r0 = struct.unpack(">iiii", vals[after_values_idx : after_values_idx + 4 * 4])

    # a bit much, but numpy should not be a dependency
    real_chromatic_reading = []
    for cr in chromatic_reading:
      real_chromatic_reading.append((cr - c0) / c100)
    real_reference_reading = []
    for rr in reference_reading:
      real_reference_reading.append((rr - r0) / r100)

    transmittance = []
    for rcr, rrr in zip(real_chromatic_reading, real_reference_reading):
      transmittance.append(rcr / rrr * 100)

    if report == "OD":
      od = []
      for t in transmittance:
        od.append(math.log10(100 / t))
      return utils.reshape_2d(od, (8, 12))

    if report == "transmittance":
      return utils.reshape_2d(transmittance, (8, 12))

  async def read_fluorescence(
    self,
    plate: Plate,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    raise NotImplementedError("Not implemented yet")
