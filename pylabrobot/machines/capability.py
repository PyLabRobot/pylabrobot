from __future__ import annotations

import functools
import sys
from abc import ABC
from typing import Any, Awaitable, Callable, TypeVar

from pylabrobot.machines.backend import MachineBackend

if sys.version_info < (3, 10):
  from typing_extensions import ParamSpec
else:
  from typing import ParamSpec

_P = ParamSpec("_P")
_R = TypeVar("_R", bound=Awaitable[Any])


def need_capability_ready(func: Callable[_P, _R]) -> Callable[_P, _R]:
  """Decorator for methods that require the capability to be set up.

  Checked by verifying `self.setup_finished` is `True`.

  Raises:
    RuntimeError: If the capability is not set up.
  """

  @functools.wraps(func)
  async def wrapper(*args, **kwargs):
    assert isinstance(args[0], Capability), "The first argument must be a Capability."
    self = args[0]

    if not self.setup_finished:
      raise RuntimeError("The capability has not been set up. Call setup() on the parent machine.")
    return await func(*args, **kwargs)

  return wrapper


class Capability(ABC):
  """Base class for machine capabilities (liquid handling, plate reading, etc.).

  Capabilities are owned by a Machine and share its backend. They are not Resources
  and do not appear in the resource tree. The parent Machine is responsible for calling
  `_on_setup()` and `_on_stop()` during its own setup/stop lifecycle.
  """

  def __init__(self, backend: MachineBackend):
    self.backend = backend
    self._setup_finished = False

  @property
  def setup_finished(self) -> bool:
    return self._setup_finished

  async def _on_setup(self):
    """Called by the parent Machine after backend.setup() completes."""
    self._setup_finished = True

  async def _on_stop(self):
    """Called by the parent Machine before backend.stop()."""
    self._setup_finished = False
