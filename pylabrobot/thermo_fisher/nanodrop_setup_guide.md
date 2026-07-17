```
The NanoDrop Open-Source Installation Guide
```

```
Step 1: The BOS Descriptor Fix (Windows 10/11) Because the NanoDrop uses an
older USB 1.1 microcontroller, plugging it into a modern Windows 10/11 system
can cause Windows to request a "BOS Descriptor"—a feature that didn't exist when
the machine was built. This causes Windows to flag it as an "Unknown Device."
1.Download the SkipBOSDescriptor.reg file from the GitHub repository.
```

`2. Double-click the .reg file and click Yes on the Administrator prompt to merge it into your registry.` 

```
Important Note: If Windows opens the file in Notepad instead of running
it, Windows is likely hiding file extensions and saved it as a .txt file. To
fix this: Open Windows File Explorer, click the View tab at the top, and check
the box for File name extensions. Rename the downloaded file to ensure it ends
in .reg (not .reg.txt).
```

```
Further, sometimes windows will not let you open .reg files regularly, in
this case you could try:
```

   `1. Press Win + R to open the Run dialogue box.` 

   `2. Type 'regedit' and press Enter to open the Registry Editor.` 

   `3. In the top menu, click File > Import.` 

   `4. Locate your .reg file, select it, and click Open.` 

`3. Unplug the NanoDrop and plug it back in. It will now be recognized.` 

```
Step 2: Installing the Python Driver (Zadig) To control the machine with Python,
we must temporarily replace the official driver with an open-source one.
```

`1. Download and run Zadig (zadig.akeo.ie).` 

`2. Go to Options -> List All Devices.` 

`3. Select the NanoDrop 1000 from the main dropdown menu.` 

```
4.On the right side of the green arrow, use the up/down arrows to select
libusb-win32.
```

```
5.Click Replace Driver (or Install Driver) and wait for the "Success"
message.
```

`6. Your Python script can now communicate with the hardware.` 

```
Reverting to the Official NanoDrop Software
```

```
If you wish to revert to the original software, you can seamlessly swap back to
the proprietary driver without uninstalling anything.
```

`1. Open Windows Device Manager.` 

`2. Scroll down and find the NanoDrop 1000 (it will likely be under "libusbwin32 devices").` 

`3. Right-click the device and select Update driver.` 

`4. Click Browse my computer for drivers.` 

`5. Click Let me pick from a list of available drivers on my computer.` 

`6. You will see a list containing the driver you just installed (libusbwin32) and the original official driver (often named something like NanoDrop 1000 Spectrometer or Cypress EZ-USB).` 

`7. Select the original official driver and click Next.` 

```
8.Wait a few seconds for Windows to swap them over. You can now open the
official NanoDrop software.
```

