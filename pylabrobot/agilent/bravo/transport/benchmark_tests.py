"""Measures the async-transport bridge's per-call handoff cost, isolated from I/O.

Bravo controllers are synchronous and run inside ``asyncio.to_thread``, so every
byte-level operation crosses ``AsyncTransportBase._run`` -- a thread-to-loop hop
via ``asyncio.run_coroutine_threadsafe`` followed by ``future.result(timeout)`` --
on its way to PyLabRobot's async I/O layer and back. This module measures the
cost of that crossing, which is what the design document's decision gate is
about.

An earlier version of this benchmark measured that cost by timing a full
``SocketTransport`` round trip over loopback TCP on both the bridged and
direct-async paths and subtracting. That does not work on a machine that stays
busy with other work: a loopback TCP round trip is itself a multi-syscall
operation with its own preemption variance, and on a loaded machine that
variance is large enough to dwarf the handoff cost being measured. Two
independently noisy, I/O-inclusive measurements do not cancel to a clean signal
on subtraction -- delta-at-min swung roughly 3x between runs of that version.

This version isolates the handoff with no I/O at all: a trivial coroutine
(``_noop``, below) is timed via ``run_coroutine_threadsafe(...).result()`` from a
worker thread, and via a bare ``await`` on the loop with no thread hop. A no-op
is short enough to often complete inside a single OS scheduling quantum, so the
low-end order statistics of many samples actually find calls that ran
uncontended, recovering the handoff's true cost instead of two I/O noise floors.

The end-to-end ``SocketTransport`` round trip is still measured here and
printed, but strictly as context: it is explicitly not the bridge's cost (it
also includes loopback TCP), it is contention-dominated on a busy machine, and
it does not feed the homing-sequence projection below.

Contention does not vanish for the handoff measurement either -- it is real CPU
contention, not just I/O noise, so the same low-end-order-statistic reasoning
applies here too: min and the low percentiles (p1, p5, p10) are the figure of
merit, and p95/p99/max are kept only for visibility into how loaded the machine
was, not as an estimate of the handoff's cost. See ``_summarize``.

This is a timing measurement, not a correctness test, so it carries the
``hardware`` marker and does not run in the default test suite. Run it
explicitly with, e.g.::

    env/bin/python -m pytest -m hardware -s \
        pylabrobot/agilent/bravo/transport/benchmark_tests.py
"""

import asyncio
import os
import statistics
import time
import unittest
from typing import Dict, List

import pytest

from pylabrobot.agilent.bravo.transport.socket import SocketTransport
from pylabrobot.agilent.bravo.transport.socket_tests import EchoServer

# The handoff measurement performs no I/O, so it is cheap even at a high sample
# count; a large count gives the low-end estimator more chances to catch a call
# that ran without being preempted, which is the whole point of reading the
# distribution's low end on a contended machine.
_HANDOFF_WARMUP_CALLS = 1000
_HANDOFF_MEASURED_CALLS = 20000

# Generous bound on the handoff's own future.result(), purely to keep this
# benchmark from hanging; it is not a statement about SocketTransport's timeout
# policy, since no transport is involved in this measurement.
_HANDOFF_RESULT_TIMEOUT_S = 5.0

# The end-to-end measurement performs real loopback TCP I/O per sample, so it is
# kept at the original, more modest sample count to keep runtime tolerable.
_E2E_WARMUP_CALLS = 300
_E2E_MEASURED_CALLS = 10000

# Small, fixed payload for the end-to-end block: representative of a short
# instrument command/reply, and identical on both of that block's paths so they
# differ only in how the call reaches the event loop, not in how much data
# crosses it.
_PAYLOAD = b"A" * 32
_IO_TIMEOUT_S = 2.0

