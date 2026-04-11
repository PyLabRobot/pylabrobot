.. currentmodule:: pylabrobot.arms

pylabrobot.arms package
=======================

Arm capabilities for picking up, moving, and placing labware.

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    arm.GripperArm
    orientable_arm.OrientableArm


Backends
--------

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    backend.GripperArmBackend
    backend.OrientableGripperArmBackend
    backend.ArticulatedGripperArmBackend
    backend.CanFreedrive
    backend.HasJoints
    backend.CanGrip


Types
-----

.. autosummary::
  :toctree: _autosummary
  :nosignatures:
  :recursive:

    standard.GripperLocation
    standard.GripDirection
