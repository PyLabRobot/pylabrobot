from .driver import HighResSampleStorage


class TundraStore(HighResSampleStorage):
  """HighRes Biosolutions TundraStore refrigerated plate store."""

  _model_name = "TundraStore"
  _verification_warning = (
    "TundraStore support is a work in progress and has not been verified against hardware. "
    "Validate it in a controlled setup and report verified behavior so this warning can be removed."
  )
