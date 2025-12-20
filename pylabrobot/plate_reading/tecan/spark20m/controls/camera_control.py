import logging
from .base_control import baseControl
from .spark_enums import CameraMode, TriggerMode, FlippingMode, BrightnessState

class cameraControl(baseControl):

    async def initialize_camera(self, ini_file_path=None):
        """Initializes the camera."""
        command = "CAMERA INIT"
        if ini_file_path:
            command += f" INIPATH={ini_file_path}"
        return await self.send_command(command)

    async def is_camera_initialized(self):
        """Checks if the camera is initialized."""
        response = await self.send_command("?CAMERA ISINITIALIZED")
        return response == "ISINITIALIZED=TRUE"

    async def reset_camera(self):
        """Resets the camera."""
        return await self.send_command("CAMERA RESET")

    async def close_camera(self):
        """Closes the camera connection."""
        return await self.send_command("CAMERA CLOSE")

    async def acquire_camera_instance(self, instance_guid):
        """Acquires the camera instance."""
        return await self.send_command(f"CAMERA ACQUIREINSTANCEGUID={instance_guid.upper()}")

    async def release_camera_instance(self):
        """Releases the camera instance."""
        return await self.send_command("CAMERA RELEASEINSTANCEGUID")

    async def set_camera_pixel_clock(self, pixel_clock):
        """Sets the camera pixel clock."""
        return await self.send_command(f"CAMERA PIXELCLOCK={pixel_clock}")

    async def set_camera_maximal_pixel_clock(self):
        """Sets the camera to its maximal pixel clock."""
        return await self.send_command("CAMERA MAXPIXELCLOCK")

    async def set_camera_bits_per_pixel(self, bits_per_pixel):
        """Sets the camera bits per pixel."""
        return await self.send_command(f"CAMERA BITSPERPIXEL={bits_per_pixel}")

    async def set_camera_mode(self, mode: CameraMode):
        """Sets the camera mode."""
        return await self.send_command(f"CAMERA MODE={mode.value}")

    async def set_camera_exposure_time(self, time):
        """Sets the camera exposure time."""
        return await self.send_command(f"CAMERA EXPOSURETIME={time}")

    async def set_camera_gain(self, gain):
        """Sets the camera gain."""
        return await self.send_command(f"CAMERA GAIN={gain}")

    async def set_camera_area_of_interest(self, x, y, width, height):
        """Sets the camera area of interest."""
        return await self.send_command(f"CAMERA AOI X={x} Y={y} WIDTH={width} HEIGHT={height}")

    async def set_camera_flipping_mode(self, flipping_mode: FlippingMode):
        """Sets the camera flipping mode."""
        return await self.send_command(f"CAMERA FLIPPINGMODE={flipping_mode.value}")

    async def set_camera_black_level(self, black_level):
        """Sets the camera black level."""
        return await self.send_command(f"CAMERA BLACKLEVEL={black_level}")

    async def optimize_camera_brightness(self, aperture_setting=None, exposure_start_time=None, start_gain=None, max_gain=None, max_exposure_time=None, target_value=None, min_percent=None, max_percent=None):
        """Optimizes the camera brightness."""
        command = "CAMERA OPTIMIZE"
        if aperture_setting:
            command += f" APERTURE={aperture_setting.upper()}"
        elif all(v is not None for v in [exposure_start_time, start_gain, max_gain, max_exposure_time, target_value, min_percent, max_percent]):
            command += f" BRIGHTNESS EXPOSURESTARTTIME={exposure_start_time} STARTGAIN={start_gain} MAXGAIN={max_gain} MAXEXPOSURETIME={max_exposure_time} TARGETVALUE={target_value} MINPERCENT={min_percent} MAXPERCENT={max_percent}"
        else:
            logging.error("Invalid parameters for optimize_camera_brightness")
            return None
        return await self.send_command(command)

    async def take_camera_image(self, cell_camera_image_type=None, retries=3, timeout_ms=None):
        """Takes an image with the camera."""
        command = "CAMERA TAKEIMAGE"
        if cell_camera_image_type:
            command += f" TYPE={cell_camera_image_type.upper()} RETRIES={retries}"
        elif timeout_ms:
            command += f" TIMEOUT={timeout_ms}"
        else:
            logging.error("Invalid parameters for take_camera_image")
            return None
        return await self.send_command(command)

    async def set_camera_trigger_mode(self, mode: TriggerMode):
        """Sets the camera trigger mode."""
        return await self.send_command(f"CAMERA TRIGGERMODE={mode.value}")

    async def prepare_take_camera_image(self):
        """Prepares the camera for taking an image."""
        return await self.send_command("CAMERA PREPARETAKEIMAGE")

    async def fetch_camera_image(self, timeout_ms=5000):
        """Fetches the image from the camera."""
        return await self.send_command(f"CAMERA FETCHIMAGE TIMEOUT={timeout_ms}")

    async def clear_camera_autofocus_result(self):
        """Clears the autofocus result."""
        return await self.send_command("CAMERA AUTOFOCUS CLEAR")

    async def get_camera_instance_guid(self):
        """Gets the camera instance GUID."""
        return await self.send_command("?CAMERA INSTANCEGUID")

    async def get_current_camera_trigger_mode(self):
        """Gets the current camera trigger mode."""
        return await self.send_command("?CAMERA TRIGGERMODE")

    async def get_available_camera_trigger_modes(self):
        """Gets the available camera trigger modes."""
        response = await self.send_command("#CAMERA TRIGGERMODE")
        return response

    async def get_current_camera_pixel_clock(self):
        """Gets the current camera pixel clock."""
        return await self.send_command("?CAMERA PIXELCLOCK")

    async def get_camera_pixel_clock_range(self):
        """Gets the camera pixel clock range."""
        return await self.send_command("#CAMERA PIXELCLOCK")

    async def get_allowed_camera_pixel_clocks(self):
        """Gets the allowed camera pixel clocks."""
        response = await self.send_command("#CAMERA CONFIG ALLOWEDPIXELCLOCKS")
        return response

    async def get_current_camera_mode(self):
        """Gets the current camera mode."""
        return await self.send_command("?CAMERA MODE")

    async def get_camera_autofocus_image_count(self):
        """Gets the number of images taken during autofocus."""
        return await self.send_command("?CAMERA AUTOFOCUS IMAGECOUNT")

    async def get_camera_autofocus_details(self, image_number):
        """Gets the autofocus details for a specific image number."""
        return await self.send_command(f"?CAMERA AUTOFOCUSDETAIL IMAGE={image_number}")

    async def get_allowed_camera_exposure_time(self):
        """Gets the allowed camera exposure time range."""
        return await self.send_command("#CAMERA EXPOSURETIME")

    async def get_current_camera_exposure_time(self):
        """Gets the current camera exposure time."""
        return await self.send_command("?CAMERA EXPOSURETIME")

    async def get_allowed_camera_area_of_interest_property(self, area_property):
        """Gets the allowed range for a specific area of interest property."""
        return await self.send_command(f"#CAMERA AOI {area_property.upper()}")

    async def get_current_camera_area_of_interest(self):
        """Gets the current camera area of interest."""
        return await self.send_command("?CAMERA AOI")

    async def get_allowed_camera_area_of_interest(self):
        """Gets the maximum allowed camera area of interest."""
        return await self.send_command("?CAMERA MAXAOI")

    async def get_minimal_camera_area_of_interest(self):
        """Gets the minimal allowed camera area of interest."""
        return await self.send_command("?CAMERA MINAOI")

    async def get_current_camera_gain(self):
        """Gets the current camera gain."""
        return await self.send_command("?CAMERA GAIN")

    async def get_current_camera_flipping_mode(self):
        """Gets the current camera flipping mode."""
        return await self.send_command("?CAMERA FLIPPINGMODE")

    async def get_current_camera_black_level(self):
        """Gets the current camera black level."""
        return await self.send_command("?CAMERA BLACKLEVEL")

    async def get_camera_error(self):
        """Gets the camera error."""
        return await self.send_command("?CAMERA ERROR")

    async def get_camera_instrument_serial_number(self):
        """Gets the camera instrument serial number."""
        return await self.send_command("?CAMERA INSTRUMENTSERIALNUMBER")

    async def terminate_camera(self):
        """Terminates the camera."""
        return await self.send_command("CAMERA TERMINATE")

    async def get_camera_number_of_warnings(self):
        """Gets the number of camera warnings."""
        return await self.send_command("?CAMERA WARNING COUNT")

    async def get_camera_pixel_size(self):
        """Gets the camera pixel size."""
        return await self.send_command("?CAMERA CONFIG PIXELSIZE")

    async def get_current_camera_bits_per_pixel(self):
        """Gets the current camera bits per pixel."""
        return await self.send_command("?CAMERA BITSPERPIXEL")

    async def get_allowed_camera_bits_per_pixel(self):
        """Gets the allowed camera bits per pixel."""
        response = await self.send_command("#CAMERA BITSPERPIXEL")
        return response

    async def get_camera_warning(self, index):
        """Gets the camera warning at the given index."""
        return await self.send_command(f"?CAMERA WARNING INDEX={index}")

    async def clear_camera_warnings(self):
        """Clears the camera warnings."""
        return await self.send_command("CAMERA WARNINGS CLEAR")

    async def probe_camera(self, max_performance):
        """Probes the camera performance."""
        return await self.send_command(f"CAMERA PROBE MAXPERFORMANCE={max_performance}")

    async def prepare_camera(self, brightness: BrightnessState):
        """Prepares the camera."""
        return await self.send_command(f"CAMERA PREPARE BRIGHTNESS={brightness.value}")

    async def get_camera_driver_version(self):
        """Gets the camera driver version."""
        return await self.send_command("?INFO HARDWARE_VERSION")

    async def set_camera_aoi(self, x, y, width, height):
        """Sets the camera area of interest."""
        return await self.send_command(f"CAMERA AOI X={x} Y={y} WIDTH={width} HEIGHT={height}")

    async def get_allowed_camera_aoi_property(self, area_property):
        """Gets the allowed range for a specific area of interest property."""
        return await self.send_command(f"#CAMERA AOI {area_property.upper()}")

    async def get_current_camera_aoi(self):
        """Gets the current camera area of interest."""
        return await self.send_command("?CAMERA AOI")

    async def get_allowed_camera_aoi(self):
        """Gets the maximum allowed camera area of interest."""
        return await self.send_command("?CAMERA MAXAOI")

    async def get_minimal_camera_aoi(self):
        """Gets the minimal allowed camera area of interest."""
        return await self.send_command("?CAMERA MINAOI")

    async def clear_camera_af_result(self):
        """Clears the autofocus result."""
        return await self.send_command("CAMERA AUTOFOCUS CLEAR")

    async def get_camera_af_image_count(self):
        """Gets the number of images taken during autofocus."""
        return await self.send_command("?CAMERA AUTOFOCUS IMAGECOUNT")

    async def get_camera_af_details(self, image_number):
        """Gets the autofocus details for a specific image number."""
        return await self.send_command(f"?CAMERA AUTOFOCUSDETAIL IMAGE={image_number}")

    async def set_bpp(self, bits_per_pixel):
        """Sets the camera bits per pixel."""
        return await self.send_command(f"CAMERA BITSPERPIXEL={bits_per_pixel}")

    async def get_current_bpp(self):
        """Gets the current camera bits per pixel."""
        return await self.send_command("?CAMERA BITSPERPIXEL")

    async def get_allowed_bpp(self):
        """Gets the allowed camera bits per pixel."""
        response = await self.send_command("#CAMERA BITSPERPIXEL")
        return response

    async def get_pixel_size(self):
        """Gets the camera pixel size."""
        return await self.send_command("?CAMERA CONFIG PIXELSIZE")

    async def set_cam_black_level(self, black_level):
        """Sets the camera black level."""
        return await self.send_command(f"CAMERA BLACKLEVEL={black_level}")

    async def get_current_cam_black_level(self):
        """Gets the current camera black level."""
        return await self.send_command("?CAMERA BLACKLEVEL")

    async def set_camera_instrument_serial_number(self, serial_number):
        """Sets the camera instrument serial number."""
        return await self.send_command(f"CAMERA INSTRUMENTSERIALNUMBER={serial_number}")

    async def get_allowed_exposure_time(self):
        """Gets the allowed camera exposure time range."""
        return await self.send_command("#CAMERA EXPOSURETIME")

    async def get_current_exposure_time(self):
        """Gets the current camera exposure time."""
        return await self.send_command("?CAMERA EXPOSURETIME")

    async def set_exposure_time(self, time):
        """Sets the camera exposure time."""
        return await self.send_command(f"CAMERA EXPOSURETIME={time}")

    async def optimize_brightness(self, aperture_setting=None, exposure_start_time=None, start_gain=None, max_gain=None, max_exposure_time=None, target_value=None, min_percent=None, max_percent=None):
        """Optimizes the camera brightness."""
        command = "CAMERA OPTIMIZE"
        if aperture_setting:
            command += f" APERTURE={aperture_setting.upper()}"
        elif all(v is not None for v in [exposure_start_time, start_gain, max_gain, max_exposure_time, target_value, min_percent, max_percent]):
            command += f" BRIGHTNESS EXPOSURESTARTTIME={exposure_start_time} STARTGAIN={start_gain} MAXGAIN={max_gain} MAXEXPOSURETIME={max_exposure_time} TARGETVALUE={target_value} MINPERCENT={min_percent} MAXPERCENT={max_percent}"
        else:
            logging.error("Invalid parameters for optimize_camera_brightness")
            return None
        return await self.send_command(command)

    async def prepare_take_image_external_trigger(self):
        """Prepares the camera for taking an image using an external trigger."""
        return await self.send_command("CAMERA PREPARETAKEIMAGE")

    async def fetch_image_external_trigger(self, timeout_ms=5000):
        """Fetches the image from the camera after an external trigger."""
        return await self.send_command(f"CAMERA FETCHIMAGE TIMEOUT={timeout_ms}")

    async def set_cam_flipping_mode(self, flipping_mode: FlippingMode):
        """Sets the camera flipping mode."""
        return await self.send_command(f"CAMERA FLIPPINGMODE={flipping_mode.value}")

    async def get_current_cam_flipping_mode(self):
        """Gets the current camera flipping mode."""
        return await self.send_command("?CAMERA FLIPPINGMODE")

    async def set_cam_gain(self, gain):
        """Sets the camera gain."""
        return await self.send_command(f"CAMERA GAIN={gain}")

    async def get_current_cam_gain(self):
        """Gets the current camera gain."""
        return await self.send_command("?CAMERA GAIN")

    async def set_cam_pixel_clock(self, pixel_clock):
        """Sets the camera pixel clock."""
        return await self.send_command(f"CAMERA PIXELCLOCK={pixel_clock}")

    async def set_cam_maximal_pixel_clock(self):
        """Sets the camera to its maximal pixel clock."""
        return await self.send_command("CAMERA MAXPIXELCLOCK")

    async def get_current_cam_pixel_clock(self):
        """Gets the current camera pixel clock."""
        return await self.send_command("?CAMERA PIXELCLOCK")

    async def get_cam_pixel_clock_range(self):
        """Gets the camera pixel clock range."""
        return await self.send_command("#CAMERA PIXELCLOCK")

    async def get_allowed_cam_pixel_clocks(self):
        """Gets the allowed camera pixel clocks."""
        response = await self.send_command("#CAMERA CONFIG ALLOWEDPIXELCLOCKS")
        return response

    async def probe_cam(self, max_performance):
        """Probes the camera performance."""
        return await self.send_command(f"CAMERA PROBE MAXPERFORMANCE={max_performance}")
