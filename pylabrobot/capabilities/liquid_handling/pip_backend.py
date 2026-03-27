"""Abstract backend for independent-channel liquid handling."""

from abc import ABCMeta, abstractmethod
from typing import List, Optional

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend
from pylabrobot.resources import Tip

from .standard import Aspiration, Dispense, Pickup, TipDrop


class PIPBackend(CapabilityBackend, metaclass=ABCMeta):
  """Backend for independent-channel liquid handling operations.

  Each operation takes a list of ops (one per channel being used) and a list
  of channel indices specifying which physical channels to use.
  """

  @property
  @abstractmethod
  def num_channels(self) -> int:
    """The number of independent channels available."""

  @abstractmethod
  async def pick_up_tips(
    self,
    ops: List[Pickup],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Pick up tips from the specified tip spots."""

  @abstractmethod
  async def drop_tips(
    self,
    ops: List[TipDrop],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Drop tips to the specified resources."""

  @abstractmethod
  async def aspirate(
    self,
    ops: List[Aspiration],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate liquid from the specified containers."""

  @abstractmethod
  async def dispense(
    self,
    ops: List[Dispense],
    use_channels: List[int],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense liquid to the specified containers."""

  def can_pick_up_tip(self, channel_idx: int, tip: Tip) -> bool:
    """Check if the tip can be picked up by the specified channel.

    Does not consider if a tip is already mounted — just whether the tip is compatible.
    Default returns True; override for hardware-specific constraints.
    """
    return True

  async def request_tip_presence(self) -> List[Optional[bool]]:
    """Request the tip presence status for each channel.

    Returns a list of length `num_channels` where each element is True if a tip is mounted,
    False if not, or None if unknown.

    Default raises NotImplementedError; override if hardware supports tip presence detection.
    """
    raise NotImplementedError()
