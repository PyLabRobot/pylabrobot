"""Control for the Nexcelom/Cyntellect Celigo image cytometer.

The :class:`~pylabrobot.celigo.celigo.Celigo` class drives the instrument's FTDI USB-IO
controller board: stage/Z/filter motion, drawer open/close, illumination channels
(brightfield + fluorescence), galvo steering, and the board's digital/analog IO and
barcode reader. Its :class:`~pylabrobot.celigo.laser.Laser` component owns laser
communication and firing operations, while :class:`~pylabrobot.celigo.galvo.Galvo`
owns galvo positioning and calibration.

The :mod:`~pylabrobot.celigo.config`, :mod:`~pylabrobot.celigo.coordinates`, and
:mod:`~pylabrobot.celigo.navigation` modules hold the configuration and plate/well
navigation math used by the device.
"""

from pylabrobot.celigo.camera import CameraError, CameraFrame, CeligoCamera
from pylabrobot.celigo.celigo import (
  AcquisitionResult,
  Celigo,
  ControllerInfo,
  ControllerStatus,
  DetectedMotorAddress,
  FocusResult,
  SelfTestReport,
)
from pylabrobot.celigo.config import (
  AnalogInputConfig,
  AxisConfig,
  Calibrated2DPolynomialTransform,
  CalibrationConfig,
  CeligoConfig,
  CeligoHardwareConfig,
  ChannelDescriptor,
  DigitalIOConfig,
  ExternalCameraControlConfig,
  FilterMapEntry,
  FilterWheelConfig,
  GalvoAxisOpticalCalibration,
  GalvoConfig,
  GalvoMagnificationCalibration,
  GalvoOpticalCalibration,
  HardwareDefaultConfig,
  IlluminationChannelConfig,
  IOConfig,
  LightingIOConfig,
  LinearAxisConfig,
  NavigationConfig,
  load_channel_descriptors,
  load_galvo_calibrations,
  load_galvo_optical_calibration,
  load_illumination_channels,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.errors import CeligoError
from pylabrobot.celigo.galvo import Galvo, GalvoControllerStatus
from pylabrobot.celigo.laser import Laser
from pylabrobot.celigo.motion import (
  Axis,
  FilterWheel,
  LinearAxis,
  MagnificationChanger,
  MotorController,
  StepperMotor,
)
from pylabrobot.celigo.navigation import well_to_stage_mm

__all__ = [
  "AcquisitionResult",
  "AnalogInputConfig",
  "Axis",
  "AxisConfig",
  "Calibrated2DPolynomialTransform",
  "CalibrationConfig",
  "CameraError",
  "CameraFrame",
  "Celigo",
  "CeligoCamera",
  "CeligoConfig",
  "CeligoError",
  "CeligoHardwareConfig",
  "ChannelDescriptor",
  "ControllerInfo",
  "ControllerStatus",
  "DetectedMotorAddress",
  "CoordinateSystems",
  "DigitalIOConfig",
  "ExternalCameraControlConfig",
  "FilterMapEntry",
  "FocusResult",
  "FilterWheel",
  "FilterWheelConfig",
  "Galvo",
  "GalvoAxisOpticalCalibration",
  "GalvoConfig",
  "GalvoControllerStatus",
  "GalvoMagnificationCalibration",
  "GalvoOpticalCalibration",
  "HardwareDefaultConfig",
  "IlluminationChannelConfig",
  "IOConfig",
  "Laser",
  "LinearAxis",
  "LinearAxisConfig",
  "LightingIOConfig",
  "MagnificationChanger",
  "MotorController",
  "NavigationConfig",
  "SelfTestReport",
  "StepperMotor",
  "load_channel_descriptors",
  "load_galvo_calibrations",
  "load_galvo_optical_calibration",
  "load_illumination_channels",
  "well_to_stage_mm",
]
