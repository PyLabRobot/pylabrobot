"""The field checks every step's rules are built from.

Each check answers with a rejection or with None, so a step's rules read as a chain: the first
check that fails is the answer, and the rest are not run. The codes are the instrument's own.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_manifold import SyringeManifold
from pylabrobot.agilent.biotek.lhc.enums.steps.buffer import BUFFERS, Buffer
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import CassetteType
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_flow_rate import PeriFlowRate
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe import Syringe
from pylabrobot.agilent.biotek.lhc.enums.steps.travel_rate import TravelRate
from pylabrobot.agilent.biotek.lhc.enums.steps.wash_format import WashFormat
from pylabrobot.agilent.biotek.lhc.protocols.validation.report import Rejection

VOLUME = 24598
FLOW_RATE = 24599
COUNT = 24600
COLUMN_SELECTION = 24610
ROW_SELECTION = 24580
CASSETTE = 24624
SYRINGE = 24688
PRIME_CYCLES = 24689
PUMP_DELAY = 24690
OFFSET_X = 24691
OFFSET_Y = 24692
OFFSET_Z = 24693
DURATION_SECONDS = 24704
DURATION_MINUTES_SECONDS = 24709
DURATION_HOURS_MINUTES = 24710
ASPIRATE_DELAY = 24721
TRAVEL_RATE = 24722
BUFFER = 24720
WASH_CYCLES = 24723
WASH_FORMAT = 24724
FRACTIONAL_VOLUME = 24727

MAX_DURATION = 300
MAX_PUMP_DELAY = 5000
MAX_PRIME_VOLUME = 8000
MAX_STRIP_DISPENSE_VOLUME = 30000
HALF_MICROLITRE = 50000
"""The volume that means half a microlitre. It is compared for exactly, before any range."""

OFFSET_X_RANGE = (-125, 125)
OFFSET_Y_RANGE = (-40, 40)
OFFSET_Z_RANGE = (1, 1500)
OFFSET_X_MANIFOLD_RANGE = (-60, 60)
OFFSET_Y_405TS_RANGE = (-60, 60)
OFFSET_Y_OTHER_RANGE = (-40, 40)
OFFSET_Z_405TS_RANGE = (1, 255)
OFFSET_Z_OTHER_RANGE = (1, 210)
"""How far a step may reach. These are the instrument's own limits, not the plate's."""


def buffer(value: Buffer, prefix: str = "") -> Rejection | None:
  """Check a buffer inlet.

  Args:
    value: The inlet the step names.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if value in BUFFERS:
    return None
  return Rejection(BUFFER, prefix + "Buffer must be A, B, C, or D")


def duration(value: int, prefix: str = "") -> Rejection | None:
  """Check a duration in seconds.

  Args:
    value: The duration in seconds.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if 1 <= value <= MAX_DURATION:
    return None
  return Rejection(DURATION_SECONDS, f"{prefix}Duration must be 1..{MAX_DURATION}")


def peri_flow_rate(value: PeriFlowRate, prefix: str = "") -> Rejection | None:
  """Check a peristaltic flow rate.

  Args:
    value: The rate the step names.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if value in ("Low", "Medium", "High"):
    return None
  return Rejection(FLOW_RATE, prefix + "Flow Rate must be Low, Medium, or High")


def flow_rate(value: int, minimum: int, maximum: int, prefix: str = "") -> Rejection | None:
  """Check a numeric flow rate.

  Args:
    value: The rate the step names.
    minimum: The slowest rate allowed.
    maximum: The fastest rate allowed.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if minimum <= value <= maximum:
    return None
  return Rejection(FLOW_RATE, f"{prefix}Flow Rate must be {minimum}..{maximum}")


def cassette_type(value: CassetteType | None, peri_wash_allowed: bool = False) -> Rejection | None:
  """Check the cassette a step requires.

  The two catch-all requirements and no requirement at all are rejected: a step has to say what it
  needs. The peristaltic wash cassettes are only allowed to a step that primes or purges one.

  Args:
    value: The cassette the step requires.
    peri_wash_allowed: Whether the peristaltic wash cassettes are allowed here.

  Returns:
    A rejection, or None.
  """
  allowed = {"Any", "1uL", "5uL", "10uL"}
  if peri_wash_allowed:
    allowed |= {"PeriWash aspirate", "PeriWash dispense"}
  if value in allowed:
    return None
  return Rejection(CASSETTE)


def syringe(value: Syringe, manifold: SyringeManifold, prefix: str = "") -> Rejection | None:
  """Check which syringe a step drives against the fitted manifold.

  Four manifolds can drive both syringes at once; on any other a step drives one of them.

  Args:
    value: The syringe the step drives.
    manifold: The fitted syringe manifold.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  both_allowed = manifold in (
    SyringeManifold.TUBE_8,
    SyringeManifold.TUBE_16_7,
    SyringeManifold.TUBE_32_SMALL_BORE,
    SyringeManifold.TUBE_32_LARGE_BORE,
  )
  if value in ("A", "B") or (value == "Both" and both_allowed):
    return None
  text = "must use syringe A, B or Both" if both_allowed else "must use syringe A or B"
  return Rejection(SYRINGE, prefix + text)


