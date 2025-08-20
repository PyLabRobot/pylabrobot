"""YoLink Client Error."""


class YoLinkError(Exception):
  """YoLink Error."""


class YoLinkClientError(YoLinkError):
  """YoLink Client Error.

  code: Error Code
  desc: Desc or Error
  """

  def __init__(
    self,
    code: str,
    desc: str,
  ) -> None:
    """Initialize the yolink api error."""

    self.code = code
    self.message = desc


class YoLinkAuthFailError(YoLinkClientError):
  """YoLink Auth Fail"""


class YoLinkDeviceConnectionFailed(YoLinkClientError):
  """YoLink device connection failed."""


class YoLinkUnSupportedMethodError(YoLinkClientError):
  """YoLink Unsupported method error."""