# Explicit, visible assumption for the projection below. Darwin-generation
# controllers poll per-axis motor state in a tight loop during commutation and
# homing; the real count depends on axis count, polling cadence, and how long
# homing takes on real hardware, none of which have been measured here. This
# number is a stated order-of-magnitude placeholder for "thousands of polls in
# one homing sequence", not a measurement -- substitute the real count once it
# is known.
# Upper bound on motor-state polls in a full multi-axis homing sequence, derived
# from the axis state machines rather than guessed. Darwin axes poll at 200 ms
# with an explicit sleep between reads, bounded by a 20 s homing timeout and a
# 15 s commutation timeout per axis (40 s and 30 s for the W and G axes). One
# axis therefore cannot exceed ~100 homing polls, and a sequence driving every
# axis to its timeout tops out near this figure -- while sleeping ~200 s to do
# it. These are 5 Hz polls, not a tight loop.
_ASSUMED_HOMING_POLL_COUNT = 1000

# Keys read as the figure of merit under contention: the low end of the
# distribution, least contaminated by preemption. p95/p99/max are computed and
# printed for visibility only -- see the module docstring.
_LOW_END_KEYS = ("min", "p1", "p5", "p10", "median")
_CONTENTION_KEYS = ("p95", "p99", "max")


async def _noop() -> bytes:
  """A coroutine that performs no I/O, so the only cost timed is the handoff.

  Returns:
    An empty byte string, never inspected; only the completion is timed.
  """
  return b""


def _percentile(sorted_values: List[float], pct: float) -> float:
  """Linearly interpolated percentile of an already-sorted sequence.

  Args:
    sorted_values: Values in ascending order.
    pct: Percentile to compute, in ``[0, 100]``.

  Returns:
    The interpolated value at ``pct``.
  """
  if len(sorted_values) == 1:
    return sorted_values[0]
  rank = (len(sorted_values) - 1) * (pct / 100.0)
  lower = int(rank)
  upper = min(lower + 1, len(sorted_values) - 1)
  frac = rank - lower
  return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def _summarize(samples_s: List[float]) -> Dict[str, float]:
  """Reduces a list of durations, in seconds, to a microsecond summary.

  Args:
    samples_s: Per-call durations, in seconds.

  Returns:
    A dict with ``min``, ``p1``, ``p5``, ``p10``, ``median``, ``p95``, ``p99``,
    ``max``, and ``mean`` keys, each in microseconds.
  """
  values_us = sorted(v * 1e6 for v in samples_s)
  return {
    "min": values_us[0],
    "p1": _percentile(values_us, 1),
    "p5": _percentile(values_us, 5),
    "p10": _percentile(values_us, 10),
    "median": statistics.median(values_us),
    "p95": _percentile(values_us, 95),
    "p99": _percentile(values_us, 99),
    "max": values_us[-1],
    "mean": statistics.fmean(values_us),
  }


def _format_low_row(label: str, summary: Dict[str, float]) -> str:
  """Formats the low-end (figure-of-merit) fields of a summary.

  Args:
    label: Row label, e.g. ``"bridged"``.
    summary: A summary as produced by :func:`_summarize`.

  Returns:
    One formatted line.
  """
  fields = "  ".join(f"{key}={summary[key]:9.2f}us" for key in _LOW_END_KEYS)
  return f"  {label:<10} {fields}"


def _format_contention_row(label: str, summary: Dict[str, float]) -> str:
  """Formats the upper-tail (contention-dominated) fields of a summary.

  Args:
    label: Row label, e.g. ``"bridged"``.
    summary: A summary as produced by :func:`_summarize`.

  Returns:
    One formatted line.
  """
  fields = "  ".join(f"{key}={summary[key]:10.2f}us" for key in _CONTENTION_KEYS)
  return f"  {label:<10} {fields}  mean={summary['mean']:9.2f}us"


def _load_average_note() -> str:
  """Best-effort description of contention for CPU at the time of the run.

  Returns:
    A one-line note; a placeholder if load average is unavailable on this
    platform.
  """
  try:
    one, five, fifteen = os.getloadavg()
    cpus = os.cpu_count() or 0
    return f"load average: {one:.2f} {five:.2f} {fifteen:.2f} over 1/5/15 min ({cpus} logical CPUs)"
  except (AttributeError, OSError):
    return "load average: unavailable on this platform"


