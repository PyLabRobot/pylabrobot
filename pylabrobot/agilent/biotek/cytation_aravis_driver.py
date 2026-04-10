"""CytationAravisDriver — BioTek serial + Aravis camera connection.

Extends BioTekBackend with AravisCamera for image acquisition.
All imaging logic (optics, capture orchestration) lives in
CytationMicroscopyBackend; this driver just owns the connections.

Layer: Driver (connection lifecycle)
Adjacent layers:
  - Above: Cytation1/Cytation5 device reads driver.microscopy_backend
  - Below: BioTekBackend serial IO + AravisCamera (GenICam/USB3 Vision)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.microscopy.standard import (
  ImagingMode,
  Objective,
)

from .aravis_camera import AravisCamera
from .biotek import BioTekBackend
from .cytation_microscopy_backend import CytationMicroscopyBackend

logger = logging.getLogger(__name__)


@dataclass
class CytationImagingConfig:
  """Imaging configuration for the Cytation with Aravis camera."""

  camera_serial_number: Optional[str] = None
  filters: Optional[List[Optional[ImagingMode]]] = None
  objectives: Optional[List[Optional[Objective]]] = None
  max_image_read_attempts: int = 10
  image_read_delay: float = 0.3


class CytationAravisDriver(BioTekBackend):
  """Driver for the Cytation using Aravis camera.

  Owns the BioTek serial connection and the AravisCamera.
  All imaging logic (optics control, capture orchestration) is in
  CytationMicroscopyBackend, which is created during setup().
  """

  def __init__(
    self,
    camera_serial: Optional[str] = None,
    timeout: float = 20,
    device_id: Optional[str] = None,
    imaging_config: Optional[CytationImagingConfig] = None,
  ) -> None:
    super().__init__(
      timeout=timeout,
      device_id=device_id,
      human_readable_device_name="Agilent BioTek Cytation (Aravis)",
    )

    self.camera = AravisCamera()
    self._camera_serial = camera_serial
    self.imaging_config = imaging_config or CytationImagingConfig(camera_serial_number=camera_serial)

    # Created during setup()
    self.microscopy_backend: CytationMicroscopyBackend

  # ─── Lifecycle ───────────────────────────────────────────────────────

  @dataclass
  class SetupParams(BackendParams):
    """Cytation-specific parameters for ``setup``.

    Args:
      use_cam: If True, initialize the Aravis camera during setup.
    """

    use_cam: bool = False

  async def setup(self, backend_params: Optional[BackendParams] = None) -> None:
    """Set up serial connection and camera."""
    if not isinstance(backend_params, CytationAravisDriver.SetupParams):
      backend_params = CytationAravisDriver.SetupParams()

    logger.info("CytationAravisDriver setting up")

    await super().setup()

    if backend_params.use_cam:
      serial = self._camera_serial or (
        self.imaging_config.camera_serial_number if self.imaging_config else None
      )
      await self.camera.setup(serial_number=serial)
      logger.info("Camera connected: %s", self.camera.get_device_info())

    # Create microscopy backend — it owns all imaging state and logic.
    # Filter/objective loading happens in microscopy._on_setup().
    self.microscopy_backend = CytationMicroscopyBackend(driver=self)

    logger.info("CytationAravisDriver setup complete")

  async def stop(self) -> None:
    """Disconnect camera and serial."""
    try:
      await self.camera.stop()
    except Exception:
      logger.exception("Error stopping camera")
    await super().stop()
    logger.info("CytationAravisDriver stopped")
