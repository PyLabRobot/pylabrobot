"""Pytest infrastructure for structured asynchronous test lifecycles."""

import collections.abc
import inspect
from typing import (
  Any,
  Callable,
  Dict,
  List,
  Mapping,
  Optional,
  Protocol,
  Tuple,
  Type,
  Union,
  cast,
)

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan, _HasLifespanMeta

BackendOptions = Union[List[str], Mapping[str, Optional[Dict[str, Any]]]]
TestMethod = Callable[..., Any]
ClassMetadata = Tuple[object, Mapping[str, TestMethod], Optional[BackendOptions]]
_lifespan_options: Dict[TestMethod, Dict[str, Any]] = {}


class _MarkedFunction(Protocol):
  """A function carrying the pytest marks applied to it."""

  pytestmark: List[pytest.Mark]


def lifespan_kwargs(**kwargs: Any) -> Callable[[TestMethod], TestMethod]:
  """Set lifespan options for one test method.

  Args:
    **kwargs: Keyword arguments forwarded to the test class lifespan.

  Returns:
    A decorator retaining the test method with its lifespan options.
  """

  def decorator(function: TestMethod) -> TestMethod:
    """Associate lifespan options with a test method."""
    _lifespan_options[function] = dict(kwargs)
    return function

  return decorator


def _normalize_backends(backends: BackendOptions) -> Dict[str, Optional[Dict[str, Any]]]:
  """Normalize backend names and options into a mapping."""
  if isinstance(backends, list):
    return {backend: None for backend in backends}
  if isinstance(backends, collections.abc.Mapping):
    return dict(backends)
  raise TypeError(
    "_anyio_backends must be a list of backend names or a mapping of names to options"
  )


def _requested_fixture_names(function: TestMethod) -> Tuple[str, ...]:
  """Return the argument names a test method requests besides its instance.

  Args:
    function: Test method declared in a class namespace.

  Returns:
    The names of every mandatory named parameter after the instance parameter.
  """
  parameters = inspect.signature(function).parameters.values()
  names = tuple(
    parameter.name
    for parameter in parameters
    if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    and parameter.default is parameter.empty
  )
  return names[1:]


def _declared_marks(function: TestMethod) -> List[pytest.Mark]:
  """Return the pytest marks applied directly to a test method.

  Args:
    function: Test method declared in a class namespace.

  Returns:
    Every mark applied to the method, or an empty list when it carries none.
  """
  marked = cast(_MarkedFunction, function)
  try:
    return list(marked.pytestmark)
  except AttributeError:
    return []


def _wrap_test(function: TestMethod, backends: BackendOptions) -> TestMethod:
  """Wrap one test with backend selection and a scoped lifespan."""
  fixture_names = _requested_fixture_names(function)
  backend_options = _normalize_backends(backends)

  def sync_wrapper(
    self: "AnyioTestBase", request: pytest.FixtureRequest, _anyio_backend: str
  ) -> Any:
    """Run one test invocation on its selected asynchronous backend."""
    fixtures = {name: request.getfixturevalue(name) for name in fixture_names}
    lifespan_options = _lifespan_options.get(function, {})

    async def run_test() -> Any:
      """Run the original test inside a newly initialized lifespan."""
      HasLifespan.__init__(self)
      async with self.lifespan(**lifespan_options):
        result = function(self, **fixtures)
        if inspect.isawaitable(result):
          return await result
        return result

    options = backend_options[_anyio_backend] or {}
    if _anyio_backend == "trio":
      import trio

      return trio.run(run_test, **options)
    return anyio.run(run_test, backend=_anyio_backend, backend_options=options)

  wrapped = cast(TestMethod, sync_wrapper)
  if fixture_names:
    pytest.mark.usefixtures(*fixture_names)(wrapped)
  pytest.mark.parametrize("_anyio_backend", list(backend_options))(wrapped)
  marked = cast(_MarkedFunction, wrapped)
  marked.pytestmark = marked.pytestmark + _declared_marks(function)
  return wrapped


