from .agilent_backend import AgilentFABackend

class Agilent5300Backend(AgilentFABackend):
  """Backend for the Agilent 5300 Fragment Analyzer.
    Requires patched Fragment Analyzer.exe (located in this directory) and enabling TCP automation in Fragment Analyzer.ini
  """

  def __init__(self, host: str, port=3000):
    super().__init__(host, port)

  def tray_out(self, tray_number):
    raise NotImplementedError("Automated tray operation is not supported on 5300")