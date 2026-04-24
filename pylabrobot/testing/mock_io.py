import anyio

from pylabrobot.io.io import IOBase


class CustomReadMock:
  def __init__(self):
    self.side_effect = None

  async def __call__(self, *args, **kwargs):
    await anyio.sleep(0)
    if self.side_effect is None:
      return b""
    if isinstance(self.side_effect, list):
      if not self.side_effect:
        raise IndexError("Mock side effect list exhausted")
      return self.side_effect.pop(0)
    if callable(self.side_effect):
      return self.side_effect(*args, **kwargs)
    return self.side_effect

  def reset_mock(self):
    self.side_effect = None


class CustomWriteMock:
  def __init__(self):
    self.side_effect = None

  async def __call__(self, data: bytes, *args, **kwargs):
    await anyio.sleep(0)
    if callable(self.side_effect):
      return self.side_effect(data, *args, **kwargs)

  def reset_mock(self):
    self.side_effect = None


class MockIO(IOBase):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._read = CustomReadMock()
    self._write = CustomWriteMock()

  async def _enter_lifespan(self, stack, **kwargs):
    pass

  @property
  def write(self):
    return self._write

  @property
  def read(self):
    return self._read
