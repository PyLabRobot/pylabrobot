import asyncio


# https://stackoverflow.com/questions/23033939/how-to-test-python-3-4-asyncio-code
def async_test(f):
  def wrapper(*args, **kwargs):
    coro = asyncio.coroutine(f)
    future = coro(*args, **kwargs)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(future)
  return wrapper
