from typing import List


class TundraStoreError(Exception):
  """A command returned an ``ERROR!`` completion status.

  The TundraStore reports failures as an error stack: the completion line is
  preceded (firmware 3.0.x) by one or more ``Error <n>: ...`` lines, the last of
  which is generally the most pertinent. Those lines are preserved in
  :attr:`error_lines`.
  """

  def __init__(self, command: str, error_lines: List[str]):
    self.command = command
    self.error_lines = error_lines
    detail = error_lines[-1] if error_lines else "no error detail returned"
    super().__init__(f"'{command}' failed: {detail}")


class TundraStoreAbortedError(Exception):
  """A command returned an ``ABORTED!`` completion status (e.g. after ``abort``)."""

  def __init__(self, command: str):
    self.command = command
    super().__init__(f"'{command}' was aborted")
