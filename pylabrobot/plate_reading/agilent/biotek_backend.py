import asyncio
import enum
import logging
import time
from typing import Dict, Iterable, List, Optional, Tuple

from pylabrobot.io.ftdi import FTDI
from pylabrobot.plate_reading.backend import PlateReaderBackend
from pylabrobot.resources import Plate, Well

logger = logging.getLogger(__name__)


class BioTekPlateReaderBackend(PlateReaderBackend):
  """Backend for Agilent BioTek plate readers."""

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
  ) -> None:
    super().__init__()
    self.timeout = timeout

    self.io = FTDI(device_id=device_id)

    self._version: Optional[str] = None

    self._plate: Optional[Plate] = None
    self._shaking = False
    self._slow_mode: Optional[bool] = None

  def _non_overlapping_rectangles(
    self,
    points: Iterable[Tuple[int, int]],
  ) -> List[Tuple[int, int, int, int]]:
    """Find non-overlapping rectangles that cover all given points.

    Example:
      >>> points = [
      >>>   (1, 1),
      >>>   (2, 2), (2, 3), (2, 4),
      >>>   (3, 2), (3, 3), (3, 4),
      >>>   (4, 2), (4, 3), (4, 4), (4, 5),
      >>>   (5, 2), (5, 3), (5, 4), (5, 5),
      >>>   (6, 2), (6, 3), (6, 4), (6, 5),
      >>>   (7, 2), (7, 3), (7, 4),
      >>> ]
      >>> non_overlapping_rectangles(points)
      [
        (1, 1, 1, 1),
        (2, 2, 7, 4),
        (4, 5, 6, 5),
      ]
    """

    pts = set(points)
    rects = []

    while pts:
      # start a rectangle from one arbitrary point
      r0, c0 = min(pts)
      # expand right
      c1 = c0
      while (r0, c1 + 1) in pts:
        c1 += 1
      # expand downward as long as entire row segment is filled
      r1 = r0
      while all((r1 + 1, c) in pts for c in range(c0, c1 + 1)):
        r1 += 1

      rects.append((r0, c0, r1, c1))
      # remove covered points
      for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
          pts.discard((r, c))

    rects.sort()
    return rects

  async def setup(self) -> None:
    logger.info(f"{self.__class__.__name__} setting up")

    await self.io.setup()
    await self.io.usb_reset()
    await self.io.set_latency_timer(16)
    await self.io.set_baudrate(9600)  # 0x38 0x41
    await self.io.set_line_property(8, 2, 0)  # 8 data bits, 2 stop bits, no parity
    SIO_RTS_CTS_HS = 0x1 << 8
    await self.io.set_flowctrl(SIO_RTS_CTS_HS)
    await self.io.set_rts(True)

    # see if we need to adjust baudrate. This appears to be the case sometimes.
    try:
      self._version = await self.get_firmware_version()
    except TimeoutError:
      await self.io.set_baudrate(38_461)  # 4e c0
      self._version = await self.get_firmware_version()

    self._shaking = False
    self._shaking_task: Optional[asyncio.Task] = None

  async def stop(self) -> None:
    logger.info(f"{self.__class__.__name__} stopping")
    await self.stop_shaking()
    await self.io.stop()

    self._slow_mode = None

  @property
  def version(self) -> str:
    if self._version is None:
      raise RuntimeError(f"{self.__class__.__name__}: Firmware version is not set")
    return self._version

  @property
  def abs_wavelength_range(self) -> tuple[int, int]:
    return (230, 999)

  @property
  def focal_height_range(self) -> tuple[float, float]:
    return (4.5, 13.88)

  @property
  def excitation_range(self) -> tuple[int, int]:
    return (250, 700)

  @property
  def emission_range(self) -> tuple[int, int]:
    return (250, 700)

  @property
  def supports_heating(self) -> bool:
    return False

  @property
  def supports_cooling(self) -> bool:
    return False

  @property
  def temperature_range(self) -> Tuple[Optional[float], Optional[float]]:
    """Return (min_temp, max_temp).
    If cooling is not supported (heating only), min_temp is None.
    If heating is not supported (cooling only), max_temp is None.
    """
    max_temp = 45.0 if self.supports_heating else None  # default BioTek max
    min_temp = 4.0 if self.supports_cooling else None  # default cooling minimum
    return (min_temp, max_temp)

  async def _purge_buffers(self) -> None:
    """Purge the RX and TX buffers, as implemented in Gen5.exe"""
    for _ in range(6):
      await self.io.usb_purge_rx_buffer()
    await self.io.usb_purge_tx_buffer()

  async def _read_until(self, terminator: bytes, timeout: Optional[float] = None) -> bytes:
    """If timeout is None, use self.timeout"""
    if timeout is None:
      timeout = self.timeout
    x = None
    res = b""
    t0 = time.time()
    while x != terminator:
      x = await self.io.read(1)
      res += x

      if time.time() - t0 > timeout:
        logger.debug(f"{self.__class__.__name__} received incomplete %s", res)
        raise TimeoutError(f"{self.__class__.__name__}: Timeout while waiting for response")

      if x == b"":
        await asyncio.sleep(0.01)

    logger.debug(f"{self.__class__.__name__} received %s", res)
    return res

  async def send_command(
    self,
    command: str,
    parameter: Optional[str] = None,
    wait_for_response=True,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    await self._purge_buffers()

    await self.io.write(command.encode())
    logger.debug(f"{self.__class__.__name__} sent %s", command)
    response: Optional[bytes] = None
    if wait_for_response or parameter is not None:
      response = await self._read_until(
        b"\x06" if parameter is not None else b"\x03", timeout=timeout
      )

    if parameter is not None:
      await self.io.write(parameter.encode())
      logger.debug(f"{self.__class__.__name__} sent %s", parameter)
      if wait_for_response:
        response = await self._read_until(b"\x03", timeout=timeout)

    return response

  async def get_serial_number(self) -> str:
    resp = await self.send_command("C", timeout=1)
    assert resp is not None
    return resp[1:].split(b" ")[0].decode()

  async def get_firmware_version(self) -> str:
    resp = await self.send_command("e", timeout=1)
    assert resp is not None
    return " ".join(resp[1:-1].decode().split(" ")[3:4])

  async def _set_slow_mode(self, slow: bool):
    if self._slow_mode == slow:
      return
    await self.send_command("&", "S1" if slow else "S0")
    self._slow_mode = slow

  async def open(self, slow: bool = False):
    await self._set_slow_mode(slow)
    return await self.send_command("J")

  async def close(self, plate: Optional[Plate], slow: bool = False):
    # reset cache
    self._plate = None

    await self._set_slow_mode(slow)
    if plate is not None:
      await self.set_plate(plate)
    return await self.send_command("A")

  async def home(self):
    return await self.send_command("i", "x")

  async def get_current_temperature(self) -> float:
    """Get current temperature in degrees Celsius."""
    resp = await self.send_command("h", timeout=1)
    assert resp is not None
    return int(resp[1:-1]) / 100000

  async def set_temperature(self, temperature: float):
    """Set temperature in degrees Celsius."""
    if not self.supports_heating and not self.supports_cooling:
      raise NotImplementedError(f"{self.__class__.__name__} does not support temperature control.")

    tmin, tmax = self.temperature_range
    current_temperature = await self.get_current_temperature()

    if (tmin is not None and temperature < tmin) or (tmax is not None and temperature > tmax):
      raise ValueError(
        f"{self.__class__.__name__}: "
        f"Requested temperature {temperature}°C is outside supported range "
        f"{tmin}-{tmax}°C"
      )
    if temperature < current_temperature and not self.supports_cooling:
      raise ValueError(f"{self.__class__.__name__}: Cooling is not supported.")
    if temperature > current_temperature and not self.supports_heating:
      raise ValueError(f"{self.__class__.__name__}: Heating is not supported.")

    return await self.send_command("g", f"{int(temperature * 1000):05}")

  async def stop_heating_or_cooling(self):
    return await self.send_command("g", "00000")

  def _parse_body(self, body: bytes) -> List[List[Optional[float]]]:
    assert self._plate is not None, "Plate must be set before reading data"
    plate = self._plate
    start_index = 22
    end_index = body.rindex(b"\r\n")
    num_rows = plate.num_items_y
    rows = body[start_index:end_index].split(b"\r\n,")[:num_rows]

    parsed_data: Dict[Tuple[int, int], float] = {}
    for row in rows:
      values = row.split(b",")
      grouped_values = [values[i : i + 3] for i in range(0, len(values), 3)]

      for group in grouped_values:
        assert len(group) == 3
        row_index = int(group[0].decode()) - 1  # 1-based index in the response
        column_index = int(group[1].decode()) - 1  # 1-based index in the response
        raw_value = group[2].decode()
        value = float("nan") if "*" in raw_value else float(raw_value)
        parsed_data[(row_index, column_index)] = value

    result: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for (row_idx, col_idx), value in parsed_data.items():
      result[row_idx][col_idx] = value
    return result

  async def set_plate(self, plate: Plate):
    # 08120112207434014351135308559127881422
    #                                   ^^^^ plate size z
    #                             ^^^^^ plate size x
    #                         ^^^^^ plate size y
    #                   ^^^^^ bottom right x
    #               ^^^^^ top left x
    #         ^^^^^ bottom right y
    #     ^^^^^ top left y
    #   ^^ columns
    # ^^ rows

    if plate is self._plate:
      return

    rows = plate.num_items_y
    columns = plate.num_items_x

    bottom_right_well = plate.get_item(plate.num_items - 1)
    assert bottom_right_well.location is not None
    bottom_right_well_center = bottom_right_well.location + bottom_right_well.get_anchor(
      x="c", y="c"
    )
    top_left_well = plate.get_item(0)
    assert top_left_well.location is not None
    top_left_well_center = top_left_well.location + top_left_well.get_anchor(x="c", y="c")

    plate_size_y = plate.get_size_y()
    plate_size_x = plate.get_size_x()
    plate_size_z = plate.get_size_z()
    if plate.lid is not None:
      plate_size_z += plate.lid.get_size_z() - plate.lid.nesting_z_height

    top_left_well_center_y = plate.get_size_y() - top_left_well_center.y  # invert y axis
    bottom_right_well_center_y = plate.get_size_y() - bottom_right_well_center.y  # invert y axis

    cmd = (
      f"{rows:02}"
      f"{columns:02}"
      f"{int(top_left_well_center_y * 100):05}"
      f"{int(bottom_right_well_center_y * 100):05}"
      f"{int(top_left_well_center.x * 100):05}"
      f"{int(bottom_right_well_center.x * 100):05}"
      f"{int(plate_size_y * 100):05}"
      f"{int(plate_size_x * 100):05}"
      f"{int(plate_size_z * 100):04}"
      "\x03"
    )

    resp = await self.send_command("y", cmd, timeout=1)
    self._plate = plate
    return resp

  def _get_min_max_row_col_tuples(
    self, wells: List[Well], plate: Plate
  ) -> List[Tuple[int, int, int, int]]:  # min_row, min_col, max_row, max_col
    # check if all wells are in the same plate
    plates = set(well.parent for well in wells)
    if len(plates) != 1 or plates.pop() != plate:
      raise ValueError("All wells must be in the specified plate")
    return self._non_overlapping_rectangles((well.get_row(), well.get_column()) for well in wells)

  async def read_absorbance(self, plate: Plate, wells: List[Well], wavelength: int) -> List[Dict]:
    min_abs, max_abs = self.abs_wavelength_range
    if not (min_abs <= wavelength <= max_abs):
      raise ValueError(f"{self.__class__.__name__}: wavelength must be within {min_abs}-{max_abs}")

    await self.set_plate(plate)

    wavelength_str = str(wavelength).zfill(4)
    all_data: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]

    for min_row, min_col, max_row, max_col in self._get_min_max_row_col_tuples(wells, plate):
      cmd = f"004701{min_row + 1:02}{min_col + 1:02}{max_row + 1:02}{max_col + 1:02}000120010000110010000010600008{wavelength_str}1"
      checksum = str(sum(cmd.encode()) % 100).zfill(2)
      cmd = cmd + checksum + "\x03"
      await self.send_command("D", cmd)

      resp = await self.send_command("O")
      assert resp == b"\x060000\x03"

      # read data
      body = await self._read_until(b"\x03", timeout=60 * 3)
      assert body is not None
      parsed_data = self._parse_body(body)
      # Merge data
      for r in range(plate.num_items_y):
        for c in range(plate.num_items_x):
          if parsed_data[r][c] is not None:
            all_data[r][c] = parsed_data[r][c]

    # Get current temperature
    try:
      temp = await self.get_current_temperature()
    except TimeoutError:
      temp = float("nan")

    return [
      {
        "wavelength": wavelength,
        "data": all_data,
        "temperature": temp,
        "time": time.time(),
      }
    ]

  async def read_luminescence(
    self, plate: Plate, wells: List[Well], focal_height: float, integration_time: float = 1
  ) -> List[Dict]:
    min_fh, max_fh = self.focal_height_range
    if not (min_fh <= focal_height <= max_fh):
      raise ValueError(f"{self.__class__.__name__}: focal height must be within {min_fh}-{max_fh}")

    await self.set_plate(plate)

    cmd = f"3{14220 + int(1000 * focal_height)}\x03"
    await self.send_command("t", cmd)

    integration_time_seconds = int(integration_time)
    assert 0 <= integration_time_seconds <= 60, "Integration time seconds must be between 0 and 60"
    integration_time_milliseconds = integration_time - int(integration_time)
    # TODO: I don't know if the multiple of 0.2 is a firmware requirement, but it's what gen5.exe requires.
    # round because of floating point precision issues
    assert (
      round(integration_time_milliseconds * 10) % 2 == 0
    ), "Integration time milliseconds must be a multiple of 0.2"
    integration_time_seconds_s = str(integration_time_seconds * 5).zfill(2)
    integration_time_milliseconds_s = str(int(float(integration_time_milliseconds * 50))).zfill(2)

    all_data: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for min_row, min_col, max_row, max_col in self._get_min_max_row_col_tuples(wells, plate):
      cmd = f"008401{min_row + 1:02}{min_col + 1:02}{max_row + 1:02}{max_col + 1:02}000120010000110010000012300{integration_time_seconds_s}{integration_time_milliseconds_s}200200-001000-003000000000000000000013510"  # 0812
      checksum = str((sum(cmd.encode()) + 8) % 100).zfill(2)  # don't know why +8
      cmd = cmd + checksum
      await self.send_command("D", cmd)

      resp = await self.send_command("O")
      assert resp == b"\x060000\x03"

      # 2m10s of reading per 1 second of integration time
      # allow 60 seconds flat
      timeout = 60 + integration_time_seconds * (2 * 60 + 10)
      body = await self._read_until(b"\x03", timeout=timeout)
      assert body is not None
      parsed_data = self._parse_body(body)
      # Merge data
      for r in range(plate.num_items_y):
        for c in range(plate.num_items_x):
          if parsed_data[r][c] is not None:
            all_data[r][c] = parsed_data[r][c]

    # Get current temperature
    try:
      temp = await self.get_current_temperature()
    except TimeoutError:
      temp = float("nan")

    return [
      {
        "data": all_data,
        "temperature": temp,
        "time": time.time(),
      }
    ]

  async def read_fluorescence(
    self,
    plate: Plate,
    wells: List[Well],
    excitation_wavelength: int,
    emission_wavelength: int,
    focal_height: float,
  ) -> List[Dict]:
    min_fh, max_fh = self.focal_height_range
    if not (min_fh <= focal_height <= max_fh):
      raise ValueError(f"{self.__class__.__name__}: focal height must be within {min_fh}-{max_fh}")

    min_ex, max_ex = self.excitation_range
    if not (min_ex <= excitation_wavelength <= max_ex):
      raise ValueError(
        f"{self.__class__.__name__}: excitation wavelength must be {min_ex}-{max_ex}"
      )

    min_em, max_em = self.emission_range
    if not (min_em <= emission_wavelength <= max_em):
      raise ValueError(f"{self.__class__.__name__}: emission wavelength must be {min_em}-{max_em}")

    await self.set_plate(plate)

    cmd = f"{614220 + int(1000 * focal_height)}\x03"
    await self.send_command("t", cmd)

    excitation_wavelength_str = str(excitation_wavelength).zfill(4)
    emission_wavelength_str = str(emission_wavelength).zfill(4)

    all_data: List[List[Optional[float]]] = [
      [None for _ in range(plate.num_items_x)] for _ in range(plate.num_items_y)
    ]
    for min_row, min_col, max_row, max_col in self._get_min_max_row_col_tuples(wells, plate):
      cmd = (
        f"008401{min_row + 1:02}{min_col + 1:02}{max_row + 1:02}{max_col + 1:02}0001200100001100100000135000100200200{excitation_wavelength_str}000"
        f"{emission_wavelength_str}000000000000000000210011"
      )
      checksum = str((sum(cmd.encode()) + 7) % 100).zfill(2)  # don't know why +7
      cmd = cmd + checksum + "\x03"
      await self.send_command("D", cmd)

      resp = await self.send_command("O")
      assert resp == b"\x060000\x03"

      body = await self._read_until(b"\x03", timeout=60 * 2)
      assert body is not None
      parsed_data = self._parse_body(body)
      # Merge data
      for r in range(plate.num_items_y):
        for c in range(plate.num_items_x):
          if parsed_data[r][c] is not None:
            all_data[r][c] = parsed_data[r][c]

    # Get current temperature
    try:
      temp = await self.get_current_temperature()
    except TimeoutError:
      temp = float("nan")

    return [
      {
        "ex_wavelength": excitation_wavelength,
        "em_wavelength": emission_wavelength,
        "data": all_data,
        "temperature": temp,
        "time": time.time(),
      }
    ]

  async def _abort(self) -> None:
    await self.send_command("x", wait_for_response=False)

  class ShakeType(enum.IntEnum):
    LINEAR = 0
    ORBITAL = 1

  async def shake(self, shake_type: ShakeType, frequency: int) -> None:
    """Warning: the duration for shaking has to be specified on the machine, and the maximum is
    16 minutes. As a hack, we start shaking for the maximum duration every time as long as stop
    is not called. I think the machine might open the door at the end of the 16 minutes and then
    move it back in. We have to find a way to shake continuously, which is possible in protocol-mode
    with kinetics.

    Args:
      frequency: speed, in mm. 360 CPM = 6mm; 410 CPM = 5mm; 493 CPM = 4mm; 567 CPM = 3mm; 731 CPM = 2mm; 1096 CPM = 1mm
    """

    max_duration = 16 * 60  # 16 minutes
    self._shaking_started = asyncio.Event()

    async def shake_maximal_duration():
      """This method will start the shaking, but returns immediately after
      shaking has started."""
      shake_type_bit = str(shake_type.value)
      duration = str(max_duration).zfill(3)
      assert 1 <= frequency <= 6, "Frequency must be between 1 and 6"
      cmd = f"0033010101010100002000000013{duration}{shake_type_bit}{frequency}01"
      checksum = str((sum(cmd.encode()) + 73) % 100).zfill(2)  # don't know why +73
      cmd = cmd + checksum + "\x03"
      await self.send_command("D", cmd)

      resp = await self.send_command("O")
      assert resp == b"\x060000\x03"

      if not self._shaking_started.is_set():
        self._shaking_started.set()

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

    await self._shaking_started.wait()

  async def stop_shaking(self) -> None:
    if self._shaking:
      await self._abort()
      self._shaking = False
    if self._shaking_task is not None:
      self._shaking_task.cancel()
      try:
        await self._shaking_task
      except asyncio.CancelledError:
        # Task cancellation is expected here; safe to ignore this exception.
        pass
      self._shaking_task = None