def volume(value: int, minimum: int, maximum: int, prefix: str = "") -> Rejection | None:
  """Check a volume against a range.

  Args:
    value: The volume in µL.
    minimum: The smallest volume allowed.
    maximum: The largest volume allowed.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if minimum <= value <= maximum:
    return None
  return Rejection(VOLUME, f"{prefix}Volume must be {minimum}..{maximum}")


def volume_or_half_microlitre(
  value: int, minimum: int, maximum: int, half_allowed: bool, prefix: str = ""
) -> Rejection | None:
  """Check a volume that may instead be half a microlitre.

  Half a microlitre is a value of its own rather than a point on the range, so it is either
  allowed or it is the rejection.

  Args:
    value: The volume in µL.
    minimum: The smallest volume allowed.
    maximum: The largest volume allowed.
    half_allowed: Whether this instrument can dispense half a microlitre.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if value == HALF_MICROLITRE:
    if half_allowed:
      return None
    return Rejection(VOLUME, prefix + "Volume of 0.5 µL is not supported by this instrument")
  if minimum <= value <= maximum:
    return None
  message = f"{prefix}Volume must be {minimum}..{maximum}"
  if half_allowed:
    message += " or 0.5 "
  return Rejection(VOLUME, message)


SYRINGE_PRIME_MINIMUM = (80, 160, 400, 480, 640)
"""The smallest syringe prime volume in µL at each flow rate."""

STRIP_PRIME_MINIMUM: dict[StripWasherManifold, tuple[int, ...]] = {
  StripWasherManifold.PLATE_6_WELL: (310, 130, 80, 240, 240, 300, 440, 400, 400, 800, 880),
  StripWasherManifold.PLATE_12_WELL: (315, 180, 180, 180, 240, 240, 300, 420, 480, 780, 870),
  StripWasherManifold.PLATE_24_WELL: (320, 160, 80, 240, 160, 320, 440, 400, 480, 800, 880),
  StripWasherManifold.PLATE_48_WELL: (360, 360, 360, 360, 360, 360, 420, 420, 600, 630, 900),
}
"""The smallest strip washer prime volume in µL at each flow rate, per fitted manifold."""

_STRIP_PRIME_DEFAULT = (320, 160, 80, 240, 160, 320, 440, 400, 480, 800, 880)

STRIP_DISPENSE_MINIMUM: dict[StripWasherManifold, tuple[int, ...]] = {
  StripWasherManifold.PLATE_6_WELL: (155, 65, 65, 120, 120, 150, 225, 225, 225, 400, 450),
  StripWasherManifold.PLATE_12_WELL: (105, 60, 60, 60, 80, 80, 100, 140, 160, 260, 290),
  StripWasherManifold.PLATE_24_WELL: (80, 40, 40, 60, 60, 80, 110, 105, 120, 200, 220),
  StripWasherManifold.PLATE_48_WELL: (60, 60, 60, 60, 60, 60, 70, 70, 100, 105, 150),
}
"""The smallest strip washer dispense volume in µL per well at each flow rate, per manifold."""

_STRIP_DISPENSE_DEFAULT = (40, 20, 20, 30, 20, 40, 55, 50, 60, 100, 110)

SYRINGE_MINIMUM: dict[int, tuple[float, ...]] = {
  6: (40, 80, 200, 240, 320),
  12: (30, 60, 140, 160, 220),
  24: (20, 40, 100, 120, 160),
  48: (40, 80, 200, 240, 320),
  96: (10, 20, 50, 60, 80),
  384: (5, 10, 25, 30, 40),
  1536: (3, 3, 3, 3, 3),
}
"""The smallest syringe dispense volume in µL at each flow rate, per well count."""

SYRINGE_MINIMUM_384_EIGHT_TUBE = (10, 20, 50, 60, 80)
"""The same for 384 wells through the eight-tube manifold, whose wider tubes each give more."""


