import anyio
import pytest

from pylabrobot.concurrency import HasLifespan
from pylabrobot.legacy.scales.chatterbox import ScaleChatterboxBackend
from pylabrobot.testing.concurrency import AnyioTestBase


class TestScaleChatterbox(AnyioTestBase):
  def test_scale_chatterbox_inherits_managed_methods(self) -> None:
    assert ScaleChatterboxBackend.setup is HasLifespan.setup
    assert ScaleChatterboxBackend.stop is HasLifespan.stop

  async def test_scale_chatterbox_rejects_options_without_output(self, capsys) -> None:
    backend = ScaleChatterboxBackend()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(port="unused"):
        pytest.fail("invalid entry reached body")
    assert capsys.readouterr().out == ""
    assert not backend.is_active

  async def test_scale_chatterbox_normal_and_cancelled_exit_are_resource_free(self, capsys) -> None:
    backend = ScaleChatterboxBackend(dummy_weight=1.25)
    with anyio.CancelScope() as scope:
      async with backend:
        assert await backend.read_weight() == 1.25
        scope.cancel()
    assert not backend.is_active
    assert capsys.readouterr().out == (
      "Setting up the scale.\nReading the weight\nStopping the scale.\n"
    )
