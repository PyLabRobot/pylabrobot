import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
  """The configuration object for PyLabRobot."""

  @dataclass
  class Logging:
    """The logging configuration."""

    level: int = logging.INFO
    log_dir: Optional[Path] = None

  logging: Logging = field(default_factory=Logging)

  @classmethod
  def from_dict(cls, d: dict) -> "Config":
    return cls(
      logging=cls.Logging(
        level=LOG_FROM_STRING[d["logging"]["level"]],
        log_dir=Path(d["logging"]["log_dir"]) if "log_dir" in d["logging"] else None,
      )
    )

  @property
  def as_dict(self) -> dict:
    return {
      "logging": {
        "level": LOG_TO_STRING[self.logging.level],
        "log_dir": str(self.logging.log_dir) if self.logging.log_dir is not None else None,
      }
    }
