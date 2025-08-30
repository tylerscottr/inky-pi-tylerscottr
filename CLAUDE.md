# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installation and Setup
- **Install InkyPi**: `sudo bash install/install.sh` (for Inky displays) or `sudo bash install/install.sh -W <model>` (for Waveshare displays)
- **Update**: `sudo bash install/update.sh` 
- **Uninstall**: `sudo bash install/uninstall.sh`
- **Create virtual environment**: `bash scripts/venv.sh`

### Testing and Development
- **Test a plugin**: `python scripts/test_plugin.py` - Tests plugins across different resolutions and orientations, generates composite output
- **Run development server**: The main application runs via `src/inkypi.py` using Waitress WSGI server on port 80

### Service Management
- **Service file location**: `/etc/systemd/system/inkypi.service`
- **Start service**: `sudo systemctl start inkypi`
- **Stop service**: `sudo systemctl stop inkypi`  
- **Check status**: `sudo systemctl status inkypi`

## Architecture Overview

### Core Application Structure
- **Main Entry**: `src/inkypi.py` - Flask application with Waitress WSGI server, registers blueprints and starts background refresh task
- **Configuration**: `src/config.py` - Manages device.json config, plugin discovery, playlist management, and environment variables
- **Display Management**: `src/display/display_manager.py` - Abstracts display hardware (Inky vs Waveshare) with image processing pipeline
- **Background Tasks**: `src/refresh_task.py` - Handles scheduled display updates and playlist management

### Plugin System
- **Base Plugin**: `src/plugins/base_plugin/base_plugin.py` - Abstract base class with HTML/CSS rendering capabilities via Jinja2 and Pyppeteer
- **Plugin Registry**: `src/plugins/plugin_registry.py` - Handles plugin discovery and instantiation
- **Plugin Structure**: Each plugin requires:
  - `plugin-info.json` - Metadata and configuration
  - Main Python file inheriting from BasePlugin
  - `generate_image(settings, device_config)` method returning PIL.Image
  - Optional: `settings.html`, `render/` directory for HTML/CSS templates

### Display Hardware Support
- **Inky Displays**: Pimoroni Inky displays via `inky` Python library (auto-detected)
- **Waveshare Displays**: E-Paper displays via custom drivers from Waveshare EPD library (specified during installation)
- **Display Types**: Determined by `display_type` config - "inky" or "epd*in*" pattern matching

### Web Interface (Flask Blueprints)
- **Main** (`blueprints/main.py`): Dashboard and display preview
- **Settings** (`blueprints/settings.py`): Device configuration
- **Plugin** (`blueprints/plugin.py`): Plugin management and settings
- **Playlist** (`blueprints/playlist.py`): Scheduled content management

### Key Utilities
- **Image Processing** (`utils/image_utils.py`): Resize, orientation, enhancement, HTML-to-image screenshot capabilities
- **App Utilities** (`utils/app_utils.py`): Path resolution, font management, startup image generation
- **Time Utilities** (`utils/time_utils.py`): Timezone and scheduling support

## Configuration Files

### Device Configuration
- **Location**: `src/config/device.json` (copied from `install/config_base/device.json`)
- **Contains**: Display settings, playlist config, refresh intervals, image settings

### Plugin Configuration  
- **Location**: Each plugin directory contains `plugin-info.json`
- **Format**: Plugin metadata, settings schema, dependencies

### Environment Variables
- **Location**: `.env` file (not in repo)
- **Usage**: API keys loaded via `device_config.load_env_key(key_name)`

## Development Workflow

### Adding New Plugins
1. Create directory in `src/plugins/` with lowercase plugin ID
2. Implement Python class inheriting from BasePlugin
3. Create `plugin-info.json` with metadata
4. Optional: Add `settings.html` and `render/` templates for HTML rendering
5. Test with `scripts/test_plugin.py` after updating the plugin_id variable

### HTML/CSS Rendering
- Plugins can render HTML templates using Jinja2 environment
- CSS files automatically loaded from `render/` directory
- Font faces available via `get_fonts()` utility
- Screenshots taken with Pyppeteer for image generation

### Image Processing Pipeline
1. Plugin generates base image
2. Display manager applies orientation changes
3. Image resized to display resolution
4. Optional inversion/rotation applied
5. Image saved to `static/images/current_image.png`
6. Passed to hardware-specific display driver

## Dependencies

### Python Requirements
- **Core**: Flask 3.1.1, Waitress 3.0.2, Pillow 11.0.0
- **Hardware**: inky 2.1.0 (Pimoroni displays)
- **Rendering**: pyppeteer 2.0.0 (HTML to image)
- **APIs**: openai 1.58.1, requests 2.32.4
- **Calendar**: icalendar 6.3.1, recurring-ical-events 3.7.0
- **System**: psutil 7.0.0, cysystemd 2.0.1

### System Requirements
- Raspberry Pi with SPI/I2C enabled
- E-Ink display (Inky or compatible Waveshare)
- Systemd for service management