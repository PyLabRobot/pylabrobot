"""Thermo Scientific Multidrop Combi device."""

from __future__ import annotations

from pylabrobot.capabilities.bulk_dispensers.peristaltic import PeristalticDispensing8
from pylabrobot.device import Device
from pylabrobot.thermo_fisher.multidrop_combi.driver import MultidropCombiDriver
from pylabrobot.thermo_fisher.multidrop_combi.peristaltic_dispensing_backend8 import (
  MultidropCombiPeristalticDispensingBackend8,
)


class MultidropCombi(Device):
  """Thermo Scientific Multidrop Combi reagent dispenser.

  Args:
    port: Serial port (e.g. "COM3", "/dev/ttyUSB0").
    timeout: Default serial read timeout in seconds.
  """

  def __init__(
    self,
    port: str,
    timeout: float = 30.0,
    *,
    driver: MultidropCombiDriver | None = None,
  ) -> None:
    if driver is None:
      driver = MultidropCombiDriver(port=port, timeout=timeout)
    super().__init__(driver=driver)
    self.driver: MultidropCombiDriver = driver
    self.peristaltic_dispenser = PeristalticDispensing8(
      backend=MultidropCombiPeristalticDispensingBackend8(driver)
    )
    self._capabilities = [self.peristaltic_dispenser]
