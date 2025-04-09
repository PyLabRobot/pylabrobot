# Setting up PLR on a Raspberry Pi

You can use PLR on any operating system, but Raspberry Pis can be a good choice if you want to run PLR on a dedicated device. They are cheap ($50) and can be left running 24/7. Any user on your network can ssh into it and use a workcell.

## Setting up the Raspberry Pi

- Use the Raspberry Pi Imager to install the Raspberry Pi OS on a microSD card: [https://www.raspberrypi.com/software/](https://www.raspberrypi.com/software/).
  - During the flashing, it is recommended to add a hostname and create an initial user so that you can SSH into the Raspberry Pi headlessly.
- After flashing, insert the microSD card into the Raspberry Pi and boot it up. Connect it to your network using an Ethernet cable.
- Alternatively, you can use WiFi if you configured it during flashing.
- SSH into the Raspberry Pi using the hostname and user you created during flashing.
  ```bash
  ssh <username>@<hostname>.local
  ```
- Update the Raspberry Pi:
  ```bash
  sudo apt update
  sudo apt upgrade
  ```
- Make USB devices accessible to users: add the following line to `/etc/udev/rules.d/99-usb.rules`:
  ```
  SUBSYSTEM=="usb", MODE="0666"
  ```
- Reload the udev rules with
  ```bash
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```

```{warning}
This adds permissions to all USB devices. This is useful when you control the device and don't want to worry when plugging in new devices, but it could be a security risk if the machine is shared with untrusted users. See [udev documentation](https://www.kernel.org/pub/linux/utils/kernel/hotplug/udev/udev.html) for more granular control.
```

## Setting up PLR

- See [installation instructions](https://docs.pylabrobot.org/user_guide/installation.html#installing-pylabrobot).
