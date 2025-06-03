class serial_error_handler:
  def __init__(self, child_handlers: list):
    self.child_handlers = child_handlers
    self.index = 0

  async def __call__(self, func, exception, **kwargs):
    if self.index >= len(self.child_handlers):
      raise RuntimeError("No more child handlers to call")
    handler = self.child_handlers[self.index]
    self.index += 1
    return await handler(func, exception, **kwargs)
