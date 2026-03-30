"""VantageChatterboxDriver: prints commands instead of sending them over USB."""

from .driver import VantageDriver


class VantageChatterboxDriver(VantageDriver):
  """Chatterbox driver for Vantage. Prints firmware commands instead of sending them over USB."""

  def __init__(self, num_channels: int = 8):
    super().__init__()
    self._num_channels_override = num_channels

  @property
  def num_channels(self) -> int:
    return self._num_channels_override

  # -- lifecycle: skip USB, use canned config --------------------------------

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    # No USB — just set up backends directly.
    self.id_ = 0
    self._num_channels = self._num_channels_override

    from .pip_backend import VantagePIPBackend

    self.pip = VantagePIPBackend(self, tip_presences=[False] * self._num_channels_override)

    if not skip_core96:
      from .head96_backend import VantageHead96Backend

      self.head96 = VantageHead96Backend(self)
    else:
      self.head96 = None

    if not skip_ipg:
      from .ipg import VantageIPG

      self.ipg = VantageIPG(driver=self)
      self.ipg._parked = True
    else:
      self.ipg = None

    from .led_backend import VantageLEDBackend
    from .loading_cover import VantageLoadingCover
    from .x_arm import VantageXArm

    self.led = VantageLEDBackend(self)
    self.loading_cover = VantageLoadingCover(driver=self)
    self.x_arm = VantageXArm(driver=self)

  async def stop(self):
    self._num_channels = None
    self.head96 = None
    self.ipg = None
    self.led = None
    self.loading_cover = None
    self.x_arm = None

  # -- I/O: print instead of USB --------------------------------------------

  async def send_command(self, module, command, auto_id=True, tip_pattern=None,
                         write_timeout=None, read_timeout=None, wait=True,
                         fmt=None, **kwargs):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id,
      tip_pattern=tip_pattern, **kwargs,
    )
    print(cmd)
    return None

  async def send_raw_command(self, command, write_timeout=None, read_timeout=None,
                             wait=True):
    print(command)
    return None
