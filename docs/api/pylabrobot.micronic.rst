.. currentmodule:: pylabrobot.micronic

pylabrobot.micronic package
===========================

Micronic Code Reader integration built on the rack-reading and barcode-scanning capabilities.

Device
------

.. currentmodule:: pylabrobot.micronic.code_reader.code_reader

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicCodeReader


Driver
------

.. currentmodule:: pylabrobot.micronic.code_reader.driver

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorDriver
    MicronicIOMonitorState
    MicronicError


Capabilities
------------

.. currentmodule:: pylabrobot.micronic.code_reader.rack_reading_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorRackReadingBackend
    MicronicRackReaderError

.. currentmodule:: pylabrobot.micronic.code_reader.barcode_scanning_backend

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    MicronicIOMonitorBarcodeScannerBackend
    MicronicBarcodeScannerError
