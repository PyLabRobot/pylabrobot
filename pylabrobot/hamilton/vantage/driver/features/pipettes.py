"""The Vantage's pipetting channels, which the arm addresses all together as `A1PM`."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
  from pylabrobot.hamilton.vantage.driver.master import VantageDriver


@dataclass
class PipettesConfiguration:
  """What the channels accept, and how a value in mm is written on the wire."""

  increments_per_mm: int = 10
  """The wire carries positions and heights in tenths of a millimeter."""

  x_range: Tuple[float, float] = (0.0, 5000.0)
  """Along X, in mm."""

  y_range: Tuple[float, float] = (0.0, 650.0)
  """Along Y, in mm."""

  z_range: Tuple[float, float] = (0.0, 360.0)
  """Along Z, in mm."""

  x_shift_range: Tuple[float, float] = (-100.0, 100.0)
  """How far a channel may shift along X while it discards a tip, in mm."""

  tip_type_range: Tuple[int, int] = (0, 199)
  """The indexes of the machine's tip type table."""

  initialize_read_timeout: int = 300
  """How long to wait for the channels to initialize, in seconds. They go to the waste, discard
  what they carry and initialize their drives, and the arm itself waits that long for them."""

  def to_increments(self, mm: float) -> int:
    """A value in mm as the wire carries it."""
    return round(mm * self.increments_per_mm)


def _check_range(name: str, values: List[float], limits: Tuple[float, float]) -> None:
  """Raise unless every value is within the limits, both ends included."""
  low, high = limits
  for value in values:
    if not low <= value <= high:
      raise ValueError(f"{name} must be from {low} to {high}, but is {value}")


