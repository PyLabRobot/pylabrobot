# pylint: disable=unused-argument, inconsistent-quotes


class ChatterBoxBackend:
  def __init__(self, num_channels: int = 8):
    raise NotImplementedError("ChatterBoxBackend is deprecated. "
                              "Use LiquidHandlerChatterboxBackend instead.")
