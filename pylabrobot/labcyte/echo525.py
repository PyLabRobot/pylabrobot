"""Deprecated Echo 525 aliases.

The Echo 525 is no longer a separate class — it is the same :class:`~pylabrobot.labcyte.echo.Echo`
frontend / :class:`~pylabrobot.labcyte.echo.EchoDriver` selected with ``model="Echo 525"``:

    from pylabrobot.labcyte import Echo
    echo = Echo("192.168.0.25", model="Echo 525")   # 25 nL increment, 2.6/2.7.3 versions

``Echo525`` and ``Echo525Driver`` remain as thin deprecation shims for backwards compatibility.
The 525-specific defaults now live in ``ECHO_MODELS["Echo 525"]`` in ``echo.py``.
"""

from __future__ import annotations

import warnings
from typing import Any, Optional

from pylabrobot.labcyte.echo import ECHO_MODELS, Echo, MedmanEchoDriver

_ECHO_525 = ECHO_MODELS["Echo 525"]

#: Deprecated module-level constants kept for backwards compatibility.
ECHO_525_TRANSFER_VOLUME_INCREMENT_NL = _ECHO_525.transfer_volume_increment_nl
ECHO_525_CLIENT_VERSION = _ECHO_525.client_version
ECHO_525_PROTOCOL_VERSION = _ECHO_525.protocol_version
ECHO_525_MODEL_NAME = _ECHO_525.name


class Echo525Driver(MedmanEchoDriver):
  """Deprecated. Use ``MedmanEchoDriver(host, model="Echo 525")``."""

  def __init__(self, host: str, **kwargs: Any):
    warnings.warn(
      'Echo525Driver is deprecated; use MedmanEchoDriver(host, model="Echo 525").',
      DeprecationWarning,
      stacklevel=2,
    )
    kwargs.setdefault("model", "Echo 525")
    super().__init__(host=host, **kwargs)


class Echo525(Echo):
  """Deprecated. Use ``Echo(host, model="Echo 525")``."""

  def __init__(self, host: Optional[str] = None, **kwargs: Any):
    warnings.warn(
      'Echo525 is deprecated; use Echo(host, model="Echo 525").',
      DeprecationWarning,
      stacklevel=2,
    )
    kwargs.setdefault("model", "Echo 525")
    super().__init__(host=host, **kwargs)
