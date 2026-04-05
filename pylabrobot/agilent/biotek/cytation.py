import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple, Union

from pylabrobot.agilent.biotek.biotek import BioTekBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.microscopy import (
  MicroscopyBackend,
)
from pylabrobot.capabilities.microscopy.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources import Plate
from pylabrobot.serializer import SerializableMixin

from .aravis_camera import AravisCamera

logger = logging.getLogger(__name__)


@dataclass
class CytationImagingConfig:
  camera_serial_number: Optional[str] = None
  max_image_read_attempts: int = 50
  objectives: Optional[List[Optional[Objective]]] = None
  filters: Optional[List[Optional[ImagingMode]]] = None


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class CytationBackend(BioTekBackend, MicroscopyBackend):
  """Backend for Agilent BioTek Cytation plate readers with imaging."""

  def __init__(
    self,
    timeout: float = 20,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
  ) -> None:
    super().__init__(
      timeout=timeout, device_id=device_id, human_readable_device_name="Agilent BioTek Cytation"
    )

    self.camera = AravisCamera()
    self.imaging_config = imaging_config or CytationImagingConfig()
    self._filters: Optional[List[Optional[ImagingMode]]] = self.imaging_config.filters
    self._objectives: Optional[List[Optional[Objective]]] = self.imaging_config.objectives
    self._exposure: Optional[Exposure] = None
    self._focal_height: Optional[FocalPosition] = None
    self._gain: Optional[Gain] = None
    self._imaging_mode: Optional[ImagingMode] = None
    self._row: Optional[int] = None
    self._column: Optional[int] = None
    self._pos_x: Optional[float] = None
    self._pos_y: Optional[float] = None
    self._objective: Optional[Objective] = None
    self._acquiring = False

  @dataclass
  class SetupParams(BackendParams):
    """Cytation-specific parameters for ``setup``.

    Args:
      use_cam: If True, initialize the Aravis camera during setup.
    """

    use_cam: bool = False

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    if not isinstance(backend_params, CytationBackend.SetupParams):
      backend_params = CytationBackend.SetupParams()

    logger.info(f"{self.__class__.__name__} setting up")

    await super().setup()

    if backend_params.use_cam:
      serial = self.imaging_config.camera_serial_number if self.imaging_config else None
      await self.camera.setup(serial_number=serial)
      logger.info("Camera connected: %s", self.camera.get_device_info())

      if self._filters is None:
        await self._load_filters()
      if self._objectives is None:
        await self._load_objectives()

  async def stop(self):
    if self._acquiring:
      self.stop_acquisition()

    try:
      await self.camera.stop()
    except Exception:
      logger.exception("Error stopping camera")

    logger.info(f"{self.__class__.__name__} stopping")
    await self.stop_shaking()
    await self.io.stop()

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
          1225111: ImagingMode.PROPIDIUM_IODIDE,
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
        weird_encoding = {
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
        middle_part = re.split(r"\s+", configuration.rstrip(b"\x03").decode("utf-8"))[1]
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

  async def close(self, plate: Optional[Plate] = None, slow: bool = False):
    await super().close(plate, slow)
    self._clear_imaging_state()

  def start_acquisition(self):
    self.camera.start_acquisition()
    self._acquiring = True

  def stop_acquisition(self):
    if not self._acquiring:
      return
    self.camera.stop_acquisition()
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
    if focal_position == "machine-auto":
      raise ValueError(
        "focal_position cannot be 'machine-auto'. Use the PLR Imager universal autofocus instead."
      )

    if focal_position == self._focal_height:
      logger.debug("Focus position is already set to %s", focal_position)
      return

    slope, intercept = (10.637991436186072, 1.0243013203461762)
    focus_integer = int(focal_position + intercept + slope * focal_position * 1000)
    focus_str = str(focus_integer).zfill(5)

    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")
    imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
    await self.send_command("i", f"F{imaging_mode_code}0{focus_str}")

    self._focal_height = focal_position

  async def set_position(self, x: float, y: float):
    if self._imaging_mode is None:
      raise ValueError("Imaging mode not set. Run set_imaging_mode() first.")

    if x == self._pos_x and y == self._pos_y:
      logger.debug("Position is already set to (%s, %s)", x, y)
      return

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
    await self.camera.set_auto_exposure(auto_exposure)

  async def set_exposure(self, exposure: Exposure):
    if exposure == self._exposure:
      logger.debug("Exposure time is already set to %s", exposure)
      return

    if isinstance(exposure, str):
      if exposure == "machine-auto":
        await self.set_auto_exposure("continuous")
        self._exposure = "machine-auto"
        return
      raise ValueError("exposure must be a number or 'auto'")
    await self.camera.set_auto_exposure("off")
    await self.camera.set_exposure(float(exposure))
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
    if gain == self._gain:
      logger.debug("Gain is already set to %s", gain)
      return

    if gain != "machine-auto":
      await self.camera.set_gain(float(gain))

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
    if mode == self._imaging_mode:
      logger.debug("Imaging mode is already set to %s", mode)
      await self.led_on(intensity=led_intensity)
      return

    if mode == ImagingMode.COLOR_BRIGHTFIELD:
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

    self._imaging_mode = mode
    await self.led_on(intensity=led_intensity)

  async def _acquire_image(self) -> Image:
    assert self.imaging_config is not None
    for attempt in range(self.imaging_config.max_image_read_attempts):
      try:
        return await self.camera.trigger(timeout_ms=5000)
      except Exception:
        if attempt < self.imaging_config.max_image_read_attempts - 1:
          logger.warning("Failed to get image, retrying...")
          self.stop_acquisition()
          self.start_acquisition()
          await asyncio.sleep(0.3)
        else:
          raise
    raise TimeoutError("max_image_read_attempts reached")

  @dataclass
  class CaptureParams(BackendParams):
    """Cytation-specific parameters for image capture.

    Args:
      led_intensity: LED intensity (0-100). Default 10.
      coverage: Image tiling coverage. ``"full"`` for full-well montage, or a
        ``(rows, cols)`` tuple for a specific tile grid. Default ``(1, 1)`` (single
        image).
      center_position: Center position of the capture area as ``(x_mm, y_mm)`` relative
        to the well center. If None, centers on the well. Default None.
      overlap: Fractional overlap between tiles (0.0-1.0) for montage stitching.
        If None, no overlap. Only used when coverage produces multiple tiles.
      auto_stop_acquisition: Whether to automatically stop image acquisition after
        capture. Default True.
    """

    led_intensity: int = 10
    coverage: Union[Literal["full"], Tuple[int, int]] = (1, 1)
    center_position: Optional[Tuple[float, float]] = None
    overlap: Optional[float] = None
    auto_stop_acquisition: bool = True

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
    backend_params: Optional[SerializableMixin] = None,
  ) -> ImagingResult:
    if not isinstance(backend_params, self.CaptureParams):
      backend_params = CytationBackend.CaptureParams()

    led_intensity = backend_params.led_intensity
    coverage = backend_params.coverage
    center_position = backend_params.center_position
    overlap = backend_params.overlap
    auto_stop_acquisition = backend_params.auto_stop_acquisition

    assert overlap is None, "not implemented yet"

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

      if center_position is None:
        center_position = (0, 0)
      positions = [
        (x * img_width + center_position[0], -y * img_height + center_position[1])
        for y in [i - (rows - 1) / 2 for i in range(rows)]
        for x in [i - (cols - 1) / 2 for i in range(cols)]
      ]

      images: List[Image] = []
      for x_pos, y_pos in positions:
        await self.set_position(x=x_pos, y=y_pos)
        t0 = time.time()
        images.append(await self._acquire_image())
        t1 = time.time()
        logger.debug("[cytation] acquired image in %.2f seconds", t1 - t0)
    finally:
      await self.led_off()
      if auto_stop_acquisition:
        self.stop_acquisition()

    exposure_ms = await self.camera.get_exposure()
    assert self._focal_height is not None
    focal_height_val = float(self._focal_height)

    return ImagingResult(images=images, exposure_time=exposure_ms, focal_height=focal_height_val)


# ---------------------------------------------------------------------------
# Backwards compatibility — device classes moved to cytation5.py / cytation1.py
# ---------------------------------------------------------------------------

from .cytation1 import Cytation1 as Cytation1  # noqa: E402, F401
from .cytation5 import Cytation5 as Cytation5  # noqa: E402, F401
