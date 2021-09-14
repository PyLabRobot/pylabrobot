# pyhamilton

**Python for Hamilton liquid handling robots**

Hamilton software only works on Windows, so the same goes for pyhamilton.

Developed for Hamilton STAR and STARlet on Windows XP and Windows 7. VANTAGE series supported with plugin. Other robot models and operating systems not supported yet.

_Contact: contactpyhamilton@gmail.com for questions, ideas, or help with installation._

## Example usage
```python
if __name__ == "__main__":
    from pyhamilton import HamiltonInterface, INITIALIZE
    with HamiltonInterface() as ham_int:
        ham_int.wait_on_response(ham_int.send_command(INITIALIZE))
```

## Documentation

[Available online](https://dgretton.github.io/pyhamilton-docs/).

## Installation

1. **Install and test the standard Hamilton software suite for your system.**
2. **Install 32-bit python 3.6.3**, preferably using the executable installer at https://www.python.org/downloads/release/python-363/. Python 3.7+ is known to cause an installation issue with some required pythonnet/pywin32 modules.
3. **Make sure git is installed.** https://git-scm.com/download/win
4. **Make sure you have .NET framework 4.0 or higher installed.** https://www.microsoft.com/en-us/download/details.aspx?id=17851
5. **Install Hamilton library dependencies** HSLJson and HSLHttp by running executable installers *"HSLJson Library v1.6 Installer.exe"* and *"Hamilton HSLHttp Library Installer Version 2.7.exe"* located in *./bin*.
6. **Copy+paste the files from /library into your (path to hamilton install)/HAMILTON/Library folder** These will ensure you have all the libraries you need in addition to the aforementioned HSLJson and HSLHttp libraries which are installed with executables.
7. **Update your pip and setuptools.**
    ```
    > python -m pip install --upgrade pip
    > pip install --upgrade setuptools
    ```
7. **Install pyhamilton.**
    ```
    > pip install git+https://github.com/dgretton/pyhamilton.git#egg=pyhamilton
    ```
8. **Run.** If you have other Python versions installed, always run pyhamilton with `py yourmethod.py` (the bundled Python launcher, which interprets shebangs) or `python3 yourmethod.py`

## Installation Troubleshooting
**1.** If you encounter an error relating to HxFan, open pyhamilton/star-oem/VENUS_Method/STAR_OEM_Test.med, navigate to the "HxFan" grouping, and delete all commands under this grouping. 
**2.** If you would like to test your PyHamilton installation on a computer not connected to a Hamilton robot, use `HamiltonInterface(simulate=True)` to open your interface. 

## Applications

- [A high-throughput platform for feedback-controlled directed evolution](https://www.biorxiv.org/content/10.1101/2020.04.01.021022v1), _preprint_

- [Flexible open-source automation for robotic bioengineering](https://www.biorxiv.org/content/10.1101/2020.04.14.041368v1), _preprint_


_Developed for the Sculpting Evolution Group at the MIT Media Lab_
