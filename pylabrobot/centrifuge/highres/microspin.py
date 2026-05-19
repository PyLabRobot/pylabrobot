"""Factory for the HighRes Biosolutions MicroSpin centrifuge.

The MicroSpin is a sealed, automation-friendly microplate centrifuge with two
swing-out buckets (1 SBS plate per bucket). It does not have a separate
plate loader -- plates are placed into the presented bucket directly by a
robot arm, then the door is closed and a spin is started. Because of this,
the :class:`~pylabrobot.centrifuge.Centrifuge` returned here is *not*
paired with a separate :class:`~pylabrobot.centrifuge.Loader` (unlike the
Agilent VSpin + Access2 combo).
"""

from __future__ import annotations

from typing import Optional

from pylabrobot.centrifuge.centrifuge import Centrifuge
from pylabrobot.centrifuge.highres.microspin_backend import MicroSpinBackend

# Spec sheet (manual §11):
#   Dimensions (H x W x L): 15" x 15" x 16"
#   Weight (empty)         : 43 kg
# pylabrobot conventionally uses size_x = width, size_y = depth, size_z = height.
_INCH_MM = 25.4
_MICROSPIN_WIDTH_MM = 15.0 * _INCH_MM  # 381.0 mm
_MICROSPIN_DEPTH_MM = 16.0 * _INCH_MM  # 406.4 mm
_MICROSPIN_HEIGHT_MM = 15.0 * _INCH_MM  # 381.0 mm


def MicroSpin(
  name: str,
  host: str,
  port: int = MicroSpinBackend.DEFAULT_PORT,
  timeout: float = 30.0,
  backend: Optional[MicroSpinBackend] = None,
) -> Centrifuge:
  """Construct a HighRes Biosolutions MicroSpin centrifuge.

  Args:
    name: A descriptive name for this centrifuge instance.
    host: IP address or DNS name of the MicroSpin's Ethernet port. The
      factory default is ``192.168.127.60`` but the IP is configurable on
      the device's ``/network.html`` web page.
    port: TCP port for the remote-control server. Defaults to
      :attr:`MicroSpinBackend.DEFAULT_PORT` (1000, the factory default).
      Pass a different value if the device's ``SERVER_PORT`` setting has
      been changed via ``/network.html``.
    timeout: Default per-command timeout in seconds. The backend extends
      this automatically for long-running operations (spin, home).
    backend: Optionally supply a pre-constructed
      :class:`MicroSpinBackend`. Useful for tests or for sharing a backend
      between front-end objects. If omitted, a new backend is built from
      ``host``/``port``/``timeout``.

  Returns:
    A :class:`~pylabrobot.centrifuge.Centrifuge` wired to a
    :class:`MicroSpinBackend`.
  """
  if backend is None:
    backend = MicroSpinBackend(host=host, port=port, timeout=timeout)

  return Centrifuge(
    backend=backend,
    name=name,
    size_x=_MICROSPIN_WIDTH_MM,
    size_y=_MICROSPIN_DEPTH_MM,
    size_z=_MICROSPIN_HEIGHT_MM,
    model="HighRes Biosolutions MicroSpin",
  )
