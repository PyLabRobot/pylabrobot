import asyncio
import logging
import math
import struct
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Union

from pylabrobot.capabilities.plate_reading.absorbance import (
  AbsorbanceBackend,
  AbsorbanceCapability,
  AbsorbanceResult,
)
from pylabrobot.capabilities.plate_reading.fluorescence import (
  FluorescenceBackend,
  FluorescenceCapability,
  FluorescenceResult,
)
from pylabrobot.capabilities.plate_reading.luminescence import (
  LuminescenceBackend,
  LuminescenceCapability,
  LuminescenceResult,
)
from pylabrobot.device import Device, Driver
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, PlateHolder, Resource
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.serializer import SerializableMixin
from pylabrobot.utils.list import reshape_2d

if sys.version_info >= (3, 8):
  from typing import Literal
else:
  from typing_extensions import Literal

logger = logging.getLogger("pylabrobot")


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class CLARIOstarBackend(AbsorbanceBackend, LuminescenceBackend, FluorescenceBackend, Driver):
  """Backend for the BMG Labtech CLARIOstar plate reader.

  Communicates over FTDI USB (VID 0x0403, PID 0xBB68) at 125000 baud.
  Supports absorbance and luminescence. Fluorescence is not yet implemented.
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

  # -- Helpers --------------------------------------------------------------

  async def _mp_and_focus_height_value(self) -> None:
    resp = await self.send(b"\x02\x00\x0f\x0c\x05\17\x00\x00\x00\x00\x00\x00")
    await self._wait_for_ready_and_return(resp)

  async def _read_order_values(self) -> bytes:
    return await self.send(b"\x02\x00\x0f\x0c\x05\x1d\x00\x00\x00\x00\x00\x00")

  async def _status_hw(self) -> bytes:
    resp = await self.send(b"\x02\x00\x09\x0c\x81\x00")
    return await self._wait_for_ready_and_return(resp)

  async def _get_measurement_values(self) -> bytes:
    return await self.send(b"\x02\x00\x0f\x0c\x05\x02\x00\x00\x00\x00\x00\x00")

  def _plate_bytes(self, plate: Plate) -> bytes:
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

  # -- Measurement runs -----------------------------------------------------

  async def _run_luminescence(self, focal_height: float, plate: Plate) -> bytes:
    assert 0 <= focal_height <= 25, "focal height must be between 0 and 25 mm"
    focal_height_data = int(focal_height * 100).to_bytes(2, byteorder="big")
    plate_bytes = self._plate_bytes(plate)

    payload = (
      b"\x04" + plate_bytes + b"\x02\x01\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27"
      b"\x0f\x27\x0f\x01" + focal_height_data + b"\x00\x00\x01\x00\x00\x0e\x10\x00\x01\x00\x01"
      b"\x00\x01\x00\x01\x00\x01\x00\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00"
      b"\x00\x01\x00\x00\x00\x01\x00\x64\x00\x20\x00\x00"
    )
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

  async def _run_absorbance(self, wavelength: float, plate: Plate) -> bytes:
    wavelength_data = int(wavelength * 10).to_bytes(2, byteorder="big")
    plate_bytes = self._plate_bytes(plate)

    payload = (
      b"\x04" + plate_bytes + b"\x82\x02\x00\x00\x00\x00\x00\x00\x00\x20\x04\x00\x1e\x27\x0f\x27"
      b"\x0f\x19\x01" + wavelength_data + b"\x00\x00\x00\x64\x00\x00\x00\x00\x00\x00\x00\x64\x00"
      b"\x00\x00\x00\x00\x02\x00\x00\x00\x00\x01\x00\x00\x00\x01\x00\x16\x00\x01\x00\x00"
    )
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

  # -- Capability methods ---------------------------------------------------

  async def read_luminescence(
    self,
    plate: Plate,
    wells: List[Well],
    focal_height: float = 13,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[LuminescenceResult]:
    if wells != plate.get_all_items():
      raise NotImplementedError("Only full plate reads are supported for now.")

    await self._mp_and_focus_height_value()
    await self._run_luminescence(focal_height=focal_height, plate=plate)
    await self._read_order_values()
    await self._status_hw()

    vals = await self._get_measurement_values()
    num_wells = plate.num_items
    start_idx = vals.index(b"\x00\x00\x00\x00\x00\x00") + len(b"\x00\x00\x00\x00\x00\x00")
    data = list(vals)[start_idx : start_idx + num_wells * 4]
    int_bytes = [data[i : i + 4] for i in range(0, len(data), 4)]
    ints = [struct.unpack(">i", bytes(int_data))[0] for int_data in int_bytes]
    floats: List[List[Optional[float]]] = reshape_2d(
      [float(i) for i in ints], (plate.num_items_y, plate.num_items_x)
    )

    return [
      LuminescenceResult(
        data=floats,
        temperature=None,
        timestamp=time.time(),
      )
    ]

  @dataclass
  class AbsorbanceParams(BackendParams):
    report: Literal["OD", "transmittance"] = "OD"

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[AbsorbanceResult]:
    if not isinstance(backend_params, self.AbsorbanceParams):
      backend_params = CLARIOstarBackend.AbsorbanceParams()

    if wells != plate.get_all_items():
      raise NotImplementedError("Only full plate reads are supported for now.")

    await self._mp_and_focus_height_value()
    await self._run_absorbance(wavelength=wavelength, plate=plate)
    await self._read_order_values()
    await self._status_hw()

    vals = await self._get_measurement_values()
    num_wells = plate.num_items
    div = b"\x00" * 6
    start_idx = vals.index(div) + len(div)
    chromatic_data = vals[start_idx : start_idx + num_wells * 4]
    ref_data = vals[start_idx + num_wells * 4 : start_idx + (num_wells * 2) * 4]
    chromatic_bytes = [bytes(chromatic_data[i : i + 4]) for i in range(0, len(chromatic_data), 4)]
    ref_bytes = [bytes(ref_data[i : i + 4]) for i in range(0, len(ref_data), 4)]
    chromatic_reading = [struct.unpack(">i", x)[0] for x in chromatic_bytes]
    reference_reading = [struct.unpack(">i", x)[0] for x in ref_bytes]

    after_values_idx = start_idx + (num_wells * 2) * 4
    c100, c0, r100, r0 = struct.unpack(">iiii", vals[after_values_idx : after_values_idx + 4 * 4])

    real_chromatic_reading = [(cr - c0) / c100 for cr in chromatic_reading]
    real_reference_reading = [(rr - r0) / r100 for rr in reference_reading]

    transmittance: List[Optional[float]] = [
      rcr / rrr * 100 for rcr, rrr in zip(real_chromatic_reading, real_reference_reading)
    ]

    data: List[List[Optional[float]]]
    if backend_params.report == "OD":
      od: List[Optional[float]] = [
        math.log10(100 / t) if t is not None and t > 0 else None for t in transmittance
      ]
      data = reshape_2d(od, (plate.num_items_y, plate.num_items_x))
    elif backend_params.report == "transmittance":
      data = reshape_2d(transmittance, (plate.num_items_y, plate.num_items_x))
    else:
      raise ValueError(f"Invalid report type: {backend_params.report}")

    return [
      AbsorbanceResult(
        data=data,
        wavelength=wavelength,
        temperature=None,
        timestamp=time.time(),
      )
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
    backend_params: Optional[SerializableMixin] = None,
  ) -> List[FluorescenceResult]:
    raise NotImplementedError("CLARIOstar fluorescence reading is not implemented yet.")


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class CLARIOstar(Resource, Device):
  """BMG Labtech CLARIOstar plate reader."""

  def __init__(
    self,
    name: str,
    device_id: Optional[str] = None,
    size_x: float = 0.0,  # TODO: measure
    size_y: float = 0.0,  # TODO: measure
    size_z: float = 0.0,  # TODO: measure
  ):
    backend = CLARIOstarBackend(device_id=device_id)
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      model="BMG CLARIOstar",
    )
    Device.__init__(self, driver=backend)
    self._driver: CLARIOstarBackend = backend
    self.absorbance = AbsorbanceCapability(backend=backend)
    self.luminescence = LuminescenceCapability(backend=backend)
    self.fluorescence = FluorescenceCapability(backend=backend)
    self._capabilities = [self.absorbance, self.luminescence, self.fluorescence]

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,  # TODO: measure
      size_y=85.48,  # TODO: measure
      size_z=0,
      pedestal_size_z=0,
      child_location=Coordinate.zero(),  # TODO: measure
    )
    self.assign_child_resource(self.plate_holder, location=Coordinate.zero())

  def serialize(self) -> dict:
    return {**Resource.serialize(self), **Device.serialize(self)}

  async def open(self) -> None:
    """Open the plate tray."""
    await self._driver.open()

  async def close(self) -> None:
    """Close the plate tray."""
    await self._driver.close()
