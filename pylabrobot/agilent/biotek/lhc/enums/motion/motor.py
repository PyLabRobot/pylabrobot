"""The motors a homing or verification command can address."""

from __future__ import annotations

import enum


class Motor(enum.IntEnum):
  """A motor addressable by the homing and verification commands.

  This is the union over the family; an instrument carries only the motors its fitted hardware
  needs. Firmware-level fault codes number motors per family instead — see
  :class:`~pylabrobot.agilent.biotek.lhc.enums.motion.basecode_motor_406.Basecode406Motor` and its
  siblings.
  """

  CARRIER_X = 0
  CARRIER_Y = 1
  DISPENSE_HEAD_Z = 2
  WASH_HEAD_Z = 3
  SYRINGE_A = 4
  SYRINGE_B = 5
  PERI_PUMP_PRIMARY = 6
  PERI_PUMP_SECONDARY = 7
  LEVEL_SENSE_Y = 8
  WASH_SYRINGE = 9
  WASH_ASPIRATE_HEAD_Z = 10
  SINGLE_WELL_Y = 11
