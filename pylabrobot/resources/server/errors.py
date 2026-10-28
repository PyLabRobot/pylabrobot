"""What a resource server refuses, said as the protocol says it."""

from __future__ import annotations

from pylabrobot.resources.server.v1 import types_pb2 as pb


class ServerError(ValueError):
  """A request the store cannot carry out, with the protocol's name for why.

  It is a `ValueError`, the exception PLR's resource tree raises for most of what the store
  refuses (a name taken, a holder already full, a plate not skirted), so a refusal reached through
  a handle is caught as PLR's was.

  Attributes:
    problem: why, as `Issue.problem` carries it.
    resource_name: the resource it is about, or empty.
  """

  def __init__(self, problem: pb.Problem, message: str, resource_name: str = ""):
    super().__init__(message)
    self.problem = problem
    self.resource_name = resource_name
