"""Which number identifies each command, and which command runs each step type."""

from __future__ import annotations

import enum

from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_interface import Step
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)


class CommandNumber(enum.IntEnum):
  """The number a command carries in its header."""

  # Identity and liveness.
  GET_SERIAL_NUMBER = 256
  PING = 115
  GET_BASECODE_VERSION = 160
  GET_PROTOCOL_STATUS = 146

  # Diagnostics and hardware configuration.
  RESET_INSTRUMENT = 112
  RUN_SELF_CHECK = 149
  HOME_VERIFY_MOTORS = 200
  GET_SENSOR_ENABLED = 210
  SET_SENSOR_ENABLED = 211
  SET_WASHER_MANIFOLD_INSTALLED = 217
  GET_PLATE_RESTRICTION = 258
  GET_CARRIER_TYPE = 266

  # Peristaltic cassettes and pump state.
  GET_CASSETTE_MODE = 223
  GET_SELECTED_PERI_STATE = 236
  GET_SELECTED_PERI_CASSETTE_TYPE = 264
  SET_SELECTED_PERI_CASSETTE_TYPE = 265
  GET_EXT_PERI_CASSETTE_HEAD = 380
  SET_EXT_PERI_CASSETTE_HEAD = 381

  # Run control.
  EXIT_PROTOCOL = 140
  INIT_PROTOCOL = 141
  ABORT_STEP = 137
  PAUSE_STEP = 138
  RESUME_STEP = 139

  # Fitted options.
  GET_SYRINGE_MANIFOLD_INSTALLED = 187
  GET_EXT_VALVE_MODULE_INSTALLED = 191
  GET_VACUUM_FILTRATION_INSTALLED = 193
  GET_WASHER_MANIFOLD_INSTALLED = 216
  GET_ULTRASONIC_CLEANER_INSTALLED = 242
  GET_CELL_WASHING_INSTALLED = 244
  GET_SYRINGE_BOX_INFO = 246
  GET_SELECTED_PERI_INSTALLED = 260
  GET_Y_AXIS_INSTALLED = 295
  GET_IS_PERI_HALF_UL_SUPPORTED = 340
  IS_STRIP_WASHER_BOX_CONNECTED = 354
  GET_STRIP_WASHER_MANIFOLD_TYPE = 355
  GET_STRIP_WASHER_HW_INSTALLED = 363
  GET_SINGLE_WELL_DISPENSER_INSTALLED = 369
  GET_FLUID_TRACKING_ENABLED = 382
  GET_WHICH_BASECODE_IS_INSTALLED = 398

  # Step execution.
  PERI_DISPENSE = 143
  PERI_DISPENSE_RANDOM_ACCESS = 375
  PERI_PRIME = 144
  PERI_PURGE = 145
  SYRINGE_DISPENSE = 161
  SYRINGE_PRIME = 162
  SHAKE_SOAK = 163
  MANIFOLD_WASH = 164
  MANIFOLD_ASPIRATE = 165
  MANIFOLD_DISPENSE = 166
  MANIFOLD_PRIME = 167
  MANIFOLD_AUTO_CLEAN = 168
  PERI_WASH_ASPIRATE = 178
  PERI_WASH_DISPENSE = 179
  WASH_1536 = 177
  STRIP_WASH = 350
  STRIP_ASPIRATE = 351
  STRIP_DISPENSE = 352
  STRIP_PRIME = 353

  # On-board protocols and file transfer.
  GET_FLASH_PROGRAM_COUNT = 321
  IS_FILE_TRANSFER_SUPPORTED = 320
  GET_FILE_BEGIN = 323
  GET_FILE_END = 325
  SET_FILE_BEGIN = 326
  SET_FILE_END = 328


STEP_TYPE_TO_COMMAND: dict[StepType, CommandNumber] = {
  StepType.PERI_DISPENSE: CommandNumber.PERI_DISPENSE,
  StepType.PERI_PRIME: CommandNumber.PERI_PRIME,
  StepType.PERI_PURGE: CommandNumber.PERI_PURGE,
  StepType.SYRINGE_DISPENSE: CommandNumber.SYRINGE_DISPENSE,
  StepType.SYRINGE_PRIME: CommandNumber.SYRINGE_PRIME,
  StepType.SHAKE_SOAK: CommandNumber.SHAKE_SOAK,
  StepType.MANIFOLD_WASH: CommandNumber.MANIFOLD_WASH,
  StepType.MANIFOLD_ASPIRATE: CommandNumber.MANIFOLD_ASPIRATE,
  StepType.MANIFOLD_DISPENSE: CommandNumber.MANIFOLD_DISPENSE,
  StepType.MANIFOLD_PRIME: CommandNumber.MANIFOLD_PRIME,
  StepType.MANIFOLD_AUTO_CLEAN: CommandNumber.MANIFOLD_AUTO_CLEAN,
  StepType.WASH_1536: CommandNumber.WASH_1536,
  StepType.STRIP_WASH: CommandNumber.STRIP_WASH,
  StepType.STRIP_ASPIRATE: CommandNumber.STRIP_ASPIRATE,
  StepType.STRIP_DISPENSE: CommandNumber.STRIP_DISPENSE,
  StepType.STRIP_PRIME: CommandNumber.STRIP_PRIME,
  StepType.PERI_WASH_ASPIRATE: CommandNumber.PERI_WASH_ASPIRATE,
  StepType.PERI_WASH_DISPENSE: CommandNumber.PERI_WASH_DISPENSE,
}
"""Which command runs each step type.

Two step classes share the peristaltic dispense step type; :data:`COMMAND_BY_STEP_CLASS` is what
separates them.
"""


COMMAND_BY_STEP_CLASS: dict[type[Step], CommandNumber] = {
  PeriRandomAccessDispense: CommandNumber.PERI_DISPENSE_RANDOM_ACCESS,
}
"""Which command runs a step class whose type alone does not decide it.

A peristaltic dispense at random access shares its step type with an ordinary one but carries a
different payload and runs as a different command.
"""


def command_for_step(step: Step) -> CommandNumber:
  """Which command runs a particular step.

  Args:
    step: The step to send.

  Returns:
    The command number.

  Raises:
    KeyError: If no command runs this step.
  """
  by_class = COMMAND_BY_STEP_CLASS.get(type(step))
  if by_class is not None:
    return by_class
  return STEP_TYPE_TO_COMMAND[step.step_type]
