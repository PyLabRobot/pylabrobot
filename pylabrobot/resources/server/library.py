"""PLR's labware as models, built from its labware factories.

A factory's model is named after the factory, and its definition is the factory's resource built
with the name `{name}`, so every name in it follows the instance's. Its uniform grids of items are
given once (see `models`). These are what PLR's stores start with (`LIBRARY`); each model is built
the first time it is asked for.

A deprecated factory's name still makes a model, built by that factory; it is not listed.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
import warnings
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from pylabrobot.events import EventBus, use_event_bus
from pylabrobot.resources.server.errors import ServerError
from pylabrobot.resources.server.models import compact_grids
from pylabrobot.resources.server.v1 import types_pb2 as pb

if TYPE_CHECKING:
  from pylabrobot.resources.resource import Resource


@functools.lru_cache(maxsize=None)
def _factories(deprecated: bool) -> List[Tuple[str, Callable[..., Resource]]]:
  """The labware factories in `pylabrobot.resources` that build from a name alone, by qualified
  name, in order: those that are not deprecated, or only those that are. Definitions that
  refuse to build are left out."""
  # Each is built to see what it makes; an event bus listening now hears none of it.
  with use_event_bus(EventBus()):
    return _find_factories(deprecated)


def _find_factories(deprecated: bool) -> List[Tuple[str, Callable[..., Resource]]]:
  import pylabrobot.resources as resources
  from pylabrobot.resources.resource import Resource

  found: Dict[str, Callable[..., Resource]] = {}
  for info in pkgutil.walk_packages(
    resources.__path__, "pylabrobot.resources.", onerror=lambda name: None
  ):
    if info.name.endswith("_tests") or ".server" in info.name:
      continue
    try:
      with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        module = importlib.import_module(info.name)
    except (ImportError, NotImplementedError, DeprecationWarning):
      continue
    for name, function in inspect.getmembers(module, inspect.isfunction):
      if function.__module__ != module.__name__ or name.startswith("_"):
        continue
      parameters = inspect.signature(function).parameters
      if "name" not in parameters or any(
        parameter.default is inspect.Parameter.empty
        for key, parameter in parameters.items()
        if key != "name"
      ):
        continue
      with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        try:
          built = function(name="labware")
          is_alias = False
        except DeprecationWarning:
          is_alias = True
        except Exception:
          continue
      if is_alias != deprecated:
        continue
      if is_alias:
        with warnings.catch_warnings():
          warnings.simplefilter("ignore")
          try:
            built = function(name="labware")
          except Exception:
            continue
      if isinstance(built, Resource):
        found[f"{module.__name__}.{name}"] = function
  return sorted(found.items())


def labware_factories() -> List[Tuple[str, Callable[..., Resource]]]:
  """Every labware factory in `pylabrobot.resources` that builds from a name alone, by its
  qualified name, in order. Deprecated factories and definitions that refuse to build are left out.
  """
  return _factories(deprecated=False)


@functools.lru_cache(maxsize=None)
def _by_name() -> Dict[str, Callable[..., Resource]]:
  """Each factory by its own name; a deprecated one only where no other has the name."""
  found = {factory.__name__: factory for _, factory in _factories(deprecated=True)}
  found.update({factory.__name__: factory for _, factory in labware_factories()})
  return found


def model_from_factory(factory: Callable[..., Resource]) -> pb.ResourceModel:
  """The model a factory builds, named after it, with its docstring as its description."""
  from pylabrobot.resources.server.definitions import definition_from_resource

  # Built quietly: an event bus listening now hears nothing of the PLR objects made to read it.
  with warnings.catch_warnings(), use_event_bus(EventBus()):
    warnings.simplefilter("ignore")
    definition = definition_from_resource(factory(name="{name}"))
  compact_grids(definition)
  return pb.ResourceModel(
    name=factory.__name__,
    definition=definition,
    description=(inspect.getdoc(factory) or "").strip(),
  )


def model_names() -> List[str]:
  """The name of every model PLR has, in order; not the deprecated factories'."""
  return sorted(factory.__name__ for _, factory in labware_factories())


@functools.lru_cache(maxsize=None)
def _built(name: str) -> pb.ResourceModel:
  factory = _by_name().get(name)
  if factory is None:
    raise ServerError(pb.PROBLEM_NOT_FOUND, f"no model named '{name}'", name)
  return model_from_factory(factory)


def get_model(name: str) -> pb.ResourceModel:
  """A model of PLR's, by its factory's name.

  Raises:
    ServerError: PLR has no factory of that name.
  """
  model = pb.ResourceModel()
  model.CopyFrom(_built(name))
  return model


class Library:
  """PLR's models, as a store starts with them: each built from its factory when first asked for."""

  def names(self) -> Sequence[str]:
    return model_names()

  def get(self, name: str) -> Optional[pb.ResourceModel]:
    if name not in _by_name():
      return None
    return get_model(name)


LIBRARY = Library()