class Pipettes:
  """The pipetting channels.

  Reached as `driver.pipettes`. The channels are addressed together, so every list a command
  takes holds one value for each channel the machine has.
  """

  default_x_position: float = 709.5
  """Where the channels go to discard what they carry when they initialize, along X, in mm."""

  default_y_positions: Dict[int, List[float]] = {
    8: [389.1, 362.3, 335.5, 308.7, 281.9, 255.1, 228.3, 201.6],
  }
  """Where each channel goes to discard, along Y, in mm, by how many channels the machine has. A
  machine whose count is not here must be told."""

  default_begin_of_tip_deposit: float = 245.0
  """Where the channels begin to deposit a tip, along Z, in mm."""

  default_end_of_tip_deposit: float = 123.5
  """Where the channels stop depositing it, along Z, in mm."""

  default_minimal_height_at_command_end: float = 245.0
  """How high the channels end initialization, along Z, in mm."""

  default_tip_type: int = 4
  """The tip type the channels initialize for. The machine wants the longest tip in use, and index 4
  is the 1000 ul tip without a filter."""

  default_x_shift_during_discard: float = 7.0
  """How far the channels shift along X while discarding, to make a tip fall into the waste, in
  mm."""

  def __init__(self, driver: "VantageDriver"):
    self._driver = driver
    self.configuration = PipettesConfiguration()

  @property
  def num_channels(self) -> int:
    """How many channels the machine has. Read from it by `VantageDriver.discover`."""
    return self._driver.num_channels

  async def request_tip_presence(self) -> List[bool]:
    """Which channels carry a tip, from the first.

    Returns:
      One entry for each channel the machine has.
    """
    resp = await self._driver.send_command(module="A1PM", command="QA", fmt={"rt": "[int]"})
    return [bool(present) for present in resp["rt"]]

  async def request_initialization_status(self) -> bool:
    """Whether the channels are initialized."""
    resp = await self._driver.send_command(module="A1PM", command="QW", fmt={"qw": "int"})
    return bool(resp["qw"] == 1)

  async def _unchecked_fw_initialize(
    self,
    x_position: List[int],
    y_position: List[int],
    begin_z_deposit_position: List[int],
    end_z_deposit_position: List[int],
    minimal_height_at_command_end: List[int],
    tip_pattern: List[bool],
    tip_type: List[int],
    x_shift: int,
  ) -> None:
    """Send the initialization as it goes on the wire, with every value in tenths of a mm and
    nothing checked."""
    await self._driver.send_command(
      module="A1PM",
      command="DI",
      read_timeout=self.configuration.initialize_read_timeout,
      xp=x_position,
      yp=y_position,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      te=minimal_height_at_command_end,
      tm=tip_pattern,
      tt=tip_type,
      ts=x_shift,
    )

  def _per_channel(self, name: str, values: Optional[List], default) -> List:
    """What was given for each channel, or the default for every channel.

    Raises:
      ValueError: If a list does not hold one value for each channel.
    """
    if values is None:
      return [default] * self.num_channels
    if len(values) != self.num_channels:
      raise ValueError(
        f"{name} must hold one value for each of the {self.num_channels} channels, "
        f"but holds {len(values)}"
      )
    return list(values)

  def _default_y_positions(self) -> List[float]:
    """Where each channel discards by default.

    Raises:
      ValueError: If the machine's channel count has no default.
    """
    defaults = self.default_y_positions.get(self.num_channels)
    if defaults is None:
      known = ", ".join(str(count) for count in sorted(self.default_y_positions))
      raise ValueError(
        f"there is no default discard position along Y for {self.num_channels} channels (only "
        f"for {known}): pass `y_positions`, one for each channel, in mm"
      )
    return list(defaults)

  async def initialize(
    self,
    x_position: Optional[List[float]] = None,
    y_positions: Optional[List[float]] = None,
    begin_of_tip_deposit: Optional[List[float]] = None,
    end_of_tip_deposit: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[List[int]] = None,
    x_shift_during_discard: Optional[float] = None,
  ) -> None:
    """Initialize the channels. They go to the waste, discard what they carry, and initialize
    their drives. Moves the channels.

    Every argument but the shift holds one value for each channel, in mm. What is not given is
    taken from the `default_*` attributes.

    Args:
      x_position: where each channel discards, along X.
      y_positions: where each channel discards, along Y. Without it the machine's channel count
        needs a default.
      begin_of_tip_deposit: where each channel begins to deposit its tip, along Z.
      end_of_tip_deposit: where each channel stops depositing it, along Z.
      minimal_height_at_command_end: how high each channel ends, along Z.
      tip_pattern: which channels are initialized. All of them by default.
      tip_type: the tip type of each channel, an index of the machine's tip type table.
      x_shift_during_discard: how far the channels shift along X while they discard, in mm.

    Raises:
      ValueError: If a list does not hold one value for each channel, a value is out of range, or
        `y_positions` is needed and not given. Nothing is sent then.
    """
    config = self.configuration
    x = self._per_channel("x_position", x_position, self.default_x_position)
    y = (
      self._per_channel("y_positions", y_positions, 0.0)
      if y_positions is not None
      else self._default_y_positions()
    )
    begin = self._per_channel(
      "begin_of_tip_deposit", begin_of_tip_deposit, self.default_begin_of_tip_deposit
    )
    end = self._per_channel(
      "end_of_tip_deposit", end_of_tip_deposit, self.default_end_of_tip_deposit
    )
    height = self._per_channel(
      "minimal_height_at_command_end",
      minimal_height_at_command_end,
      self.default_minimal_height_at_command_end,
    )
    pattern = self._per_channel("tip_pattern", tip_pattern, True)
    types = self._per_channel("tip_type", tip_type, self.default_tip_type)
    shift = (
      self.default_x_shift_during_discard
      if x_shift_during_discard is None
      else x_shift_during_discard
    )

    _check_range("x_position", x, config.x_range)
    _check_range("y_positions", y, config.y_range)
    _check_range("begin_of_tip_deposit", begin, config.z_range)
    _check_range("end_of_tip_deposit", end, config.z_range)
    _check_range("minimal_height_at_command_end", height, config.z_range)
    _check_range("tip_type", types, config.tip_type_range)  # type: ignore[arg-type]
    _check_range("x_shift_during_discard", [shift], config.x_shift_range)

    await self._unchecked_fw_initialize(
      x_position=[config.to_increments(value) for value in x],
      y_position=[config.to_increments(value) for value in y],
      begin_z_deposit_position=[config.to_increments(value) for value in begin],
      end_z_deposit_position=[config.to_increments(value) for value in end],
      minimal_height_at_command_end=[config.to_increments(value) for value in height],
      tip_pattern=[bool(value) for value in pattern],
      tip_type=[int(value) for value in types],
      x_shift=config.to_increments(shift),
    )
