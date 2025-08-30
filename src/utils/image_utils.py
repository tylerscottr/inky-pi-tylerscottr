import requests
from PIL import Image, ImageEnhance
from io import BytesIO
import os
import logging
import hashlib
import tempfile
import asyncio
import platform
import shutil
from pyppeteer import launch

logger = logging.getLogger(__name__)

def get_image(image_url):
    response = requests.get(image_url)
    img = None
    if 200 <= response.status_code < 300 or response.status_code == 304:
        img = Image.open(BytesIO(response.content))
    else:
        logger.error(f"Received non-200 response from {image_url}: status_code: {response.status_code}")
    return img

def change_orientation(image, orientation, inverted=False):
    if orientation == 'horizontal':
        angle = 0
    elif orientation == 'vertical':
        angle = 90

    if inverted:
        angle = (angle + 180) % 360

    return image.rotate(angle, expand=1)

def resize_image(image, desired_size, image_settings=[]):
    img_width, img_height = image.size
    desired_width, desired_height = desired_size
    desired_width, desired_height = int(desired_width), int(desired_height)

    img_ratio = img_width / img_height
    desired_ratio = desired_width / desired_height

    keep_width = "keep-width" in image_settings

    x_offset, y_offset = 0,0
    new_width, new_height = img_width,img_height
    # Step 1: Determine crop dimensions
    desired_ratio = desired_width / desired_height
    if img_ratio > desired_ratio:
        # Image is wider than desired aspect ratio
        new_width = int(img_height * desired_ratio)
        if not keep_width:
            x_offset = (img_width - new_width) // 2
    else:
        # Image is taller than desired aspect ratio
        new_height = int(img_width / desired_ratio)
        if not keep_width:
            y_offset = (img_height - new_height) // 2

    # Step 2: Crop the image
    image = image.crop((x_offset, y_offset, x_offset + new_width, y_offset + new_height))

    # Step 3: Resize to the exact desired dimensions (if necessary)
    return image.resize((desired_width, desired_height), Image.LANCZOS)

def apply_image_enhancement(img, image_settings={}):

    # Apply Brightness
    img = ImageEnhance.Brightness(img).enhance(image_settings.get("brightness", 1.0))

    # Apply Contrast
    img = ImageEnhance.Contrast(img).enhance(image_settings.get("contrast", 1.0))

    # Apply Saturation (Color)
    img = ImageEnhance.Color(img).enhance(image_settings.get("saturation", 1.0))

    # Apply Sharpness
    img = ImageEnhance.Sharpness(img).enhance(image_settings.get("sharpness", 1.0))

    return img

def compute_image_hash(image):
    """Compute SHA-256 hash of an image."""
    image = image.convert("RGB")
    img_bytes = image.tobytes()
    return hashlib.sha256(img_bytes).hexdigest()

def take_screenshot_html(html_str, dimensions, timeout_ms=None):
    image = None
    try:
        # Create a temporary HTML file
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as html_file:
            html_file.write(html_str.encode("utf-8"))
            html_file_path = html_file.name

        image = take_screenshot(html_file_path, dimensions, timeout_ms)

        # Remove html file
        os.remove(html_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image

def get_chrome_executable():
    """Find the Chrome/Chromium executable for the current platform."""

    # Common Chrome installation paths on Windows
    chrome_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in chrome_paths:
        if os.path.exists(path):
            return path
    # Fallback to searching in PATH
    chrome_cmd = shutil.which("chrome")
    if chrome_cmd:
        return chrome_cmd
    
    # Try common executable names
    for cmd in ["chromium-headless-shell", "chrome", "chrome.exe", "chromium", "chromium.exe"]:
        if shutil.which(cmd):
            return cmd

    # We'll let pyppeteer download its own Chromium
    return None

async def take_screenshot_pyppeteer(target, output_path, dimensions, timeout_ms=None):
    """Take a screenshot using Pyppeteer."""
    browser = None

    # Get Chrome executable path
    chrome_executable = get_chrome_executable()
    logger.info(f"Using Chrome executable: {chrome_executable if chrome_executable else 'Pyppeteer default'}")
    
    try:
        launch_options = {
            'headless': True,
            'args': [
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--use-gl=swiftshader',
                '--hide-scrollbars',
                '--in-process-gpu',
                '--js-flags=--jitless',
                '--disable-zero-copy',
                '--disable-gpu-memory-buffer-compositor-resources',
                '--disable-extensions',
                '--disable-plugins',
                '--mute-audio',
                '--no-sandbox',
            ],
            'handleSIGINT': False,
            'handleSIGTERM': False,
            'handleSIGHUP': False,
        }
        
        # Use existing Chrome if available, otherwise let Pyppeteer download
        if chrome_executable:
            launch_options['executablePath'] = chrome_executable

        # Launch browser and load the page
        browser = await launch(**launch_options)
        page = await browser.newPage()
        
        # Set viewport
        await page.setViewport({
            'width': dimensions[0],
            'height': dimensions[1],
            'deviceScaleFactor': 1
        })

        # Navigate to target
        url = target
        if not target.startswith(('http://', 'https://', 'file://')):
            # Convert file path to file:// URL
            url = f"file://{os.path.abspath(target)}"

        # Set timeout if specified
        timeout = timeout_ms if timeout_ms else 30000
        
        await page.goto(url, {
            'waitUntil': 'networkidle0',
            'timeout': timeout
        })

        # Take screenshot
        await page.screenshot({'path': output_path})
        
        return True

    except Exception as e:
        logger.error(f"Pyppeteer screenshot failed: {str(e)}")
        return False
    finally:
        if browser:
            await browser.close()

def take_screenshot(target, dimensions, timeout_ms=None):
    """Take a screenshot using Pyppeteer (wrapper for async function)."""
    image = None
    try:
        # Create a temporary output file for the screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_file:
            img_file_path = img_file.name

        print(f"Screenshot target: {target}")

        # Run the async Pyppeteer function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(
                take_screenshot_pyppeteer(target, img_file_path, dimensions, timeout_ms)
            )
        finally:
            loop.close()

        if not success or not os.path.exists(img_file_path):
            logger.error("Failed to take screenshot with Pyppeteer")
            return None

        # Load the image using PIL
        with Image.open(img_file_path) as img:
            image = img.copy()

        # Remove image files
        os.remove(img_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image
