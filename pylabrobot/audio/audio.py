"""Defines audio that machines can generate.

Source of audio: https://simpleguics2pygame.readthedocs.io/en/latest/_static/links/snd_links.html
"""

import functools

try:
  from IPython.display import Audio, display

  USE_AUDIO = True
except ImportError as e:
  USE_AUDIO = False
  _AUDIO_IMPORT_ERROR = e


def _audio_check(func):
  @functools.wraps(func)
  def wrapper(*args, **kwargs):
    if not USE_AUDIO:
      raise RuntimeError(
        f"Audio functionality requires IPython.display. Import error: {_AUDIO_IMPORT_ERROR}"
      )
    return func(*args, **kwargs)

  return wrapper


# ====== 1. Identifying items on deck (e.g. through LLD or Z-drive engagement) ======


@_audio_check
def play_not_found():
  display(
    Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/pang/arrow.mp3",
      autoplay=True,
    )
  )


@_audio_check
def play_got_item():
  display(
    Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/descent/gotitem.mp3",
      autoplay=True,
    )
  )
