import asyncio
import atexit
import logging
import math
import re
import time
import warnings
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple, Union

from pylabrobot.plate_reading.agilent.biotek_backend import BioTekPlateReaderBackend
from pylabrobot.plate_reading.backend import ImagerBackend
from pylabrobot.resources import Plate

try:
  import PySpin  # type: ignore

  # can be downloaded from https://www.teledynevisionsolutions.com/products/spinnaker-sdk/
  USE_PYSPIN = True
except ImportError as e:
  USE_PYSPIN = False
  _PYSPIN_IMPORT_ERROR = e

from pylabrobot.plate_reading.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  ImagingResult,
  Objective,
)

SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR = (
  PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR if USE_PYSPIN else -1
)
PixelFormat_Mono8 = PySpin.PixelFormat_Mono8 if USE_PYSPIN else -1
SpinnakerException = PySpin.SpinnakerException if USE_PYSPIN else Exception

logger = logging.getLogger(__name__)


@dataclass
class CytationImagingConfig:
  camera_serial_number: Optional[str] = None
  max_image_read_attempts: int = 50

  # if not specified, these will be loaded from machine configuration (register with gen5.exe)
  objectives: Optional[List[Optional[Objective]]] = None
  filters: Optional[List[Optional[ImagingMode]]] = None


def retry(func, *args, **kwargs):
  """Call func with retries and logging."""
  max_tries = 10
  delay = 0.1
  tries = 0
  while True:
    try:
      return func(*args, **kwargs)
    except SpinnakerException as ex:
      tries += 1
      if tries >= max_tries:
        raise RuntimeError(f"Failed after {max_tries} tries") from ex
      logger.warning(
        "Retry %d/%d failed: %s",
        tries,
        max_tries,
        str(ex),
      )
      time.sleep(delay)


