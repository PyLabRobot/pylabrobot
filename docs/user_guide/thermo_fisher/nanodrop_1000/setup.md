# NanoDrop 1000 setup

Install PyLabRobot with USB support:

```bash
pip install "pylabrobot[usb]"
```

## Windows USB driver

The NanoDrop 1000 must use a libusb-compatible driver before PyLabRobot can communicate with it.
Changing this driver prevents the official NanoDrop software from using the device until you switch
the driver back.

Some Windows 10 and 11 systems report the NanoDrop as an unknown USB device because its older USB
controller does not provide a BOS descriptor. Only if Device Manager reports that problem, open an
administrator Command Prompt and run:

```bat
reg add "HKLM\SYSTEM\CurrentControlSet\Control\usbflags\245710020002" /v SkipBOSDescriptorQuery /t REG_DWORD /d 1 /f
```

Unplug and reconnect the NanoDrop after changing the setting.

Install the libusb driver with Zadig:

1. Download and run [Zadig](https://zadig.akeo.ie/).
2. Select **Options > List All Devices**.
3. Select **NanoDrop 1000**.
4. Select **libusb-win32** as the replacement driver.
5. Click **Replace Driver** or **Install Driver**.

## Restore the official driver

To use the official NanoDrop software again:

1. Open Device Manager.
2. Find the NanoDrop 1000 under **libusb-win32 devices**.
3. Select **Update driver > Browse my computer for drivers > Let me pick from a list**.
4. Select the original NanoDrop or Cypress EZ-USB driver.

If you added the BOS descriptor registry setting and want to remove it, run this from an
administrator Command Prompt:

```bat
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\usbflags\245710020002" /v SkipBOSDescriptorQuery /f
```

Continue with the [NanoDrop 1000 hello world](hello-world.md).
