import anyio
import pytest
from trio.testing import MockClock

from pylabrobot.legacy.plate_reading.imager import _golden_ratio_search
from pylabrobot.testing.concurrency import AnyioTestBase


class TestGoldenRatioSearch(AnyioTestBase):
  _anyio_backends = {"trio": {"clock": MockClock(autojump_threshold=0)}}

  async def test_stalled_objective_times_out(self) -> None:
    async def stalled_objective(value: float) -> float:
      del value
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="Timeout while searching for optimal focus position"):
        await _golden_ratio_search(stalled_objective, 0, 10, 0.1, timeout=0.01)

  async def test_golden_ratio_search_uses_backend_native_clock(self) -> None:
    async def objective(value: float) -> float:
      await anyio.sleep(1)
      return value

    with pytest.raises(TimeoutError, match="Timeout while searching for optimal focus position"):
      await _golden_ratio_search(objective, 0, 10, 0.1, timeout=2)
