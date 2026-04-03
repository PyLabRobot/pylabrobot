"""VantageChatterboxDriver: mock driver that prints commands instead of sending to hardware."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from .driver import VantageDriver

logger = logging.getLogger("pylabrobot")


class VantageChatterboxDriver(VantageDriver):
  """A VantageDriver that prints firmware commands instead of communicating with hardware.

  Useful for testing, debugging, and development without a physical Vantage.
  """

  def __init__(self):
    super().__init__()

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    # Skip USB and hardware discovery entirely.
    # Import backends here to avoid circular imports.
    from .head96_backend import VantageHead96Backend
    from .ipg import IPGBackend
    from .loading_cover import VantageLoadingCover
    from .pip_backend import VantagePIPBackend
    from .x_arm import VantageXArm

    self.id_ = 0
    self._num_channels = 8

    self.pip = VantagePIPBackend(self)
    self.head96 = VantageHead96Backend(self) if not skip_core96 else None
    self.ipg = IPGBackend(driver=self) if not skip_ipg else None
    if self.ipg is not None:
      self.ipg._parked = True
    self.x_arm = VantageXArm(driver=self)
    self.loading_cover = VantageLoadingCover(driver=self)

    # Initialize subsystems.
    for sub in self._subsystems:
      await sub._on_setup()

  async def stop(self):
    # Stop subsystems (no-ops for chatterbox, but follows the pattern).
    for sub in reversed(self._subsystems):
      await sub._on_stop()
    # Clear state (skip super().stop() since there is no USB to close).
    self._num_channels = None
    self._tth2tti.clear()
    self.head96 = None
    self.ipg = None
    self.x_arm = None
    self.loading_cover = None

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module,
      command=command,
      tip_pattern=tip_pattern,
      auto_id=auto_id,
      **kwargs,
    )
    logger.info("Chatterbox: %s", cmd)
    return None

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    logger.info("Chatterbox raw: %s", command)
    return None
