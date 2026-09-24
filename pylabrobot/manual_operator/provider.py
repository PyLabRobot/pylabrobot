"""Provider interfaces and built-in transports for operator acknowledgement."""

import asyncio
from typing import Callable, Protocol

from .standard import OperatorActionRequest, OperatorActionResult


class OperatorActionProvider(Protocol):
  """Transport an action request to an operator and await their reported outcome."""

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    """Wait until an operator reports an outcome for ``action``."""


class ConsoleOperatorActionProvider:
  """A terminal provider where pressing Enter reports successful completion.

  The input call runs in a worker thread so other tasks on the protocol event loop can continue.
  Applications with their own prompt lifecycle should implement :class:`OperatorActionProvider`
  instead.
  """

  def __init__(
    self,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
  ):
    self._input = input_fn
    self._output = output_fn

  async def request(self, action: OperatorActionRequest) -> OperatorActionResult:
    self._output(f"\n{action.title}\n\n{action.instructions}\n")
    await asyncio.to_thread(self._input, f"{action.confirmation_text}: ")
    return OperatorActionResult.completed()