def syringe_prime_volume(value: int, rate: int, prefix: str = "") -> Rejection | None:
  """Check a syringe prime volume, whose floor moves with the flow rate.

  Args:
    value: The volume in µL.
    rate: The flow rate the step primes at.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  minimum = SYRINGE_PRIME_MINIMUM[rate - 1]
  if minimum <= value <= MAX_PRIME_VOLUME:
    return None
  return Rejection(
    VOLUME,
    f"{prefix}Volume for the specified\r\nFlow Rate must be {minimum}..{MAX_PRIME_VOLUME}",
  )


def strip_prime_volume(
  value: int, rate: int, manifold: StripWasherManifold, prefix: str = ""
) -> Rejection | None:
  """Check a strip washer prime volume, whose floor moves with the rate and the manifold.

  Args:
    value: The volume in µL.
    rate: The flow rate the step primes at.
    manifold: The fitted strip washer manifold.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  minimum = STRIP_PRIME_MINIMUM.get(manifold, _STRIP_PRIME_DEFAULT)[rate - 1]
  if minimum <= value <= MAX_PRIME_VOLUME:
    return None
  return Rejection(
    VOLUME,
    f"{prefix}Volume for the specified Flow \r\nRate and Manifold type must be "
    f"{minimum}..{MAX_PRIME_VOLUME}",
  )


def strip_dispense_volume(
  value: int, rate: int, manifold: StripWasherManifold, prefix: str = ""
) -> Rejection | None:
  """Check a strip washer dispense volume, whose floor moves with the rate and the manifold.

  Args:
    value: The volume in µL.
    rate: The flow rate the step dispenses at.
    manifold: The fitted strip washer manifold.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  minimum = STRIP_DISPENSE_MINIMUM.get(manifold, _STRIP_DISPENSE_DEFAULT)[rate - 1]
  if minimum <= value <= MAX_STRIP_DISPENSE_VOLUME:
    return None
  return Rejection(
    VOLUME,
    f"{prefix}Volume for the specified Flow \r\nRate and Manifold type must be "
    f"{minimum}..{MAX_STRIP_DISPENSE_VOLUME}",
  )


def syringe_volume(
  value: float,
  rate: int,
  manifold: SyringeManifold,
  wells: int,
  maximum: int,
  prefix: str = "",
) -> Rejection | None:
  """Check a syringe dispense volume against the plate and the flow rate.

  A plate whose well count is not in the table has no floor at all. A fractional volume through the
  sixteen-tube manifold is rejected on its own account.

  Args:
    value: The volume in µL.
    rate: The flow rate the step dispenses at.
    manifold: The fitted syringe manifold.
    wells: How many wells the plate has.
    maximum: The largest volume allowed, which depends on what the instrument has fitted.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  table = SYRINGE_MINIMUM.get(wells, (0.0, 0.0, 0.0, 0.0, 0.0))
  if wells == 384 and manifold is SyringeManifold.TUBE_8:
    table = SYRINGE_MINIMUM_384_EIGHT_TUBE
  minimum = table[rate - 1]
  if value < minimum or value > maximum:
    return Rejection(
      VOLUME,
      f"{prefix}Volume for the specified Flow Rate\r\nand Plate Type must be {minimum}..{maximum}",
    )
  if value != int(value) and manifold is SyringeManifold.TUBE_16:
    return Rejection(FRACTIONAL_VOLUME)
  return None


def pump_delay(value: int, prefix: str = "") -> Rejection | None:
  """Check a pump delay.

  Args:
    value: The delay in ms.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if 0 <= value <= MAX_PUMP_DELAY:
    return None
  return Rejection(PUMP_DELAY, f"{prefix}Pump Delay must be 0..{MAX_PUMP_DELAY}")


def offset_x(value: int, minimum: int, maximum: int, prefix: str = "") -> Rejection | None:
  """Check an offset across the plate.

  Args:
    value: The offset.
    minimum: The furthest allowed one way.
    maximum: The furthest allowed the other.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if minimum <= value <= maximum:
    return None
  return Rejection(OFFSET_X, f"{prefix}X-axis offset must be {minimum}..{maximum}")


