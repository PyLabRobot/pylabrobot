from typing import Optional

from .backend import FACSMelodyDriver


class FACSMelodyChatterbox(FACSMelodyDriver):
  """Device-free FACSMelody driver.

  Pinned to ``armed=False`` so it can never open a link or transmit: it exercises
  the real protocol translation (frame building) and logs what it would send,
  which makes it useful for building and testing a full ``FACSMelody`` device with
  no instrument present. For pure capability-level testing, the ``CellSorter``
  capability also ships ``CellSorterChatterboxBackend``.
  """

  def __init__(self, protocol_path: Optional[str] = None):
    super().__init__(protocol_path=protocol_path, armed=False, allow_actuation=False)
