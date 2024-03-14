""" Defines audio that machines can generate. """

from IPython.display import display, Audio


# ====== 1. Identifying items on deck (e.g. through LLD or Z-drive engagement) ======

def notFoundAudio():
  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/pang/arrow.mp3",
      autoplay=True))
    # https://simpleguics2pygame.readthedocs.io/en/latest/_static/links/snd_links.html

def gotItemAudio():
  display(Audio(
      url="https://codeskulptor-demos.commondatastorage.googleapis.com/descent/gotitem.mp3",
      autoplay=True))
