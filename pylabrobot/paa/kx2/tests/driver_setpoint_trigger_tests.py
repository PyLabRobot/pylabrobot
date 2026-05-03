"""Unit tests for the KX2 driver's CiA 402 PPM new-setpoint handshake.

Covers ``KX2Driver._trigger_new_setpoint``: the four-step CW/SW dance
(drop bit 4, wait bit 12 low, raise bit 4, wait bit 12 high) and its
retry-on-missed-edge behavior. The drive's SW state is faked by setting
``self._statusword[nid]`` directly and signalling
``self._statusword_event[nid]`` — same shape as the real TPDO3 callback,
without any CAN traffic.
"""
import asyncio
import unittest
from typing import List, Tuple

from pylabrobot.paa.kx2.driver import CanError, KX2Driver


SW_BIT_12 = 1 << 12


class _SwScript:
  """Drives the fake SW state machine.

  Each step is one of:
    ("set_low",)   -> clear bit 12 immediately
    ("set_high",)  -> raise bit 12 immediately
    ("hang",)      -> leave SW unchanged so _wait_setpoint_ack times out

  The script is consumed in order on each ``_control_word_set`` call.
  """

  def __init__(self, steps: List[Tuple[str, ...]]):
    self.steps = list(steps)
    self.calls: List[int] = []  # values passed to _control_word_set

  def consume(self) -> Tuple[str, ...]:
    if not self.steps:
      raise AssertionError("script exhausted but _control_word_set was called again")
    return self.steps.pop(0)


def _build_driver(nid: int, script: _SwScript) -> KX2Driver:
  drv = KX2Driver()
  loop = asyncio.get_event_loop()
  drv._loop = loop
  drv._statusword = {nid: 0}
  drv._statusword_event = {nid: asyncio.Event()}

  async def _fake_cw_set(node_id: int, value: int, sync: bool = True) -> None:
    script.calls.append(value)
    step = script.consume()
    tag = step[0]
    if tag == "set_low":
      drv._statusword[node_id] = drv._statusword.get(node_id, 0) & ~SW_BIT_12
      drv._statusword_event[node_id].set()
    elif tag == "set_high":
      drv._statusword[node_id] = drv._statusword.get(node_id, 0) | SW_BIT_12
      drv._statusword_event[node_id].set()
    elif tag == "hang":
      # Leave SW unchanged. _wait_sw_bit will exhaust its 50 ms timeout
      # waiting on the event + falling back to SDO; we also stub the SDO
      # path below to return the cached SW (= still wrong) so it always
      # times out.
      pass
    else:
      raise AssertionError(f"unknown step {tag!r}")

  async def _fake_sdo_upload(node_id: int, idx: int, sub: int) -> bytes:
    return drv._statusword.get(node_id, 0).to_bytes(2, "little") + b"\x00\x00"

  drv._control_word_set = _fake_cw_set  # type: ignore[assignment]
  drv._can_sdo_upload = _fake_sdo_upload  # type: ignore[assignment]
  return drv


class TriggerNewSetpointHappyPathTests(unittest.TestCase):
  def test_first_attempt_succeeds(self):
    """SW bit 12 clears, then rises promptly -> returns, no raise.
    `_control_word_set` is called exactly twice (cw_low then cw_high)."""
    nid = 1
    script = _SwScript([("set_low",), ("set_high",)])

    async def _go():
      drv = _build_driver(nid, script)
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)

    asyncio.new_event_loop().run_until_complete(_go())
    self.assertEqual(script.calls, [47, 63])
    self.assertEqual(script.steps, [])  # script fully consumed


class TriggerNewSetpointRetryThenSuccessTests(unittest.TestCase):
  def test_second_attempt_succeeds(self):
    """First attempt: bit 12 clears but never rises (timeout). Second
    attempt: clears + rises -> returns. CW is set 4 times total."""
    nid = 1
    # Attempt 1: cw_low clears bit 12, cw_high hangs (bit 12 doesn't rise)
    # Attempt 2: cw_low clears, cw_high raises.
    script = _SwScript([
      ("set_low",), ("hang",),
      ("set_low",), ("set_high",),
    ])

    async def _go():
      drv = _build_driver(nid, script)
      # Use a shorter wait timeout indirectly via the script: ("hang",) just
      # means "leave SW unchanged"; the wait will spin its 50 ms timeout.
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)

    asyncio.new_event_loop().run_until_complete(_go())
    self.assertEqual(script.calls, [47, 63, 47, 63])
    self.assertEqual(script.steps, [])


class TriggerNewSetpointAllAttemptsFailTests(unittest.TestCase):
  def test_raises_after_max_attempts(self):
    """SW bit 12 never rises -> raises CanError tagged with the axis nid.
    With max_attempts=2 the handshake runs twice (4 CW writes) then gives up."""
    nid = 3
    script = _SwScript([
      ("set_low",), ("hang",),
      ("set_low",), ("hang",),
    ])

    async def _go():
      drv = _build_driver(nid, script)
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=2)

    with self.assertRaises(CanError) as ctx:
      asyncio.new_event_loop().run_until_complete(_go())
    msg = str(ctx.exception)
    self.assertIn(f"Axis {nid}", msg)
    self.assertIn("did not accept new PPM setpoint", msg)
    self.assertIn("after 2 attempts", msg)
    self.assertEqual(script.calls, [47, 63, 47, 63])


class TriggerNewSetpointBit12StuckHighTests(unittest.TestCase):
  def test_recovers_when_bit12_does_not_clear_on_first_cw_low(self):
    """Pre-existing bit 12 high. Attempt 1 cw_low fails to clear it -> the
    inner ``cleared`` check is False, the attempt restarts (continues the
    loop) without writing cw_high. Attempt 2's cw_low clears, cw_high raises.

    State machine recovery proof: cw_high is NOT written on attempt 1
    (script only sees [cw_low, cw_low, cw_high])."""
    nid = 2
    # Attempt 1: cw_low call -> "hang" (bit 12 stays high; cleared==False).
    # Attempt 2: cw_low call -> set_low (clears), cw_high call -> set_high.
    script = _SwScript([
      ("hang",),
      ("set_low",), ("set_high",),
    ])

    async def _go():
      drv = _build_driver(nid, script)
      drv._statusword[nid] = SW_BIT_12  # bit 12 high before any handshake
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)

    asyncio.new_event_loop().run_until_complete(_go())
    # Attempt 1 wrote cw_low only; attempt 2 wrote cw_low + cw_high.
    self.assertEqual(script.calls, [47, 47, 63])
    self.assertEqual(script.steps, [])


class TriggerNewSetpointMaxAttemptsOneTests(unittest.TestCase):
  def test_single_attempt_failure_raises_immediately(self):
    """With ``max_attempts=1`` the handshake gets exactly one shot. If
    SW bit 12 doesn't rise, raises straight away (no second attempt)."""
    nid = 4
    script = _SwScript([("set_low",), ("hang",)])

    async def _go():
      drv = _build_driver(nid, script)
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=1)

    with self.assertRaises(CanError) as ctx:
      asyncio.new_event_loop().run_until_complete(_go())
    self.assertIn("after 1 attempts", str(ctx.exception))
    self.assertEqual(script.calls, [47, 63])

  def test_single_attempt_success_returns(self):
    """``max_attempts=1`` happy path: still works on a clean handshake."""
    nid = 4
    script = _SwScript([("set_low",), ("set_high",)])

    async def _go():
      drv = _build_driver(nid, script)
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=1)

    asyncio.new_event_loop().run_until_complete(_go())
    self.assertEqual(script.calls, [47, 63])


if __name__ == "__main__":
  unittest.main()