def offset_y(value: int, minimum: int, maximum: int, prefix: str = "") -> Rejection | None:
  """Check an offset along the plate.

  Args:
    value: The offset.
    minimum: The furthest allowed one way.
    maximum: The furthest allowed the other.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if minimum <= value <= maximum:
    return None
  return Rejection(OFFSET_Y, f"{prefix}Y-axis offset must be {minimum}..{maximum}")


def offset_z(value: int, minimum: int, maximum: int, prefix: str = "") -> Rejection | None:
  """Check a depth offset.

  Args:
    value: The offset.
    minimum: The shallowest allowed.
    maximum: The deepest allowed.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if minimum <= value <= maximum:
    return None
  return Rejection(OFFSET_Z, f"{prefix}Z-axis offset must be {minimum}..{maximum}")


def aspirate_delay(value: int, vacuum: bool, prefix: str = "") -> Rejection | None:
  """Check how long an aspirate keeps going.

  The delay is a filtration time in seconds when the vacuum does the work, and a delay in
  milliseconds when the tips do.

  Args:
    value: The delay.
    vacuum: Whether the step filters under vacuum.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if vacuum:
    if 5 <= value <= 999:
      return None
    return Rejection(ASPIRATE_DELAY, prefix + "Delay must be 5..999 sec")
  if 0 <= value <= MAX_PUMP_DELAY:
    return None
  return Rejection(ASPIRATE_DELAY, prefix + "Delay must be 0..5000 msec")


def travel_rate(
  value: TravelRate, allowed: tuple[TravelRate, ...], prefix: str = ""
) -> Rejection | None:
  """Check a travel rate against the ones this manifold offers.

  Args:
    value: The rate the step names.
    allowed: The rates the manifold offers.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if value in allowed:
    return None
  return Rejection(TRAVEL_RATE, prefix + "Travel Rate is invalid")


def column_selection(values: list[int], prefix: str = "") -> Rejection | None:
  """Check a column selection.

  Selecting no column at all is allowed, and washes nothing.

  Args:
    values: The selection, one entry per column.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if all(value in (0, 1) for value in values):
    return None
  return Rejection(
    COLUMN_SELECTION, prefix + "Column selection settings are invalid; data is corrupt"
  )


def row_selection(values: list[int], sections: int, prefix: str = "") -> Rejection | None:
  """Check a row selection.

  Unlike a column selection this one has to select something, and only the sections the plate
  actually has are looked at.

  Args:
    values: The selection, one entry per row.
    sections: How many row sections the plate has.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if all(value in (0, 1) for value in values) and any(values[:sections]):
    return None
  return Rejection(ROW_SELECTION, prefix + "Row selection values are invalid; data is corrupt")


def count(value: int, prefix: str = "") -> Rejection | None:
  """Check how many times a step pre-dispenses.

  Args:
    value: The count.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if 1 <= value <= 9:
    return None
  return Rejection(COUNT, prefix + "Count must be 1..9")


def prime_cycles(value: int) -> Rejection | None:
  """Check how many prime cycles a step runs.

  Args:
    value: The number of cycles.

  Returns:
    A rejection, or None.
  """
  if 1 <= value <= 99:
    return None
  return Rejection(PRIME_CYCLES, "Number of prime cycles  must be 1..99")


def wash_cycles(value: int, prefix: str = "") -> Rejection | None:
  """Check how many wash cycles a step runs.

  Args:
    value: The number of cycles.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if 1 <= value <= 250:
    return None
  return Rejection(WASH_CYCLES, prefix + "Number of wash cycles  must be 1..250")


def wash_format(value: WashFormat, prefix: str = "") -> Rejection | None:
  """Check what a wash covers.

  Args:
    value: The format the step names.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if value in ("Plate", "Strip", "Sector"):
    return None
  return Rejection(WASH_FORMAT, prefix + "Invalid Wash Format")


def short_duration(value: int, prefix: str = "") -> Rejection | None:
  """Check a duration stored as minutes and seconds.

  Args:
    value: The duration in seconds.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  if 1 <= value <= 59 * 60 + 59:
    return None
  return Rejection(DURATION_MINUTES_SECONDS, prefix + "Duration must be 00:01..59:59")


def long_duration(value: int, short_range: bool, prefix: str = "") -> Rejection | None:
  """Check a duration stored as hours and minutes.

  Args:
    value: The duration in seconds.
    short_range: Whether this duration is limited to four hours rather than a day.
    prefix: What to call the step in the message.

  Returns:
    A rejection, or None.
  """
  limit = 3 if short_range else 23
  minutes = value // 60
  if 1 <= minutes <= limit * 60 + 59:
    return None
  return Rejection(DURATION_HOURS_MINUTES, prefix + f"Duration must be 00:01..{limit:02d}:59")
