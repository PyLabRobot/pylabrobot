# pyhamilton

**Python for Hamilton liquid handling robots**

Hamilton software only works on Windows, so the same goes for pyhamilton.

Developed for Hamilton STARlet on Windows XP or Windows 7. Other robot models not supported yet.

## Installation

Base install:

```
pip install git+git://github.com/dgretton/pyhamilton.git#egg=pyhamilton
```

the pyhamilton source currently includes some code that might not be in the public domain, so it is distributed separately. You will need a copy of the STAR-OEM directory in the main package (C:...\python3\...\site-packages\pyhamilton\STAR-OEM) to run the background application that interfaces with the Hamilton robot.

## Example usage
```python
from pyhamilton import HamiltonInterface, INITIALIZE
with HamiltonInterface() as ham_int:
    ham_int.wait_on_response(ham_int.send_command(INITIALIZE))
```
