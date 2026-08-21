"""Wire-protocol implementations for Agilent Bravo controllers.

A Bravo instrument speaks one of two unrelated binary protocols depending on
its controller generation:

- ``gemini`` -- the framed TCP protocol used by Darwin-generation firmware.
- The V11/Agile protocol (``agile_packet``, ``agile_7612_packet``,
  ``agile_7612_commands``, ``agile_7612_crc``, ``commands``, ``v11_comm``,
  ``v11_agile_7612_comm``) -- the length-prefixed protocol used to reach the
  Rabbit microcontroller on Agile and Agile 7612 controllers.

Both protocols encode and decode bytes only; neither module in this package
opens a connection. Callers construct a
:class:`~pylabrobot.agilent.bravo.transport.Transport` themselves and hand it
to a comm class (:class:`~pylabrobot.agilent.bravo.protocol.gemini.engine.GeminiEngine`,
:class:`~pylabrobot.agilent.bravo.protocol.v11_comm.V11DeviceComm`, or
:class:`~pylabrobot.agilent.bravo.protocol.v11_agile_7612_comm.V11Agile7612DeviceComm`).
"""

from __future__ import annotations
