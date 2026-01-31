.. currentmodule:: pylabrobot.plate_reading

pylabrobot.plate_reading package
================================

This package contains APIs for working with plate readers.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

   plate_reader.PlateReader
   imager.Imager
   standard.ImagingResult


Backends
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    chatterbox.PlateReaderChatterboxBackend
    bmg_labtech.clario_star_backend.CLARIOstarBackend
    agilent.biotek_cytation_backend.CytationBackend
    agilent.biotek_synergyh1_backend.SynergyH1Backend
