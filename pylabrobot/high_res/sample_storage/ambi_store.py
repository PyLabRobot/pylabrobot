from .driver import HighResSampleStorage


class AmbiStore(HighResSampleStorage):
  """HighRes Biosolutions AmbiStore plate store.

  The AmbiStore is ambient, so it exposes only plate storage and retrieval.
  Its environment-control behavior has not yet been verified against hardware.
  """

  _model_name = "AmbiStore"
  _verification_warning = (
    "AmbiStore support is a work in progress and has not been verified against hardware. "
    "Validate it in a controlled setup and report verified behavior so this warning can be removed."
  )
