"""CytationAravisMicroscopyBackend — MicroscopyBackend for the Cytation with Aravis.

Orchestrates capture by sequencing the driver's optics and camera methods.
Same role as STARPIPBackend: translates capability operations into driver calls.

Layer: Capability backend (orchestration)
Adjacent layers:
  - Above: Microscopy capability calls capture()
  - Below: CytationAravisDriver (optics + camera commands)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
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
from pylabrobot.serializer import SerializableMixin

if TYPE_CHECKING:
    from .cytation_aravis_driver import CytationAravisDriver

logger = logging.getLogger(__name__)


class CytationAravisMicroscopyBackend(MicroscopyBackend):
    """MicroscopyBackend for the Cytation using Aravis camera.

    Orchestrates a capture by calling the driver's optics and camera methods
    in the correct sequence. Same pattern as STARPIPBackend: the backend
    translates capability operations into driver calls.

    Created by CytationAravisDriver during setup() and accessed via
    ``driver.microscopy_backend``.
    """

    def __init__(self, driver: CytationAravisDriver) -> None:
        self.driver = driver

    async def setup(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    # ─── Vendor Params ──────────────────────────────────────────────────

    @dataclass
    class CaptureParams(BackendParams):
        """Aravis-specific capture parameters.

        Passed via ``backend_params`` in ``Microscopy.capture()``.
        """

        led_intensity: int = 10
        coverage: Union[Literal["full"], Tuple[int, int]] = (1, 1)
        center_position: Optional[Tuple[float, float]] = None
        auto_stop_acquisition: bool = True

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
        backend_params: Optional[SerializableMixin] = None,
    ) -> ImagingResult:
        """Capture image(s) from a well.

        Orchestrates the full imaging pipeline via the driver:
        1. Set plate geometry (serial)
        2. Start camera acquisition
        3. Set objective (turret motor, serial)
        4. Set imaging mode / filter (filter wheel, serial)
        5. Select well (stage motor, serial)
        6. Set exposure and gain (AravisCamera)
        7. Set focal height (focus motor, serial)
        8. Trigger and grab image (AravisCamera)
        9. Return ImagingResult
        """
        if not isinstance(backend_params, self.CaptureParams):
            backend_params = CytationAravisMicroscopyBackend.CaptureParams()

        led_intensity = backend_params.led_intensity
        coverage = backend_params.coverage
        center_position = backend_params.center_position
        auto_stop_acquisition = backend_params.auto_stop_acquisition

        d = self.driver
        await d.set_plate(plate)

        if not d._acquiring:
            d.start_acquisition()

        try:
            await d.set_objective(objective)
            await d.set_imaging_mode(mode, led_intensity=led_intensity)
            await d.select(row, column)
            await d.set_exposure(exposure_time)
            await d.set_gain(gain)
            await d.set_focus(focal_height)

            if center_position is not None:
                await d.set_position(center_position[0], center_position[1])

            images: List[Image] = []

            if coverage == (1, 1):
                image = await d.acquire_image()
                images.append(image)
            else:
                if d._objective is None:
                    raise RuntimeError("Objective not set.")
                magnification = d._objective.magnification

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
                        await d.set_position(x=x_pos, y=y_pos)
                        image = await d.acquire_image()
                        images.append(image)

        finally:
            await d.led_off()
            if auto_stop_acquisition:
                d.stop_acquisition()

        exposure_ms = await d.get_exposure()
        focal_height_val = float(d._focal_height) if d._focal_height else 0.0

        return ImagingResult(
            images=images,
            exposure_time=exposure_ms,
            focal_height=focal_height_val,
        )
