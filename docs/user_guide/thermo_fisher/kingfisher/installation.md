# Installation

Install PyLabRobot with HID support:

```console
pip install 'pylabrobot[hid]'
```

Install the system HIDAPI library as well. On macOS:

```console
brew install hidapi
```

If Python cannot find `libhidapi.dylib`, set `DYLD_LIBRARY_PATH` to the library
folder when launching Python (typically `/opt/homebrew/lib` on Apple Silicon).

On Linux, install your distribution's HIDAPI package and configure USB permissions
for Thermo Fisher vendor `0x0AB6`. The Flex product ID is `0x02C7`; Presto is `0x02C9`.
Close BindIt before connecting with PLR.
