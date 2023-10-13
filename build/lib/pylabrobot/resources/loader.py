# type: ignore

import inspect

import lwdb

import pylabrobot.resources as resources_module
from pylabrobot.resources import Resource


client = lwdb.Client()


def deserialize_resource(dict_resource: dict, name: str = None) -> Resource:
  """ Deserialize a single resource. """

  resource_classes = [c[0] for c in inspect.getmembers(resources_module)]

  # Get class name.
  class_name = dict_resource["type"]
  if class_name in resource_classes:
    klass = getattr(resources_module, class_name)

    # LWDb does not store the name of the resource, so we need to add it.
    # TODO: we should serialize the definition separately from the resource.
    if name is not None:
      dict_resource["name"] = name

    resource = klass.deserialize(dict_resource)
    for child_dict in dict_resource["children"]:
      child_resource = deserialize_resource(child_dict)
      resource.assign_child_resource(child_resource)
    return resource
  else:
    raise ValueError(f"Resource with classname {class_name} not found.")


def load_resource(lwdb_name: str, resource_name: str, skip_cache: bool = False) -> Resource:
  """ Load a resource from lwdb.

  Args:
    lwdb_name: The name of the definition in lwdb.
    resource_name: The name of the PyLabRobot Resource.
    skip_cache: If True, the resource will be loaded from lwdb, otherwise it will be loaded from the
      cache, if available.
  """

  dict_resource = client.get_labware(lwdb_name, skip_cache=skip_cache)
  dict_resource = dict_resource["definition"]
  return deserialize_resource(dict_resource, resource_name)
