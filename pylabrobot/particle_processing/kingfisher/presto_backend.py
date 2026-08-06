"""KingFisher Presto backend — thin subclass of KingFisherBackend."""

import xml.etree.ElementTree as ET
from typing import Callable, Optional

from pylabrobot.particle_processing.kingfisher.bdz import InstrumentContract, PRESTO_CONTRACT
from pylabrobot.particle_processing.kingfisher.kingfisher_backend import (
  KingFisherBackend,
  TurntableLocation,  # re-exported for backward compatibility
)
from pylabrobot.particle_processing.kingfisher.presto_connection import (
  KINGFISHER_PID,
  KINGFISHER_VID,
)

__all__ = ["KingFisherPrestoBackend", "TurntableLocation"]


class KingFisherPrestoBackend(KingFisherBackend):
  """Backend for the KingFisher Presto magnetic particle processor.

  Inherits all USB HID connection and command logic from
  :class:`KingFisherBackend`.  Provides the Presto-specific
  :attr:`contract` and USB VID/PID defaults.
  """

  def __init__(
    self,
    vid: int = KINGFISHER_VID,
    pid: int = KINGFISHER_PID,
    serial_number: Optional[str] = None,
    on_event: Optional[Callable[[ET.Element], None]] = None,
  ) -> None:
    super().__init__(
      vid=vid,
      pid=pid,
      serial_number=serial_number,
      on_event=on_event,
    )

  @property
  def contract(self) -> InstrumentContract:
    """Instrument contract carrying all Presto-specific structural constants."""
    return PRESTO_CONTRACT
