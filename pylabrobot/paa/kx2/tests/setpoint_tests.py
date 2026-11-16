"""Unit tests for the KX2 driver's CiA 402 PPM new-setpoint handshake.

Covers ``KX2._trigger_new_setpoint``: the four-step CW/SW dance
(drop bit 4, wait bit 12 low, raise bit 4, wait bit 12 high) and its
retry-on-missed-edge behavior. The drive's SW state is faked by calling
``_dispatch_statusword`` — same shape as the real TPDO3 callback,
without any CAN traffic.
"""

from typing import List, Tuple

import anyio
import pytest

from pylabrobot.paa.kx2.kx2 import KX2
from pylabrobot.paa.kx2.protocol import CanError
from pylabrobot.testing import AnyioTestBase

SW_BIT_12 = 1 << 12


class _SwScript:
  """Drives the fake SW state machine.

  Each step is one of:
    ("set_low",)   -> clear bit 12 immediately
    ("set_high",)  -> raise bit 12 immediately
    ("hang",)      -> leave SW unchanged so _wait_setpoint_ack times out

  The script is consumed in order on each ``_control_word_set`` call.
  """

  def __init__(self, steps: List[Tuple[str, ...]]) -> None:
    self.steps = list(steps)
    self.calls: List[int] = []  # values passed to _control_word_set

  def consume(self) -> Tuple[str, ...]:
    if not self.steps:
      raise AssertionError("script exhausted but _control_word_set was called again")
    return self.steps.pop(0)


def _build_driver(nid: int, script: _SwScript) -> KX2:
  drv = KX2()
  drv._statusword = {nid: 0}
  drv._statusword_event = {nid: anyio.Event()}

  async def _fake_cw_set(node_id: int, value: int, sync: bool = True) -> None:
    del sync
    script.calls.append(value)
    step = script.consume()
    tag = step[0]
    if tag == "set_low":
      drv._dispatch_statusword(node_id, drv._statusword.get(node_id, 0) & ~SW_BIT_12)
    elif tag == "set_high":
      drv._dispatch_statusword(node_id, drv._statusword.get(node_id, 0) | SW_BIT_12)
    elif tag == "hang":
      # Leave SW unchanged. _wait_sw_bit will exhaust its 50 ms timeout
      # waiting on the event + falling back to SDO; we also stub the SDO
      # path below to return the cached SW (= still wrong) so it always
      # times out.
      pass
    else:
      raise AssertionError(f"unknown step {tag!r}")

  async def _fake_sdo_upload(node_id: int, index: int, sub_index: int) -> bytes:
    del index, sub_index
    return drv._statusword.get(node_id, 0).to_bytes(2, "little") + b"\x00\x00"

  drv._control_word_set = _fake_cw_set  # type: ignore[method-assign]
  drv._can_sdo_upload = _fake_sdo_upload  # type: ignore[method-assign]
  return drv


class TestTriggerNewSetpointHappyPath(AnyioTestBase):
  async def test_first_attempt_succeeds(self) -> None:
    """SW bit 12 clears, then rises promptly -> returns, no raise.
    `_control_word_set` is called exactly twice (cw_low then cw_high)."""
    nid = 1
    script = _SwScript([("set_low",), ("set_high",)])
    drv = _build_driver(nid, script)
    await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)
    assert script.calls == [47, 63]
    assert script.steps == []


class TestTriggerNewSetpointRetryThenSuccess(AnyioTestBase):
  async def test_second_attempt_succeeds(self) -> None:
    """First attempt: bit 12 clears but never rises (timeout). Second
    attempt: clears + rises -> returns. CW is set 4 times total."""
    nid = 1
    script = _SwScript(
      [
        ("set_low",),
        ("hang",),
        ("set_low",),
        ("set_high",),
      ]
    )
    drv = _build_driver(nid, script)
    await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)
    assert script.calls == [47, 63, 47, 63]
    assert script.steps == []


class TestTriggerNewSetpointAllAttemptsFail(AnyioTestBase):
  async def test_raises_after_max_attempts(self) -> None:
    """SW bit 12 never rises -> raises CanError tagged with the axis nid.
    With max_attempts=2 the handshake runs twice (4 CW writes) then gives up."""
    nid = 3
    script = _SwScript(
      [
        ("set_low",),
        ("hang",),
        ("set_low",),
        ("hang",),
      ]
    )
    drv = _build_driver(nid, script)
    with pytest.raises(CanError) as ctx:
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=2)
    msg = str(ctx.value)
    assert f"Axis {nid}" in msg
    assert "did not accept new PPM setpoint" in msg
    assert "after 2 attempts" in msg
    assert script.calls == [47, 63, 47, 63]


class TestTriggerNewSetpointBit12StuckHigh(AnyioTestBase):
  async def test_recovers_when_bit12_does_not_clear_on_first_cw_low(self) -> None:
    """Pre-existing bit 12 high. Attempt 1 cw_low fails to clear it -> the
    inner ``cleared`` check is False, the attempt restarts (continues the
    loop) without writing cw_high. Attempt 2's cw_low clears, cw_high raises.

    State machine recovery proof: cw_high is NOT written on attempt 1
    (script only sees [cw_low, cw_low, cw_high])."""
    nid = 2
    script = _SwScript(
      [
        ("hang",),
        ("set_low",),
        ("set_high",),
      ]
    )
    drv = _build_driver(nid, script)
    drv._statusword[nid] = SW_BIT_12  # bit 12 high before any handshake
    await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63)
    assert script.calls == [47, 47, 63]
    assert script.steps == []


class TestTriggerNewSetpointMaxAttemptsOne(AnyioTestBase):
  async def test_single_attempt_failure_raises_immediately(self) -> None:
    """With ``max_attempts=1`` the handshake gets exactly one shot. If
    SW bit 12 doesn't rise, raises straight away (no second attempt)."""
    nid = 4
    script = _SwScript([("set_low",), ("hang",)])
    drv = _build_driver(nid, script)
    with pytest.raises(CanError) as ctx:
      await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=1)
    assert "after 1 attempts" in str(ctx.value)
    assert script.calls == [47, 63]

  async def test_single_attempt_success_returns(self) -> None:
    """``max_attempts=1`` happy path: still works on a clean handshake."""
    nid = 4
    script = _SwScript([("set_low",), ("set_high",)])
    drv = _build_driver(nid, script)
    await drv._trigger_new_setpoint(nid, cw_low=47, cw_high=63, max_attempts=1)
    assert script.calls == [47, 63]
