"""What the instrument is doing, as a status poll reports it."""

from __future__ import annotations

import enum


class RunState(enum.IntEnum):
  """What the instrument is doing, as reported by a status poll.

  A step command returns as soon as the instrument accepts it, so polling for a state other than
  :attr:`BUSY` is the only way to learn that the step has finished.
  """

  READY = 1
  BUSY = 2
  PAUSED = 3
  ERROR = 4
  STOPPED = 5
