"""What a protocol entry does, beyond operating the instrument."""

from __future__ import annotations

import enum


class StepAction(enum.IntEnum):
  """A control-flow or plate-handling action attached to a protocol step.

  These sit alongside the liquid-handling operation in :class:`StepType`: they sequence a run
  rather than move liquid, covering delays, loops, remarks, and plate transfers to and from a
  stacker.
  """

  UNDEFINED = 0
  END_OF_LIST = 1
  CUSTOM = 2
  DELAY = 3
  REMARK = 4
  LOOP_START = 5
  LOOP_END = 6
  DELIVER_PLATE = 7
  NTH_PLATE = 8
  NTH_PLATE_END = 9
  RETRIEVE_PLATE = 10
  RESTACK = 11
  DELAY_START_TIMER = 12
