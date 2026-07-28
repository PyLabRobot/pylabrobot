.. currentmodule:: pylabrobot.celigo

pylabrobot.celigo package
=========================

Celigo
------

Load the vendor configuration explicitly, construct the instrument, then assign the
PyLabRobot plate used for well navigation:

.. code-block:: python

   from pylabrobot.celigo import Celigo, CeligoConfig
   from pylabrobot.resources.corning.plates import Cor_96_wellplate_360ul_Fb

   config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
   celigo = Celigo(config=config)
   celigo.set_plate(Cor_96_wellplate_360ul_Fb(name="imaging_plate"))

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Celigo
    AcquisitionResult
    FocusResult
    ControllerInfo
    ControllerStatus
    DetectedMotorAddress
    SelfTestReport

Camera
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    CeligoCamera
    CameraFrame
    CameraError

Motion and optics
-----------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Axis
    LinearAxis
    StepperMotor
    MotorController
    FilterWheel
    MagnificationChanger
    Galvo
    GalvoControllerStatus
    Laser

Configuration
-------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    CeligoConfig
    CeligoHardwareConfig
    AxisConfig
    LinearAxisConfig
    FilterWheelConfig
    FilterMapEntry
    IOConfig
    AnalogInputConfig
    DigitalIOConfig
    LightingIOConfig
    HardwareDefaultConfig
    NavigationConfig
    CalibrationConfig
    Calibrated2DPolynomialTransform
    ChannelDescriptor
    ExternalCameraControlConfig
    GalvoAxisOpticalCalibration
    GalvoConfig
    GalvoMagnificationCalibration
    GalvoOpticalCalibration
    IlluminationChannelConfig

Coordinates and navigation
--------------------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    CoordinateSystems
    well_to_stage_mm

Configuration loaders
---------------------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    load_channel_descriptors
    load_galvo_calibrations
    load_galvo_optical_calibration
    load_illumination_channels

Errors
------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    CeligoError
