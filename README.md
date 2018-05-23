# pyhamilton
**Python for Hamilton liquid handling robots**
Hamilton software only works on Windows, so the same goes for pyhamilton.

## Installation
Base install:
```
pip install git+git://github.com/dgretton/pyhamilton.git#egg=pyhamilton
```
Pyhamilton currently includes some code that is not in the public domain, so it is distributed separately.
You will need to copy in a STAR-OEM directory in the main package directory (C:...\python3\...\site-packages\pyhamilton\STAR-OEM)

## Example usage
```python
from pyhamilton import HamiltonInterface, INITIALIZE
with HamiltonInterface() as ham_int:
    ham_int.wait_on_response(ham_int.send_command(INITIALIZE))
```
