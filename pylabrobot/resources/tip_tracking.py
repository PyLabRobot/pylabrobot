import contextlib
import sys

this = sys.modules[__name__]
this.tip_tracking_enabled = False  # type: ignore


def set_tip_tracking(enabled: bool):
  this.tip_tracking_enabled = enabled  # type: ignore


def does_tip_tracking() -> bool:
  return this.tip_tracking_enabled  # type: ignore


@contextlib.contextmanager
def no_tip_tracking():
  old_value = this.tip_tracking_enabled
  this.tip_tracking_enabled = False  # type: ignore
  try:
    yield
  finally:
    this.tip_tracking_enabled = old_value  # type: ignore
