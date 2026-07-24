"""Control for the Nexcelom/Cyntellect Celigo image cytometer.

The :class:`~pylabrobot.celigo.celigo.Celigo` class drives the instrument's FTDI USB-IO
controller board: stage/Z/filter motion, drawer open/close, illumination channels
(brightfield + fluorescence), galvo steering, and the board's digital/analog IO and
barcode reader.

The :mod:`~pylabrobot.celigo.config`, :mod:`~pylabrobot.celigo.coordinates`,
:mod:`~pylabrobot.celigo.transforms` and :mod:`~pylabrobot.celigo.navigation` modules hold
the configuration and plate/well navigation math used by the device.
"""

from pylabrobot.celigo.camera import CameraError, CameraFrame, CeligoCamera
from pylabrobot.celigo.celigo import (
  AcquisitionResult,
  Celigo,
  CeligoError,
  ControllerStatus,
  DiagnosticReport,
  FocusResult,
  ShootingStatus,
)
from pylabrobot.celigo.config import (
  AxisConfig,
  Calibrated2DCubicTransform,
  Calibrated2DPolynomialTransform,
  CalibrationConfig,
  CeligoHardwareConfig,
  ChannelDescriptor,
  ExternalCameraControlConfig,
  GalvoConfig,
  GalvoAxisOpticalCalibration,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
  HardwareDefaultConfig,
  IlluminationChannelConfig,
  load_calibration,
  load_channels,
  load_galvo_calibration,
  load_galvo_calibrations,
  load_galvo_optical_calibration,
  load_hardware_defaults,
  load_illumination_channels,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.navigation import (
  NavigationConfig,
  load_navigation,
  well_to_encoder_ticks,
  well_to_stage_mm,
)

__all__ = [
  "Celigo",
  "CeligoError",
  "AcquisitionResult",
  "AxisConfig",
  "Calibrated2DCubicTransform",
  "Calibrated2DPolynomialTransform",
  "CalibrationConfig",
  "CameraError",
  "CameraFrame",
  "CeligoCamera",
  "CeligoHardwareConfig",
  "ChannelDescriptor",
  "ControllerStatus",
  "CoordinateSystems",
  "DiagnosticReport",
  "ExternalCameraControlConfig",
  "FocusResult",
  "GalvoConfig",
  "GalvoAxisOpticalCalibration",
  "GalvoMagnificationCalibration",
  "GalvoOpticalCalibration",
  "HardwareDefaultConfig",
  "IlluminationChannelConfig",
  "NavigationConfig",
  "ShootingStatus",
  "load_calibration",
  "load_channels",
  "load_galvo_calibration",
  "load_galvo_calibrations",
  "load_galvo_optical_calibration",
  "load_hardware_defaults",
  "load_illumination_channels",
  "load_navigation",
  "well_to_encoder_ticks",
  "well_to_stage_mm",
]
