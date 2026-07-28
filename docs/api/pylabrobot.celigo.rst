.. currentmodule:: pylabrobot.celigo

pylabrobot.celigo package
=========================

Celigo
------

Load the vendor configuration explicitly before constructing the instrument:

.. code-block:: python

   config = CeligoConfig.from_install("/path/to/Celigo/ConfigFiles")
   celigo = Celigo(config=config)

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    Celigo
    Axis
    LinearAxis
    FilterWheel
    MagnificationChanger
    StepperMotor
    Galvo
    GalvoControllerStatus
    Laser
    ControllerInfo
    ControllerStatus
    DetectedMotorAddress
    SelfTestReport
    CameraFrame
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
