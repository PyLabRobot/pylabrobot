import asyncio
import enum
import logging
import time
from typing import List, Literal, Optional

from pylabrobot.resources.plate import Plate

try:
  import numpy as np  # type: ignore

  USE_NUMPY = True
except ImportError:
  USE_NUMPY = False

try:
  import PySpin  # type: ignore

  # can be downloaded from https://www.teledynevisionsolutions.com/products/spinnaker-sdk/
  USE_PYSPIN = True
except ImportError:
  USE_PYSPIN = False

from pylabrobot.io.ftdi import FTDI
from pylabrobot.plate_reading.backend import ImageReaderBackend
from pylabrobot.plate_reading.standard import Exposure, FocalPosition, Gain, ImagingMode

logger = logging.getLogger("pylabrobot.plate_reading.biotek")


SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR = (
  PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR if USE_PYSPIN else -1
)
PixelFormat_Mono8 = PySpin.PixelFormat_Mono8 if USE_PYSPIN else -1
SpinnakerException = PySpin.SpinnakerException if USE_PYSPIN else Exception


def _laplacian_2d(u):
  # thanks chat (one shotted this)
  # verified to be the same as scipy.ndimage.laplace
  # 6.09 ms ± 40.5 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)

  # Assumes u is a 2D numpy array and that dx = dy = 1
  laplacian = np.zeros_like(u)

  # Applying the finite difference approximation for interior points
  laplacian[1:-1, 1:-1] = (u[2:, 1:-1] - 2 * u[1:-1, 1:-1] + u[:-2, 1:-1]) + (
    u[1:-1, 2:] - 2 * u[1:-1, 1:-1] + u[1:-1, :-2]
  )

  # Handle the edges using reflection
  laplacian[0, 1:-1] = (u[1, 1:-1] - 2 * u[0, 1:-1] + u[0, 1:-1]) + (
    u[0, 2:] - 2 * u[0, 1:-1] + u[0, :-2]
  )

  laplacian[-1, 1:-1] = (u[-2, 1:-1] - 2 * u[-1, 1:-1] + u[-1, 1:-1]) + (
    u[-1, 2:] - 2 * u[-1, 1:-1] + u[-1, :-2]
  )

  laplacian[1:-1, 0] = (u[2:, 0] - 2 * u[1:-1, 0] + u[:-2, 0]) + (
    u[1:-1, 1] - 2 * u[1:-1, 0] + u[1:-1, 0]
  )

  laplacian[1:-1, -1] = (u[2:, -1] - 2 * u[1:-1, -1] + u[:-2, -1]) + (
    u[1:-1, -2] - 2 * u[1:-1, -1] + u[1:-1, -1]
  )

  # Handle the corners (reflection)
  laplacian[0, 0] = (u[1, 0] - 2 * u[0, 0] + u[0, 0]) + (u[0, 1] - 2 * u[0, 0] + u[0, 0])
  laplacian[0, -1] = (u[1, -1] - 2 * u[0, -1] + u[0, -1]) + (u[0, -2] - 2 * u[0, -1] + u[0, -1])
  laplacian[-1, 0] = (u[-2, 0] - 2 * u[-1, 0] + u[-1, 0]) + (u[-1, 1] - 2 * u[-1, 0] + u[-1, 0])
  laplacian[-1, -1] = (u[-2, -1] - 2 * u[-1, -1] + u[-1, -1]) + (
    u[-1, -2] - 2 * u[-1, -1] + u[-1, -1]
  )

  return laplacian


async def _golden_ratio_search(func, a, b, tol, timeout):
  """Golden ratio search to maximize a unimodal function `func` over the interval [a, b]."""
  # thanks chat
  phi = (1 + np.sqrt(5)) / 2  # Golden ratio

  c = b - (b - a) / phi
  d = a + (b - a) / phi

  t0 = time.time()
  iteration = 0
  while abs(b - a) > tol:
    if (await func(c)) > (await func(d)):
      b = d
    else:
      a = c
    c = b - (b - a) / phi
    d = a + (b - a) / phi
    if time.time() - t0 > timeout:
      raise TimeoutError("Timeout while searching for optimal focus position")
    iteration += 1
    logger.debug("Golden ratio search (autofocus) iteration %d, a=%s, b=%s", iteration, a, b)

  return (b + a) / 2


