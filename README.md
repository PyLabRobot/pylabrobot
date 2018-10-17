# pyhamilton

**Python for Hamilton liquid handling robots**

Hamilton software only works on Windows, so the same goes for pyhamilton.

Developed for Hamilton STARlet on Windows XP and Windows 7. Other robot models and operating systems not supported yet.

## Example usage
```python
from pyhamilton import HamiltonInterface, INITIALIZE
with HamiltonInterface() as ham_int:
    ham_int.wait_on_response(ham_int.send_command(INITIALIZE))
```

## Installation

The pyhamilton source currently includes some code that might not be in the public domain, so it is distributed separately. You will need a copy of the _STAR-OEM_ directory in the pyhamilton package (_C:...\python3\...\site-packages\pyhamilton\STAR-OEM_) to run the background application that interfaces with the Hamilton robot.

1. **Install and test the standard Hamilton software suite for your system.**
2. **Install 32-bit python 3.6.3**, preferably using the executable installer at https://www.python.org/downloads/release/python-363/. Python 3.7 is known to cause an installation issue with some required pythonnet/pywin32 modules.
3. **Make sure git is installed.** https://git-scm.com/download/win
4. **Make sure you have .NET framework 4.0 or higher installed.** https://www.microsoft.com/en-us/download/details.aspx?id=17851
5. **Update your pip and setuptools.**
    ```
    > python -m pip install --upgrade pip
    > pip install --upgrade setuptools
    ```
6. **Install pyhamilton.**
    ```
    > pip install git+https://github.com/dgretton/pyhamilton.git#egg=pyhamilton
    ```
7.  `...\Python36-32\Lib\site-packages\pyhamilton\` should now exist. **Place the** ***STAR-OEM*** **directory there.**
8. **Run.** If you have other Python versions installed, always run pyhamilton with `py yourmethod.py` (the bundled Python launcher, which interprets shebangs) or `python3 yourmethod.py`

_Contact: dgretton@media.mit.edu_

_Developed for the Sculpting Evolution Group at the MIT Media Lab_
