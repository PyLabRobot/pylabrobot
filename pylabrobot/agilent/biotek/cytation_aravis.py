"""Cytation 5 backend using Aravis instead of PySpin for camera control.

Layer: PLR MicroscopyBackend implementation
Role: Drop-in replacement for CytationBackend — same serial protocol for
  filter wheel, objective turret, focus motor, LED, and well positioning,
  but uses AravisCamera for image acquisition instead of PySpin.
Adjacent layers:
  - Above: PLR MicroscopyCapability calls capture()
  - Below: BioTekBackend serial IO (filter/objective/focus/LED) +
           AravisCamera (image acquisition via Aravis/GenICam)

This module exists because CytationBackend (cytation.py) imports PySpin at
module level. Inheriting from CytationBackend would require PySpin to be
installed — defeating the purpose. Instead, CytationAravisBackend inherits
directly from BioTekBackend + MicroscopyBackend and copies the serial protocol
methods from CytationBackend. The camera methods delegate to AravisCamera.

The serial protocol methods (filter wheel, objectives, focus, LED, well
positioning) are copied from cytation.py (PLR commit 226e6d41). These
methods use self.io (BioTekBackend serial) and do not touch PySpin.

Architecture label: **[Proposed]** — Aravis as alternative to PySpin for PLR.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple, Union

import numpy as np

from pylabrobot.capabilities.microscopy.backend import MicroscopyBackend
from pylabrobot.capabilities.microscopy.standard import (
    Exposure,
    FocalPosition,
    Gain,
    Image,
    ImagingMode,
    ImagingResult,
    Objective,
)
from pylabrobot.resources.plate import Plate

from .aravis_camera import AravisCamera
from .biotek import BioTekBackend

logger = logging.getLogger(__name__)


@dataclass
class AravisImagingConfig:
    """Imaging configuration for CytationAravisBackend.

    Equivalent to CytationImagingConfig but without PySpin dependencies.
    Defines filter wheel positions, objective positions, and camera serial.
    """

    camera_serial_number: Optional[str] = None
    filters: Optional[List[Optional[ImagingMode]]] = None
    objectives: Optional[List[Optional[Objective]]] = None
    max_image_read_attempts: int = 10
    image_read_delay: float = 0.3


class CytationAravisBackend(BioTekBackend, MicroscopyBackend):
    """Cytation 5 backend with Aravis camera instead of PySpin.

    This class implements PLR's MicroscopyBackend.capture() by orchestrating:
    1. Well positioning (serial protocol → BioTekBackend)
    2. Filter/objective/focus/LED (serial protocol → copied from CytationBackend)
    3. Exposure/gain (AravisCamera → GenICam nodes)
    4. Image acquisition (AravisCamera → Aravis buffer)

    The serial protocol code is copied from CytationBackend (PLR commit 226e6d41).
    Camera operations delegate to AravisCamera.

    Usage:
        backend = CytationAravisBackend(camera_serial="12345678")
        cytation = Cytation5(backend=backend)
        await cytation.setup()
        result = await cytation.microscope.capture(...)
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

        self._aravis = AravisCamera()
        self._camera_serial = camera_serial
        self.imaging_config = imaging_config or AravisImagingConfig(
            camera_serial_number=camera_serial
        )

        # Imaging state (mirrors CytationBackend)
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
        """Set up serial connection and camera.

        Args:
            use_cam: If True (default), initialize the Aravis camera.
                Set to False for serial-only operations (plate reading).
        """
        logger.info("%s setting up", self.__class__.__name__)
        await super().setup()

        # Load filter and objective configuration from Cytation via serial
        if self._filters is None:
            await self._load_filters()
        if self._objectives is None:
            await self._load_objectives()

        if use_cam:
            serial = (
                self._camera_serial
                or (self.imaging_config.camera_serial_number if self.imaging_config else None)
            )
            if serial is None:
                raise RuntimeError(
                    "No camera serial number provided. Pass camera_serial to constructor "
                    "or set imaging_config.camera_serial_number."
                )
            try:
                await self._aravis.setup(serial)
            except Exception:
                try:
                    await self.stop()
                except Exception:
                    pass
                raise

    async def stop(self) -> None:
        """Stop camera and serial connection."""
        logger.info("%s stopping", self.__class__.__name__)

        if self._acquiring:
            self.stop_acquisition()

        await self._aravis.stop()
        await super().stop()

        self._objectives = None
        self._filters = None
        self._clear_imaging_state()

    def _clear_imaging_state(self) -> None:
        self._exposure = None
        self._focal_height = None
        self._gain = None
        self._imaging_mode = None
        self._row = None
        self._column = None
        self._pos_x = None
        self._pos_y = None

    # ─── Filter & Objective Loading (copied from CytationBackend) ────────

    async def _load_filters(self) -> None:
        """Load filter wheel configuration from Cytation via serial.

        Copied from CytationBackend (PLR commit 226e6d41).
        Reads 4 filter positions and maps Cytation codes to ImagingMode enums.
        """
        self._filters = []
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
        for spot in range(1, 5):
            configuration = await self.send_command("i", f"q{spot}")
            assert configuration is not None
            parts = configuration.decode().strip().split(" ")
            if len(parts) == 1:
                self._filters.append(None)
            else:
                cytation_code = int(parts[0])
                if cytation_code not in cytation_code2imaging_mode:
                    self._filters.append(None)
                else:
                    self._filters.append(cytation_code2imaging_mode[cytation_code])

    async def _load_objectives(self) -> None:
        """Load objective turret configuration from Cytation via serial.

        Copied from CytationBackend (PLR commit 226e6d41).
        Reads objective positions and maps to Objective enums.
        """
        self._objectives = []
        if self.version.startswith("1"):
            weird_encoding = {
                0x00: " ", 0x14: ".", 0x15: "/", 0x16: "0", 0x17: "1",
                0x18: "2", 0x19: "3", 0x20: "4", 0x21: "5", 0x22: "6",
                0x23: "7", 0x24: "8", 0x25: "9", 0x33: "A", 0x34: "B",
                0x35: "C", 0x36: "D", 0x37: "E", 0x38: "F", 0x39: "G",
                0x40: "H", 0x41: "I", 0x42: "J", 0x43: "K", 0x44: "L",
                0x45: "M", 0x46: "N", 0x47: "O", 0x48: "P", 0x49: "Q",
                0x50: "R", 0x51: "S", 0x52: "T", 0x53: "U", 0x54: "V",
                0x55: "W", 0x56: "X", 0x57: "Y", 0x58: "Z",
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
            for spot in [1, 2]:
                configuration = await self.send_command("i", f"o{spot}")
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
                        part_number2objective.get(part_number.lower())
                    )
        elif self.version.startswith("2"):
            annulus_part_number2objective = {
                1320520: Objective.O_4X_PL_FL_Phase,
                1320521: Objective.O_20X_PL_FL_Phase,
                1322026: Objective.O_40X_PL_FL_Phase,
            }
            for spot in range(1, 7):
                configuration = await self.send_command("i", f"h{spot + 1}")
                assert configuration is not None
                if configuration.startswith(b"****"):
                    self._objectives.append(None)
                else:
                    code = int(
                        configuration.decode("latin").strip().split(" ")[0]
                    )
                    self._objectives.append(
                        annulus_part_number2objective.get(code)
                    )
        self._objective = None

    # ─── Acquisition Control ─────────────────────────────────────────────

    def start_acquisition(self) -> None:
        """Start camera acquisition."""
        if self._acquiring:
            return
        self._aravis.start_acquisition()
        self._acquiring = True

    def stop_acquisition(self) -> None:
        """Stop camera acquisition."""
        if not self._acquiring:
            return
        self._aravis.stop_acquisition()
        self._acquiring = False

    # ─── Camera Parameters ───────────────────────────────────────────────

    async def set_exposure(self, exposure: Exposure) -> None:
        """Set exposure time (milliseconds) or 'machine-auto'.

        Mirrors CytationBackend.set_exposure().
        """
        if exposure == self._exposure:
            return

        if isinstance(exposure, str):
            if exposure == "machine-auto":
                await self.set_auto_exposure("continuous")
                self._exposure = "machine-auto"
                return
            raise ValueError("exposure must be a number or 'machine-auto'")

        await self._aravis.set_exposure(float(exposure))
        self._exposure = exposure

    async def set_gain(self, gain: Gain) -> None:
        """Set gain value or 'machine-auto'.

        Mirrors CytationBackend.set_gain().
        """
        if gain == self._gain:
            return

        if gain == "machine-auto":
            # Aravis auto-gain via GainAuto node
            self._aravis._device.set_string_feature_value("GainAuto", "Continuous")
            self._gain = "machine-auto"
            return

        await self._aravis.set_gain(float(gain))
        self._gain = gain

    async def set_auto_exposure(
        self, auto_exposure: Literal["off", "once", "continuous"]
    ) -> None:
        """Set auto-exposure mode. Delegates to AravisCamera."""
        await self._aravis.set_auto_exposure(auto_exposure)

    # ─── Image Acquisition ───────────────────────────────────────────────

    async def _acquire_image(self) -> Image:
        """Capture a single frame via AravisCamera.

        Mirrors CytationBackend._acquire_image(). Includes retry logic
        matching the original's max_image_read_attempts pattern.
        """
        max_attempts = self.imaging_config.max_image_read_attempts
        delay = self.imaging_config.image_read_delay

        for attempt in range(max_attempts):
            try:
                image = await self._aravis.trigger(timeout_ms=5000)
                return image
            except RuntimeError as e:
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Image capture attempt %d/%d failed: %s",
                        attempt + 1,
                        max_attempts,
                        e,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        raise RuntimeError("Image capture failed after all attempts")

    # ─── Serial Protocol (copied from CytationBackend, PLR commit 226e6d41) ─

    def _imaging_mode_code(self, mode: ImagingMode) -> int:
        """Map ImagingMode to Cytation filter wheel code.

        Brightfield and phase contrast use code 5.
        Fluorescence modes use their index in the filter list (1-based).
        """
        if mode in (ImagingMode.BRIGHTFIELD, ImagingMode.PHASE_CONTRAST):
            return 5
        return self.filters.index(mode) + 1

    def _objective_code(self, objective: Objective) -> int:
        """Map Objective to Cytation turret position (1-based)."""
        return self.objectives.index(objective) + 1

    async def set_imaging_mode(
        self, mode: ImagingMode, led_intensity: int = 10
    ) -> None:
        """Set filter wheel position and LED. Copied from CytationBackend."""
        if mode == self._imaging_mode:
            logger.debug("Imaging mode is already set to %s", mode)
            await self.led_on(intensity=led_intensity)
            return

        if mode == ImagingMode.COLOR_BRIGHTFIELD:
            raise NotImplementedError("Color brightfield imaging not implemented yet")

        await self.led_off()
        filter_index = self._imaging_mode_code(mode)

        if self.version.startswith("1"):
            if mode == ImagingMode.PHASE_CONTRAST:
                raise NotImplementedError(
                    "Phase contrast not implemented on Cytation1"
                )
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

    async def set_objective(self, objective: Objective) -> None:
        """Move objective turret. Copied from CytationBackend."""
        if objective == self._objective:
            return

        objective_code = self._objective_code(objective)

        if self.version.startswith("1"):
            await self.send_command("Y", f"P0d{objective_code:02}", timeout=60)
        else:
            await self.send_command("Y", f"P0e{objective_code:02}", timeout=60)

        self._objective = objective
        self._imaging_mode = None

    async def set_focus(self, focal_position: FocalPosition) -> None:
        """Set focus motor position (mm). Copied from CytationBackend."""
        if focal_position == "machine-auto":
            raise ValueError(
                "focal_position cannot be 'machine-auto'. "
                "Use the PLR Imager universal autofocus instead."
            )

        if focal_position == self._focal_height:
            return

        slope, intercept = (10.637991436186072, 1.0243013203461762)
        focus_integer = int(
            focal_position + intercept + slope * focal_position * 1000
        )
        focus_str = str(focus_integer).zfill(5)

        if self._imaging_mode is None:
            raise ValueError("Imaging mode not set. Call set_imaging_mode() first.")
        imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
        await self.send_command("i", f"F{imaging_mode_code}0{focus_str}")

        self._focal_height = focal_position

    async def led_on(self, intensity: int = 10) -> None:
        """Turn on LED. Copied from CytationBackend."""
        if not 1 <= intensity <= 10:
            raise ValueError("intensity must be between 1 and 10")
        intensity_str = str(intensity).zfill(2)
        if self._imaging_mode is None:
            raise ValueError("Imaging mode not set. Call set_imaging_mode() first.")
        imaging_mode_code = self._imaging_mode_code(self._imaging_mode)
        await self.send_command("i", f"L0{imaging_mode_code}{intensity_str}")

    async def led_off(self) -> None:
        """Turn off LED. Copied from CytationBackend."""
        await self.send_command("i", "L0001")

    async def select(self, row: int, column: int) -> None:
        """Move to well position. Copied from CytationBackend."""
        if row == self._row and column == self._column:
            return
        row_str = str(row).zfill(2)
        column_str = str(column).zfill(2)
        await self.send_command("Y", f"W6{row_str}{column_str}")
        self._row, self._column = row, column
        self._pos_x, self._pos_y = None, None
        await self.set_position(0, 0)

    async def set_position(self, x: float, y: float) -> None:
        """Set precise position within well. Adapted from CytationBackend."""
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

        self._pos_x, self._pos_y = x, y

    # set_plate() is inherited from BioTekBackend — sends plate geometry
    # (well positions, plate dimensions, plate height) to the Cytation via
    # serial command "y". Do NOT override — the Cytation needs this to
    # position the stage correctly.

    # ─── MicroscopyBackend.capture() ─────────────────────────────────────

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
        **kwargs,
    ) -> ImagingResult:
        """Capture image(s) from a well. Implements MicroscopyBackend.capture().

        Orchestrates the full imaging pipeline:
        1. Set plate geometry
        2. Start camera acquisition
        3. Set objective (turret motor, serial)
        4. Set imaging mode / filter (filter wheel, serial)
        5. Select well (stage motor, serial)
        6. Set exposure and gain (AravisCamera → GenICam)
        7. Set focal height (focus motor, serial)
        8. Trigger and grab image (AravisCamera → Aravis buffer)
        9. Return ImagingResult

        This mirrors CytationBackend.capture() but with simplified tiling
        (single position only for the initial proof-of-concept).
        """
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

            if center_position is not None:
                await self.set_position(center_position[0], center_position[1])

            images: List[Image] = []

            if coverage == (1, 1):
                # Single image capture
                image = await self._acquire_image()
                images.append(image)
            else:
                # Multi-position tiling (simplified — matches CytationBackend pattern)
                if self._objective is None:
                    raise RuntimeError("Objective not set.")
                magnification = self._objective.magnification

                # Image field of view by magnification (mm)
                fov_map = {4: 3.474, 20: 0.694, 40: 0.347}
                fov = fov_map.get(int(magnification), 3.474)

                if coverage == "full":
                    first_well = plate.get_item(0)
                    well_w = first_well.get_size_x()
                    well_h = first_well.get_size_y()
                    rows_n = math.ceil(well_h / fov)
                    cols_n = math.ceil(well_w / fov)
                else:
                    rows_n, cols_n = coverage

                cx = center_position[0] if center_position else 0.0
                cy = center_position[1] if center_position else 0.0

                for yi in range(rows_n):
                    for xi in range(cols_n):
                        x_pos = (xi - (cols_n - 1) / 2) * fov + cx
                        y_pos = -(yi - (rows_n - 1) / 2) * fov + cy
                        await self.set_position(x=x_pos, y=y_pos)
                        image = await self._acquire_image()
                        images.append(image)

        finally:
            await self.led_off()
            self.stop_acquisition()

        exposure_ms = await self._aravis.get_exposure()
        focal_height_val = float(self._focal_height) if self._focal_height else 0.0

        return ImagingResult(
            images=images,
            exposure_time=exposure_ms,
            focal_height=focal_height_val,
        )
