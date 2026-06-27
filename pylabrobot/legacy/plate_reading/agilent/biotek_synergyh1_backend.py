"""Legacy. Use pylabrobot.agilent instead."""

from pylabrobot.agilent.biotek.plate_readers.synergy import synergy_h1
from pylabrobot.legacy.plate_reading.agilent.biotek_backend import BioTekPlateReaderBackend


class SynergyH1Backend(BioTekPlateReaderBackend):
  """Legacy. Use pylabrobot.agilent.SynergyH1Backend instead."""

  def __init__(self, timeout: float = 20, device_id=None) -> None:
    self._new = synergy_h1.SynergyH1Backend(timeout=timeout, device_id=device_id)

  @property
  def supports_heating(self):
    return True

  @property
  def supports_cooling(self):
    return False

  @property
  def focal_height_range(self):
    return (4.5, 10.68)
