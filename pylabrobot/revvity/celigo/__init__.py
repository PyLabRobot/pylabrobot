"""Control for the Revvity Celigo image cytometer.

The :class:`~pylabrobot.revvity.celigo.celigo.Celigo` class drives the instrument's FTDI USB-IO
controller board: stage/Z/filter motion, drawer open/close, illumination channels
(brightfield + fluorescence), galvo steering, and the board's digital/analog IO and
barcode reader. Its :class:`~pylabrobot.revvity.celigo.laser.Laser` component owns laser
communication and firing operations, while :class:`~pylabrobot.revvity.celigo.galvo.Galvo`
owns galvo positioning and calibration.

The :mod:`~pylabrobot.revvity.celigo.config`, :mod:`~pylabrobot.revvity.celigo.coordinates`, and
:mod:`~pylabrobot.revvity.celigo.navigation` modules hold the configuration and plate/well
navigation math used by the device.
"""

from pylabrobot.revvity.celigo.camera import CameraError, CameraFrame, CeligoCamera
from pylabrobot.revvity.celigo.celigo import (
  AcquisitionResult,
  Celigo,
  ControllerInfo,
  ControllerStatus,
  DetectedMotorAddress,
  FocusResult,
  SelfTestReport,
)
from pylabrobot.revvity.celigo.config import (
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
from pylabrobot.revvity.celigo.coordinates import CoordinateSystems
from pylabrobot.revvity.celigo.errors import CeligoError
from pylabrobot.revvity.celigo.galvo import Galvo, GalvoControllerStatus
from pylabrobot.revvity.celigo.laser import Laser
from pylabrobot.revvity.celigo.motion import (
  Axis,
  FilterWheel,
  LinearAxis,
  MagnificationChanger,
  MotorController,
  StepperMotor,
)
from pylabrobot.revvity.celigo.navigation import well_to_sample_mm, well_to_stage_mm
from pylabrobot.revvity.celigo.scan import (
  AutofocusMethod,
  BlockShape,
  Capture,
  CoordinateMM,
  FrameResult,
  PlannedFrame,
  ScanBlock,
  ScanEstimateModel,
  ScanPlan,
  ScanPosition,
  ScanRegion,
  ScanResult,
  ScanSpec,
)
__all__ = [
  "AcquisitionResult",
  "AnalogInputConfig",
  "AutofocusMethod",
  "Axis",
  "AxisConfig",
  "BlockShape",
  "Calibrated2DPolynomialTransform",
  "CalibrationConfig",
  "CameraError",
  "CameraFrame",
  "Capture",
  "Celigo",
  "CeligoCamera",
  "CeligoConfig",
  "CeligoError",
  "CeligoHardwareConfig",
  "ChannelDescriptor",
  "CoordinateMM",
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
  "FrameResult",
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
  "PlannedFrame",
  "ScanBlock",
  "ScanEstimateModel",
  "ScanPlan",
  "ScanPosition",
  "ScanRegion",
  "ScanResult",
  "ScanSpec",
  "SelfTestReport",
  "StepperMotor",
  "load_channel_descriptors",
  "load_galvo_calibrations",
  "load_galvo_optical_calibration",
  "load_illumination_channels",
  "well_to_sample_mm",
  "well_to_stage_mm",
]
