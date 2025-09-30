import asyncio
import time
import typing

import serial as pyserial

from pylabrobot.io.serial import Serial
from pylabrobot.resources import Plate, PlateHolder

from . import IncubatorShakerBackend


class InhecoIncubatorShakerBackend(IncubatorShakerBackend):
  def __init__(
    self, port: str = "/dev/cu.usbserial-1140", baudrate: int = 19200, device_id: int = 2
  ):
    self.io = Serial(port=port, baudrate=baudrate)
    assert 0 <= device_id <= 5, "Device ID must be between 0 and 5"
    self.device_id = device_id

  async def setup(self) -> None:
    await self.io.setup()
    if hasattr(self.io, "_serial") and isinstance(self.io._serial, pyserial.Serial):
      self.io._serial.setDTR(True)
      self.io._serial.setRTS(True)
      self.io._serial.reset_input_buffer()
      self.io._serial.reset_output_buffer()
    time.sleep(1.0)
    await self.send_command("AID", timeout=5.0)

  async def stop(self) -> None:
    await self.stop_shaking()
    await self.send_command("SHE0")
    await self.io.stop()

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      **self.io.serialize(),
      "device_id": self.device_id,
    }

  def _build_message(self, command: str) -> bytes:
    payload = bytearray()
    payload.append(0x54)  # 'T'
    payload.append(0x30)  # '0'
    payload.append(ord("0") + self.device_id)
    payload.extend(command.encode("ascii"))

    msg_length = len(payload) + 3
    header = bytearray()
    header.append(msg_length)
    header.append(0x30 + self.device_id)
    header.append(0xC0 + len(payload))

    message = header + payload
    crc = self._calculate_crc(message)
    message.append(crc)
    return bytes(message)

  def _calculate_crc(self, data: bytes) -> int:
    crc = 0xA1
    for b in data:
      crc = self._crc8(b, crc)
    return crc

  def _crc8(self, data: int, crc: int) -> int:
    for _ in range(8):
      if (data ^ crc) & 1:
        crc ^= 0x18
        crc = (crc >> 1) | 0x80
      else:
        crc >>= 1
      data >>= 1
    return crc

  async def test_raw_command(self, command: str, timeout: float = 5.0):
    msg = self._build_message(command)
    print("Sending:", msg)
    await self.io.write(msg)
    response = b""
    start = time.time()
    while time.time() - start < timeout:
      data = await self.io.read()
      if data:
        response += data
    print("Raw response:", response)

  async def _read_response(self, command: str, timeout: float = 3.0) -> str:
    msg = self._build_message(command)
    await self.io.write(msg)
    print("Sending:", msg)
    response = b""
    start = time.time()
    while time.time() - start < timeout:
      data = await self.io.read()
      if data:
        response += data
        if len(response) >= 4 and response[-2:] == b"\x20\x60":
          break
      await asyncio.sleep(0.01)

    print("Raw response:", response)

    if not response.startswith(bytes([0xB0 + self.device_id])):
      print("Warning: Device ID mismatch or unexpected header.")

    if response[-2:] != b"\x20\x60":
      raise RuntimeError(f"Unexpected response terminator: {response}")

    stripped = response[1:-2]
    try:
      return stripped.decode("ascii", errors="ignore").strip()
    except Exception as e:
      raise RuntimeError(f"Could not decode response: {stripped}") from e

  async def send_command(self, cmd: str, timeout: float = 3.0) -> str:
    return await self._read_response(cmd, timeout=timeout)

  async def open_door(self):
    await self.send_command("AOD")

  async def close_door(self):
    await self.send_command("ACD")

  async def get_door_status(self) -> bool:
    resp = await self.send_command("RDS")
    return resp == "1"

  async def fetch_plate_to_loading_tray(self, plate: Plate):
    raise NotImplementedError("This device does not support automated plate fetching.")

  async def take_in_plate(self, plate: Plate, site: PlateHolder):
    raise NotImplementedError("This device does not support automated plate loading.")

  async def set_temperature(self, temperature: float):
    t = int(temperature * 10)
    await self.send_command(f"STT{t}")
    await self.send_command("SHE1")

  async def get_temperature(self) -> float:
    resp = await self.send_command("RAT1")
    return float(resp) / 10

  async def get_target_temperature(self) -> float:
    resp = await self.send_command("RTT")
    return float(resp) / 10

  async def get_heater_status(self) -> int:
    resp = await self.send_command("RHE")
    return int(resp)

  async def start_shaking(self, frequency: float):
    f10 = int(frequency * 10)
    await self.send_command(f"SFX{f10}")
    await self.send_command("ASE1")

  async def shake(self, speed: float):
    hz = speed / 60
    await self.start_shaking(frequency=hz)

  async def stop_shaking(self):
    await self.send_command("ASE0")

  async def get_shaker_status(self) -> int:
    resp = await self.send_command("RSE")
    return int(resp)

  async def lock_plate(self):
    raise NotImplementedError("Plate locking is not supported on this device.")

  async def unlock_plate(self):
    raise NotImplementedError("Plate unlocking is not supported on this device.")

  async def self_test(self) -> int:
    resp = await self.send_command("AQS", timeout=5.0)
    return int(resp)

  async def get_device_info(self, index: int) -> str:
    return await self.send_command(f"RFV{index}")
