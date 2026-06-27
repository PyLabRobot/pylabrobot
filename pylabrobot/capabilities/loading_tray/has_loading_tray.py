from .loading_tray import LoadingTray


class HasLoadingTray:
  """Mixin for devices that have a loading tray."""

  loading_tray: LoadingTray
