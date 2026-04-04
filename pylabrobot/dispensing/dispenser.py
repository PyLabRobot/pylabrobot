"""Front-end for chip-based contactless liquid dispensers."""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Union

from pylabrobot.machines.machine import Machine, need_setup_finished
from pylabrobot.resources import Well

from .backend import DispenserBackend
from .standard import DispenseOp

logger = logging.getLogger(__name__)


class Dispenser(Machine):
  """Front-end for chip-based contactless liquid dispensers.

  Dispensers use disposable silicon chips with microvalves and pressure-driven
  dispensing to deliver nanoliter-to-microliter volumes into microplate wells
  without contacting the liquid.

  Example::

    >>> from pylabrobot.dispensing.mantis import MantisBackend
    >>> d = Dispenser(backend=MantisBackend(serial_number="M-000438"))
    >>> await d.setup()
    >>> await d.dispense(plate["A1:H12"], volume=5.0, chip=3)
    >>> await d.stop()
  """

  def __init__(self, backend: DispenserBackend) -> None:
    super().__init__(backend=backend)
    self.backend: DispenserBackend = backend  # fix type for IDE

  @need_setup_finished
  async def dispense(
    self,
    resources: Union[Well, Sequence[Well]],
    volume: float,
    chip: Optional[int] = None,
    **backend_kwargs,
  ) -> None:
    """Dispense liquid into target wells.

    Args:
      resources: Target well(s) to dispense into.
      volume: Volume in µL to dispense per well.
      chip: Chip number to use (1-6). If ``None``, the backend selects automatically.
      **backend_kwargs: Additional keyword arguments passed to the backend.

    Raises:
      RuntimeError: If setup has not been called.
      ValueError: If *volume* is not positive.
    """
    if isinstance(resources, Well):
      resources = [resources]

    if volume <= 0:
      raise ValueError(f"Volume must be positive, got {volume}")

    ops: List[DispenseOp] = [
      DispenseOp(resource=well, volume=volume, chip=chip) for well in resources
    ]

    logger.info("Dispensing %.2f µL into %d well(s)", volume, len(ops))
    await self.backend.dispense(ops, **backend_kwargs)
