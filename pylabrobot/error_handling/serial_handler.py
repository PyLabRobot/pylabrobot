class SerialErrorHandler:
  def __init__(self, child_handlers: list):
    self.child_handlers = child_handlers
    self.fallback = fallback
    self.index = 0
  
  def __call__(self, func, *args, **kwargs):
    print("serial error handler is choosing next child handler")
    if self.index >= len(self.child_handlers):
      raise RuntimeError("No more child handlers to call")
    handler = self.child_handlers[self.index]
    self.index += 1
    return handler(func, *args, **kwargs)
