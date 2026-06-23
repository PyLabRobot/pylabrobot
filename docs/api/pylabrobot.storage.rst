.. currentmodule:: pylabrobot.storage

pylabrobot.storage package
==========================

This package contains APIs for automated storage devices and incubators.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    incubator.Incubator
    stacker.Stacker
    agilent.benchcel.BenchCel4R


Backends
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backend.IncubatorBackend
    stacker_backend.StackerBackend
    stacker_chatterbox.StackerChatterboxBackend
    agilent.benchcel_backend.BenchCel4RBackend
    cytomat.cytomat.CytomatBackend
    liconic.liconic_backend.ExperimentalLiconicBackend


Agilent BenchCel support classes
--------------------------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    agilent.benchcel_backend.Frame
    agilent.benchcel_backend.SensorStatus
    agilent.benchcel_backend.ArmStatus
    agilent.benchcel_backend.GeneralStatus
    agilent.benchcel_backend.Teachpoint
    agilent.benchcel_backend.AxisBoundsResponse
    agilent.benchcel_backend.CurrentPositionResponse
    agilent.benchcel_labware.BenchCelLabwareSettings
    agilent.benchcel_labware.PlateNotchSettings
    agilent.benchcel_labware.apply_benchcel_labware_settings
    agilent.benchcel_labware.calculate_benchcel_labware_settings
    agilent.benchcel_labware.calculate_robot_gripper_offset
    agilent.benchcel_labware.calculate_sensor_offset
    agilent.benchcel_labware.calculate_stacker_gripper_offset
    agilent.benchcel_labware.calculate_stacking_thickness
    agilent.benchcel_mock_server.BenchCelMockServer
    agilent.stacks.benchcel_4r_stacks


Errors
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    agilent.benchcel_backend.BenchCelDeviceError
    agilent.benchcel_backend.BenchCelProtocolError
    agilent.benchcel_backend.BenchCelTimeoutError
