"""Nimbus device: wires NimbusDriver backends to PIP capability frontend."""

from typing import Optional

from pylabrobot.capabilities.liquid_handling.pip import PIP
from pylabrobot.device import Device
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .chatterbox import NimbusChatterboxDriver
from .driver import NimbusDriver


class Nimbus(Device):
  """Hamilton Nimbus liquid handler.

  User-facing device that wires the PIP capability frontend to the
  NimbusDriver's PIP backend after hardware discovery during setup().
  """

  def __init__(
    self,
    deck: NimbusDeck,
    chatterbox: bool = False,
    host: Optional[str] = None,
    port: int = 2000,
  ):
    if chatterbox:
      driver: NimbusDriver = NimbusChatterboxDriver()
    else:
      if not host:
        raise ValueError("host must be provided when chatterbox is False.")
      driver = NimbusDriver(host=host, port=port)
    super().__init__(driver=driver)
    self.driver: NimbusDriver = driver
    self.deck = deck
    self.pip: PIP  # set in setup()

  async def setup(self):
    """Initialize the Nimbus instrument.

    Establishes the TCP connection, discovers hardware objects, queries channel
    configuration and tip presence, locks the door (if available), conditionally
    runs InitializeSmartRoll, and wires the PIP capability frontend to the driver's
    PIP backend.
    """
    try:
      await self.driver.setup(deck=self.deck)

      self.pip = PIP(backend=self.driver.pip)
      self._capabilities = [self.pip]
      await self.pip._on_setup()
      self._setup_finished = True
    except Exception:
      await self.driver.stop()
      raise

  async def stop(self):
    """Tear down the Nimbus instrument.

    Stops all capabilities in reverse order and closes the driver connection.
    """
    if not self._setup_finished:
      return
    for cap in reversed(self._capabilities):
      await cap._on_stop()
    await self.driver.stop()
    self._setup_finished = False

  # -- Convenience methods delegating to driver/subsystems --------------------

  async def park(self):
    """Park the instrument."""
    await self.driver.park()

  async def lock_door(self):
    """Lock the door."""
    if self.driver.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    await self.driver.door.lock()

  async def unlock_door(self):
    """Unlock the door."""
    if self.driver.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    await self.driver.door.unlock()

  async def is_door_locked(self) -> bool:
    """Check if the door is locked."""
    if self.driver.door is None:
      raise RuntimeError("Door lock is not available on this instrument.")
    return await self.driver.door.is_locked()
