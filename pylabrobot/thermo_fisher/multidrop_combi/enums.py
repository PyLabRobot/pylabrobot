import enum


class CassetteType(enum.IntEnum):
  STANDARD = 0
  SMALL = 1
  USER_DEFINED_1 = 2
  USER_DEFINED_2 = 3


class DispensingOrder(enum.IntEnum):
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
