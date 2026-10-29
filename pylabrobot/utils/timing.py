"""Waiting on the event loop's clock, and cycles at a fixed cycle time.

`run_cycles` runs a cycle function at fixed times from the first cycle: cycle `i` is due
`i * cycle_time` seconds after cycle 0, whatever the cycles before it took. While a cycle runs,
`get_current_cycle` gives its schedule, so what the cycle records can say which run and which
cycle it belongs to and when the cycle was due.
"""

import contextvars
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, TypeVar

import anyio

logger = logging.getLogger(__name__)

T = TypeVar("T")

_current_cycle: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
  "_current_cycle", default=None
)


async def sleep_until(due: float) -> None:
  """Wait until `due` on the event loop's clock, letting other tasks run.

  Never returns before `due`; a `due` already past returns after one turn of the event loop.

  Args:
    due: the time to wait for, in seconds on the running loop's clock
      (`anyio.current_time()`, monotonic).
  """
  await anyio.sleep(max(0.0, due - anyio.current_time()))
  while anyio.current_time() < due:  # a timer may fire up to the clock's resolution early
    await anyio.sleep(due - anyio.current_time())


def get_current_cycle() -> Dict[str, Any]:
  """The schedule of the cycle running in this task; its values None outside `run_cycles`.

  Keys: `run_start_timestamp` (when the run's cycle 0 was due), `cycle` (the cycle's index) and
  `due_timestamp` (when this cycle was due), timestamps timezone-aware in UTC.
  """
  current_cycle = _current_cycle.get()
  if current_cycle is None:
    return {"run_start_timestamp": None, "cycle": None, "due_timestamp": None}
  return dict(current_cycle)


async def run_cycles(
  cycle_function: Callable[[int], Awaitable[T]],
  *,
  cycle_time: float,
  cycles: int,
  late: Literal["run", "skip"] = "run",
  progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Optional[T]]:
  """Run `cycle_function(cycle)` for each cycle, the cycles `cycle_time` seconds apart.

  Cycle 0 runs at once, cycle `i` at `i * cycle_time` seconds after it. A cycle is late when its
  time has passed as the cycle before returns; the cycles after it keep their times. Cycles never
  overlap. To stop early, cancel the call or raise from `cycle_function`.

  Args:
    cycle_function: awaited with the cycle's index.
    cycle_time: seconds between the cycles' due times.
    cycles: how many cycles there are, cycle 0 to `cycles - 1`.
    late: what a late cycle does: "run" starts it at once, "skip" leaves it out; either with a
      warning naming the cycle and how late it is.
    progress_callback: called as each cycle starts, with its schedule (as `get_current_cycle`
      gives it), for other tasks to follow the run; it should return quickly, and an error from
      it is logged and the run goes on.

  Returns:
    What `cycle_function` returned for each cycle, in cycle order; None for a skipped cycle.

  Raises:
    ValueError: if `cycle_time` is not a positive finite number, `cycles` is less than 1, or
      `late` is neither "run" nor "skip".
  """
  if not (math.isfinite(cycle_time) and cycle_time > 0):
    raise ValueError(f"cycle_time must be a positive finite number of seconds, not {cycle_time}")
  if isinstance(cycles, bool) or cycles < 1:
    raise ValueError(f"cycles must be at least 1, not {cycles}")
  if late not in ("run", "skip"):
    raise ValueError(f"late must be 'run' or 'skip', not {late!r}")
  first_due = anyio.current_time()
  run_start = datetime.now(timezone.utc)
  returned: List[Optional[T]] = [None] * cycles
  logger.debug("run_cycles: %d cycles, cycle time %.1f s, late=%s", cycles, cycle_time, late)
  for cycle in range(cycles):
    due = first_due + cycle * cycle_time
    lateness = anyio.current_time() - due
    if cycle > 0 and lateness > 0:
      logger.warning(
        "cycle %d of %d %s %.3f s late: the cycle before ran past its due time",
        cycle,
        cycles,
        "starts" if late == "run" else "skipped,",
        lateness,
      )
      if late == "skip":
        continue
    await sleep_until(due)
    started = anyio.current_time()
    logger.debug("cycle %d of %d starts %.3f s after its due time", cycle, cycles, started - due)
    token = _current_cycle.set(
      {
        "run_start_timestamp": run_start,
        "cycle": cycle,
        "due_timestamp": run_start + timedelta(seconds=cycle * cycle_time),
      }
    )
    try:
      if progress_callback is not None:
        try:
          progress_callback(get_current_cycle())
        except Exception:
          logger.exception(
            "progress_callback raised for cycle %d of %d; the run goes on", cycle, cycles
          )
      returned[cycle] = await cycle_function(cycle)
    finally:
      _current_cycle.reset(token)
    logger.debug(
      "cycle %d of %d took %.1f s of its %.1f s cycle time",
      cycle,
      cycles,
      anyio.current_time() - started,
      cycle_time,
    )
  return returned
