"""Opentrons OT-2 Device/Driver/PIPBackend for the capability architecture."""

__all__ = [
  "OpentronsOT2",
  "OpentronsOT2Driver",
  "OpentronsOT2PIPBackend",
  "OpentronsOT2SimulatorDriver",
  "OpentronsOT2SimulatorPIPBackend",
]

# Lazy imports: this package is reachable from the legacy backends __init__,
# which is imported when pylabrobot.legacy.liquid_handling loads, which can
# happen before pylabrobot.capabilities.liquid_handling finishes initializing.


def __getattr__(name):
  if name == "OpentronsOT2Driver":
    from .driver import OpentronsOT2Driver

    return OpentronsOT2Driver
  if name == "OpentronsOT2PIPBackend":
    from .pip_backend import OpentronsOT2PIPBackend

    return OpentronsOT2PIPBackend
  if name == "OpentronsOT2":
    from .ot2 import OpentronsOT2

    return OpentronsOT2
  if name == "OpentronsOT2SimulatorDriver":
    from .simulator import OpentronsOT2SimulatorDriver

    return OpentronsOT2SimulatorDriver
  if name == "OpentronsOT2SimulatorPIPBackend":
    from .simulator import OpentronsOT2SimulatorPIPBackend

    return OpentronsOT2SimulatorPIPBackend
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
