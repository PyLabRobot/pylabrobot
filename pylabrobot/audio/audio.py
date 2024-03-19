""" Defines audio that machines can generate.

Source of audio: https://simpleguics2pygame.readthedocs.io/en/latest/_static/links/snd_links.html
"""

import functools


try:
  from IPython.display import display, Audio
  USE_AUDIO = True
except ImportError:
  USE_AUDIO = False


def _audio_check(func):
  @functools.wraps(func)
  def wrapper(*args, **kwargs):
    if not USE_AUDIO:
      return
    return func(*args, **kwargs)
  return wrapper


# ====== 1. Identifying items on deck (e.g. through LLD or Z-drive engagement) ======

@_audio_check
def play_not_found():
  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/pang/arrow.mp3",
      autoplay=True))


@_audio_check
def play_got_item():
  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/descent/gotitem.mp3",
      autoplay=True))