class Cytation5Backend(ImageReaderBackend):
  """Backend for biotek cytation 5 image reader.

  For imaging, the filter used during development is Olympus 4X PL FL Phase, and the magnification
  is 4X, numerical aperture is 0.13. The camera is interfaced using the Spinnaker SDK, and the
  camera used during development is the Point Grey Research Inc. Blackfly BFLY-U3-23S6M.
  """

  def __init__(
    self,
    timeout: float = 20,
    camera_serial_number: Optional[float] = None,
    device_id: Optional[str] = None,
  ) -> None:
    super().__init__()
    self.timeout = timeout

    self.io = FTDI(device_id=device_id)

    self.spinnaker_system: Optional["PySpin.SystemPtr"] = None
    self.cam: Optional["PySpin.CameraPtr"] = None
    self.camera_serial_number = camera_serial_number
    self.max_image_read_attempts = 8

    self._plate: Optional[Plate] = None
    self._exposure: Optional[Exposure] = None
    self._focal_height: Optional[FocalPosition] = None
    self._gain: Optional[Gain] = None
    self._imaging_mode: Optional["ImagingMode"] = None
    self._row: Optional[int] = None
    self._column: Optional[int] = None
    self._shaking = False

  async def setup(self, use_cam: bool = False) -> None:
    logger.info("[cytation5] setting up")

    await self.io.setup()
    self.io.usb_reset()
    self.io.set_latency_timer(16)
    self.io.set_baudrate(9600)  # 0x38 0x41
    self.io.set_line_property(8, 2, 0)  # 8 bits, 2 stop bits, no parity
    SIO_RTS_CTS_HS = 0x1 << 8
    self.io.set_flowctrl(SIO_RTS_CTS_HS)
    self.io.set_rts(True)

    self._shaking = False
    self._shaking_task: Optional[asyncio.Task] = None

    if use_cam:
      if not USE_PYSPIN:
        raise RuntimeError("PySpin is not installed. Please follow the imaging setup instructions.")

      logger.debug("[cytation5] setting up camera")

      # -- Retrieve singleton reference to system object (Spinnaker) --
      self.spinnaker_system = PySpin.System.GetInstance()
      version = self.spinnaker_system.GetLibraryVersion()
      logger.debug(
        "[cytation5] Library version: %d.%d.%d.%d",
        version.major,
        version.minor,
        version.type,
        version.build,
      )

      # -- Get the camera by serial number, or the first. --
      cam_list = self.spinnaker_system.GetCameras()
      num_cameras = cam_list.GetSize()
      logger.debug("[cytation5] number of cameras detected: %d", num_cameras)

      for cam in cam_list:
        info = self._get_device_info(cam)
        serial_number = info["DeviceSerialNumber"]
        logger.debug("[cytation5] camera detected: %s", serial_number)

        if self.camera_serial_number is not None and serial_number == self.camera_serial_number:
          self.cam = cam
          logger.info("[cytation5] using camera with serial number %s", serial_number)
          break
      else:  # if no specific camera was found by serial number so use the first one
        if num_cameras > 0:
          self.cam = cam_list.GetByIndex(0)
          logger.info(
            "[cytation5] using first camera with serial number %s", info["DeviceSerialNumber"]
          )
      cam_list.Clear()

      if self.cam is None:
        raise RuntimeError(
          "No camera found. Make sure the camera is connected and the serial " "number is correct."
        )

      # -- Initialize camera --
      self.cam.Init()
      nodemap = self.cam.GetNodeMap()

      # -- Configure trigger to be software --
      # This is needed for longer exposure times (otherwise 23ms is the maximum)
      # 1. Set trigger selector to frame start
      ptr_trigger_selector = PySpin.CEnumerationPtr(nodemap.GetNode("TriggerSelector"))
      if not PySpin.IsReadable(ptr_trigger_selector) or not PySpin.IsWritable(ptr_trigger_selector):
        raise RuntimeError(
          "unable to configure TriggerSelector " "(can't read or write TriggerSelector)"
        )
      ptr_frame_start = PySpin.CEnumEntryPtr(ptr_trigger_selector.GetEntryByName("FrameStart"))
      if not PySpin.IsReadable(ptr_frame_start):
        raise RuntimeError("unable to configure TriggerSelector (can't read FrameStart)")
      ptr_trigger_selector.SetIntValue(int(ptr_frame_start.GetNumericValue()))

      # 2. Set trigger source to software
      ptr_trigger_source = PySpin.CEnumerationPtr(nodemap.GetNode("TriggerSource"))
      if not PySpin.IsReadable(ptr_trigger_source) or not PySpin.IsWritable(ptr_trigger_source):
        raise RuntimeError("unable to configure TriggerSource (can't read or write TriggerSource)")
      ptr_inference_ready = PySpin.CEnumEntryPtr(ptr_trigger_source.GetEntryByName("Software"))
      if not PySpin.IsReadable(ptr_inference_ready):
        raise RuntimeError("unable to configure TriggerSource (can't read Software)")
      ptr_trigger_source.SetIntValue(int(ptr_inference_ready.GetNumericValue()))

      # 3. Set trigger mode to on
      ptr_trigger_mode = PySpin.CEnumerationPtr(nodemap.GetNode("TriggerMode"))
      if not PySpin.IsReadable(ptr_trigger_mode) or not PySpin.IsWritable(ptr_trigger_mode):
        raise RuntimeError("unable to configure TriggerMode (can't read or write TriggerMode)")
      ptr_trigger_on = PySpin.CEnumEntryPtr(ptr_trigger_mode.GetEntryByName("On"))
      if not PySpin.IsReadable(ptr_trigger_on):
        raise RuntimeError("unable to query TriggerMode On")
      ptr_trigger_mode.SetIntValue(int(ptr_trigger_on.GetNumericValue()))

  async def stop(self) -> None:
    logger.info("[cytation5] stopping")
    await self.stop_shaking()
    await self.io.stop()

    if hasattr(self, "cam") and self.cam is not None:
      self.cam.DeInit()
      del self.cam
    if hasattr(self, "spinnaker_system") and self.spinnaker_system is not None:
      self.spinnaker_system.ReleaseInstance()

  async def _purge_buffers(self) -> None:
    """Purge the RX and TX buffers, as implemented in Gen5.exe"""
    for _ in range(6):
      self.io.usb_purge_rx_buffer()
    self.io.usb_purge_tx_buffer()

  async def _read_until(self, char: bytes, timeout: Optional[float] = None) -> bytes:
    """If timeout is None, use self.timeout"""
    if timeout is None:
      timeout = self.timeout
    x = None
    res = b""
    t0 = time.time()
    while x != char:
      x = self.io.read(1)
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
    command: str,
    parameter: Optional[str] = None,
    wait_for_response=True,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    await self._purge_buffers()
    self.io.write(command.encode())
    logger.debug("[cytation5] sent %s", command)
    response: Optional[bytes] = None
    if wait_for_response or parameter is not None:
      response = await self._read_until(
        b"\x06" if parameter is not None else b"\x03", timeout=timeout
      )

    if parameter is not None:
      self.io.write(parameter.encode())
      logger.debug("[cytation5] sent %s", parameter)
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
    return " ".join(resp[1:-1].decode().split(" ")[0:4])

  async def open(self):
    return await self.send_command("J")

  async def close(self):
    return await self.send_command("A")

  async def get_current_temperature(self) -> float:
    """Get current temperature in degrees Celsius."""
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
      grouped_values = [values[i : i + 3] for i in range(0, len(values), 3)]

      for group in grouped_values:
        assert len(group) == 3
        value = float(group[2].decode())
        parsed_data[row_idx].append(value)
    return parsed_data

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

    top_left_well_center_y = plate.get_size_y() - top_left_well_center.y  # invert y axis
    bottom_right_well_center_y = plate.get_size_y() - bottom_right_well_center.y  # invert y axis

    cmd = (
      f"{rows:02}"
      f"{columns:02}"
      f"{int(top_left_well_center_y*100):05}"
      f"{int(bottom_right_well_center_y*100):05}"
      f"{int(top_left_well_center.x*100):05}"
      f"{int(bottom_right_well_center.x*100):05}"
      f"{int(plate_size_y*100):05}"
      f"{int(plate_size_x*100):05}"
      f"{int(plate_size_z*100):04}"
      "\x03"
    )

    return await self.send_command("y", cmd)

  async def read_absorbance(self, plate: Plate, wavelength: int) -> List[List[float]]:
    if not 230 <= wavelength <= 999:
      raise ValueError("Wavelength must be between 230 and 999")

    await self.set_plate(plate)

    wavelength_str = str(wavelength).zfill(4)
    cmd = f"00470101010812000120010000110010000010600008{wavelength_str}1"
    checksum = str(sum(cmd.encode()) % 100)
    cmd = cmd + checksum + "\x03"
    await self.send_command("D", cmd)

    resp = await self.send_command("O")
    assert resp == b"\x060000\x03"

    # read data
    body = await self._read_until(b"\x03", timeout=60 * 3)
    assert resp is not None
    return self._parse_body(body)

  async def read_luminescence(self, plate: Plate, focal_height: float) -> List[List[float]]:
    if not 4.5 <= focal_height <= 13.88:
      raise ValueError("Focal height must be between 4.5 and 13.88")

    cmd = f"3{14220 + int(1000*focal_height)}\x03"
    await self.send_command("t", cmd)

    await self.set_plate(plate)

    cmd = "008401010108120001200100001100100000123000500200200-001000-00300000000000000000001351092"
    await self.send_command("D", cmd)

    resp = await self.send_command("O")
    assert resp == b"\x060000\x03"

    body = await self._read_until(b"\x03", timeout=60 * 3)
    assert body is not None
    return self._parse_body(body)

  async def read_fluorescence(
    self,
    plate: Plate,
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

    cmd = f"{614220 + int(1000*focal_height)}\x03"
    await self.send_command("t", cmd)

    await self.set_plate(plate)

    excitation_wavelength_str = str(excitation_wavelength).zfill(4)
    emission_wavelength_str = str(emission_wavelength).zfill(4)
    cmd = (
      f"008401010108120001200100001100100000135000100200200{excitation_wavelength_str}000"
      f"{emission_wavelength_str}000000000000000000210011"
    )
    checksum = str((sum(cmd.encode()) + 7) % 100)  # don't know why +7
    cmd = cmd + checksum + "\x03"
    resp = await self.send_command("D", cmd)

    resp = await self.send_command("O")
    assert resp == b"\x060000\x03"

    body = await self._read_until(b"\x03", timeout=60 * 2)
    assert body is not None
    return self._parse_body(body)

  async def _abort(self) -> None:
    await self.send_command("x", wait_for_response=False)

  class ShakeType(enum.IntEnum):
    LINEAR = 0
    ORBITAL = 1

  async def shake(self, shake_type: ShakeType) -> None:
    """Warning: the duration for shaking has to be specified on the machine, and the maximum is
    16 minutes. As a hack, we start shaking for the maximum duration every time as long as stop
    is not called."""
    max_duration = 16 * 60  # 16 minutes

    async def shake_maximal_duration():
      """This method will start the shaking, but returns immediately after
      shaking has started."""
      resp = await self.send_command("y", "08120112207434014351135308559127881422\x03")

      shake_type_bit = str(shake_type.value)
      duration = str(max_duration).zfill(3)
      cmd = f"0033010101010100002000000013{duration}{shake_type_bit}301"
      checksum = str((sum(cmd.encode()) + 73) % 100)  # don't know why +73
      cmd = cmd + checksum + "\x03"
      await self.send_command("D", cmd)

      resp = await self.send_command("O")
      assert resp == b"\x060000\x03"

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

  def _get_device_info(self, cam):
    """Get device info for cameras."""
    # should have keys:
    # - DeviceID
    # - DeviceSerialNumber
    # - DeviceUserID
    # - DeviceVendorName
    # - DeviceModelName
    # - DeviceVersion
    # - DeviceBootloaderVersion
    # - DeviceType
    # - DeviceDisplayName
    # - DeviceAccessStatus
    # - DeviceDriverVersion
    # - DeviceIsUpdater
    # - DeviceInstanceId
    # - DeviceLocation
    # - DeviceCurrentSpeed
    # - DeviceU3VProtocol
    # - DevicePortId
    # - GenICamXMLLocation
    # - GenICamXMLPath
    # - GUIXMLLocation
    # - GUIXMLPath

    device_info = {}

    nodemap = cam.GetTLDeviceNodeMap()
    node_device_information = PySpin.CCategoryPtr(nodemap.GetNode("DeviceInformation"))
    if not PySpin.IsReadable(node_device_information):
      raise RuntimeError("Device control information not readable.")

    features = node_device_information.GetFeatures()
    for feature in features:
      node_feature = PySpin.CValuePtr(feature)
      node_feature_name = node_feature.GetName()
      node_feature_value = node_feature.ToString() if PySpin.IsReadable(node_feature) else None
      device_info[node_feature_name] = node_feature_value

    return device_info

  async def led_on(self, intensity: int = 10):
    if not 1 <= intensity <= 10:
      raise ValueError("intensity must be between 1 and 10")
    intensity_str = str(intensity).zfill(2)
    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")
    imaging_mode_code = {
      ImagingMode.BRIGHTFIELD: "05",
      ImagingMode.GFP: "02",
      ImagingMode.TEXAS_RED: "03",
      ImagingMode.PHASE_CONTRAST: "07",
    }[self._imaging_mode]
    await self.send_command("i", f"L{imaging_mode_code}{intensity_str}")

  async def led_off(self):
    await self.send_command("i", "L0001")

  async def set_focus(self, focal_position: FocalPosition):
    """focus position in mm"""

    if focal_position == "auto":
      await self.auto_focus()
      return

    if focal_position == self._focal_height:
      logger.debug("Focus position is already set to %s", focal_position)
      return

    # There is a difference between the number in the program and the number sent to the machine,
    # which is modelled using the following linear relation. R^2=0.999999999
    # convert from mm to um
    slope, intercept = (10.637991436186072, 1.0243013203461762)
    focus_integer = int(focal_position + intercept + slope * focal_position * 1000)
    focus_str = str(focus_integer).zfill(5)

    # this is actually position., 101 should be 000. might also include imaging mode?
    await self.send_command("Y", "Z1560101000000000000")
    await self.send_command("i", f"F50{focus_str}")

    self._focal_height = focal_position

  async def auto_focus(self, timeout: float = 30):
    plate = self._plate
    if plate is None:
      raise RuntimeError("Plate not set. Run set_plate() first.")
    imaging_mode = self._imaging_mode
    if imaging_mode is None:
      raise RuntimeError("Imaging mode not set. Run set_imaging_mode() first.")
    exposure = self._exposure
    if exposure is None:
      raise RuntimeError("Exposure time not set. Run set_exposure() first.")
    gain = self._gain
    if gain is None:
      raise RuntimeError("Gain not set. Run set_gain() first.")
    row, column = self._row, self._column
    if row is None or column is None:
      raise RuntimeError("Row and column not set. Run select() first.")
    if not USE_NUMPY:
      # This is strange, because Spinnaker requires numpy
      raise RuntimeError("numpy is not installed. See Cytation5 installation instructions.")

    # these seem reasonable, but might need to be adjusted in the future
    focus_min = 2
    focus_max = 5

    # objective function: variance of laplacian
    async def evaluate_focus(focus_value):
      image = await self.capture(
        plate=plate,
        row=row,
        column=column,
        mode=imaging_mode,
        focal_height=focus_value,
        exposure_time=exposure,
        gain=gain,
      )
      laplacian = _laplacian_2d(np.asarray(image))
      return np.var(laplacian)

    # Use golden ratio search to find the best focus value
    best_focus_value = await _golden_ratio_search(
      func=evaluate_focus,
      a=focus_min,
      b=focus_max,
      tol=0.01,
      timeout=timeout,
    )

    return best_focus_value

  async def set_auto_exposure(self, auto_exposure: Literal["off", "once", "continuous"]):
    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if self.cam.ExposureAuto.GetAccessMode() != PySpin.RW:
      raise RuntimeError("unable to write ExposureAuto")
    self.cam.ExposureAuto.SetValue(
      {
        "off": PySpin.ExposureAuto_Off,
        "once": PySpin.ExposureAuto_Once,
        "continuous": PySpin.ExposureAuto_Continuous,
      }[auto_exposure]
    )

  async def set_exposure(self, exposure: Exposure):
    """exposure (integration time) in ms, or "auto" """

    if exposure == self._exposure:
      logger.debug("Exposure time is already set to %s", exposure)
      return

    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    # either set auto exposure to continuous, or turn off
    if isinstance(exposure, str):
      if exposure == "auto":
        await self.set_auto_exposure("continuous")
        self._exposure = "auto"
        return
      raise ValueError("exposure must be a number or 'auto'")
    self.cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)

    # set exposure time (in microseconds)
    if self.cam.ExposureTime.GetAccessMode() != PySpin.RW:
      raise RuntimeError("unable to write ExposureTime")
    exposure_us = int(exposure * 1000)
    min_et = self.cam.ExposureTime.GetMin()
    if exposure_us < min_et:
      raise ValueError(f"exposure must be >= {min_et}")
    max_et = self.cam.ExposureTime.GetMax()
    if exposure_us > max_et:
      raise ValueError(f"exposure must be <= {max_et}")
    self.cam.ExposureTime.SetValue(exposure_us)
    self._exposure = exposure

  async def select(self, row: int, column: int):
    if row == self._row and column == self._column:
      logger.debug("Already selected %s, %s", row, column)
      return
    await self.send_command("Y", "Z1260101000000000000")
    row_str, column_str = str(row).zfill(2), str(column).zfill(2)
    await self.send_command("Y", f"W6{row_str}{column_str}")
    self._row, self._column = row, column

  async def set_gain(self, gain: Gain):
    """gain of unknown units, or "auto" """
    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if gain == self._gain:
      logger.debug("Gain is already set to %s", gain)
      return

    if not (gain == "auto" or 0 <= gain <= 30):
      raise ValueError("gain must be between 0 and 30 (inclusive), or 'auto'")

    nodemap = self.cam.GetNodeMap()

    # set/disable automatic gain
    node_gain_auto = PySpin.CEnumerationPtr(nodemap.GetNode("GainAuto"))
    if not PySpin.IsReadable(node_gain_auto) or not PySpin.IsWritable(node_gain_auto):
      raise RuntimeError("unable to set automatic gain")
    node = (
      PySpin.CEnumEntryPtr(node_gain_auto.GetEntryByName("Continuous"))
      if gain == "auto"
      else PySpin.CEnumEntryPtr(node_gain_auto.GetEntryByName("Off"))
    )
    if not PySpin.IsReadable(node):
      raise RuntimeError("unable to set automatic gain (enum entry retrieval)")
    node_gain_auto.SetIntValue(node.GetValue())

    if not gain == "auto":
      node_gain = PySpin.CFloatPtr(nodemap.GetNode("Gain"))
      if (
        not PySpin.IsReadable(node_gain)
        or not PySpin.IsWritable(node_gain)
        or node_gain.GetMax() == 0
      ):
        raise RuntimeError("unable to set gain")
      min_gain = node_gain.GetMin()
      if gain < min_gain:
        raise ValueError(f"gain must be >= {min_gain}")
      max_gain = node_gain.GetMax()
      if gain > max_gain:
        raise ValueError(f"gain must be <= {max_gain}")
      node_gain.SetValue(gain)

    self._gain = gain

  async def set_imaging_mode(self, mode: ImagingMode):
    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if mode == self._imaging_mode:
      logger.debug("Imaging mode is already set to %s", mode)
      return

    if mode == ImagingMode.COLOR_BRIGHTFIELD:
      # color brightfield will quickly switch through different filters, 05, 06, 07, 08
      # it sometimes calls (i, l{4,5,6,7}) before switching to the next filter. unclear.
      raise NotImplementedError("Color brightfield imaging not implemented yet")

    await self.led_off()

    if mode == ImagingMode.PHASE_CONTRAST:
      await self.send_command("Y", "P1120")
      await self.send_command("Y", "P0d05")
      await self.send_command("Y", "P1002")
    elif mode == ImagingMode.BRIGHTFIELD:
      await self.send_command("Y", "Z1500000000000000000")
      await self.send_command("i", "F5000000")  # reset focus
      await self.send_command("i", "W000000")  # reset select
      await self.send_command("Y", "P1101")
      await self.send_command("Y", "P0d05")
      await self.send_command("Y", "P1002")
    elif mode == ImagingMode.GFP:
      await self.send_command("Y", "P1101")
      await self.send_command("Y", "P0d02")
      await self.send_command("Y", "P1001")
    elif mode == ImagingMode.TEXAS_RED:
      await self.send_command("Y", "P1101")
      await self.send_command("Y", "P0d03")
      await self.send_command("Y", "P1001")

    # Turn led on in the new mode
    self._imaging_mode = mode
    await self.led_on()

  async def _acquire_image(
    self,
    color_processing_algorithm: int = SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR,
    pixel_format: int = PixelFormat_Mono8,
  ) -> List[List[float]]:
    assert self.cam is not None
    nodemap = self.cam.GetNodeMap()

    # Start acquisition mode (continuous)
    # node_acquisition_mode = PySpin.CEnumerationPtr(nodemap.GetNode("AcquisitionMode"))
    # if not PySpin.IsReadable(node_acquisition_mode) or not \
    #   PySpin.IsWritable(node_acquisition_mode):
    #   raise RuntimeError("unable to set acquisition mode to continuous (enum retrieval)")
    # node_acquisition_mode_single_frame = node_acquisition_mode.GetEntryByName("Continuous")
    # if not PySpin.IsReadable(node_acquisition_mode_single_frame):
    #   raise RuntimeError("unable to set acquisition mode to single frame (entry retrieval)")
    # node_acquisition_mode.SetIntValue(node_acquisition_mode_single_frame.GetValue())

    self.cam.BeginAcquisition()
    try:
      num_tries = 0
      while num_tries < self.max_image_read_attempts:
        node_softwaretrigger_cmd = PySpin.CCommandPtr(nodemap.GetNode("TriggerSoftware"))
        if not PySpin.IsWritable(node_softwaretrigger_cmd):
          raise RuntimeError("unable to execute software trigger")
        node_softwaretrigger_cmd.Execute()

        try:
          image_result = self.cam.GetNextImage(1000)
          if not image_result.IsIncomplete():
            processor = PySpin.ImageProcessor()
            processor.SetColorProcessing(color_processing_algorithm)
            image_converted = processor.Convert(image_result, pixel_format)
            image_result.Release()
            return image_converted.GetNDArray().tolist()  # type: ignore
        except SpinnakerException as e:
          # the image is not ready yet, try again
          logger.debug("Failed to get image: %s", e)
        num_tries += 1
        await asyncio.sleep(0.3)
      raise TimeoutError("max_image_read_attempts reached")
    finally:
      self.cam.EndAcquisition()

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
    color_processing_algorithm: int = SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR,
    pixel_format: int = PixelFormat_Mono8,
  ) -> List[List[float]]:
    """Capture image using the microscope

    speed: 211 ms ± 331 μs per loop (mean ± std. dev. of 7 runs, 10 loops each)

    Args:
      exposure_time: exposure time in ms, or `"auto"`
      focal_height: focal height in mm, or `"auto"`
      color_processing_algorithm: color processing algorithm. See
        PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_*
      pixel_format: pixel format. See PySpin.PixelFormat_*
    """
    # Adopted from the Spinnaker SDK Acquisition example

    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    await self.set_plate(plate)

    await self.select(row, column)
    await self.set_imaging_mode(mode)
    await self.set_exposure(exposure_time)
    await self.set_gain(gain)
    await self.set_focus(focal_height)
    return await self._acquire_image(
      color_processing_algorithm=color_processing_algorithm, pixel_format=pixel_format
    )
