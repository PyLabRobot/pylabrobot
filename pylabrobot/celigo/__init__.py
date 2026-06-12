"""Native (Windows-free) driver for the Nexcelom/Cyntellect Celigo image cytometer.

Layers (low -> high):

* :mod:`~pylabrobot.celigo.packets` — FTDI USB-IO wire protocol (framing, opcodes).
* :mod:`~pylabrobot.celigo.transport` — :class:`FtdiTransport` (pyftdi).
* :mod:`~pylabrobot.celigo.controller` — :class:`CeligoController` board commands.
* :mod:`~pylabrobot.celigo.ezstepper` — AllMotion EZStepper motor command strings.
* :mod:`~pylabrobot.celigo.config` — typed loaders for the ``ConfigFiles`` XML.
* :mod:`~pylabrobot.celigo.transforms` — encoder-tick and galvo DAC math.
* :mod:`~pylabrobot.celigo.coordinates` — pixel<->sample-mm<->stage-mm affine frames.
* :mod:`~pylabrobot.celigo.navigation` — plate/well navigation + galvo FOV grid.
"""

from pylabrobot.celigo.config import (
  AxisConfig,
  Calibrated2DCubicTransform,
  CalibrationConfig,
  CeligoHardwareConfig,
  ChannelDescriptor,
  GalvoConfig,
  HardwareDefaultConfig,
  load_calibration,
  load_channels,
  load_galvo_calibration,
  load_hardware_defaults,
)
from pylabrobot.celigo.controller import (
  CeligoController,
  ControllerStatus,
  GalvoType,
)
from pylabrobot.celigo.coordinates import CoordinateSystems
from pylabrobot.celigo.navigation import (
  CORNING_3603_96,
  NavigationConfig,
  PlateGeometry,
  load_navigation,
  well_to_encoder_ticks,
  well_to_stage_mm,
)
from pylabrobot.celigo.packets import IO_CTLR_CMDS, USBIOError
from pylabrobot.celigo.transport import FtdiTransport

__all__ = [
  "AxisConfig",
  "Calibrated2DCubicTransform",
  "CalibrationConfig",
  "CeligoController",
  "CeligoHardwareConfig",
  "ChannelDescriptor",
  "ControllerStatus",
  "CoordinateSystems",
  "CORNING_3603_96",
  "FtdiTransport",
  "GalvoConfig",
  "GalvoType",
  "HardwareDefaultConfig",
  "IO_CTLR_CMDS",
  "NavigationConfig",
  "PlateGeometry",
  "USBIOError",
  "load_calibration",
  "load_channels",
  "load_galvo_calibration",
  "load_hardware_defaults",
  "load_navigation",
  "well_to_encoder_ticks",
  "well_to_stage_mm",
]
