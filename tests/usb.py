class MockDev:
  def __init__(self, send_response=None):
    self.send_response = send_response
    # split into chunks of 64 bytes
    self.read_chunks = [
      self.send_response[i : i + 64] for i in range(0, len(self.send_response), 64)
    ]
    self.chunk = 0

  def read(self, endpoint, size, timeout=None):
    if self.chunk >= len(self.read_chunks):
      return b""
    chunk = self.read_chunks[self.chunk]
    self.chunk += 1
    return chunk.encode("utf-8")

  def write(self, endpoint, data, timeout=None):
    return len(data)


class MockEndpoint:
  def __init__(self):
    self.wMaxPacketSize = 64
