import asyncio
from typing import Awaitable, Optional, TypeVar

T = TypeVar("T")


def run_with_timeout(
  coro: Awaitable[T],
  loop: asyncio.AbstractEventLoop,
  timeout: Optional[float] = None) -> T:
  """ Run a coroutine with a timeout.

  Args:
    loop: The event loop to run the coroutine in. If None, a new event loop will be created if
      one does not already exist.
    coro: The coroutine to run.
    timeout: The timeout in seconds. If None, there is no timeout.

  Returns:
    The result of the coroutine.

  Raises:
    TimeoutError: If the coroutine times out.
  """

  # https://ipython.readthedocs.io/en/stable/interactive/autoawait.html#difference-between-terminal-
  # ipython-and-ipykernel says that IPython does not allow the synchronous foreground task to submit
  # asyncio tasks and block while waiting. This means that we cannot use this function in a jupyter
  # notebook.
  try:
    _ = get_ipython() # type: ignore
    raise RuntimeError("It looks like you're using a synchronous PLR class in a jupyter notebook. "
      "This is not supported because of a Jupyter Notebook limitation. Please use the asynchronous "
      "version with `await` instead.")
  except NameError:
    pass

  try:
    result = loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
  except asyncio.TimeoutError:
    raise TimeoutError("Timed out waiting for data") from None
  return result
