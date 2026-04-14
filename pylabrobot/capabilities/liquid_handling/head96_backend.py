"""Abstract backend for 96-head liquid handling."""

from abc import ABCMeta, abstractmethod
from typing import Optional, Union

from pylabrobot.capabilities.capability import BackendParams, CapabilityBackend

from .standard import (
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)


class Head96Backend(CapabilityBackend, metaclass=ABCMeta):
  """Backend for 96-head liquid handling operations."""

  @abstractmethod
  async def pick_up_tips96(
    self, pickup: PickupTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Pick up tips from a tip rack using the 96-head."""

  @abstractmethod
  async def drop_tips96(self, drop: DropTipRack, backend_params: Optional[BackendParams] = None):
    """Drop tips using the 96-head."""

  @abstractmethod
  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate using the 96-head."""

  @abstractmethod
  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense using the 96-head."""
