import asyncio
import enum
import logging
import time
from typing import List, Optional, Union

try:
  from pylibftdi import Device
  USE_FTDI = True
except ImportError:
  USE_FTDI = False

from pylabrobot.plate_reading.backend import PlateReaderBackend


logger = logging.getLogger("pylabrobot.plate_reading.biotek")


class Cytation5Backend(PlateReaderBackend):
  """ Backend for biotek cytation 5 image reader """
  def __init__(self, timeout: float = 20) -> None:
    super().__init__()
    self.timeout = timeout
    if not USE_FTDI:
      raise RuntimeError("pylibftdi is not installed. Run `pip install pylabrobot[plate_reading]`.")

    self.dev = Device(lazy_open=True)

  async def setup(self) -> None:
    logger.info("[cytation5] setting up")
    self.dev.open()
    # self.dev.baudrate = 9600 # worked in the past
    self.dev.baudrate = 38400
    self.dev.ftdi_fn.ftdi_set_line_property(8, 2, 0) # 8 bits, 2 stop bits, no parity
    SIO_RTS_CTS_HS = 0x1 << 8
    self.dev.ftdi_fn.ftdi_setflowctrl(SIO_RTS_CTS_HS)
    self.dev.ftdi_fn.ftdi_setrts(1)

    self._shaking = False
    self._shaking_task: Optional[asyncio.Task] = None

  async def stop(self) -> None:
    logger.info("[cytation5] stopping")
    await self.stop_shaking()
    self.dev.close()

  async def _purge_buffers(self) -> None:
    """ Purge the RX and TX buffers, as implemented in Gen5.exe """
    for _ in range(6):
      self.dev.ftdi_fn.ftdi_usb_purge_rx_buffer()
    self.dev.ftdi_fn.ftdi_usb_purge_tx_buffer()

  async def _read_until(self, char: bytes, timeout: Optional[float] = None) -> bytes:
    """ If timeout is None, use self.timeout """
    if timeout is None:
      timeout = self.timeout
    x = None
    res = b""
    t0 = time.time()
    while x != char:
      x = self.dev.read(1)
      res += x

      if time.time() - t0 > timeout:
        logger.debug("[cytation5] received incomplete %s", res)
        raise TimeoutError("Timeout while waiting for response")

      if x == b"":
        await asyncio.sleep(0.01)

    logger.debug("[cytation5] received %s", res)
    return res

  async def send_command(
    self,
    command: Union[bytes, str],
    purge: bool = True,
    wait_for_char: Optional[bytes] = b"\x03") -> Optional[bytes]:
    if purge:
      # real software does this, but I don't think it's necessary
      await self._purge_buffers()

    if not isinstance(command, bytes):
      command = command.encode()
    self.dev.write(command)
    logger.debug("[cytation5] sent %s", command)

    if wait_for_char is None:
      return None

    return await self._read_until(wait_for_char)

  async def get_serial_number(self) -> str:
    resp = await self.send_command("C")
    assert resp is not None
    return resp[1:].split(b" ")[0].decode()

  async def get_firmware_version(self) -> str:
    resp = await self.send_command("e")
    assert resp is not None
    return " ".join(resp[1:-1].decode().split(" ")[0:4])

  async def open(self):
    return await self.send_command("J")

  async def close(self):
    return await self.send_command("A")

  async def get_current_temperature(self) -> float:
    """ Get current temperature in degrees Celsius. """
    resp = await self.send_command("h")
    assert resp is not None
    return int(resp[1:-1]) / 100000

  def _parse_body(self, body: bytes) -> List[List[float]]:
    start_index = body.index(b"01,01")
    end_index = body.rindex(b"\r\n")
    num_rows = 8
    rows = body[start_index:end_index].split(b"\r\n,")[:num_rows]

    parsed_data: List[List[float]] = []
    for row_idx, row in enumerate(rows):
      parsed_data.append([])
      values = row.split(b",")
      grouped_values = [values[i:i+3] for i in range(0, len(values), 3)]

      for group in grouped_values:
        assert len(group) == 3
        value = float(group[2].decode())
        parsed_data[row_idx].append(value)
    return parsed_data

  async def read_absorbance(self, wavelength: int) -> List[List[float]]:
    if not 230 <= wavelength <= 999:
      raise ValueError("Wavelength must be between 230 and 999")

    resp = await self.send_command("y", wait_for_char=b"\x06")
    assert resp == b"\x06"
    await self.send_command(b"08120112207434014351135308559127881772\x03", purge=False)

    resp = await self.send_command("D", wait_for_char=b"\x06")
    assert resp == b"\x06"
    wavelength_str = str(wavelength).zfill(4)
    cmd = f"00470101010812000120010000110010000010600008{wavelength_str}1".encode()
    checksum = str(sum(cmd) % 100).encode()
    cmd = cmd + checksum + b"\x03"
    await self.send_command(cmd, purge=False)

    resp1 = await self.send_command("O", wait_for_char=b"\x06")
    assert resp1 == b"\x06"
    resp2 = await self._read_until(b"\x03")
    assert resp2 == b"0000\x03"

    # read data
    body = await self._read_until(b"\x03")
    assert resp is not None
    return self._parse_body(body)

  async def read_luminescence(self, focal_height: float) -> List[List[float]]:
    if not 4.5 <= focal_height <= 13.88:
      raise ValueError("Focal height must be between 4.5 and 13.88")

    resp = await self.send_command("t", wait_for_char=b"\x06")
    assert resp == b"\x06"

    cmd = f"3{14220 + int(1000*focal_height)}\x03".encode()
    await self.send_command(cmd, purge=False)

    resp = await self.send_command("y", wait_for_char=b"\x06")
    assert resp == b"\x06"
    await self.send_command(b"08120112207434014351135308559127881772\x03", purge=False)

    resp = await self.send_command("D", wait_for_char=b"\x06")
    assert resp == b"\x06"
    cmd = (b"008401010108120001200100001100100000123000500200200"
           b"-001000-00300000000000000000001351092")
    await self.send_command(cmd, purge=False)

    resp1 = await self.send_command("O", wait_for_char=b"\x06")
    assert resp1 == b"\x06"
    resp2 = await self._read_until(b"\x03")
    assert resp2 == b"0000\x03"

    body = await self._read_until(b"\x03", timeout=60*3)
    assert body is not None
    return self._parse_body(body)

  async def read_fluorescence(
    self,
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[List[float]]:
    if not 4.5 <= focal_height <= 13.88:
      raise ValueError("Focal height must be between 4.5 and 13.88")
    if not 250 <= excitation_wavelength <= 700:
      raise ValueError("Excitation wavelength must be between 250 and 700")
    if not 250 <= emission_wavelength <= 700:
      raise ValueError("Emission wavelength must be between 250 and 700")

    resp = await self.send_command("t", wait_for_char=b"\x06")
    assert resp == b"\x06"

    cmd = f"{614220 + int(1000*focal_height)}\x03".encode()
    await self.send_command(cmd, purge=False)

    resp = await self.send_command("y", wait_for_char=b"\x06")
    assert resp == b"\x06"
    await self.send_command(b"08120112207434014351135308559127881772\x03", purge=False)

    resp = await self.send_command("D", wait_for_char=b"\x06")
    assert resp == b"\x06"
    excitation_wavelength_str = str(excitation_wavelength).zfill(4)
    emission_wavelength_str = str(emission_wavelength).zfill(4)
    cmd = (f"008401010108120001200100001100100000135000100200200{excitation_wavelength_str}000"
      f"{emission_wavelength_str}000000000000000000210011").encode()
    checksum = str((sum(cmd)+7) % 100).encode() # don't know why +7
    cmd = cmd + checksum + b"\x03"
    await self.send_command(cmd, purge=False)

    resp1 = await self.send_command("O", wait_for_char=b"\x06")
    assert resp1 == b"\x06"
    resp2 = await self._read_until(b"\x03")
    assert resp2 == b"0000\x03"

    body = await self._read_until(b"\x03", timeout=60*2)
    assert body is not None
    return self._parse_body(body)

  async def _abort(self) -> None:
    await self.send_command("x", wait_for_char=None)

  class ShakeType(enum.IntEnum):
    LINEAR = 0
    ORBITAL = 1

  async def shake(self, shake_type: ShakeType) -> None:
    """ Warning: the duration for shaking has to be specified on the machine, and the maximum is
    16 minutes. As a hack, we start shaking for the maximum duration every time as long as stop
    is not called. """
    max_duration = 16*60 # 16 minutes

    async def shake_maximal_duration():
      """ This method will start the shaking, but returns immediately after
      shaking has started. """
      resp = await self.send_command("y", wait_for_char=b"\x06")
      assert resp == b"\x06"
      await self.send_command(b"08120112207434014351135308559127881422\x03", purge=False)

      resp = await self.send_command("D", wait_for_char=b"\x06")
      assert resp == b"\x06"
      shake_type_bit = str(shake_type.value)

      duration = str(max_duration).zfill(3)
      cmd = f"0033010101010100002000000013{duration}{shake_type_bit}301".encode()
      checksum = str((sum(cmd)+73) % 100).encode() # don't know why +73
      cmd = cmd + checksum + b"\x03"
      await self.send_command(cmd, purge=False)

      resp = await self.send_command("O", wait_for_char=b"\x06")
      assert resp == b"\x06"
      resp = await self._read_until(b"\x03")
      assert resp == b"0000\x03"

    async def shake_continuous():
      while self._shaking:
        await shake_maximal_duration()

        # short sleep allows = frequent checks for fast stopping
        seconds_since_start: float = 0
        loop_wait_time = 0.25
        while seconds_since_start < max_duration and self._shaking:
          seconds_since_start += loop_wait_time
          await asyncio.sleep(loop_wait_time)

    self._shaking = True
    self._shaking_task = asyncio.create_task(shake_continuous())

  async def stop_shaking(self) -> None:
    await self._abort()
    if self._shaking:
      self._shaking = False
    if self._shaking_task is not None:
      self._shaking_task.cancel()
      try:
        await self._shaking_task
      except asyncio.CancelledError:
        pass
      self._shaking_task = None
