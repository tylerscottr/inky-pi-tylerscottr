import logging
from inky.auto import auto
from display.abstract_display import AbstractDisplay


logger = logging.getLogger(__name__)

# EEPROM display variants of the Inky Impression boards, which have four buttons.
IMPRESSION_VARIANTS = {14, 15, 16, 20, 21, 22, 25, 26, 27}
# The 13.3" Impression's driver uses GPIO 16 as a chip select, so its button C is on GPIO 25.
IMPRESSION_13_VARIANTS = {21, 27}
# Spectra 6 Impressions have an LED on GPIO 13. install/led_dimming.sh lists the same variants.
SPECTRA_VARIANTS = {21, 22, 25, 26, 27}

class InkyDisplay(AbstractDisplay):

    """
    Handles the Inky e-paper display.

    This class initializes and manages interactions with the Inky display,
    ensuring proper image rendering and configuration storage.

    The Inky display driver supports auto configuration.
    """
   
    def initialize_display(self):
        
        """
        Initializes the Inky display device.

        Sets the display border and stores the display resolution in the device configuration.

        Raises:
            ValueError: If the resolution cannot be retrieved or stored.
        """
        
        self.inky_display = auto()
        self.inky_display.set_border(self.inky_display.BLACK)

        # store display resolution in device config
        if not self.device_config.get_config("resolution"):
            self.device_config.update_value(
                "resolution",
                [int(self.inky_display.width), int(self.inky_display.height)], 
                write=True)

    def _display_variant(self):
        eeprom = getattr(self.inky_display, "eeprom", None)
        return getattr(eeprom, "display_variant", None)

    def default_button_pins(self):
        variant = self._display_variant()
        if variant not in IMPRESSION_VARIANTS:
            return {}
        return {"A": 5, "B": 6, "C": 25 if variant in IMPRESSION_13_VARIANTS else 16, "D": 24}

    def default_led_pin(self):
        return 13 if self._display_variant() in SPECTRA_VARIANTS else None

    def display_image(self, image, image_settings=[]):
        
        """
        Displays the provided image on the Inky display.

        The image has been processed by adjusting orientation and resizing 
        before being sent to the display.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): Additional settings to modify image rendering.

        Raises:
            ValueError: If no image is provided.
        """

        logger.info("Displaying image to Inky display.")
        if not image:
            raise ValueError(f"No image provided.")

        # Display the image on the Inky display
        inky_saturation = self.device_config.get_config('image_settings').get("inky_saturation", 0.5)
        logger.info(f"Inky Saturation: {inky_saturation}")
        self.inky_display.set_image(image, saturation=inky_saturation)
        self.inky_display.show()