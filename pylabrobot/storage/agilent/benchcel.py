"""Factory for the Agilent BenchCel 4R storage device."""

from __future__ import annotations

from typing import List, Optional, Union

from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.resource_stack import ResourceStack
from pylabrobot.storage.stacker import Stacker

from .benchcel_backend import BenchCel4RBackend
from .benchcel_labware import BenchCelLabwareSettings, resolve_benchcel_labware_settings
from .stacks import benchcel_4r_stacks


def BenchCel4R(
  name: str,
  host: str,
  *,
  port: int = BenchCel4RBackend.DEFAULT_PORT,
  timeout: float = 30.0,
  read_poll_timeout: float = 0.25,
  loading_tray_teachpoint_id: Optional[int] = None,
  source_ip: Optional[str] = None,
  backend: Optional[BenchCel4RBackend] = None,
  stacks: Optional[List[ResourceStack]] = None,
  labware: Optional[Union[Plate, BenchCelLabwareSettings, dict]] = None,
  loading_tray_location: Optional[Coordinate] = None,
  size_x: float = 0.0,
  size_y: float = 0.0,
  size_z: float = 0.0,
) -> Stacker:
  """Construct an Agilent BenchCel 4R as a PLR :class:`~pylabrobot.storage.Stacker`.

  The BenchCel is a sequential ("stacking access") storage device: each of its four stackers is a
  single-ended LIFO stack of plates. It is therefore modelled with the ``Stacker`` capability
  rather than the random-access ``Incubator``. The generated ``ResourceStack`` stacks track the
  expected plate order/content of each stacker; their height follows each plate's
  ``stacking_z_height``.

  Args:
    name: Resource name for the returned :class:`~pylabrobot.storage.Stacker`. Like all PLR
      resources, the BenchCel needs a unique name for the resource tree, serialization, and
      lookups (consistent with other device factories such as ``MicroSpin(...)``).
    host: IP address or DNS name of the BenchCel Ethernet interface.
    loading_tray_teachpoint_id: Teachpoint target ID used as the transfer point by
      ``downstack``/``upstack``. The BenchCel has no fixed loading position; this must be a
      teachpoint taught on the device. Transfers raise unless it is set here or passed per call.
      There is no default because an unset/wrong teachpoint can send the arm to a home-like pose.
    stacks: Optionally provide custom ``ResourceStack`` stacks; defaults to four generic stacks.
    loading_tray_location: Cosmetic only. The ``Coordinate`` of the ``Stacker.loading_tray``
      resource used for the resource tree and visualization. It does NOT drive any motion -- the
      real transfer position is determined entirely by ``loading_tray_teachpoint_id`` on the
      device.
  """
  labware_settings = resolve_benchcel_labware_settings(labware) if labware is not None else None

  if backend is None:
    backend = BenchCel4RBackend(
      host=host,
      port=port,
      timeout=timeout,
      read_poll_timeout=read_poll_timeout,
      loading_tray_teachpoint_id=loading_tray_teachpoint_id,
      source_ip=source_ip,
      labware=labware_settings,
    )
  elif labware_settings is not None:
    existing_labware = backend.labware_settings
    if existing_labware is not None and existing_labware != labware_settings:
      raise ValueError(
        "BenchCel4R backend labware "
        f"{existing_labware.name!r} does not match factory labware "
        f"{labware_settings.name!r}"
      )
    backend.labware_settings = labware_settings

  if stacks is None:
    stacks = benchcel_4r_stacks()

  return Stacker(
    backend=backend,
    name=name,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    stacks=stacks,
    loading_tray_location=loading_tray_location or Coordinate.zero(),
    model="Agilent BenchCel 4R",
  )
