"""Dispensing from a syringe.

The capability object an instrument with a syringe box exposes. Each method runs one step on its
own, checked and bracketed in a batch; several of them inside ``async with device.batch():`` share
one opening.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.devices.execution import run_steps
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe import Syringe
from pylabrobot.agilent.biotek.lhc.enums.steps.syringe_bottle import SyringeBottle
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import Head
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import PreDispense, Submerge
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_prime import SyringePrime


class SyringeDispenser:
  """The syringes an instrument is fitted with.

  Args:
    runtime: The state of the device this belongs to, which is where the link, the fitted options
      and the plate on the carrier come from.
  """

  def __init__(self, runtime: Runtime) -> None:
    self._runtime = runtime

  def _at(self, head: Head) -> Positioning:
    """Where a head works the plate on the carrier, when the caller names no position.

    A step carries the height it works at outright, so a default height that suits one plate is too
    deep on a shallower one. Taking it from the plate is what keeps a defaulted call safe on every
    plate the instrument works.

    Args:
      head: Which head is doing the work.

    Returns:
      The nominal position for that head, with no offset across or along the well.

    Raises:
      RejectedError: If no plate has been set.
    """
    return Positioning(z_steps=self._runtime.plate_record.height_for(head))

  async def dispense(
    self,
    volume: int,
    syringe: Syringe = "A",
    flow_rate: int = 2,
    syringe_bottle: SyringeBottle = "A1",
    pump_delay: int = 0,
    positioning: Positioning | None = None,
    pre_dispense: PreDispense | None = None,
    columns: WellMask | None = None,
    rows: WellMask | None = None,
  ) -> None:
    """Dispense into every selected well from a syringe.

    Args:
      volume: Volume per well in µL.
      syringe: Which syringe to dispense from. Dispensing from both at once needs a double box.
      flow_rate: How fast to dispense.
      syringe_bottle: Which bottle to draw from.
      pump_delay: How long the pump waits between wells, in ms.
      positioning: Where in the well to dispense. Defaults to the nominal height for this
        head over the plate on the carrier, with no offset across or along the well.
      pre_dispense: Whether to pre-dispense first, at what volume and how many times.
      columns: Which columns to dispense into.
      rows: Which rows to dispense into. Only instruments that select rows use this.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = SyringeDispense(
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      syringe_bottle=syringe_bottle,
      pump_delay=pump_delay,
      selects_rows=rows is not None,
      positioning=positioning if positioning is not None else self._at("dispenser"),
    )
    if pre_dispense is not None:
      step.pre_dispense = pre_dispense
    if columns is not None:
      step.columns = columns
    if rows is not None:
      step.rows = rows
    await run_steps(self._runtime, [step])

  async def prime(
    self,
    volume: int = 5_000,
    syringe: Syringe = "A",
    flow_rate: int = 5,
    cycles: int = 2,
    syringe_bottle: SyringeBottle = "A1",
    pump_delay: int = 0,
    submerge: Submerge | None = None,
  ) -> None:
    """Draw fluid through a syringe until its lines are full.

    Args:
      volume: Volume to draw in µL.
      syringe: Which syringe to prime.
      flow_rate: How fast to draw.
      cycles: How many prime cycles to run.
      syringe_bottle: Which bottle to draw from.
      pump_delay: How long the pump waits between cycles, in ms.
      submerge: Whether to leave the tips in fluid afterwards, and for how long.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = SyringePrime(
      volume=volume,
      syringe=syringe,
      flow_rate=flow_rate,
      cycles=cycles,
      syringe_bottle=syringe_bottle,
      pump_delay=pump_delay,
    )
    if submerge is not None:
      step.submerge = submerge
    await run_steps(self._runtime, [step])
