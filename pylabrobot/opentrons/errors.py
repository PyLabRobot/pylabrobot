"""Errors from the Opentrons robot-server protocol."""


class OpentronsError(RuntimeError):
  """An Opentrons operation could not be completed."""


class OpentronsProtocolError(OpentronsError):
  """A robot-server response does not match the expected protocol."""


class OpentronsCommandError(OpentronsError):
  """A command failed, with its server identifiers available for inspection."""

  def __init__(
    self, run_id: str, command_id: str, command_type: str, error_type: str, detail: str
  ) -> None:
    self.run_id = run_id
    self.command_id = command_id
    self.command_type = command_type
    self.error_type = error_type
    self.detail = detail
    super().__init__(f"{command_type} failed with {error_type}: {detail}")


class OpentronsCommandTimeout(TimeoutError):
  """The command's completion is unknown; its ID can be used to query the server."""

  def __init__(self, run_id: str, command_id: str, command_type: str) -> None:
    self.run_id = run_id
    self.command_id = command_id
    self.command_type = command_type
    super().__init__(
      f"Timed out waiting for Opentrons command {command_type!r} "
      f"({command_id} in run {run_id}); completion is unknown"
    )
