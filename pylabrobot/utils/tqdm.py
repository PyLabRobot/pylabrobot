"""Minimal progress bar, stdlib-only.

Adapted verbatim from tinygrad/helpers.py
(https://github.com/tinygrad/tinygrad/blob/master/tinygrad/helpers.py).

Kept as a pylabrobot utility to avoid pulling in the ``tqdm`` dependency for
long-running protocols (multi-plate transfers, incubator waits, firmware
replay, etc.). API is a subset of the ``tqdm`` package: iterator + context
manager + ``update()`` + ``tqdm.write()``. The ``trange`` helper is omitted
— use ``tqdm(range(n))`` directly.

----------------------------------------------------------------------------
The ``tqdm`` class and its helpers in this module are redistributed under the
following MIT license:

    Copyright (c) 2024, the tiny corp

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to
    the following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
    TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
    SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
----------------------------------------------------------------------------
"""

import math
import shutil
import sys
import time
from typing import Generic, Iterable, Iterator, Optional, TypeVar

T = TypeVar("T")


class tqdm(Generic[T]):
  """Progress bar. Wrap an iterable, or use as a context manager with manual ``update()``."""

  def __init__(
    self,
    iterable: Optional[Iterable[T]] = None,
    desc: str = "",
    disable: bool = False,
    unit: str = "it",
    unit_scale: bool = False,
    total: Optional[int] = None,
    rate: int = 100,
  ):
    self.iterable, self.disable, self.unit, self.unit_scale, self.rate = (
      iterable,
      disable,
      unit,
      unit_scale,
      rate,
    )
    self.st, self.i, self.n, self.skip, self.t = (
      time.perf_counter(),
      -1,
      0,
      1,
      getattr(iterable, "__len__", lambda: 0)() if total is None else total,
    )
    self.set_description(desc)
    self.update(0)

  def __iter__(self) -> Iterator[T]:
    assert self.iterable is not None, "need an iterable to iterate"
    for item in self.iterable:
      yield item
      self.update(1)
    self.update(close=True)

  def __enter__(self):
    return self

  def __exit__(self, *_):
    self.update(close=True)

  def set_description(self, desc: str):
    self.desc = f"{desc}: " if desc else ""

  def update(self, n: int = 0, close: bool = False):
    self.n, self.i = self.n + n, self.i + 1
    if self.disable or (not close and self.i % self.skip != 0):
      return
    prog, elapsed, ncols = (
      self.n / self.t if self.t else 0,
      time.perf_counter() - self.st,
      shutil.get_terminal_size().columns,
    )
    if elapsed and self.i / elapsed > self.rate and self.i:
      self.skip = max(int(self.i / elapsed) // self.rate, 1)

    def HMS(t):
      return ":".join(
        f"{x:02d}" if i else str(x)
        for i, x in enumerate([int(t) // 3600, int(t) % 3600 // 60, int(t) % 60])
        if i or x
      )

    def SI(x):
      if not x:
        return "0.00"
      v = f"{x / 1000 ** int(g := round(math.log(x, 1000), 6)):.{int(3 - 3 * math.fmod(g, 1))}f}"[
        :4
      ].rstrip(".")
      return (
        (f"{x / 1000 ** (int(g) + 1):.3f}"[:4].rstrip(".") + " kMGTPEZY"[int(g) + 1])
        if v == "1000"
        else v + " kMGTPEZY"[int(g)].strip()
      )

    prog_text = (
      f"{SI(self.n)}{f'/{SI(self.t)}' if self.t else self.unit}"
      if self.unit_scale
      else f"{self.n}{f'/{self.t}' if self.t else self.unit}"
    )
    est_text = f"<{HMS(elapsed / prog - elapsed) if self.n else '?'}" if self.t else ""
    it_text = (
      (SI(self.n / elapsed) if self.unit_scale else f"{self.n / elapsed:5.2f}") if self.n else "?"
    )
    suf = f"{prog_text} [{HMS(elapsed)}{est_text}, {it_text}{self.unit}/s]"
    sz = max(ncols - len(self.desc) - 3 - 2 - 2 - len(suf), 1)
    bar = (
      "\r"
      + self.desc
      + (
        f"{100 * prog:3.0f}%|{('█' * int(num := sz * prog) + ' ▏▎▍▌▋▊▉'[int(8 * num) % 8].strip()).ljust(sz, ' ')}| "
        if self.t
        else ""
      )
      + suf
    )
    print(bar[: ncols + 1], flush=True, end="\n" * close, file=sys.stderr)

  @classmethod
  def write(cls, s: str):
    print(f"\r\033[K{s}", flush=True, file=sys.stderr)
