# **Controlling the Waveshare Robot Arm with PyLabRobot**

The Waveshare robot arm implementation in PyLabRobot is designed to be used with a Raspberry Pi server running the roarm\_server.py code. This code facilitates communication between your main Python program and the physical robot arm.

## **Setting up your Raspberry Pi Server**

Follow these steps to set up your Raspberry Pi and run the server code:

**1\. Prepare your Raspberry Pi:**

* Install the Raspberry Pi OS on your device. We recommend using a fresh install for optimal performance.
* Ensure your Raspberry Pi is connected to your network, either via Ethernet or Wi-Fi. This is crucial for communication with your main computer.
* Establish an SSH connection to your Raspberry Pi. This will allow you to remotely control and monitor the server.

**2\. Install necessary dependencies:**

* Update your Raspberry Pi OS and install the required Python packages:

Bash

sudo apt-get update
sudo apt-get upgrade
sudo apt-get install python3-pip
pip3 install pylabrobot RPi.GPIO

**3\. Configure the server code:**

* Navigate to the directory you’ve placed roarm\_server.py on your Raspberry Pi.

* Open the roarm\_server.py file and adjust the following settings if needed:

  * HOST: The IP address of your Raspberry Pi.
  * PORT: The port number used for communication (default is 65432).

**4\. Run the server:**

* Execute the server code on your Raspberry Pi:

Bash

python3 roarm\_server.py

* The server will start running and wait for connections from your main program.

**5\. Connect from your main program:**

* In your main Python program, instantiate the RoboticArm class from PyLabRobot with the WaveshareRoarm backend, providing the IP address and port number of your Raspberry Pi server.

Now you can control your Waveshare robot arm using PyLabRobot commands\!

## **Need More Help?**

Please consult the [forum](https://discuss.pylabrobot.org/).
