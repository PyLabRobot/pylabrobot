from typing import Callable
from typing import Callable, Optional


class until_success:
  """
  Error handler that retries the given handler until the main function does not raise
  an exception, or until the maximum number of tries is reached.

  Args:
    handler: The async function to be executed.
    max_tries: Maximum number of retries. Default is None, which means infinite retries.
  """

  def __init__(self, handler: Callable, max_tries: Optional[int] = None):
    self.handler = handler
    self.max_tries = max_tries
    self.attempts = 0

  async def __call__(self, *args, **kwargs):
    if self.max_tries is not None and self.attempts >= self.max_tries:
      raise RuntimeError("Maximum number of retries reached")
    self.attempts += 1
    return await self.handler(*args, **kwargs)
