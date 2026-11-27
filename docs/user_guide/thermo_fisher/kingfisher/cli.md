# Command-line tools

The KingFisher Flex CLI inspects BindIt exports and identifies connected instruments.
Offline file operations are grouped under `bdz`; `probe` identifies the instrument.
See [installation](installation.md) for dependencies and USB setup.

## Inspect a Flex protocol

```console
plr dev kingfisher-flex bdz inspect protocol.bdz
```

Prints JSON with protocol metadata, compatibility issues, and loading/pause prompts.
This command does not access hardware.

## Check multiple protocols

```console
plr dev kingfisher-flex bdz audit /path/to/protocols other-protocol.bdz
```

Checks files and directories without accessing hardware. Directories are scanned
nonrecursively for `.bdz` files. Exits with status 1 if any input is invalid or unsupported.

## Identify a Flex

```console
plr dev kingfisher-flex probe
plr dev kingfisher-flex probe --serial-number SERIAL --transcript transcript.json
```

Opens the instrument, reads its identity, and disconnects. It does not initialize
or run a protocol.

See the [hello-world notebook](flex/hello-world.ipynb) to run protocols from Python.
