# Config

Example config file:

```ini
[logging]
level = DEBUG
log_dir = .
```

## Module structure

- `io`: Input/output: `ConfigReader` and `ConfigWriter`. Currently, we have `FileReader` and `FileWriter`.
- `formats`: Config file formats: `ConfigLoader` and `ConfigSaver`. Currently, we have `Ini` and `Json`.
