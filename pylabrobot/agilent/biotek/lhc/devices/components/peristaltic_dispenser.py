"""Dispensing from a peristaltic pump.

The capability object an instrument with peristaltic pumps exposes. Each method runs one step on its
own, checked and bracketed in a batch; several of them inside ``async with device.batch():`` share
one opening.

Two things about a pump are decided before a step runs rather than by it. Which cassette is in which
pump is reconciled when the batch opens, from what the checked protocol asked for, so a step names
the cassette it needs and the opening is what fits it. And the peristaltic wash steps are a separate
pair of methods because they draw through wash cassettes, which no ordinary peristaltic step can
share a pump with.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.devices.execution import run_steps
from pylabrobot.agilent.biotek.lhc.devices.runtime import Runtime
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_head import CassetteHead
from pylabrobot.agilent.biotek.lhc.enums.steps.cassette_type import CassetteType
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_flow_rate import PeriFlowRate
from pylabrobot.agilent.biotek.lhc.enums.steps.peri_pump import PeriPump
from pylabrobot.agilent.biotek.lhc.plate_geometry.plate_record import Head
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.groups import (
  PreDispense,
  RandomAccess,
  WellVolumeMap,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.masks import WellMask
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_prime import PeriPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_purge import PeriPurge
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_random_access_dispense import (
  PeriRandomAccessDispense,
)
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_aspirate import PeriWashAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_dispense import PeriWashDispense


class PeristalticDispenser:
  """The peristaltic pumps an instrument is fitted with.

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
    flow_rate: PeriFlowRate = "High",
    cassette_type: CassetteType | None = "Any",
    peri_pump: PeriPump | None = "Primary",
    positioning: Positioning | None = None,
    pre_dispense: PreDispense | None = None,
    columns: WellMask | None = None,
    rows: WellMask | None = None,
    well_volumes: WellVolumeMap | None = None,
    cassette_head: CassetteHead | None = None,
  ) -> None:
    """Dispense from a peristaltic pump.

    Giving a per-well volume map, a dispense head, or both makes this a dispense into individually
    chosen wells, which is a different payload and a different command. An instrument old enough not
    to carry those fields cannot run it, and the check refuses the step rather than quietly running
    it across the whole plate.

    Args:
      volume: Volume per tube in µL. Ignored for the wells a volume map gives a volume of their own.
      flow_rate: How fast to dispense.
      cassette_type: The cassette the step requires, or None to accept whatever is fitted.
      peri_pump: Which pump to drive, or None to leave the choice to the instrument.
      positioning: Where in the well to dispense. Defaults to the nominal height for this
        head over the plate on the carrier, with no offset across or along the well.
      pre_dispense: Whether to pre-dispense first, at what volume and how many times.
      columns: Which columns to dispense into.
      rows: Which rows to dispense into.
      well_volumes: A volume for each of sixteen individually chosen wells.
      cassette_head: How the fitted cassette's tubes map onto wells.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    where = positioning if positioning is not None else self._at("dispenser")
    step: PeriDispense
    if well_volumes is None and cassette_head is None:
      step = PeriDispense(
        volume=volume,
        flow_rate=flow_rate,
        cassette_type=cassette_type,
        peri_pump=peri_pump,
        positioning=where,
      )
    else:
      step = PeriRandomAccessDispense(
        volume=volume,
        flow_rate=flow_rate,
        cassette_type=cassette_type,
        peri_pump=peri_pump,
        cassette_head=cassette_head,
        positioning=where,
      )
      if well_volumes is not None:
        step.well_volumes = well_volumes
    if pre_dispense is not None:
      step.pre_dispense = pre_dispense
    if columns is not None:
      step.columns = columns
    if rows is not None:
      step.rows = rows
    await run_steps(self._runtime, [step])

  async def prime(
    self,
    volume: int = 300,
    duration: int | None = None,
    flow_rate: PeriFlowRate = "High",
    cassette_type: CassetteType | None = "Any",
    peri_pump: PeriPump | None = "Primary",
    home_when_finished: bool = True,
    random_access: RandomAccess | None = None,
  ) -> None:
    """Pump fluid through a cassette until its tubing is full.

    Args:
      volume: Volume per tube in µL, used when no duration is given.
      duration: How long to pump in seconds. Given, the step runs to a duration rather than to a
        volume.
      flow_rate: How fast to pump.
      cassette_type: The cassette the step requires, or None to accept whatever is fitted.
      peri_pump: Which pump to drive, or None to leave the choice to the instrument.
      home_when_finished: Whether the carrier homes after the step.
      random_access: Whether the step primes a random-access head, and which one.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = PeriPrime(
      volume=volume,
      fixed_volume=duration is None,
      flow_rate=flow_rate,
      cassette_type=cassette_type,
      peri_pump=peri_pump,
      home_when_finished=home_when_finished,
    )
    if duration is not None:
      step.duration = duration
    if random_access is not None:
      step.random_access = random_access
    await run_steps(self._runtime, [step])

  async def purge(
    self,
    volume: int = 300,
    duration: int | None = None,
    flow_rate: PeriFlowRate = "High",
    cassette_type: CassetteType | None = "Any",
    peri_pump: PeriPump | None = "Primary",
    home_when_finished: bool = True,
    random_access: RandomAccess | None = None,
  ) -> None:
    """Pump a cassette empty, running fluid to waste rather than to the plate.

    Args:
      volume: Volume per tube in µL, used when no duration is given.
      duration: How long to pump in seconds. Given, the step runs to a duration rather than to a
        volume.
      flow_rate: How fast to pump.
      cassette_type: The cassette the step requires, or None to accept whatever is fitted.
      peri_pump: Which pump to drive, or None to leave the choice to the instrument.
      home_when_finished: Whether the carrier homes after the step.
      random_access: Whether the step purges a random-access head, and which one.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = PeriPurge(
      volume=volume,
      fixed_volume=duration is None,
      flow_rate=flow_rate,
      cassette_type=cassette_type,
      peri_pump=peri_pump,
      home_when_finished=home_when_finished,
    )
    if duration is not None:
      step.duration = duration
    if random_access is not None:
      step.random_access = random_access
    await run_steps(self._runtime, [step])

  async def wash_aspirate(
    self,
    volume: int = 100,
    flow_rate: int = 2,
    peri_pump: PeriPump | None = "Primary",
    positioning: Positioning | None = None,
    columns: WellMask | None = None,
    rows: WellMask | None = None,
  ) -> None:
    """Draw spent medium off gently through a peristaltic wash manifold.

    Paired with :meth:`wash_dispense`, this exchanges medium without disturbing what is growing in
    the well. It needs a wash cassette and manifold on the pump it drives, which no ordinary
    peristaltic step may then share.

    Args:
      volume: Volume per tube in µL.
      flow_rate: How fast to aspirate, as a position on the aspirate rate scale.
      peri_pump: Which pump to drive.
      positioning: Where in the well to aspirate. Defaults to the nominal height for this
        head over the plate on the carrier, with no offset across or along the well.
      columns: Which columns to aspirate.
      rows: Which row sections to aspirate.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = PeriWashAspirate(
      volume=volume,
      flow_rate=flow_rate,
      peri_pump=peri_pump,
      positioning=positioning if positioning is not None else self._at("manifold aspirate"),
    )
    if columns is not None:
      step.columns = columns
    if rows is not None:
      step.rows = rows
    await run_steps(self._runtime, [step])

  async def wash_dispense(
    self,
    volume: int = 100,
    flow_rate: int = 2,
    peri_pump: PeriPump | None = "Primary",
    positioning: Positioning | None = None,
    pre_dispense: PreDispense | None = None,
    columns: WellMask | None = None,
    rows: WellMask | None = None,
  ) -> None:
    """Add fresh medium gently through a peristaltic wash manifold.

    Paired with :meth:`wash_aspirate`. Pre-dispensing into the priming trough on every plate makes
    up for what evaporates from the manifold tubing between plates.

    Args:
      volume: Volume per tube in µL.
      flow_rate: How fast to dispense, as a position on the dispense rate scale.
      peri_pump: Which pump to drive.
      positioning: Where in the well to dispense. Defaults to the nominal height for this
        head over the plate on the carrier, with no offset across or along the well.
      pre_dispense: Whether to pre-dispense first, at what volume and how many times.
      columns: Which columns to dispense into.
      rows: Which row sections to dispense into.

    Raises:
      BiotekError: If the step cannot run, or fails while running.
    """
    step = PeriWashDispense(
      volume=volume,
      flow_rate=flow_rate,
      peri_pump=peri_pump,
      positioning=positioning if positioning is not None else self._at("manifold aspirate"),
    )
    if pre_dispense is not None:
      step.pre_dispense = pre_dispense
    if columns is not None:
      step.columns = columns
    if rows is not None:
      step.rows = rows
    await run_steps(self._runtime, [step])