def _validate_test_bases(name: str, bases: Tuple[Type[Any], ...]) -> None:
  """Require every base of a derived test class to carry test metadata.

  Args:
    name: Name of the class being created.
    bases: Declared bases of the class being created.

  Raises:
    TypeError: If a derived test class inherits from an ordinary class.
  """
  if not any(isinstance(base, _AnyioTestMeta) for base in bases):
    return
  for base in bases:
    if not isinstance(base, _AnyioTestMeta):
      raise TypeError(
        f"Base {base.__name__} of class {name} must be an AnyioTestBase subclass; "
        "share test behavior through an AnyioTestBase subclass instead of a mixin"
      )


def _merge_metadata_mros(bases: Tuple[Type[Any], ...]) -> Tuple[ClassMetadata, ...]:
  """Merge base metadata according to Python's C3 linearization rules."""
  sequences: List[List[ClassMetadata]] = []
  direct_bases: List[ClassMetadata] = []
  for base in bases:
    if isinstance(base, _AnyioTestMeta):
      base_metadata = list(base._anyio_metadata_mro)
      sequences.append(base_metadata)
      direct_bases.append(base_metadata[0])
  sequences.append(direct_bases)

  merged: List[ClassMetadata] = []
  while any(sequences):
    candidate: Optional[ClassMetadata] = None
    for sequence in sequences:
      if not sequence:
        continue
      head = sequence[0]
      if not any(any(layer[0] is head[0] for layer in other[1:]) for other in sequences):
        candidate = head
        break
    if candidate is None:
      raise TypeError("Cannot create a consistent method resolution order")
    merged.append(candidate)
    for sequence in sequences:
      if sequence and sequence[0][0] is candidate[0]:
        sequence.pop(0)
  return tuple(merged)


class _AnyioTestMeta(_HasLifespanMeta):
  """Create pytest methods from explicit class namespace declarations."""

  _invalid_hooks = ("setUp", "asyncSetUp", "tearDown", "asyncTearDown")

  def __new__(
    mcls: Type["_AnyioTestMeta"],
    name: str,
    bases: Tuple[Type[Any], ...],
    namespace: Dict[str, Any],
    **kwargs: Any,
  ) -> "_AnyioTestMeta":
    """Validate bases and hooks, then wrap declared or inherited test methods."""
    _validate_test_bases(name, bases)
    for hook_name in mcls._invalid_hooks:
      if hook_name in namespace:
        raise TypeError(
          f"Class {name} cannot define {hook_name}; implement _enter_lifespan instead"
        )

    declared_methods: Dict[str, TestMethod] = {}
    for method_name, value in tuple(namespace.items()):
      if method_name.startswith("test_"):
        declared_methods[method_name] = cast(TestMethod, value)

    declared_backends = (
      cast(BackendOptions, namespace["_anyio_backends"]) if "_anyio_backends" in namespace else None
    )
    metadata: ClassMetadata = (object(), declared_methods, declared_backends)
    metadata_mro = (metadata,) + _merge_metadata_mros(bases)

    methods: Dict[str, TestMethod] = {}
    backends: Optional[BackendOptions] = None
    for _, declarations, backend_policy in metadata_mro:
      for method_name, function in declarations.items():
        if method_name not in methods:
          methods[method_name] = function
      if backends is None and backend_policy is not None:
        backends = backend_policy

    if backends is None:
      backends = ["asyncio", "trio"]

    for method_name, function in methods.items():
      namespace[method_name] = _wrap_test(function, backends)

    namespace["_anyio_test_methods"] = methods
    namespace["_anyio_metadata_mro"] = metadata_mro
    created = super().__new__(mcls, name, bases, namespace, **kwargs)
    return cast("_AnyioTestMeta", created)


class AnyioTestBase(HasLifespan, metaclass=_AnyioTestMeta):
  """Run pytest test methods inside a lifespan on asyncio and Trio."""

  __init__ = object.__init__
  _anyio_backends: BackendOptions = ["asyncio", "trio"]
  _anyio_test_methods: Mapping[str, TestMethod] = {}
  _anyio_metadata_mro: Tuple[ClassMetadata, ...] = ()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Provide an empty test lifespan for subclasses to extend."""
    await super()._enter_lifespan(stack)
