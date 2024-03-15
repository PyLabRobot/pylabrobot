""" Defines audio that machines can generate.

Source of audio: https://simpleguics2pygame.readthedocs.io/en/latest/_static/links/snd_links.html
"""

try:
  from IPython.display import display, Audio
  USE_AUDIO = True
except ImportError:
  USE_AUDIO = False


def can_play_audio():
  return USE_AUDIO


# ====== 1. Identifying items on deck (e.g. through LLD or Z-drive engagement) ======

def play_not_found():
  if not can_play_audio():
    return

  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/pang/arrow.mp3",
      autoplay=True))


def play_got_item():
  if not can_play_audio():
    return

  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/descent/gotitem.mp3",
      autoplay=True))
