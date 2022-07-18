# PyHamilton Reference Guide
Author: Stefan Golas _(Contact stefanmgolas@gmail.com)_

## Contents
- Intro
- Installation
- Your First PyHamilton Method
- Breaking it Down
- How PyHamilton Works
- Expanding The API
## Intro
PyHamilton is an open-source Python interface for programming Hamilton liquid-handling robots. PyHamilton is designed to be accessible while affording unlimited flexibility to the developer. We believe that an open-source community driven framework will accelerate discovery and enable a new generation of biological workflows.

 ## Installation

1. **Install and test the standard Hamilton software suite for your system.**
2. **Install 32-bit python <=3.9**, preferably using the executable installer at https://www.python.org/downloads/release/python-390/. Python 3.10+ is known to cause an installation issue with some required pythonnet/pywin32 modules.
3. **Make sure git is installed.** https://git-scm.com/download/win
4. **Make sure you have .NET framework 4.0 or higher installed.** https://www.microsoft.com/en-us/download/details.aspx?id=17851
5. **Update your pip and setuptools.**
    ```
    > python -m pip install --upgrade pip
    > pip install --upgrade setuptools
    ```
6. **Install pyhamilton.**
   
    ```
    pip install pyhamilton
    ```
    
7. **Run the pyhamilton autoconfig tool from the command line.** 

    ```
    pyhamilton-config
    ``` 

    Press accept to proceed with the bundled installers.

## Your First PyHamilton Method

Here is how to write your first PyHamilton method.

First, create a new directory called `my-project`. Then,  open the Hamilton Method Editor and create a new Layout file. Add 5 96-tip tip carriers named "tips_1", "tips_2", etc. Then add 5 96-well plates named "plate_1", "plate_2", etc. <br>
![Deck layout](https://raw.githubusercontent.com/dgretton/pyhamilton/master/imgs/decklay.png) 
_deck.lay_

Next, create a file named `robot_method.py` in your preferred text editor. Inside this file, type 

``` 
from pyhamilton import (HamiltonInterface, LayoutManager, ResourceType,  Plate96, Tip96, initialize, tip_pick_up, tip_eject, aspirate, dispense, tip_pick_up_96, tip_eject_96, aspirate_96, dispense_96, oemerr, , move_plate)
 ```


```
my-project
│   deck.lay
│   robot_method.py 
```
_Project directory structure_ 

In `robot_method.py`, 

<br>
<br>
<br>

```
my-project
│   README.md
│   file001.txt    
│
└───folder1
│   │   file011.txt
```