class CytationBackend(BioTekPlateReaderBackend, ImagerBackend):
  """Backend for Agilent BioTek Cytation plate readers.

  The camera is interfaced using the Spinnaker SDK, and the camera used during development is the
  Point Grey Research Inc. Blackfly BFLY-U3-23S6M. This uses a Sony IMX249 sensor.
  """

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
  ) -> None:
    super().__init__(timeout=timeout, device_id=device_id)

    self._spinnaker_system: Optional["PySpin.SystemPtr"] = None
    self._cam: Optional["PySpin.CameraPtr"] = None
    self.imaging_config = imaging_config or CytationImagingConfig()
    self._filters: Optional[List[Optional[ImagingMode]]] = self.imaging_config.filters
    self._objectives: Optional[List[Optional[Objective]]] = self.imaging_config.objectives
    self._exposure: Optional[Exposure] = None
    self._focal_height: Optional[FocalPosition] = None
    self._gain: Optional[Gain] = None
    self._imaging_mode: Optional["ImagingMode"] = None
    self._row: Optional[int] = None
    self._column: Optional[int] = None
    self._pos_x: Optional[float] = None
    self._pos_y: Optional[float] = None
    self._objective: Optional[Objective] = None
    self._acquiring = False

  async def setup(self, use_cam: bool = False) -> None:
    logger.info(f"{self.__class__.__name__} setting up")

    await super().setup()

    if use_cam:
      try:
        await self._set_up_camera()
      except:
        # if setting up the camera fails, we have to close the ftdi connection
        # so that the user can try calling setup() again.
        # if we don't close the ftdi connection here, it will be open until the
        # python kernel is restarted.
        try:
          await self.stop()
        except Exception:
          pass
        raise

  async def stop(self):
    await super().stop()

    if self._acquiring:
      self.stop_acquisition()

    logger.info(f"{self.__class__.__name__} stopping")
    await self.stop_shaking()
    await self.io.stop()

    self._stop_camera()

    self._objectives = None
    self._filters = None
    self._slow_mode = None

    self._clear_imaging_state()

  def _clear_imaging_state(self):
    self._exposure = None
    self._focal_height = None
    self._gain = None
    self._imaging_mode = None
    self._row = None
    self._column = None
    self._pos_x, self._pos_y = 0, 0
    self._objective = None

  @property
  def supports_heating(self):
    return True

  @property
  def supports_cooling(self):
    return True

  async def _set_up_camera(self) -> None:
    atexit.register(self._stop_camera)

    if not USE_PYSPIN:
      raise RuntimeError(
        "PySpin is not installed. Please follow the imaging setup instructions. "
        f"Import error: {_PYSPIN_IMPORT_ERROR}"
      )
    if self.imaging_config is None:
      raise RuntimeError("Imaging configuration is not set.")

    logger.debug(f"{self.__class__.__name__} setting up camera")

    # -- Retrieve singleton reference to system object (Spinnaker) --
    self._spinnaker_system = PySpin.System.GetInstance()
    version = self._spinnaker_system.GetLibraryVersion()
    logger.debug(
      f"{self.__class__.__name__} Library version: %d.%d.%d.%d",
      version.major,
      version.minor,
      version.type,
      version.build,
    )

    # -- Get the camera by serial number, or the first. --
    cam_list = self._spinnaker_system.GetCameras()
    num_cameras = cam_list.GetSize()
    logger.debug(f"{self.__class__.__name__} number of cameras detected: %d", num_cameras)

    for cam in cam_list:
      info = self._get_device_info(cam)
      serial_number = info["DeviceSerialNumber"]
      logger.debug(f"{self.__class__.__name__} camera detected: %s", serial_number)

      if (
        self.imaging_config.camera_serial_number is not None
        and serial_number == self.imaging_config.camera_serial_number
      ):
        self._cam = cam
        logger.info(f"{self.__class__.__name__} using camera with serial number %s", serial_number)
        break
    else:  # if no specific camera was found by serial number so use the first one
      if num_cameras > 0:
        self._cam = cam_list.GetByIndex(0)
        logger.info(
          f"{self.__class__.__name__} using first camera with serial number %s",
          info["DeviceSerialNumber"],
        )
      else:
        logger.error(f"{self.__class__.__name__}: No cameras found")
        self._cam = None
    cam_list.Clear()

    if self._cam is None:
      raise RuntimeError(
        f"{self.__class__.__name__}: No camera found. Make sure the camera is connected and the serial "
        "number is correct."
      )

    # -- Initialize camera --
    for _ in range(10):
      try:
        self._cam.Init()  # SpinnakerException: Spinnaker: Could not read the XML URL [-1010]
        break
      except:  # noqa
        await asyncio.sleep(0.1)
        pass
    else:
      raise RuntimeError(
        "Failed to initialize camera. Make sure the camera is connected and the "
        "Spinnaker SDK is installed correctly."
      )
    nodemap = self._cam.GetNodeMap()

    # -- Configure trigger to be software --
    # This is needed for longer exposure times (otherwise 27.8ms is the maximum)
    # 1. Set trigger selector to frame start
    ptr_trigger_selector = PySpin.CEnumerationPtr(nodemap.GetNode("TriggerSelector"))
    if not PySpin.IsReadable(ptr_trigger_selector) or not PySpin.IsWritable(ptr_trigger_selector):
      raise RuntimeError(
        "unable to configure TriggerSelector (can't read or write TriggerSelector)"
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

    # "NOTE: Blackfly and Flea3 GEV cameras need 1 second delay after trigger mode is turned on"
    await asyncio.sleep(1)

    # -- Load filter information --
    if self._filters is None:
      await self._load_filters()

    # -- Load objective information --
    if self._objectives is None:
      await self._load_objectives()

  @property
  def objectives(self) -> List[Optional[Objective]]:
    if self._objectives is None:
      raise RuntimeError(f"{self.__class__.__name__}: Objectives are not set")
    return self._objectives

  @property
  def filters(self) -> List[Optional[ImagingMode]]:
    if self._filters is None:
      raise RuntimeError(f"{self.__class__.__name__}: Filters are not set")
    return self._filters

  async def _load_filters(self):
    self._filters = []
    for spot in range(1, 5):
      configuration = await self.send_command("i", f"q{spot}")
      assert configuration is not None
      parts = configuration.decode().strip().split(" ")
      if len(parts) == 1:
        self._filters.append(None)
      else:
        cytation_code = int(parts[0])
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

  async def _load_objectives(self):
    self._objectives = []
    if self.version.startswith("1"):
      for spot in [1, 2]:
        configuration = await self.send_command("i", f"o{spot}")
        weird_encoding = {  # ?
          0x00: " ",
          0x14: ".",
          0x15: "/",
          0x16: "0",
          0x17: "1",
          0x18: "2",
          0x19: "3",
          0x20: "4",
          0x21: "5",
          0x22: "6",
          0x23: "7",
          0x24: "8",
          0x25: "9",
          0x33: "A",
          0x34: "B",
          0x35: "C",
          0x36: "D",
          0x37: "E",
          0x38: "F",
          0x39: "G",
          0x40: "H",
          0x41: "I",
          0x42: "J",
          0x43: "K",
          0x44: "L",
          0x45: "M",
          0x46: "N",
          0x47: "O",
          0x48: "P",
          0x49: "Q",
          0x50: "R",
          0x51: "S",
          0x52: "T",
          0x53: "U",
          0x54: "V",
          0x55: "W",
          0x56: "X",
          0x57: "Y",
          0x58: "Z",
        }
        if configuration is None:
          raise RuntimeError("Failed to load objective configuration")
        # TODO: loading when no objective is set. I believe it's four 0s.
        middle_part = re.split(r"\s+", configuration.rstrip(b"\x03").decode("utf-8"))[1]
        # not the real part number, but it's what's used in the xml files. eg "UPLFLN"
        if middle_part == "0000":
          self._objectives.append(None)
        else:
          part_number = "".join([weird_encoding[x] for x in bytes.fromhex(middle_part)])
          part_number2objective = {
            "uplsapo 40x2": Objective.O_40X_PL_APO,
            "lucplfln 60X": Objective.O_60X_PL_FL,
            "uplfln 4x": Objective.O_4X_PL_FL,
            "lucplfln 20xph": Objective.O_20X_PL_FL_Phase,
            "lucplfln 40xph": Objective.O_40X_PL_FL_Phase,
            "u plan": Objective.O_2_5X_PL_ACH_Meiji,
            "uplfln 10xph": Objective.O_10X_PL_FL_Phase,
            "plapon 1.25x": Objective.O_1_25X_PL_APO,
            "uplfln 10x": Objective.O_10X_PL_FL,
            "uplfln 60xoi": Objective.O_60X_OIL_PL_FL,
            "pln 4x": Objective.O_4X_PL_ACH,
            "pln 40x": Objective.O_40X_PL_ACH,
            "lucplfln 40x": Objective.O_40X_PL_FL,
            "ec-h-plan/2x": Objective.O_2X_PL_ACH_Motic,
            "uplfln 100xO2": Objective.O_100X_OIL_PL_FL,
            "uplfln 4xph": Objective.O_4X_PL_FL_Phase,
            "lucplfln 20X": Objective.O_20X_PL_FL,
            "pln 20x": Objective.O_20X_PL_ACH,
            "fluar 2.5x/0.12": Objective.O_2_5X_FL_Zeiss,
            "uplsapo 100xo": Objective.O_100X_OIL_PL_APO,
            "plapon 60xo": Objective.O_60X_OIL_PL_APO,
            "uplsapo 20x": Objective.O_20X_PL_APO,
          }
          self._objectives.append(part_number2objective[part_number.lower()])
    elif self.version.startswith("2"):
      for spot in range(1, 7):
        # +1 for some reason, eg first is h2
        configuration = await self.send_command("i", f"h{spot + 1}")
        assert configuration is not None
        if configuration.startswith(b"****"):
          self._objectives.append(None)
        else:
          annulus_part_number = int(configuration.decode("latin").strip().split(" ")[0])
          annulus_part_number2objective = {
            1320520: Objective.O_4X_PL_FL_Phase,
            1320521: Objective.O_20X_PL_FL_Phase,
            1322026: Objective.O_40X_PL_FL_Phase,
          }
          self._objectives.append(annulus_part_number2objective[annulus_part_number])
    else:
      raise RuntimeError(f"{self.__class__.__name__}: Unsupported version: {self.version}")

  def _stop_camera(self) -> None:
    if self._cam is not None:
      if self._acquiring:
        self.stop_acquisition()

      self._reset_trigger()

      self._cam.DeInit()
      self._cam = None
    if self._spinnaker_system is not None:
      self._spinnaker_system.ReleaseInstance()

  def _reset_trigger(self):
    if self._cam is None:
      return

    # adopted from example
    try:
      nodemap = self._cam.GetNodeMap()
      node_trigger_mode = PySpin.CEnumerationPtr(nodemap.GetNode("TriggerMode"))
      if not PySpin.IsReadable(node_trigger_mode) or not PySpin.IsWritable(node_trigger_mode):
        return

      node_trigger_mode_off = node_trigger_mode.GetEntryByName("Off")
      if not PySpin.IsReadable(node_trigger_mode_off):
        return

      node_trigger_mode.SetIntValue(node_trigger_mode_off.GetValue())
    except PySpin.SpinnakerException:
      pass

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
      try:
        node_feature_value = node_feature.ToString() if PySpin.IsReadable(node_feature) else None
      except Exception as e:
        raise RuntimeError(
          f"Got an error while reading feature {node_feature_name}. "
          "Is the cytation in use by another notebook? "
          f"Error: {str(e)}"
        ) from e
      device_info[node_feature_name] = node_feature_value

    return device_info

  async def close(self, plate: Optional[Plate], slow: bool = False):
    await super().close(plate, slow)
    self._clear_imaging_state()

  def start_acquisition(self):
    if self._cam is None:
      raise RuntimeError(f"{self.__class__.__name__}: Camera is not initialized.")
    if self._acquiring:
      return
    retry(self._cam.BeginAcquisition)
    self._acquiring = True

  def stop_acquisition(self):
    if self._cam is None:
      raise RuntimeError(f"{self.__class__.__name__}: Camera is not initialized.")
    if not self._acquiring:
      return
    retry(self._cam.EndAcquisition)
    self._acquiring = False

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

    if focal_position == "machine-auto":
      raise ValueError(
        "focal_position cannot be 'machine-auto'. Use the PLR Imager universal autofocus instead."
      )

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

    if x == self._pos_x and y == self._pos_y:
      logger.debug("Position is already set to (%s, %s)", x, y)
      return

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

    relative_x, relative_y = x - (self._pos_x or 0), y - (self._pos_y or 0)
    if relative_x != 0:
      relative_x_str = str(round(relative_x * 100 * 0.984)).zfill(6)
      await self.send_command("Y", f"O00{relative_x_str}")
    if relative_y != 0:
      relative_y_str = str(round(relative_y * 100 * 0.984)).zfill(6)
      await self.send_command("Y", f"O01{relative_y_str}")

    self._pos_x, self._pos_y = x, y
    await asyncio.sleep(0.1)

  async def set_auto_exposure(self, auto_exposure: Literal["off", "once", "continuous"]):
    if self._cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if self._cam.ExposureAuto.GetAccessMode() != PySpin.RW:
      raise RuntimeError("unable to write ExposureAuto")

    retry(
      self._cam.ExposureAuto.SetValue,
      {
        "off": PySpin.ExposureAuto_Off,
        "once": PySpin.ExposureAuto_Once,
        "continuous": PySpin.ExposureAuto_Continuous,
      }[auto_exposure],
    )

  async def set_exposure(self, exposure: Exposure):
    """exposure (integration time) in ms, or "machine-auto" """

    if exposure == self._exposure:
      logger.debug("Exposure time is already set to %s", exposure)
      return

    if self._cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    # either set auto exposure to continuous, or turn off
    if isinstance(exposure, str):
      if exposure == "machine-auto":
        await self.set_auto_exposure("continuous")
        self._exposure = "machine-auto"
        return
      raise ValueError("exposure must be a number or 'auto'")
    retry(self._cam.ExposureAuto.SetValue, PySpin.ExposureAuto_Off)

    # set exposure time (in microseconds)
    if self._cam.ExposureTime.GetAccessMode() != PySpin.RW:
      raise RuntimeError("unable to write ExposureTime")
    exposure_us = int(exposure * 1000)
    min_et = retry(self._cam.ExposureTime.GetMin)
    if exposure_us < min_et:
      raise ValueError(f"exposure must be >= {min_et}")
    max_et = retry(self._cam.ExposureTime.GetMax)
    if exposure_us > max_et:
      raise ValueError(f"exposure must be <= {max_et}")
    retry(self._cam.ExposureTime.SetValue, exposure_us)
    self._exposure = exposure

  async def select(self, row: int, column: int):
    if row == self._row and column == self._column:
      logger.debug("Already selected %s, %s", row, column)
      return
    row_str, column_str = str(row).zfill(2), str(column).zfill(2)
    await self.send_command("Y", f"W6{row_str}{column_str}")
    self._row, self._column = row, column
    self._pos_x, self._pos_y = None, None
    await self.set_position(0, 0)

  async def set_gain(self, gain: Gain):
    """gain of unknown units, or "machine-auto" """
    if self._cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if gain == self._gain:
      logger.debug("Gain is already set to %s", gain)
      return

    if not (gain == "machine-auto" or 0 <= gain <= 30):
      raise ValueError("gain must be between 0 and 30 (inclusive), or 'auto'")

    nodemap = self._cam.GetNodeMap()

    # set/disable automatic gain
    node_gain_auto = PySpin.CEnumerationPtr(nodemap.GetNode("GainAuto"))
    if not PySpin.IsReadable(node_gain_auto) or not PySpin.IsWritable(node_gain_auto):
      raise RuntimeError("unable to set automatic gain")
    node = (
      PySpin.CEnumEntryPtr(node_gain_auto.GetEntryByName("Continuous"))
      if gain == "machine-auto"
      else PySpin.CEnumEntryPtr(node_gain_auto.GetEntryByName("Off"))
    )
    if not PySpin.IsReadable(node):
      raise RuntimeError("unable to set automatic gain (enum entry retrieval)")
    node_gain_auto.SetIntValue(node.GetValue())

    if not gain == "machine-auto":
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
    return self.filters.index(mode) + 1

  def _objective_code(self, objective: Objective) -> int:
    return self.objectives.index(objective) + 1

  async def set_objective(self, objective: Objective):
    if objective == self._objective:
      logger.debug("Objective is already set to %s", objective)
      return

    if self.imaging_config is None:
      raise RuntimeError("Need to set imaging_config first")

    objective_code = self._objective_code(objective)

    if self.version.startswith("1"):
      await self.send_command("Y", f"P0d{objective_code:02}", timeout=60)
    else:
      await self.send_command("Y", f"P0e{objective_code:02}", timeout=60)

    self._objective = objective
    self._imaging_mode = None

  async def set_imaging_mode(self, mode: ImagingMode, led_intensity: int):
    if self._cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    if mode == self._imaging_mode:
      logger.debug("Imaging mode is already set to %s", mode)
      await self.led_on(intensity=led_intensity)
      return

    if mode == ImagingMode.COLOR_BRIGHTFIELD:
      # color brightfield will quickly switch through different filters, 05, 06, 07, 08
      # it sometimes calls (i, l{4,5,6,7}) before switching to the next filter. unclear.
      raise NotImplementedError("Color brightfield imaging not implemented yet")

    await self.led_off()

    if self.imaging_config is None:
      raise RuntimeError("Need to set imaging_config first")

    filter_index = self._imaging_mode_code(mode)

    if self.version.startswith("1"):
      if mode == ImagingMode.PHASE_CONTRAST:
        raise NotImplementedError("Phase contrast imaging not implemented yet on Cytation1")
      elif mode == ImagingMode.BRIGHTFIELD:
        await self.send_command("Y", "P0c05")
        await self.send_command("Y", "P0f02")
      else:
        await self.send_command("Y", f"P0c{filter_index:02}")
        await self.send_command("Y", "P0f01")
    else:
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
        await self.send_command("Y", f"P0d{filter_index:02}")
        await self.send_command("Y", "P1001")

    # Turn led on in the new mode
    self._imaging_mode = mode
    await self.led_on(intensity=led_intensity)

  async def _acquire_image(
    self,
    color_processing_algorithm: int = SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR,
    pixel_format: int = PixelFormat_Mono8,
  ) -> Image:
    assert self._cam is not None
    nodemap = self._cam.GetNodeMap()

    assert self.imaging_config is not None, "Need to set imaging_config first"

    num_tries = 0
    while num_tries < self.imaging_config.max_image_read_attempts:
      node_softwaretrigger_cmd = PySpin.CCommandPtr(nodemap.GetNode("TriggerSoftware"))
      if not PySpin.IsWritable(node_softwaretrigger_cmd):
        raise RuntimeError("unable to execute software trigger")

      try:
        node_softwaretrigger_cmd.Execute()
        timeout = int(self._cam.ExposureTime.GetValue() / 1000 + 1000)  # from example
        image_result = self._cam.GetNextImage(timeout)
        if not image_result.IsIncomplete():
          processor = PySpin.ImageProcessor()
          processor.SetColorProcessing(color_processing_algorithm)
          image_converted = processor.Convert(image_result, pixel_format)
          image_result.Release()
          return image_converted.GetNDArray()  # type: ignore
      except SpinnakerException as e:
        # the image is not ready yet, try again
        logger.warning("Failed to get image: %s", e)
        self.stop_acquisition()
        self.start_acquisition()
        if "[-1011]" in str(e):
          logger.warning(
            "[-1011] error might occur when the camera is plugged into a USB hub that does not have enough throughput."
          )

      num_tries += 1
      await asyncio.sleep(0.3)
    raise TimeoutError("max_image_read_attempts reached")

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
    auto_stop_acquisition=True,
  ) -> ImagingResult:
    """Capture image using the microscope

    speed: 211 ms ± 331 μs per loop (mean ± std. dev. of 7 runs, 10 loops each)

    Args:
      exposure_time: exposure time in ms, or `"machine-auto"`
      focal_height: focal height in mm, or `"machine-auto"`
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

    if self._cam is None:
      raise ValueError("Camera not initialized. Run setup(use_cam=True) first.")

    await self.set_plate(plate)

    if not self._acquiring:
      self.start_acquisition()

    try:
      await self.set_objective(objective)
      await self.set_imaging_mode(mode, led_intensity=led_intensity)
      await self.select(row, column)
      await self.set_exposure(exposure_time)
      await self.set_gain(gain)
      await self.set_focus(focal_height)

      def image_size(magnification: float) -> Tuple[float, float]:
        # "wide fov" is an option in gen5.exe, but in reality it takes the same pictures. So we just
        # simply take the wide fov option.
        # um to mm (plr unit)
        if magnification == 4:
          return (3474 / 1000, 3474 / 1000)
        if magnification == 20:
          return (694 / 1000, 694 / 1000)
        if magnification == 40:
          return (347 / 1000, 347 / 1000)
        raise ValueError(f"Don't know image size for magnification {magnification}")

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
        t0 = time.time()
        images.append(
          await self._acquire_image(
            color_processing_algorithm=color_processing_algorithm, pixel_format=pixel_format
          )
        )
        t1 = time.time()
        logger.debug(
          "[cytation5] acquired image in %.2f seconds at position",
          t1 - t0,
        )
    finally:
      await self.led_off()
      if auto_stop_acquisition:
        self.stop_acquisition()

    exposure_ms = float(self._cam.ExposureTime.GetValue()) / 1000
    assert self._focal_height is not None, "Focal height not set. Run set_focus() first."
    focal_height_val = float(self._focal_height)

    return ImagingResult(images=images, exposure_time=exposure_ms, focal_height=focal_height_val)


class Cytation5ImagingConfig(CytationImagingConfig):
  def __init__(self, *args, **kwargs):
    warnings.warn(
      "`Cytation5ImagingConfig` is deprecated. Please use `CytationImagingConfig` instead. ",
      FutureWarning,
    )
    super().__init__(*args, **kwargs)


class Cytation5Backend(CytationBackend):
  def __init__(self, *args, **kwargs):
    warnings.warn(
      "`Cytation5Backend` is deprecated. Please use `CytationBackend` instead. ",
      FutureWarning,
    )
    super().__init__(*args, **kwargs)
