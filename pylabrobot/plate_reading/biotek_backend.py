import asyncio
import enum
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, List, Literal, Optional, Tuple, Union

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
from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  Objective,
)

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


async def _golden_ratio_search(
  func: Callable[..., Coroutine[Any, Any, float]], a: float, b: float, tol: float, timeout: float
):
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


@dataclass
class Cytation5ImagingConfig:
  camera_serial_number: Optional[str] = None
  max_image_read_attempts: int = 8


class Cytation5Backend(ImageReaderBackend):
  """Backend for biotek cytation 5 image reader.

  The camera is interfaced using the Spinnaker SDK, and the camera used during development is the
  Point Grey Research Inc. Blackfly BFLY-U3-23S6M. This uses a Sony IMX249 sensor.
  """

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
    imaging_config: Optional[Cytation5ImagingConfig] = None,
  ) -> None:
    super().__init__()
    self.timeout = timeout

    self.io = FTDI(device_id=device_id)

    self.spinnaker_system: Optional["PySpin.SystemPtr"] = None
    self.cam: Optional["PySpin.CameraPtr"] = None
    self.imaging_config = imaging_config or Cytation5ImagingConfig()
    self._filters: List[Optional[ImagingMode]] = []
    self._objectives: List[Optional[Objective]] = []

    self._plate: Optional[Plate] = None
    self._exposure: Optional[Exposure] = None
    self._focal_height: Optional[FocalPosition] = None
    self._gain: Optional[Gain] = None
    self._imaging_mode: Optional["ImagingMode"] = None
    self._row: Optional[int] = None
    self._column: Optional[int] = None
    self._auto_focus_search_range: Optional[Tuple[float, float]] = None
    self._shaking = False
    self._pos_x, self._pos_y = 0.0, 0.0
    self._objective: Optional[Objective] = None

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
      if self.imaging_config is None:
        raise RuntimeError("Imaging configuration is not set.")

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

        if (
          self.imaging_config.camera_serial_number is not None
          and serial_number == self.imaging_config.camera_serial_number
        ):
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

      # -- Load filter information --
      for spot in range(1, 5):
        configuration = await self.send_command("i", f"q{spot}")
        assert configuration is not None
        # TODO: what happens when the filter is not set?
        cytation_code = int(configuration.decode().strip().split(" ")[0])
        cytation_code2imaging_mode = {
          1225121: ImagingMode.C377_647,
          1225123: ImagingMode.C400_647,
          1225113: ImagingMode.C469_593,
          1225109: ImagingMode.ACRIDINE_ORANGE,
          1225107: ImagingMode.CFP,
          1225118: ImagingMode.CFP_FRET_V2,
          1225110: ImagingMode.CFP_YFP_FRET,
          1225119: ImagingMode.CFP_YFP_FRET_V2,
          1225112: ImagingMode.CHLOROPHYLL_A,
          1225105: ImagingMode.CY5,
          1225114: ImagingMode.CY5_5,
          1225106: ImagingMode.CY7,
          1225100: ImagingMode.DAPI,
          1225101: ImagingMode.GFP,
          1225116: ImagingMode.GFP_CY5,
          1225122: ImagingMode.OXIDIZED_ROGFP2,
          1225111: ImagingMode.PROPOIDIUM_IODIDE,
          1225103: ImagingMode.RFP,
          1225117: ImagingMode.RFP_CY5,
          1225115: ImagingMode.TAG_BFP,
          1225102: ImagingMode.TEXAS_RED,
          1225104: ImagingMode.YFP,
        }
        if cytation_code not in cytation_code2imaging_mode:
          self._filters.append(None)
        else:
          self._filters.append(cytation_code2imaging_mode[cytation_code])

      # -- Load objective information --
      for spot in range(1, 7):
        # +1 for some reason, eg first is h2
        configuration = await self.send_command("i", f"h{spot + 1}")
        assert configuration is not None
        if configuration.startswith(b"****"):
          self._objectives.append(None)
        else:
          annulus_part_number = int(configuration.decode("latin").strip().split(" ")[0])
          annulus_part_number2objective = {
            1320520: Objective.O_4x_PL_FL_PHASE,
            1320521: Objective.O_20x_PL_FL_PHASE,
            1322026: Objective.O_40x_PL_FL_PHASE,
          }
          self._objectives.append(annulus_part_number2objective[annulus_part_number])

  async def stop(self) -> None:
    logger.info("[cytation5] stopping")
    await self.stop_shaking()
    await self.io.stop()

    if hasattr(self, "cam") and self.cam is not None:
      self.cam.DeInit()
      del self.cam
    if hasattr(self, "spinnaker_system") and self.spinnaker_system is not None:
      self.spinnaker_system.ReleaseInstance()

    self._objectives = []
    self._filters = []

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

  async def _set_slow_mode(self, slow: bool):
    await self.send_command("&", "S1" if slow else "S0")

  async def open(self, slow: bool = False):
    await self._set_slow_mode(slow)
    return await self.send_command("J")

  async def close(self, plate: Optional[Plate], slow: bool = False):
    await self._set_slow_mode(slow)
    if plate is not None:
      await self.set_plate(plate)
    self._row, self._column = None, None
    return await self.send_command("A")

  async def home(self):
    return await self.send_command("i", "x")

  async def get_current_temperature(self) -> float:
    """Get current temperature in degrees Celsius."""
    resp = await self.send_command("h")
    assert resp is not None
    return int(resp[1:-1]) / 100000

  async def set_temperature(self, temperature: float):
    """Set temperature in degrees Celsius."""
    return await self.send_command("g", f"{int(temperature * 1000):05}")

  async def stop_heating_or_cooling(self):
    return await self.send_command("g", "00000")

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
    if plate.lid is not None:
      plate_size_z += plate.lid.get_size_z() - plate.lid.nesting_z_height

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

    resp = await self.send_command("y", cmd, timeout=1)
    self._plate = plate
    return resp

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

  async def shake(self, shake_type: ShakeType, frequency: int) -> None:
    """Warning: the duration for shaking has to be specified on the machine, and the maximum is
    16 minutes. As a hack, we start shaking for the maximum duration every time as long as stop
    is not called. I think the machine might open the door at the end of the 16 minutes and then
    move it back in. We have to find a way to shake continuously, which is possible in protocol-mode
    with kinetics.

    Args:
      frequency: speed, in mm
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
      checksum = str((sum(cmd.encode()) + 73) % 100)  # don't know why +73
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
    imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
    await self.send_command("i", f"L0{imaging_mode_code}{intensity_str}")

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

    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")
    imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
    await self.send_command("i", f"F{imaging_mode_code}0{focus_str}")

    self._focal_height = focal_position

  async def set_position(self, x: float, y: float):
    """
    Args:
      x: in mm from the center of the selected well
      y: in mm from the center of the selected well
    """
    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")

    # firmware is in (10/0.984 (10/0.984))um units. plr is mm. To convert
    x_str, y_str = (
      str(round(x * 100 * 0.984)).zfill(6),
      str(round(y * 100 * 0.984)).zfill(6),
    )

    if self._row is None or self._column is None:
      raise ValueError("Row and column not set. Run select() first.")
    row_str, column_str = str(self._row).zfill(2), str(self._column).zfill(2)

    if self._objective is None:
      raise ValueError("Objective not set. Run set_objective() first.")
    objective_code = self._objective_code(self._objective)
    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")
    imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
    await self.send_command(
      "Y", f"Z{objective_code}{imaging_mode_code}6{row_str}{column_str}{y_str}{x_str}"
    )

    relative_x, relative_y = x - self._pos_x, y - self._pos_y
    if relative_x != 0:
      relative_x_str = str(round(relative_x * 100 * 0.984)).zfill(6)
      await self.send_command("Y", f"O00{relative_x_str}")
    if relative_y != 0:
      relative_y_str = str(round(relative_y * 100 * 0.984)).zfill(6)
      await self.send_command("Y", f"O01{relative_y_str}")

    self._pos_x, self._pos_y = x, y

  def set_auto_focus_search_range(self, min_focal_height: float, max_focal_height: float):
    self._auto_focus_search_range = (min_focal_height, max_focal_height)

  async def auto_focus(self, timeout: float = 30):
    """Set auto focus search range with set_auto_focus_search_range()."""

    plate = self._plate
    if plate is None:
      raise RuntimeError("Plate not set. Run set_plate() first.")
    imaging_mode = self._imaging_mode
    if imaging_mode is None:
      raise RuntimeError("Imaging mode not set. Run set_imaging_mode() first.")
    objective = self._objective
    if objective is None:
      raise RuntimeError("Objective not set. Run set_objective() first.")
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

    # objective function: variance of laplacian
    async def evaluate_focus(focus_value):
      images = await self.capture(  # TODO: _acquire_image
        plate=plate,
        row=row,
        column=column,
        mode=imaging_mode,
        objective=objective,
        focal_height=focus_value,
        exposure_time=exposure,
        gain=gain,
      )
      image = images[0]  # self.capture returns List now
      laplacian = _laplacian_2d(np.asarray(image))
      return np.var(laplacian)

    # Use golden ratio search to find the best focus value
    focus_min, focus_max = self._auto_focus_search_range or (1.8, 2.5)
    best_focal_height = await _golden_ratio_search(
      func=evaluate_focus,
      a=focus_min,
      b=focus_max,
      tol=0.01,
      timeout=timeout,
    )
    self._focal_height = best_focal_height
    return best_focal_height

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
    row_str, column_str = str(row).zfill(2), str(column).zfill(2)
    await self.send_command("Y", f"W6{row_str}{column_str}")
    self._row, self._column = row, column
    self._pos_x, self._pos_y = 0, 0
    await self.set_position(0, 0)

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

  def _imaging_mode_code(self, mode: ImagingMode) -> int:
    if mode == ImagingMode.BRIGHTFIELD or mode == ImagingMode.PHASE_CONTRAST:
      return 5
    return self._filters.index(mode) + 1

  def _objective_code(self, objective: Objective) -> int:
    return self._objectives.index(objective) + 1

  async def set_objective(self, objective: Objective):
    if objective == self._objective:
      logger.debug("Objective is already set to %s", objective)
      return

    if self.imaging_config is None:
      raise RuntimeError("Need to set imaging_config first")

    objective_code = self._objective_code(objective)
    await self.send_command("Y", f"P0e{objective_code:02}", timeout=60)

    self._objective = objective

  async def set_imaging_mode(self, mode: ImagingMode, led_intensity: int):
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

    if self.imaging_config is None:
      raise RuntimeError("Need to set imaging_config first")

    filter_index = self._imaging_mode_code(mode)

    if mode == ImagingMode.PHASE_CONTRAST:
      await self.send_command("Y", "P1120")
      await self.send_command("Y", "P0d05")
      await self.send_command("Y", "P1002")
    elif mode == ImagingMode.BRIGHTFIELD:
      await self.send_command("Y", "P1101")
      await self.send_command("Y", "P0d05")
      await self.send_command("Y", "P1002")
    else:
      await self.send_command("Y", "P1101")
      await self.send_command("Y", f"P0d{filter_index+1:02}")
      await self.send_command("Y", "P1001")

    # Turn led on in the new mode
    self._imaging_mode = mode
    await self.led_on(intensity=led_intensity)

  async def _acquire_image(
    self,
    color_processing_algorithm: int = SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR,
    pixel_format: int = PixelFormat_Mono8,
  ) -> Image:
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

    assert self.imaging_config is not None, "Need to set imaging_config first"

    self.cam.BeginAcquisition()
    try:
      num_tries = 0
      while num_tries < self.imaging_config.max_image_read_attempts:
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
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
    led_intensity: int = 10,
    coverage: Union[Literal["full"], Tuple[int, int]] = (1, 1),
    center_position: Optional[Tuple[float, float]] = None,
    overlap: Optional[float] = None,
    color_processing_algorithm: int = SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR,
    pixel_format: int = PixelFormat_Mono8,
  ) -> List[Image]:
    """Capture image using the microscope

    speed: 211 ms ± 331 μs per loop (mean ± std. dev. of 7 runs, 10 loops each)

    Args:
      exposure_time: exposure time in ms, or `"auto"`
      focal_height: focal height in mm, or `"auto"`
      coverage: coverage of the well, either `"full"` or a tuple of `(num_rows, num_columns)`.
        Around `center_position`.
      center_position: center position of the well, in mm from the center of the selected well. If
        `None`, the center of the selected well is used (eg (0, 0) offset). If `coverage` is
        specified, this is the center of the coverage area.
      color_processing_algorithm: color processing algorithm. See
        PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_*
      pixel_format: pixel format. See PySpin.PixelFormat_*
    """

    assert overlap is None, "not implemented yet"

    if self.cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    await self.set_plate(plate)
    await self.set_objective(objective)
    await self.set_imaging_mode(mode, led_intensity=led_intensity)
    await self.select(row, column)
    await self.set_exposure(exposure_time)
    await self.set_gain(gain)
    await self.set_focus(focal_height)

    def image_size(magnification: int) -> Tuple[float, float]:
      # "wide fov" is an option in gen5.exe, but in reality it takes the same pictures. So we just
      # simply take the wide fov option.
      # um to mm (plr unit)
      if magnification == 4:
        return (3474 / 1000, 3474 / 1000)
      if magnification == 20:
        return (694 / 1000, 694 / 1000)
      if magnification == 40:
        return (347 / 1000, 347 / 1000)
      raise ValueError("Invalid magnification")

    if self._objective is None:
      raise RuntimeError("Objective not set. Run set_objective() first.")
    magnification = self._objective.magnification
    img_width, img_height = image_size(magnification)

    first_well = plate.get_item(0)
    well_size_x, well_size_y = (first_well.get_size_x(), first_well.get_size_y())
    if coverage == "full":
      coverage = (
        math.ceil(well_size_x / image_size(magnification)[0]),
        math.ceil(well_size_y / image_size(magnification)[1]),
      )
    rows, cols = coverage

    # Get positions, centered around enter_position
    if center_position is None:
      center_position = (0, 0)
    # Going in a snake pattern is not faster (strangely)
    positions = [
      (x * img_width + center_position[0], -y * img_height + center_position[1])
      for y in [i - (rows - 1) / 2 for i in range(rows)]
      for x in [i - (cols - 1) / 2 for i in range(cols)]
    ]

    images: List[Image] = []
    for x_pos, y_pos in positions:
      await self.set_position(x=x_pos, y=y_pos)
      images.append(
        await self._acquire_image(
          color_processing_algorithm=color_processing_algorithm, pixel_format=pixel_format
        )
      )

    return images
