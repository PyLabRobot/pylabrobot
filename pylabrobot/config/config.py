import logging
from dataclasses import dataclass, field
from pathlib import Path

LOG_FROM_STRING = {
  "DEBUG": logging.DEBUG,
  "INFO": logging.INFO,
  "WARNING": logging.WARNING,
  "ERROR": logging.ERROR,
  "CRITICAL": logging.CRITICAL,
}

LOG_TO_STRING = {v: k for k, v in LOG_FROM_STRING.items()}


@dataclass
class Config:
  """The configuration object for the application."""

  @dataclass
  class Logging:
    """The logging configuration."""
    level: int = logging.INFO
    log_dir: Path = Path(".")

  logging: Logging = field(default_factory=Logging)

  @classmethod
  def from_dict(cls, d: dict) -> "Config":
    """Create a Config object from a dictionary."""
    return cls(
      logging=cls.Logging(
        level=LOG_FROM_STRING[d["logging"]["level"]],
        log_dir=Path(d["logging"]["log_dir"]),
      )
    )

  @property
  def as_dict(self) -> dict:
    """Convert the Config object to a dictionary."""
    return {
      "logging": {
        "level": LOG_TO_STRING[self.logging.level],
        "log_dir": str(self.logging.log_dir),
      }
    }
