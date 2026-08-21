"""Pure-Python controller stack for Darwin-generation Bravo instruments.

Submodules:
  topology     -- node-tree layout (axis <-> InstructionAddress)
  axis         -- per-axis state machines (commutate/home/initialize)
  params       -- pointer-cached parameter database access
  waxis_params -- W-axis per-head-type PID/motion table
  calibration  -- hardware ranges and mm/normalized conversion
  waxis_config -- per-head-type W-axis calibration and unit conversion
  motion       -- instruction load, trigger, and settle polling
  sequences    -- composite procedures (grip, open_gripper, jog)
  controller   -- DarwinController(BravoController) facade
"""

from __future__ import annotations

from .controller import DarwinController

__all__ = ["DarwinController"]
