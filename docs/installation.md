# Installation

While PyHamilton is written in pure Python, you will probably need to replace the Hamilton driver
with a USB driver that is compatible with [PyUSB](https://github.com/pyusb/pyusb). These
instructions describe how to install PyHamilton using pip, and how to install a PyUSB driver.

## Installing PyHamilton

It is recommended that you use a virtual environment to install PyHamilton. See the
[virtualenvironment](https://virtualenv.pypa.io/en/latest/) documentation for more information.

```bash
mkdir your_project
cd your_project
python -m virtualenv env
source env/bin/activate
```

### Using pip

The following will install PyHamilton and the essential dependencies:

```bash
pip install pyhamilton
```

If you want to build documentation or run tests, you need install the additional dependencies, also
using pip:

```bash
pip install pyhamilton[docs]
pip install pyhamilton[tests]
```

Or install all dependencies at once:

```bash
pip install pyhamilton[all]
```

### From source

```bash
git clone https://github.com/pyhamilton/pyhamilton.git
cd pyhamilton
pip install -e .[all]
```

See [the developer guide](development) for specific instructions on testing, documentation and
development.

## Updating the USB driver

[Zadig](https://zadig.akeo.ie)

TODO
