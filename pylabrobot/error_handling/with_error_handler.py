from __future__ import annotations

import functools
import inspect
from typing import Any, Awaitable, Callable, Optional, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R", bound=Awaitable[Any])

Handler: Callable[[Callable[_P, _R], Exception, dict[str, Any]], Awaitable[Any]]


def with_error_handler(func: Callable[_P, _R]) -> Callable[_P, _R]:
  @functools.wraps(func)
  async def wrapper(self, *args, error_handler: Optional[Handler] = None, **kwargs):
    try:
      return await func(self, *args, **kwargs)
    except Exception as error:
      print("caught error", error)
      if error_handler is not None:
        bound = wrapper.__get__(self, type(self))

        # convert all args to kwargs, remove self
        sig = inspect.signature(func)
        bound_args = sig.bind(self, *args, **kwargs)
        bound_args = {k: v for k, v in bound_args.arguments.items() if k != "self"}
        bound_args["error_handler"] = error_handler

        return await error_handler(bound, error, **bound_args)
      raise

  return wrapper
