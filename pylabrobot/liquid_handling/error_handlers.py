from pylabrobot.liquid_handling.errors import ChannelizedError

def try_next_tip_spot(try_tip_spots):
  async def handler(func, error: Exception, **kwargs):
    assert isinstance(error, ChannelizedError)

    new_tip_spots, new_use_channels = [], []

    tip_spots = kwargs.pop("tip_spots")
    if "use_channels" not in kwargs:
      use_channels = list(range(len(tip_spots)))
    else:
      use_channels = kwargs.pop("use_channels")

    for idx, channel_idx in zip(tip_spots, use_channels):
      if channel_idx in error.errors.keys():
        new_tip_spots.append(next(try_tip_spots))
        new_use_channels.append(channel_idx)

    print(f"Retrying with tip spots: {new_tip_spots} and use channels: {new_use_channels}")
    return await func(tip_spots=new_tip_spots, use_channels=new_use_channels, **kwargs)

  return handler