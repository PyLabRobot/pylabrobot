""" Tecan backend errors """


class TecanError(Exception):
  """ Tecan backend errors, raised by a single module. """

  def __init__(
    self,
    message: str,
    module: str,
    error_code: int,
  ):
    self.message = message
    self.module = module
    self.error_code = error_code

  def __repr__(self) -> str:
    return f"{self.__class__.__name__}('{self.message}')"


def error_code_to_exception(module: str, error_code: int) -> TecanError:
  """ Convert an error code to an exception """
  table = None

  if module == "C5":
    table = {
      1: "Initialization failed",
      2: "Invalid command",
      3: "Invalid operand",
      4: "CAN acknowledge problems",
      5: "Device not implemented",
      6: "CAN answer timeout",
      7: "Device not initialized",
      8: "Command overflow of TeCU",
      9: "No liquid detected",
      10: "Drive no load",
      11: "Not enough liquid",
      12: "Not enough liquid",
      13: "No Flash access",
      15: "Command overflow of subdevice",
      17: "Measurement failed",
      18: "Clot limit passed",
      19: "No clot exit detected",
      20: "No liquid exit detected",
      21: "Delta pressure overrun (pLLD)",
      22: "Tip Guard in wrong position",
      23: "Not yet moved or move aborted",
      24: "llid pulse error or reed crosstalk error",
      25: "Tip not fetched",
      26: "Tip not mounted",
      27: "Tip mounted",
      28: "Subdevice error",
      29: "Application switch and axes mismatch",
      30: "Wrong DC-Servo type",
      31: "Virtual Drive"
    }
  elif module == "C1":
    table = {
      1: "Initialization failed",
      2: "Invalid command",
      3: "Invalid operand",
      4: "CAN acknowledge problems",
      5: "Device not implemented",
      6: "CAN answer timeout",
      7: "Device not initialized",
      8: "Command overflow of TeCU",
      9: "Plate not fetched",
      10: "Drive no load",
      11: "Sub device not ready yet",
      13: "No access to Flash-EPROM",
      14: "Hardware not defined",
      15: "Command overflow of this device",
      17: "Verification failed",
      21: "BCS communication error",
      25: "Download Error",
      28: "Sub device error",
      30: "Invalid servo version"
    }

  if table is not None and error_code in table:
    return TecanError(table[error_code], module, error_code)

  return TecanError(f"Unknown error code {error_code}", module, error_code)