class BridgeOverheadBenchmark(unittest.IsolatedAsyncioTestCase):
  @pytest.mark.hardware
  async def test_bridge_overhead(self):
    loop = asyncio.get_running_loop()

    # ---------------------------------------------------------------------
    # Block 1: bridge handoff cost, no I/O at all -- the figure of merit.
    # ---------------------------------------------------------------------
    # The bridged path, exercised from inside asyncio.to_thread: this is the
    # real calling context every controller call runs in. A single to_thread
    # call hosts the whole measured loop, exactly as a single controller
    # method hosts many sequential transport calls -- not one to_thread call
    # per handoff, which would time thread dispatch instead of the bridge.
    def run_handoff_bridged() -> List[float]:
      for _ in range(_HANDOFF_WARMUP_CALLS):
        asyncio.run_coroutine_threadsafe(_noop(), loop).result(_HANDOFF_RESULT_TIMEOUT_S)
      samples = []
      for _ in range(_HANDOFF_MEASURED_CALLS):
        start = time.perf_counter()
        asyncio.run_coroutine_threadsafe(_noop(), loop).result(_HANDOFF_RESULT_TIMEOUT_S)
        samples.append(time.perf_counter() - start)
      return samples

    handoff_bridged_samples = await asyncio.to_thread(run_handoff_bridged)

    # The direct-async baseline: the same no-op coroutine, awaited straight on
    # the event loop with no thread hop and no run_coroutine_threadsafe call.
    for _ in range(_HANDOFF_WARMUP_CALLS):
      await _noop()
    handoff_direct_samples = []
    for _ in range(_HANDOFF_MEASURED_CALLS):
      start = time.perf_counter()
      await _noop()
      handoff_direct_samples.append(time.perf_counter() - start)

    handoff_bridged = _summarize(handoff_bridged_samples)
    handoff_direct = _summarize(handoff_direct_samples)
    handoff_delta = {key: handoff_bridged[key] - handoff_direct[key] for key in handoff_bridged}

    # The homing projection is computed from this block's deltas only -- see
    # the module docstring for why the end-to-end block below is not used.
    projected_min_s = handoff_delta["min"] * _ASSUMED_HOMING_POLL_COUNT / 1e6
    projected_p5_s = handoff_delta["p5"] * _ASSUMED_HOMING_POLL_COUNT / 1e6

    # ---------------------------------------------------------------------
    # Block 2: end-to-end round trip through SocketTransport -- context only.
    # Includes loopback TCP; not the bridge's cost; not used for the
    # projection below. See the module docstring.
    # ---------------------------------------------------------------------
    server = EchoServer()
    await server.start()
    self.addAsyncCleanup(server.stop)

    transport = SocketTransport(
      human_readable_device_name="benchmark bravo",
      host="127.0.0.1",
      port=server.port,
    )
    await transport.setup()
    self.addAsyncCleanup(transport.stop)

    def run_e2e_bridged() -> List[float]:
      for _ in range(_E2E_WARMUP_CALLS):
        transport.send(_PAYLOAD)
        transport.receive_exact(len(_PAYLOAD))
      samples = []
      for _ in range(_E2E_MEASURED_CALLS):
        start = time.perf_counter()
        transport.send(_PAYLOAD)
        transport.receive_exact(len(_PAYLOAD))
        samples.append(time.perf_counter() - start)
      return samples

    e2e_bridged_samples = await asyncio.to_thread(run_e2e_bridged)

    # The same underlying Socket object the transport above wraps, driven
    # straight from the event loop with no thread hop. Same server, same
    # connection, same payload, same pair of operations (write, read_exact) --
    # the only variable is whether the call crosses
    # run_coroutine_threadsafe/future.result or not.
    io = transport._io
    for _ in range(_E2E_WARMUP_CALLS):
      await io.write(_PAYLOAD, timeout=_IO_TIMEOUT_S)
      await io.read_exact(len(_PAYLOAD), timeout=_IO_TIMEOUT_S)
    e2e_direct_samples = []
    for _ in range(_E2E_MEASURED_CALLS):
      start = time.perf_counter()
      await io.write(_PAYLOAD, timeout=_IO_TIMEOUT_S)
      await io.read_exact(len(_PAYLOAD), timeout=_IO_TIMEOUT_S)
      e2e_direct_samples.append(time.perf_counter() - start)

    e2e_bridged = _summarize(e2e_bridged_samples)
    e2e_direct = _summarize(e2e_direct_samples)
    e2e_delta = {key: e2e_bridged[key] - e2e_direct[key] for key in e2e_bridged}

    # ---------------------------------------------------------------------
    # Report.
    # ---------------------------------------------------------------------
    print()
    print("=" * 88)
    print("Bravo transport bridge benchmark")
    print("=" * 88)
    print(f"  {_load_average_note()}")
    print("=" * 88)
    print(
      "BLOCK 1 -- bridge handoff cost, no I/O. THIS IS THE FIGURE OF MERIT: the "
      "number the design document's decision gate is about."
    )
    print(
      f"  calls measured per path : {_HANDOFF_MEASURED_CALLS} "
      f"(plus {_HANDOFF_WARMUP_CALLS} warm-up, discarded)"
    )
    print("-" * 88)
    print("Low end of the distribution -- least contaminated by preemption:")
    print(_format_low_row("bridged", handoff_bridged))
    print(_format_low_row("direct", handoff_direct))
    print(_format_low_row("delta", handoff_delta))
    print("-" * 88)
    print("Upper tail -- contention-dominated, NOT the figure of merit, visibility only:")
    print(_format_contention_row("bridged", handoff_bridged))
    print(_format_contention_row("direct", handoff_direct))
    print(_format_contention_row("delta", handoff_delta))
    print("-" * 88)
    print(
      f"  handoff delta at min: {handoff_delta['min']:.2f}us     "
      f"handoff delta at p5: {handoff_delta['p5']:.2f}us"
    )
    print("=" * 88)
    print(
      "BLOCK 2 -- end-to-end round trip through SocketTransport, over loopback "
      "TCP. CONTEXT ONLY: not the bridge's cost (includes real socket I/O on "
      "both paths), and contention-dominated on a busy machine. Not used below."
    )
    print(
      f"  calls measured per path : {_E2E_MEASURED_CALLS} "
      f"(plus {_E2E_WARMUP_CALLS} warm-up, discarded)"
    )
    print(f"  payload size            : {len(_PAYLOAD)} bytes, echoed back")
    print("-" * 88)
    print("Low end of the distribution:")
    print(_format_low_row("bridged", e2e_bridged))
    print(_format_low_row("direct", e2e_direct))
    print(_format_low_row("delta", e2e_delta))
    print("-" * 88)
    print("Upper tail -- contention-dominated, visibility only:")
    print(_format_contention_row("bridged", e2e_bridged))
    print(_format_contention_row("direct", e2e_direct))
    print(_format_contention_row("delta", e2e_delta))
    print("=" * 88)
    print(
      f"Projected addition to a Darwin homing sequence, ASSUMING "
      f"{_ASSUMED_HOMING_POLL_COUNT} poll round trips (stated assumption, not "
      f"measured -- see module docstring / _ASSUMED_HOMING_POLL_COUNT), computed "
      f"from the BLOCK 1 handoff deltas (not the end-to-end block):"
    )
    print(f"  at handoff delta-at-min: {projected_min_s * 1000:.2f} ms total")
    print(f"  at handoff delta-at-p5 : {projected_p5_s * 1000:.2f} ms total")
    print("=" * 88)

    # Sanity: this benchmark's own premise. Fail loudly, rather than printing a
    # silently-meaningless comparison, if any path produced no samples.
    self.assertEqual(len(handoff_bridged_samples), _HANDOFF_MEASURED_CALLS)
    self.assertEqual(len(handoff_direct_samples), _HANDOFF_MEASURED_CALLS)
    self.assertEqual(len(e2e_bridged_samples), _E2E_MEASURED_CALLS)
    self.assertEqual(len(e2e_direct_samples), _E2E_MEASURED_CALLS)


if __name__ == "__main__":
  unittest.main()
