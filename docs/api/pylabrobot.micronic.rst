.. currentmodule:: pylabrobot.micronic

pylabrobot.micronic package
===========================

Micronic integrations built on the rack-reading and barcode-scanning capabilities.

Device
------

.. currentmodule:: pylabrobot.micronic.code_reader.code_reader

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicCodeReader
    MicronicDirectCodeReader


Driver
------

.. currentmodule:: pylabrobot.micronic.code_reader.driver

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorDriver
    MicronicIOMonitorState
    MicronicRackReaderDriver
    MicronicError

.. currentmodule:: pylabrobot.micronic.code_reader.direct_driver

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicDirectDriver
    MicronicDirectRackReaderError


Capabilities
------------

.. currentmodule:: pylabrobot.micronic.code_reader.rack_reading_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorRackReadingBackend
    MicronicRackReadingBackend
    MicronicDirectRackReadingBackend
    MicronicRackReaderError

.. currentmodule:: pylabrobot.micronic.code_reader.barcode_scanning_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorBarcodeScannerBackend
    MicronicBarcodeScannerError
