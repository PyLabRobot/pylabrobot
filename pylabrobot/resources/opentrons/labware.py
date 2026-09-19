"""Explicit, serializable Opentrons labware identity metadata."""

from pylabrobot.resources.resource import Resource


def set_opentrons_labware(
  resource: Resource, load_name: str, version: int = 1, namespace: str = "opentrons"
) -> None:
  """Choose the server definition used to load a resource on an Opentrons robot."""
  if (
    not isinstance(load_name, str)
    or not isinstance(namespace, str)
    or not load_name
    or not namespace
    or not isinstance(version, int)
    or isinstance(version, bool)
    or version < 1
  ):
    raise ValueError("Labware identity needs a namespace, load name, and positive integer version")
  resource.metadata["opentrons_labware"] = {
    "namespace": namespace,
    "load_name": load_name,
    "version": version,
  }
