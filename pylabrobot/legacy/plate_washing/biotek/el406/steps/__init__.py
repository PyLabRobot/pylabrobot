"""EL406 protocol step methods.

This package contains the mixin class for protocol step operations on the
BioTek EL406 plate washer (prime, dispense, aspirate, wash, shake, etc.).

The methods are split into per-subsystem modules for maintainability, but
the composed ``EL406StepsMixin`` class is the only public API.
"""

from ._manifold import EL406ManifoldStepsMixin
from ._peristaltic import EL406PeristalticStepsMixin
from ._shake import EL406ShakeStepsMixin
from ._syringe import EL406SyringeStepsMixin


class EL406StepsMixin(
  EL406PeristalticStepsMixin,
  EL406SyringeStepsMixin,
  EL406ManifoldStepsMixin,
  EL406ShakeStepsMixin,
):
  """Mixin providing all protocol step methods for the EL406.

  This class composes all per-subsystem step mixins:
  - Peristaltic: peristaltic_prime, peristaltic_dispense, peristaltic_purge
  - Syringe: syringe_dispense, syringe_prime
  - Manifold: manifold_aspirate, manifold_dispense, manifold_wash, manifold_prime, manifold_auto_clean
  - Shake: shake

  Requires:
    self._send_step_command: Async method for sending framed commands
    self.timeout: Default timeout in seconds
  """
