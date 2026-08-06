"""KingFisher Duo backend — inherits all HID logic from KingFisherPrestoBackend."""

import xml.etree.ElementTree as ET
from typing import Callable, Optional

from pylabrobot.particle_processing.kingfisher.bdz import DUO_CONTRACT, InstrumentContract
from pylabrobot.particle_processing.kingfisher.kingfisher_backend import KingFisherBackend
from pylabrobot.particle_processing.kingfisher.presto_connection import (
  KINGFISHER_PID,
  KINGFISHER_VID,
)


class KingFisherDuoBackend(KingFisherBackend):
  """Backend for the KingFisher Duo magnetic particle processor.

  Inherits all USB HID connection and command logic from
  :class:`KingFisherPrestoBackend`.  Override ``_DUO_VID`` / ``_DUO_PID``
  once the Duo's USB VID/PID are confirmed from hardware.

  .. note::

      The Duo's USB VID/PID are not yet confirmed.  Connect the Duo and run
      ``lsusb`` (Linux/macOS) or check Device Manager (Windows) to find them.
      The Presto uses ``VID=0x0AB6 / PID=0x02C9``; replace the placeholders
      below once confirmed.

  Example::

      backend = KingFisherDuoBackend()
      kf = KingFisher(backend=backend)
      await kf.setup()
  """

  # TODO: confirm Duo USB VID/PID — using Presto values as placeholder
  _DUO_VID = KINGFISHER_VID
  _DUO_PID = KINGFISHER_PID

  def __init__(
    self,
    serial_number: Optional[str] = None,
    on_event: Optional[Callable[[ET.Element], None]] = None,
  ) -> None:
    super().__init__(
      vid=self._DUO_VID,
      pid=self._DUO_PID,
      serial_number=serial_number,
      on_event=on_event,
    )

  @property
  def contract(self) -> InstrumentContract:
    """Instrument contract carrying all Duo-specific structural constants."""
    return DUO_CONTRACT
