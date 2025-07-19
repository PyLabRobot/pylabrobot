# Configuring PLR

The `pylabrobot.config` module provides the `Config` class for configuring PLR. The configuration can be set programmatically or loaded from a file.

The configuration currently only supports logging configuration.

## The `Config` class

You can create a `Config` object as follows:

```python
import logging
from pathlib import Path
from pylabrobot.config import Config

config = Config(
  logging=Config.Logging(
    level=logging.DEBUG,
    log_dir=Path("my_logs")
  )
)
```

Then, call `pylabrobot.configure` to apply the configuration:

```python
import pylabrobot
pylabrobot.configure(config)
```

## Loading from a file

PLR supports loading configuration from a number of file formats. The supported formats are:

- INI files
- JSON files

Files are loaded using the `pylabrobot.config.load_config` function:

```python
from pylabrobot.config import load_config
config = load_config("config.json")

import pylabrobot
pylabrobot.configure(config)
```

If no file is found, a default configuration is used.

`load_config` has the following parameters:

```python
def load_config(
  base_file_name: str,
  create_default: bool = False,
  create_module_level: bool = True
) -> Config:
```

A `pylabrobot.ini` file is used if found in the current directory. If not found, it is searched for in all parent directories. If it still is not found, it gets created at either the project level that contains the `.git` directory, or the current directory.

### INI files

Example of an INI file:

```ini
[logging]
level = DEBUG
log_dir = .
```

### JSON files

```json
{
  "logging": {
    "level": "DEBUG",
    "log_dir": "."
  }
}
```
