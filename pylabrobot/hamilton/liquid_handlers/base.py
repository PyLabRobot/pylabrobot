import logging
from abc import ABCMeta, abstractmethod
from typing import (
  Any,
  List,
  Optional,
  Sequence,
  Tuple,
  Union,
)

from pylabrobot.capabilities.liquid_handling.standard import Aspiration, Dispense, Pickup, TipDrop
from pylabrobot.hamilton.usb.driver import HamiltonUSBDriver
from pylabrobot.resources import TipSpot
from pylabrobot.resources.hamilton import (
  HamiltonTip,
  TipPickupMethod,
  TipSize,
)

PipettingOp = Union[Pickup, TipDrop, Aspiration, Dispense]

logger = logging.getLogger(__name__)


class HamiltonLiquidHandler(HamiltonUSBDriver, metaclass=ABCMeta):
  """
  Abstract base class for Hamilton liquid handling robot backends.
  """

  @abstractmethod
  def __init__(
    self,
    id_product: int,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 30,
    write_timeout: int = 30,
  ):
    super().__init__(
      id_product=id_product,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )
    self._tth2tti: dict[int, int] = {}  # hash to tip type index

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """The number of pipette channels present on the robot."""

  async def stop(self):
    self._tth2tti.clear()
    await super().stop()

  deck: Any  # Set by subclasses; used for coordinate calculations.

  def _ops_to_fw_positions(
    self, ops: Sequence[PipettingOp], use_channels: List[int]
  ) -> Tuple[List[int], List[int], List[bool]]:
    """use_channels is a list of channels to use. STAR expects this in one-hot encoding. This is
    method converts that, and creates a matching list of x and y positions."""
    if use_channels != sorted(use_channels):
      raise ValueError("Channels must be sorted.")

    x_positions: List[int] = []
    y_positions: List[int] = []
    channels_involved: List[bool] = []
    for i, channel in enumerate(use_channels):
      while channel > len(channels_involved):
        channels_involved.append(False)
        x_positions.append(0)
        y_positions.append(0)
      channels_involved.append(True)

      x_pos = ops[i].resource.get_location_wrt(self.deck, x="c", y="c", z="b").x + ops[i].offset.x
      x_positions.append(round(x_pos * 10))

      y_pos = ops[i].resource.get_location_wrt(self.deck, x="c", y="c", z="b").y + ops[i].offset.y
      y_positions.append(round(y_pos * 10))

    # check that the minimum d between any two y positions is >9mm
    # O(n^2) search is not great but this is most readable, and the max size is 16, so it's fine.
    for channel_idx1, (x1, y1) in enumerate(zip(x_positions, y_positions)):
      for channel_idx2, (x2, y2) in enumerate(zip(x_positions, y_positions)):
        if channel_idx1 == channel_idx2:
          continue
        if not channels_involved[channel_idx1] or not channels_involved[channel_idx2]:
          continue
        if x1 != x2:  # channels not on the same column -> will be two operations on the machine
          continue
        if y1 != y2 and abs(y1 - y2) < 90:
          raise ValueError(
            f"Minimum distance between two y positions is <9mm: {y1}, {y2}"
            f" (channel {channel_idx1} and {channel_idx2})"
          )

    if len(ops) > self.num_channels:
      raise ValueError(f"Too many channels specified: {len(ops)} > {self.num_channels}")

    if len(x_positions) < self.num_channels:
      # We do want to have a trailing zero on x_positions, y_positions, and channels_involved, for
      # some reason, if the length < 8.
      x_positions = x_positions + [0]
      y_positions = y_positions + [0]
      channels_involved = channels_involved + [False]

    return x_positions, y_positions, channels_involved

  @abstractmethod
  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ):
    """Tip/needle definition in firmware."""

  async def request_or_assign_tip_type_index(self, tip: HamiltonTip) -> int:
    """Get a tip type table index for the tip.

    If the tip has previously been defined, used that index. Otherwise, define a new tip type.
    """

    tip_type_hash = hash(tip)

    if tip_type_hash not in self._tth2tti:
      ttti = len(self._tth2tti) + 1
      if ttti > 99:
        raise ValueError("Too many tip types defined.")

      await self.define_tip_needle(
        tip_type_table_index=ttti,
        has_filter=tip.has_filter,
        tip_length=round((tip.total_tip_length - tip.fitting_depth) * 10),  # in 0.1mm
        maximum_tip_volume=round(tip.maximal_volume * 10),  # in 0.1ul
        tip_size=tip.tip_size,
        pickup_method=tip.pickup_method,
      )
      self._tth2tti[tip_type_hash] = ttti

    return self._tth2tti[tip_type_hash]

  def _get_hamilton_tip(self, tip_spots: List[TipSpot]) -> HamiltonTip:
    """Get the single tip type for all tip spots. If it does not exist or is not a HamiltonTip,
    raise an error."""
    tips = set(tip_spot.get_tip() for tip_spot in tip_spots)
    if len(tips) > 1:
      raise ValueError("Cannot mix tips with different tip types.")
    if len(tips) == 0:
      raise ValueError("No tips specified.")
    tip = tips.pop()
    if not isinstance(tip, HamiltonTip):
      raise ValueError(f"Tip {tip} is not a HamiltonTip.")
    return tip
