"""How the STAR senses a liquid: one enum for its channels and its heads alike."""

import enum


class LLDMode(enum.Enum):
  """How a channel or a head senses the liquid. Numbered as the Prep's and the firmware's `lm`.

  Z touch finds a floor, not a liquid: an aspiration with it expects liquid where there may be
  none, so `aspirate` warns when asked for it. The 96-head has OFF and CAPACITIVE only.
  """

  OFF = 0
  CAPACITIVE = 1
  PRESSURE = 2
  DUAL = 3
  ZTOUCH = 4
