import functools
import inspect

def handles_errors(func):
  @functools.wraps(func)
  async def wrapper(self, *args, **kwargs):
    try:
      return await func(self, *args, **kwargs)
    except Exception as error:
      handler = self._error_handlers.get(type(error))
      if handler:
        print(f"Handling error {error} with: {handler}")
        # bind the wrapper to this instance so that
        # retries still go through the decorator
        bound = wrapper.__get__(self, type(self))

        # convert all args to kwargs, remove self
        sig = inspect.signature(func)
        bound_args = sig.bind(self, *args, **kwargs)
        bound_args = {k: v for k, v in bound_args.arguments.items() if k != "self"}

        # call the handler, passing it the *decorated* method
        return await handler(bound, error, **bound_args)
      # no handler registered -> re‑raise
      raise
  return wrapper
