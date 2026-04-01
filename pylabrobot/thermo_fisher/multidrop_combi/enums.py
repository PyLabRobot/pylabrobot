import enum


class CassetteType(enum.IntEnum):
  STANDARD = 0
  SMALL = 1
  USER_DEFINED_1 = 2
  USER_DEFINED_2 = 3


class DispensingOrder(enum.IntEnum):
  """Controls the order in which the cassette's tips traverse wells on the plate.

  Only applicable to 384-well and 1536-well plates. For 96-well plates, the
  cassette fills all rows in a column simultaneously, so this setting has no effect.

  The cassette has 8 tips (one per row). On 384+ plates, multiple passes are needed
  per column. This setting determines the pass order:

  - ROW_WISE (0): A1 → A2 → A3 → ... → A12, then B1 → B2 → ... (fill across columns
    within each row before moving to the next row).
  - COLUMN_WISE (1): A1 → B1 → ... → H1, then A2 → B2 → ... (fill down rows within
    each column before moving to the next column).

  Per-column volumes (set via SCV) are independent of dispensing order.
  """
  ROW_WISE = 0
  COLUMN_WISE = 1


class PrimeMode(enum.IntEnum):
  STANDARD = 0
  CONTINUOUS = 1
  STOP_CONTINUOUS = 2
  CALIBRATION = 3


class EmptyMode(enum.IntEnum):
  STANDARD = 0
  CONTINUOUS = 1
