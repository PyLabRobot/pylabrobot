from .agilent_backend import AgilentFABackend


class Agilent5400Backend(AgilentFABackend):
  """Backend for the Agilent 5400 Fragment Analyzer.
  Requires enabling TCP automation in Fragment Analyzer.ini
  """

  def __init__(self, host: str, port=3000):
    super().__init__(host, port)
