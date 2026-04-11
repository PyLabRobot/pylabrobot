"""Diaphragm dispensing capability backend for the Formulatrix Mantis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from pylabrobot.capabilities.bulk_dispensers.diaphragm import DiaphragmDispenserBackend
from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.formulatrix.mantis.driver import MantisDriver
from pylabrobot.formulatrix.mantis.mantis_kinematics import apply_stage_homography
from pylabrobot.resources import Container, Plate, Well

logger = logging.getLogger(__name__)


class MantisDiaphragmDispenserBackend(DiaphragmDispenserBackend):
  """Translates DiaphragmDispenser operations into Mantis driver calls."""

  def __init__(self, driver: MantisDriver):
    super().__init__()
    self._driver = driver

  @dataclass
  class DispenseParams(BackendParams):
    """Parameters for a Mantis diaphragm dispense.

    Args:
      chip: Chip number (1-6) to use. If ``None``, uses the driver's first
        configured chip.
      dispense_z: Machine-frame Z height in mm at which to dispense. This is a
        per-plate calibration (chip-to-plate clearance).
      prime_volume: Prime volume in uL used when (re-)priming the chip.
    """

    chip: Optional[int] = None
    dispense_z: float = 44.331
    prime_volume: float = 20.0

  @dataclass
  class PrimeParams(BackendParams):
    """Parameters for a Mantis prime.

    Args:
      chip: Chip number (1-6) to prime. If ``None``, uses the driver's first
        configured chip.
      volume: Prime volume in uL.
    """

    chip: Optional[int] = None
    volume: float = 20.0

  async def dispense(
    self,
    containers: List[Container],
    volumes: List[float],
    backend_params: Optional[BackendParams] = None,
  ) -> None:
    if not containers:
      return
    if not isinstance(backend_params, self.DispenseParams):
      backend_params = self.DispenseParams()

    chip_number = (
      backend_params.chip if backend_params.chip is not None else self._driver.default_chip()
    )
    logger.info(
      "[Mantis] dispense %d container(s), volumes=%.2f-%.2f uL, chip=%d",
      len(containers),
      min(volumes),
      max(volumes),
      chip_number,
    )

    if not (self._driver.current_chip == chip_number and self._driver.is_primed):
      await self._driver.prime_chip(chip_number, volume=backend_params.prime_volume)

    try:
      c_type = self._driver.get_chip_type(chip_number)
      if "low_volume" in c_type:
        large_vol, small_vol = 0.5, 0.1
        large_seq, small_seq = "dispense_500nL", "dispense_100nL"
      else:
        large_vol, small_vol = 5.0, 1.0
        large_seq, small_seq = "dispense_5uL", "dispense_1uL"

      for container, volume in zip(containers, volumes):
        x, y, z = self._container_to_machine_coord(container, backend_params.dispense_z)
        await self._driver.move_to(x, y, z)

        num_large = int(volume / large_vol)
        rem = volume - (num_large * large_vol)
        num_small = int(round(rem / small_vol))
        if num_large == 0 and num_small == 0:
          num_small = 1

        for _ in range(num_large):
          await self._driver.execute_ppi_sequence(chip_number, large_seq)
        for _ in range(num_small):
          await self._driver.execute_ppi_sequence(chip_number, small_seq)

      await self._driver.move_to_home()
      sid = await self._driver.move_to_ready()
      await self._driver.wait_for_seq_progress(sid)

    finally:
      await self._driver.detach_chip(chip_number)

  async def prime(self, backend_params: Optional[BackendParams] = None) -> None:
    if not isinstance(backend_params, self.PrimeParams):
      backend_params = self.PrimeParams()
    chip_number = (
      backend_params.chip if backend_params.chip is not None else self._driver.default_chip()
    )
    await self._driver.prime_chip(chip_number, volume=backend_params.volume)

  @staticmethod
  def _container_to_machine_coord(
    container: Container, dispense_z: float
  ) -> Tuple[float, float, float]:
    """Compute the Mantis machine-frame (x, y, z) for a container.

    PLR wells store locations as LFB (left-front-bottom) in the plate frame
    with A1 at the back (high y). The Mantis plate frame has A1 at the front
    (low y), so y is mirrored across the plate's size_y before applying the
    stage homography. Z comes from ``dispense_z`` (machine calibration).
    """
    if not isinstance(container, Well):
      raise ValueError(
        f"Mantis only supports Well containers (got {type(container).__name__} {container.name!r})."
      )
    plate = container.parent
    if not isinstance(plate, Plate):
      raise ValueError(
        f"Well {container.name!r} has no Plate parent; cannot compute Mantis coordinate."
      )
    center = container.get_location_wrt(plate, x="c", y="c", z="b")
    ideal_x = center.x
    ideal_y = plate.get_size_y() - center.y
    mx, my = apply_stage_homography(ideal_x, ideal_y)
    return mx, my, dispense_z
