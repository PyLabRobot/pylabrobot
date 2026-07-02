"""VantageChatterboxDriver: mock driver that prints commands instead of sending to hardware."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from .driver import VantageDriver

logger = logging.getLogger("pylabrobot")


class VantageChatterboxDriver(VantageDriver):
  """A VantageDriver that logs firmware commands instead of communicating with hardware.

  This mock driver is used for testing, debugging, and development without a physical
  Hamilton Vantage instrument. All firmware commands are logged at INFO level via the
  ``pylabrobot`` logger instead of being sent over USB.

  The chatterbox driver:

  - Skips USB connection and hardware discovery entirely.
  - Assumes 8 PIP channels.
  - Creates all subsystem backends (PIP, Head96, IPG, X-arm, loading cover) with no
    firmware initialization.
  - Returns None from all :meth:`send_command` and :meth:`send_raw_command` calls.

  Usage::

    from pylabrobot.hamilton.liquid_handlers.vantage import Vantage

    vantage = Vantage(deck=deck, chatterbox=True)
    await vantage.setup()  # no hardware needed
  """

  def __init__(self):
    super().__init__()

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    """Set up the chatterbox driver without any hardware communication.

    Creates all subsystem backends in mock mode. See :meth:`VantageDriver.setup`
    for the ``skip_*`` parameters.
    """
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
    self.loading_cover = VantageLoadingCover(driver=self) if not skip_loading_cover else None

    # _on_setup() is deliberately not called on the subsystems here. Real-driver
    # hooks issue status-query firmware commands (e.g. query_tip_presence) that
    # would fail because chatterbox's send_command returns None instead of a
    # parsed response dict. All firmware commands through this driver are
    # logged-and-dropped, so the subsystems never need real initialization.

  async def stop(self):
    """Stop the chatterbox driver and clear subsystem state.

    Calls ``_on_stop()`` on all subsystems (no-ops in chatterbox mode) and clears
    internal state. Does not call ``super().stop()`` since there is no USB connection.
    """
    # Stop subsystems (no-ops for chatterbox, but follows the pattern).
    for sub in reversed(self._subsystems):
      await sub._on_stop()
    # Clear state (skip super().stop() since there is no USB to close).
    self._num_channels = None
    self._tth2tti.clear()
    self.pip = None
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
    """Assemble a firmware command string and log it instead of sending over USB.

    Returns None (no firmware response in chatterbox mode).
    """
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
    """Log a raw firmware command string instead of sending over USB.

    Returns None (no firmware response in chatterbox mode).
    """
    logger.info("Chatterbox raw: %s", command)
    return None
