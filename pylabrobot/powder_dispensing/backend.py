from __future__ import annotations
from abc import ABCMeta, abstractmethod
from typing import List

from pylabrobot.machine import MachineBackend
from pylabrobot.resources import Resource, Powder


class PowderDispenserBackend(MachineBackend, metaclass=ABCMeta):
  """
  An abstract class for a powder dispenser backend.
  """

  @abstractmethod
  async def setup(self) -> None:

    """Set up the powder dispenser."""

  @abstractmethod
  async def stop(self) -> None:
    """Close all connections to the powder dispenser and make sure setup() can be called again."""

  @abstractmethod
  async def dispense(
    self,
    dispense_parameters: List[PowderDispense],
    **backend_kwargs
  ) -> List[DispenseResults]:
    """Dispense powders with set of dispense parameters."""


class PowderDispense:
  """
  A class for input parameters for powder dispensing.
  """

  def __init__(
    self,
    resource: Resource,
    powder: Powder,
    amount: float,
    **kwargs
  ) -> None:
    self.resource = resource
    self.powder = powder
    self.amount = amount
    self.kwargs = kwargs


class DispenseResults(dict):
  """
  A class for results of powder dispensing, behaving like a dictionary
  but ensuring the presence of 'actual_amount'.
  """

  def __init__(self, actual_amount: float, **kwargs):
    super().__init__(actual_amount=actual_amount, **kwargs)
