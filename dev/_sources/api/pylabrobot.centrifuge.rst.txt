.. currentmodule:: pylabrobot.centrifuge

pylabrobot.centrifuge package
================================

This package contains APIs for working with centrifuges.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    centrifuge.Centrifuge
    centrifuge.Loader


Factory functions
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    access2.Access2
    highres.microspin.MicroSpin


Backends
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    vspin_backend.VSpinBackend
    highres.microspin_backend.MicroSpinBackend


Errors
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    standard.BucketHasPlateError
    standard.BucketNoPlateError
    standard.CentrifugeDoorError
    standard.LoaderNoPlateError
    standard.NotAtBucketError
    highres.microspin_backend.MicroSpinError
    highres.microspin_backend.MicroSpinAbortedError
    highres.microspin_backend.MicroSpinProtocolError


Testing utilities
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    highres.mock_server.MicroSpinMockServer
