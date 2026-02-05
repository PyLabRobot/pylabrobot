"""EL406 enumeration types.

This module contains all enumeration types used by the BioTek EL406
plate washer backend.
"""

from __future__ import annotations

import enum


class EL406PlateType(enum.IntEnum):
  """Plate types supported by the EL406."""

  PLATE_1536_WELL = 0
  PLATE_384_WELL = 1
  PLATE_384_PCR = 2
  PLATE_96_WELL = 4
  PLATE_1536_FLANGE = 14


class EL406WasherManifold(enum.IntEnum):
  """Washer manifold types."""

  TUBE_96_DUAL = 0
  TUBE_192 = 1
  TUBE_128 = 2
  TUBE_96_SINGLE = 3
  DEEP_PIN_96 = 4
  NOT_INSTALLED = 255


class EL406SyringeManifold(enum.IntEnum):
  """Syringe manifold types."""

  NOT_INSTALLED = 0
  TUBE_16 = 1
  TUBE_32_LARGE_BORE = 2
  TUBE_32_SMALL_BORE = 3
  TUBE_16_7 = 4
  TUBE_8 = 5
  PLATE_6_WELL = 6
  PLATE_12_WELL = 7
  PLATE_24_WELL = 8
  PLATE_48_WELL = 9


class EL406Sensor(enum.IntEnum):
  """Sensor types for the EL406."""

  VACUUM = 0  # Vacuum sensor
  WASTE = 1  # Waste container sensor
  FLUID = 2  # Fluid level sensor
  FLOW = 3  # Flow sensor
  FILTER_VAC = 4  # Filter vacuum sensor
  PLATE = 5  # Plate presence sensor


class EL406StepType(enum.IntEnum):
  """Step types for EL406 operations."""

  UNDEFINED = 0
  P_DISPENSE = 1  # Peristaltic pump dispense
  P_PRIME = 2  # Peristaltic pump prime
  P_PURGE = 3  # Peristaltic pump purge
  S_DISPENSE = 4  # Syringe dispense
  S_PRIME = 5  # Syringe prime
  M_WASH = 6  # Manifold wash
  M_ASPIRATE = 7  # Manifold aspirate
  M_DISPENSE = 8  # Manifold dispense
  M_PRIME = 9  # Manifold prime
  M_AUTO_CLEAN = 10  # Manifold auto-clean
  SHAKE_SOAK = 11  # Shake/soak


class EL406Motor(enum.IntEnum):
  """Motor types for the EL406."""

  CARRIER_X = 0  # X-axis plate carrier motor
  CARRIER_Y = 1  # Y-axis plate carrier motor
  DISP_HEAD_Z = 2  # Dispense head Z-axis motor
  WASH_HEAD_Z = 3  # Wash head Z-axis motor
  SYRINGE_A = 4  # Syringe pump A motor
  SYRINGE_B = 5  # Syringe pump B motor
  PERI_PUMP_PRIMARY = 6  # Primary peristaltic pump motor
  PERI_PUMP_SECONDARY = 7  # Secondary peristaltic pump motor
  LEVEL_SENSE_Y = 8  # Level sense Y-axis motor
  WASH_SYRINGE = 9  # Wash syringe motor
  WASH_ASP_HEAD_Z = 10  # Wash aspirate head Z-axis motor
  SINGLE_WELL_Y = 11  # Single well Y-axis motor


class EL406MotorHomeType(enum.IntEnum):
  """Motor home types for the EL406."""

  INIT_ALL_MOTORS = 1  # Initialize all motors
  INIT_PERI_PUMP = 2  # Initialize peristaltic pump
  HOME_MOTOR = 3  # Home a specific motor
  HOME_XYZ_MOTORS = 4  # Home all XYZ motors
  VERIFY_MOTOR = 5  # Verify a specific motor position
  VERIFY_XYZ_MOTORS = 6  # Verify all XYZ motor positions
