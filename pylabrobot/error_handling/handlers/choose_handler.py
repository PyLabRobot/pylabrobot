from typing import Callable, Dict


def choose_handler(error, handlers: Dict[Exception, Callable]) -> Callable:
  """Choose the appropriate error handler based on the type of error."""

  async def handler(func, exception, **kwargs):
    for exc_type, handler in handlers.items():
      if isinstance(error, exc_type):
        return await handler(func, exception, **kwargs)

  return handler
