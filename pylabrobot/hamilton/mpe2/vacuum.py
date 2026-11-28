"""The MPE2's waste system: its vacuum pump and waste containers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional, cast

from pylabrobot.hamilton.mpe2 import commands as cmd
from pylabrobot.hamilton.mpe2.errors import (
  MPE2ConnectionError,
  MPE2Error,
  MPE2TimeoutError,
  MPE2VacuumError,
  MPE2WasteError,
)
from pylabrobot.hamilton.mpe2.states import WASTE, WasteContainerNumber
from pylabrobot.hamilton.transport.tcp.packets import Address

if TYPE_CHECKING:
  from pylabrobot.hamilton.mpe2.mpe2 import MPE2

logger = logging.getLogger(__name__)

NO_ADDRESS = Address(0, 0, 0)


async def stop_quietly(vacuum: "Vacuum", after: str) -> None:
  """Stop the vacuum pump after something failed, logging (not raising) a failure to stop."""
  try:
    await vacuum.stop()
  except Exception as exc:  # noqa: BLE001 - the original failure is what gets raised
    logger.error("Could not stop the MPE2's vacuum pump after %s: %s", after, exc)


class Vacuum:
  """The MPE2's waste system: the vacuum pump and waste containers 1 and 2.

  The pump draws what passes the filter plate down the waste line into the selected container.
  """

  def __init__(self, mpe: "MPE2"):
    self._mpe = mpe

  async def request_is_on(self) -> bool:
    """Whether the pump runs.

    The waste system's own answer is asked first. After a vacuum fault it answers every query with
    that fault instead (such as VacuumNotStableBeforeTimeout), so the vacuum object's
    IsVacuumActive, which still answers then, is asked instead.
    """
    try:
      return cast(bool, await self._mpe._query(cmd.GetVacuumState))
    except (MPE2TimeoutError, MPE2ConnectionError):
      raise
    except MPE2Error as exc:
      logger.warning("The MPE2's waste system reports a fault: %s", exc)
      return cast(bool, await self._mpe._query(cmd.GetVacuumActive))

  async def request_selected_waste_container(self) -> Optional[WasteContainerNumber]:
    """The waste container the pump draws into (1 or 2), or None."""
    value = await self._mpe._query(cmd.GetSelectedWasteContainer)
    if value == cmd.WasteContainer.CONTAINER_1:
      return 1
    if value == cmd.WasteContainer.CONTAINER_2:
      return 2
    return None

  async def request_waste_status(self, container: WasteContainerNumber) -> cmd.WasteStatus:
    """Whether a waste container is in place, and whether it is full."""
    if container not in (1, 2):
      raise ValueError(f"The MPE2 has waste containers 1 and 2, not {container}.")
    query = cmd.GetWaste1Status if container == 1 else cmd.GetWaste2Status
    return cast(cmd.WasteStatus, await self._mpe._query(query))

  async def start(
    self,
    waste_container: WasteContainerNumber = 1,
    *,
    check_vacuum: bool = True,
    stabilize_timeout: Optional[float] = None,
  ) -> None:
    """Start the pump, drawing into a waste container. It runs until ``stop()``.

    The selected waste container stays selected after, as with Hamilton's driver.

    Nothing is sent if it already draws into that container; if it draws into the other one, it
    is stopped and started again. Prefer ``run_for()``, which always stops it.

    Args:
      waste_container: the waste container to draw into, 1 or 2.
      check_vacuum: let the firmware stop the pump when the vacuum gets too strong (a pinched or
        clogged line).
      stabilize_timeout: how long the vacuum may take to build and settle, in seconds (HSL's
        StartVacuum); past it the firmware reports VacuumNotStableBeforeTimeout and the pump
        goes on running. None sets no limit (HSL's StartMPEVacuum). It is not a run time: 3 s
        can be too short.
    """
    if waste_container not in (1, 2):
      raise ValueError(f"The MPE2 has waste containers 1 and 2, not {waste_container}.")
    if stabilize_timeout is not None and stabilize_timeout <= 0:
      raise ValueError(f"The stabilize timeout is longer than 0 s ({stabilize_timeout}).")
    status = await self.request_waste_status(waste_container)
    if not status.present:
      raise MPE2WasteError(f"Waste container {waste_container} is not in place.")
    if await self.request_is_on():
      if await self.request_selected_waste_container() == waste_container:
        return
      await self.stop()
    await self._mpe._command(cmd.SetSelectedWasteContainer, waste_container=WASTE[waste_container])
    try:
      if stabilize_timeout is None:
        await self._mpe._command(cmd.StartVacuum, disable_upper_limit_check=not check_vacuum)
      else:
        # Whether the reply comes at once or once the vacuum is stable isn't known yet: wait long
        # enough for either.
        await self._mpe._send(
          cmd.StartVacuumFor(
            dest=NO_ADDRESS,
            time_ms=round(stabilize_timeout * 1000),
            disable_upper_limit_check=not check_vacuum,
          ),
          stabilize_timeout + self._mpe.timeout,
        )
    except BaseException:
      # The start may have gone out before it failed or was interrupted: stop the pump.
      await stop_quietly(self, "a vacuum start that failed")
      raise

  async def run_for(
    self,
    duration: float,
    waste_container: WasteContainerNumber = 1,
    *,
    check_vacuum: bool = True,
  ) -> None:
    """Run the pump for a time, then stop it, whatever happens in between.

    Args:
      duration: how long to run it, in seconds.
      waste_container: the waste container to draw into, 1 or 2.
      check_vacuum: let the firmware stop the pump when the vacuum gets too strong.
    """
    if not duration > 0:
      raise ValueError(f"The vacuum's run time is longer than 0 s ({duration}).")
    try:
      await self.start(waste_container=waste_container, check_vacuum=check_vacuum)
      await asyncio.sleep(duration)
    finally:
      await self.stop()

  async def stop(self) -> None:
    """Stop the pump, and confirm it stopped.

    The stop is always sent, without asking first whether the pump runs: after a vacuum fault the
    waste system answers queries with the fault, and a stop that waited for an answer was never
    sent, and the pump kept running. A fault reported by the stop itself is logged, not
    raised, once the pump is confirmed off.

    Raises:
      MPE2VacuumError: the pump still runs after the stop.
    """
    fault: Optional[MPE2Error] = None
    try:
      await self._mpe._command(cmd.StopVacuum)
    except (MPE2TimeoutError, MPE2ConnectionError):
      raise
    except MPE2Error as exc:  # a fault the stop reported: logged once the pump is seen off
      fault = exc
    if await self._mpe._query(cmd.GetVacuumActive):
      raise MPE2VacuumError(
        "The MPE2's vacuum pump still runs after a stop" + (f": {fault}" if fault else ".")
      )
    if fault is not None:
      logger.warning("The vacuum pump is off; the MPE2 reported: %s", fault)
