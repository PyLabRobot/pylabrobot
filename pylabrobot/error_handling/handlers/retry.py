async def basic_retry_handler(func, error, **kwargs):
  """Will simply retry the function call with the same arguments."""
  return await func(**kwargs) 
