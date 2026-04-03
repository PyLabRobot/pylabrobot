"""CytationAravisDriver — connection + optics + camera for the Cytation using Aravis.

Extends BioTekBackend (serial IO) with AravisCamera for image acquisition.
This is the driver layer — it owns the connections and low-level commands.
The MicroscopyBackend (capture orchestration) is created during setup()
and accessed via ``self.microscopy_backend``.

Follows the STARDriver pattern: the driver creates backends during setup(),
the device reads them to wire capabilities.

Layer: Driver (connection + low-level commands)
Adjacent layers:
  - Above: Cytation1/Cytation5 device reads driver.microscopy_backend
  - Below: BioTekBackend serial IO + AravisCamera (GenICam/USB3 Vision)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Literal, Optional

from pylabrobot.capabilities.microscopy.standard import (
    Exposure,
    FocalPosition,
    Gain,
    Image,
    ImagingMode,
    Objective,
)

from .aravis_camera import AravisCamera
from .biotek import BioTekBackend
from .cytation_aravis_microscopy import CytationAravisMicroscopyBackend

logger = logging.getLogger(__name__)


@dataclass
class AravisImagingConfig:
    """Imaging configuration for the Cytation with Aravis camera.

    Defines filter wheel positions, objective positions, and camera serial.
    """

    camera_serial_number: Optional[str] = None
    filters: Optional[List[Optional[ImagingMode]]] = None
    objectives: Optional[List[Optional[Objective]]] = None
    max_image_read_attempts: int = 10
    image_read_delay: float = 0.3


class CytationAravisDriver(BioTekBackend):
    """Driver for the Cytation using Aravis camera instead of PySpin.

    Extends BioTekBackend with:
    - AravisCamera for image acquisition (replaces PySpin)
    - Optics control methods (filter wheel, objectives, focus, LED, positioning)
    - Camera control methods (exposure, gain, auto-exposure, trigger)

    During setup(), creates a CytationAravisMicroscopyBackend that the
    device class reads to wire the Microscopy capability.

    Usage::

        driver = CytationAravisDriver(camera_serial="22580842")
        await driver.setup()
        # driver.microscopy_backend is now available for Microscopy(backend=...)
    """

    def __init__(
        self,
        camera_serial: Optional[str] = None,
        timeout: float = 20,
        device_id: Optional[str] = None,
        imaging_config: Optional[AravisImagingConfig] = None,
    ) -> None:
        super().__init__(
            timeout=timeout,
            device_id=device_id,
            human_readable_device_name="Agilent BioTek Cytation (Aravis)",
        )

        self.camera = AravisCamera()
        self._camera_serial = camera_serial
        self.imaging_config = imaging_config or AravisImagingConfig(
            camera_serial_number=camera_serial
        )

        # Imaging state
        self._filters: Optional[List[Optional[ImagingMode]]] = (
            self.imaging_config.filters
        )
        self._objectives: Optional[List[Optional[Objective]]] = (
            self.imaging_config.objectives
        )
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

        # Created during setup()
        self.microscopy_backend: CytationAravisMicroscopyBackend

    @property
    def filters(self) -> List[Optional[ImagingMode]]:
        if self._filters is None:
            raise RuntimeError("Filters not loaded. Call setup() first.")
        return self._filters

    @property
    def objectives(self) -> List[Optional[Objective]]:
        if self._objectives is None:
            raise RuntimeError("Objectives not loaded. Call setup() first.")
        return self._objectives

    # ─── Lifecycle ───────────────────────────────────────────────────────

    async def setup(self, use_cam: bool = True) -> None:
        """Set up serial connection and camera."""
        logger.info("CytationAravisDriver setting up")

        await super().setup()

        if use_cam:
            serial = (
                self._camera_serial
                or (self.imaging_config.camera_serial_number if self.imaging_config else None)
            )
            await self.camera.setup(serial_number=serial)
            logger.info("Camera connected: %s", self.camera.get_device_info())

        if self._filters is None:
            await self._load_filters()

        if self._objectives is None:
            await self._load_objectives()

        # Create microscopy backend (device reads this to wire Microscopy capability)
        self.microscopy_backend = CytationAravisMicroscopyBackend(driver=self)

        logger.info("CytationAravisDriver setup complete")

    async def stop(self) -> None:
        """Disconnect camera and serial."""
        self._clear_imaging_state()
        try:
            await self.camera.stop()
        except Exception:
            logger.exception("Error stopping camera")
        await super().stop()
        logger.info("CytationAravisDriver stopped")

    def _clear_imaging_state(self) -> None:
        self._exposure = None
        self._focal_height = None
        self._gain = None
        self._imaging_mode = None
        self._row = None
        self._column = None
        self._pos_x = None
        self._pos_y = None
        self._objective = None
        self._acquiring = False

    # ─── Filter / Objective Discovery ────────────────────────────────────

    async def _load_filters(self) -> None:
        """Discover installed filter cube positions from firmware.

        Queries each slot individually with command ``i q{slot}``.
        Uses the same code mapping as CytationBackend (cytation.py).
        """
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

        self._filters = []
        for slot in range(1, 5):
            configuration = await self.send_command("i", f"q{slot}")
            assert configuration is not None
            parts = configuration.decode().strip().split(" ")
            if len(parts) == 1:
                self._filters.append(None)
            else:
                cytation_code = int(parts[0])
                self._filters.append(
                    cytation_code2imaging_mode.get(cytation_code, None)
                )

        logger.info("Loaded filters: %s", self._filters)

    async def _load_objectives(self) -> None:
        """Discover installed objective positions from firmware.

        Queries each slot individually. Uses the same weird encoding
        and part number mapping as CytationBackend (cytation.py).
        Firmware version 1.x uses ``i o{slot}``, version 2.x uses ``i h{slot}``.
        """
        weird_encoding = {
            0x00: " ", 0x14: ".", 0x15: "/",
            0x16: "0", 0x17: "1", 0x18: "2", 0x19: "3",
            0x20: "4", 0x21: "5", 0x22: "6", 0x23: "7",
            0x24: "8", 0x25: "9",
            0x33: "A", 0x34: "B", 0x35: "C", 0x36: "D",
            0x37: "E", 0x38: "F", 0x39: "G",
            0x40: "H", 0x41: "I", 0x42: "J", 0x43: "K",
            0x44: "L", 0x45: "M", 0x46: "N", 0x47: "O",
            0x48: "P", 0x49: "Q", 0x50: "R", 0x51: "S",
            0x52: "T", 0x53: "U", 0x54: "V", 0x55: "W",
            0x56: "X", 0x57: "Y", 0x58: "Z",
        }
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

        self._objectives = []
        if self.version.startswith("1"):
            for slot in [1, 2]:
                configuration = await self.send_command("i", f"o{slot}")
                if configuration is None:
                    raise RuntimeError("Failed to load objective configuration")
                middle_part = re.split(
                    r"\s+", configuration.rstrip(b"\x03").decode("utf-8")
                )[1]
                if middle_part == "0000":
                    self._objectives.append(None)
                else:
                    part_number = "".join(
                        [weird_encoding[x] for x in bytes.fromhex(middle_part)]
                    )
                    self._objectives.append(
                        part_number2objective.get(part_number.lower(), None)
                    )
        elif self.version.startswith("2"):
            for slot in range(1, 7):
                configuration = await self.send_command("i", f"h{slot + 1}")
                assert configuration is not None
                if configuration.startswith(b"****"):
                    self._objectives.append(None)
                else:
                    annulus_code = int(
                        configuration.decode("latin").strip().split(" ")[0]
                    )
                    annulus2objective = {
                        1320520: Objective.O_4X_PL_FL_Phase,
                        1320521: Objective.O_20X_PL_FL_Phase,
                        1322026: Objective.O_40X_PL_FL_Phase,
                    }
                    self._objectives.append(
                        annulus2objective.get(annulus_code, None)
                    )
        else:
            raise RuntimeError(f"Unsupported firmware version: {self.version}")

        logger.info("Loaded objectives: %s", self._objectives)

    # ─── Camera Control ──────────────────────────────────────────────────

    async def set_exposure(self, exposure: Exposure) -> None:
        """Set camera exposure time in ms."""
        if exposure == "machine-auto":
            await self.camera.set_auto_exposure("continuous")
        else:
            await self.camera.set_auto_exposure("off")
            await self.camera.set_exposure(float(exposure))
        self._exposure = exposure

    async def set_gain(self, gain: Gain) -> None:
        """Set camera gain."""
        if gain == "machine-auto":
            pass
        else:
            await self.camera.set_gain(float(gain))
        self._gain = gain

    async def set_auto_exposure(
        self, auto_exposure: Literal["off", "once", "continuous"]
    ) -> None:
        """Set camera auto-exposure mode."""
        await self.camera.set_auto_exposure(auto_exposure)

    def start_acquisition(self) -> None:
        """Start camera acquisition (buffered streaming)."""
        self.camera.start_acquisition()
        self._acquiring = True

    def stop_acquisition(self) -> None:
        """Stop camera acquisition."""
        if self._acquiring:
            self.camera.stop_acquisition()
            self._acquiring = False

    async def acquire_image(self) -> Image:
        """Trigger camera and read image."""
        config = self.imaging_config
        for attempt in range(config.max_image_read_attempts):
            try:
                image = await self.camera.trigger(timeout_ms=5000)
                return image
            except Exception:
                if attempt < config.max_image_read_attempts - 1:
                    await asyncio.sleep(config.image_read_delay)
                else:
                    raise

    async def get_exposure(self) -> float:
        """Get current exposure time in ms."""
        return await self.camera.get_exposure()

    # ─── Optics Control (serial protocol) ────────────────────────────────

    def _imaging_mode_code(self, mode: ImagingMode) -> int:
        """Get filter wheel position index for an imaging mode.

        Brightfield and phase contrast use position 5 (no filter cube).
        """
        if mode == ImagingMode.BRIGHTFIELD or mode == ImagingMode.PHASE_CONTRAST:
            return 5
        for i, f in enumerate(self.filters):
            if f == mode:
                return i + 1
        raise ValueError(f"Mode {mode} not found in filters: {self.filters}")

    def _objective_code(self, objective: Objective) -> int:
        """Get turret position index for an objective."""
        for i, o in enumerate(self.objectives):
            if o == objective:
                return i + 1
        raise ValueError(f"Objective {objective} not found: {self.objectives}")

    async def set_imaging_mode(
        self, mode: ImagingMode, led_intensity: int = 10
    ) -> None:
        """Set filter wheel position and LED.

        Brightfield uses filter position 5 (empty slot) and light path
        mode 02 (transmitted). Fluorescence modes use the filter cube
        position and light path mode 01 (epifluorescence).
        """
        if mode == self._imaging_mode:
            await self.led_on(intensity=led_intensity)
            return

        if mode == ImagingMode.COLOR_BRIGHTFIELD:
            raise NotImplementedError("Color brightfield not implemented")

        await self.led_off()
        filter_index = self._imaging_mode_code(mode)

        if self.version.startswith("1"):
            if mode == ImagingMode.PHASE_CONTRAST:
                raise NotImplementedError("Phase contrast not implemented on Cytation 1")
            elif mode == ImagingMode.BRIGHTFIELD:
                await self.send_command("Y", "P0c05")
                await self.send_command("Y", "P0f02")
            else:
                await self.send_command("Y", f"P0c{filter_index:02}")
                await self.send_command("Y", "P0f01")
        else:
            await self.send_command("Y", f"P0c{filter_index:02}")

        self._imaging_mode = mode
        await self.led_on(intensity=led_intensity)
        await asyncio.sleep(0.5)

    async def set_objective(self, objective: Objective) -> None:
        """Rotate objective turret to the specified objective."""
        if objective == self._objective:
            return
        obj_code = self._objective_code(objective)
        if self.version.startswith("1"):
            await self.send_command("Y", f"P0d{obj_code:02}", timeout=60)
        else:
            await self.send_command("Y", f"P0e{obj_code:02}", timeout=60)
        self._objective = objective
        self._imaging_mode = None  # force re-set after objective change

    async def set_focus(self, focal_position: FocalPosition) -> None:
        """Move focus motor to the specified height (mm).

        Uses the same linear calibration as CytationBackend.
        """
        if focal_position == "machine-auto":
            raise ValueError(
                "focal_position cannot be 'machine-auto'. "
                "Use PLR's Microscopy auto-focus instead."
            )

        if focal_position == self._focal_height:
            return

        slope, intercept = (10.637991436186072, 1.0243013203461762)
        focus_integer = int(
            float(focal_position) + intercept + slope * float(focal_position) * 1000
        )
        focus_str = str(focus_integer).zfill(5)

        if self._imaging_mode is None:
            raise ValueError("Imaging mode not set. Call set_imaging_mode() first.")
        imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
        await self.send_command("i", f"F{imaging_mode_code}0{focus_str}")

        self._focal_height = focal_position

    async def led_on(self, intensity: int = 10) -> None:
        """Turn on LED at specified intensity (1–10)."""
        if not 1 <= intensity <= 10:
            raise ValueError("intensity must be between 1 and 10")
        if self._imaging_mode is None:
            raise ValueError("Imaging mode not set. Call set_imaging_mode() first.")
        imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
        intensity_str = str(intensity).zfill(2)
        await self.send_command("i", f"L0{imaging_mode_code}{intensity_str}")

    async def led_off(self) -> None:
        """Turn off LED."""
        await self.send_command("i", "L0001")

    async def select(self, row: int, column: int) -> None:
        """Move plate stage to a well position."""
        if row == self._row and column == self._column:
            return
        row_str = str(row).zfill(2)
        col_str = str(column).zfill(2)
        await self.send_command("Y", f"W6{row_str}{col_str}")
        self._row, self._column = row, column
        self._pos_x, self._pos_y = None, None
        await self.set_position(0, 0)

    async def set_position(self, x: float, y: float) -> None:
        """Fine-position the plate stage within a well (mm)."""
        if self._imaging_mode is None:
            raise ValueError("Imaging mode not set. Call set_imaging_mode() first.")

        if x == self._pos_x and y == self._pos_y:
            return

        x_str = str(round(x * 100 * 0.984)).zfill(6)
        y_str = str(round(y * 100 * 0.984)).zfill(6)

        if self._row is None or self._column is None:
            raise ValueError("Row and column not set. Call select() first.")
        row_str = str(self._row).zfill(2)
        column_str = str(self._column).zfill(2)

        if self._objective is None:
            raise ValueError("Objective not set. Call set_objective() first.")
        objective_code = self._objective_code(self._objective)
        imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
        await self.send_command(
            "Y",
            f"Z{objective_code}{imaging_mode_code}6{row_str}{column_str}"
            f"{y_str}{x_str}",
        )

        relative_x = x - (self._pos_x or 0)
        relative_y = y - (self._pos_y or 0)
        if relative_x != 0:
            relative_x_str = str(round(relative_x * 100 * 0.984)).zfill(6)
            await self.send_command("Y", f"O00{relative_x_str}")
        if relative_y != 0:
            relative_y_str = str(round(relative_y * 100 * 0.984)).zfill(6)
            await self.send_command("Y", f"O01{relative_y_str}")

        self._pos_x, self._pos_y = x, y
