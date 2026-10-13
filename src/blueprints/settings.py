from flask import Blueprint, request, jsonify, current_app, render_template, Response
from buttons import BUTTONS, GENERIC_ACTIONS, PLAYLIST_ACTION_PREFIX, get_button_config, is_valid_action
from led import LED_USES, get_led_config
from refresh_schedule import DEFAULT_HOLD_SETTINGS, HOLD_FIXED, HOLD_MINIMUM, PRERENDER_MODES, PRERENDER_ON_SCREEN
from datetime import datetime, timedelta
import os
import pytz
import logging
import io

# Try to import cysystemd for journal reading (Linux only)
try:
    from cysystemd.reader import JournalReader, JournalOpenMode, Rule
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False
    # Define dummy classes for when cysystemd is not available
    class JournalOpenMode:
        SYSTEM = None
    class Rule:
        pass
    class JournalReader:
        def __init__(self, *args, **kwargs):
            pass


logger = logging.getLogger(__name__)
settings_bp = Blueprint("settings", __name__)

@settings_bp.route('/settings')
def settings_page():
    device_config = current_app.config['DEVICE_CONFIG']
    timezones = sorted(pytz.all_timezones_set)
    playlists = device_config.get_playlist_manager().playlists
    return render_template(
        'settings.html',
        device_settings=device_config.get_config(),
        timezones=timezones,
        hold_settings={**DEFAULT_HOLD_SETTINGS, **(device_config.get_config("hold_settings", default=None) or {})},
        prerender_mode=device_config.get_config("prerender_mode", default=PRERENDER_ON_SCREEN),
        button_config=get_button_config(device_config),
        led_config=get_led_config(device_config),
        led_status=current_app.config['LED'].status,
        buttons=BUTTONS,
        button_actions=GENERIC_ACTIONS + [
            {"id": PLAYLIST_ACTION_PREFIX + p.id, "label": f"Playlist: {p.name}"} for p in playlists])

def _parse_hold_settings(form_data):
    """Returns the hold settings from the form, or raises ValueError."""
    mode = form_data.get("holdMode")
    if mode not in [HOLD_FIXED, HOLD_MINIMUM]:
        raise ValueError("Hold mode is required")
    settings = {"mode": mode}
    for key, field in [("fixed_minutes", "holdFixedMinutes"), ("minimum_minutes", "holdMinimumMinutes"),
                       ("maximum_minutes", "holdMaximumMinutes")]:
        value = form_data.get(field, "")
        if not value.isnumeric() or int(value) <= 0:
            raise ValueError("Hold durations must be whole minutes")
        settings[key] = int(value)
    if settings["minimum_minutes"] > settings["maximum_minutes"]:
        raise ValueError("The minimum hold can't be longer than the maximum")
    return settings

def _parse_button_settings(form_data, playlist_ids):
    """Returns the button settings from the form, or raises ValueError."""
    try:
        long_press_seconds = float(form_data.get("longPressSeconds", ""))
    except ValueError:
        raise ValueError("Long press time is required")
    if not 0.3 <= long_press_seconds <= 5:
        raise ValueError("Long press time must be between 0.3 and 5 seconds")

    settings = {"long_press_seconds": long_press_seconds}
    pins = []
    for button in BUTTONS:
        pin = form_data.get(f"button{button}Pin", "").strip()
        if pin and (not pin.isnumeric() or int(pin) > 27):
            raise ValueError(f"Button {button} pin must be a GPIO number from 0 to 27")
        if pin and int(pin) in pins:
            raise ValueError(f"GPIO {pin} is used by more than one button")
        pins.append(int(pin) if pin else None)

        actions = {press: form_data.get(f"button{button}{press.title()}") for press in ["short", "long"]}
        for action in actions.values():
            if not action or not is_valid_action(action, playlist_ids):
                raise ValueError(f"Invalid action for button {button}")
        settings[button] = {"pin": pins[-1], **actions}
    return settings

def _parse_led_settings(form_data, button_settings):
    """Returns the LED settings from the form, or raises ValueError."""
    pin = form_data.get("ledPin", "").strip()
    if pin and (not pin.isnumeric() or int(pin) > 27):
        raise ValueError("LED pin must be a GPIO number from 0 to 27")
    pin = int(pin) if pin else None
    if pin is not None and any(button_settings[button]["pin"] == pin for button in BUTTONS):
        raise ValueError(f"GPIO {pin} is used by a button")

    brightness = form_data.get("ledMaxBrightness", "")
    if not brightness.isnumeric() or not 1 <= int(brightness) <= 100:
        raise ValueError("LED brightness must be from 1 to 100%")

    settings = {"pin": pin, "max_brightness": int(brightness)}
    for use in LED_USES:
        settings[use] = form_data.get(f"led_{use}") == "on"
    return settings

@settings_bp.route('/save_settings', methods=['POST'])
def save_settings():
    device_config = current_app.config['DEVICE_CONFIG']

    try:
        form_data = request.form.to_dict()

        time_format = form_data.get("timeFormat")
        if not form_data.get("timezoneName"):
            return jsonify({"error": "Time Zone is required"}), 400
        if not time_format or time_format not in ["12h", "24h"]:
            return jsonify({"error": "Time format is required"}), 400
        if form_data.get("prerenderMode") not in PRERENDER_MODES:
            return jsonify({"error": "Background rendering mode is required"}), 400
        try:
            hold_settings = _parse_hold_settings(form_data)
            playlist_ids = [p.id for p in device_config.get_playlist_manager().playlists]
            button_settings = _parse_button_settings(form_data, playlist_ids)
            led_settings = _parse_led_settings(form_data, button_settings)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        settings = {
            "name": form_data.get("deviceName"),
            "orientation": form_data.get("orientation"),
            "inverted_image": form_data.get("invertImage"),
            "log_system_stats": form_data.get("logSystemStats"),
            "timezone": form_data.get("timezoneName"),
            "time_format": form_data.get("timeFormat"),
            "hold_settings": hold_settings,
            "prerender_mode": form_data.get("prerenderMode"),
            "buttons": button_settings,
            "led": led_settings,
            "image_settings": {
                "saturation": float(form_data.get("saturation", "1.0")),
                "brightness": float(form_data.get("brightness", "1.0")),
                "sharpness": float(form_data.get("sharpness", "1.0")),
                "contrast": float(form_data.get("contrast", "1.0"))
            }
        }
        if "inky_saturation" in form_data:
            settings["image_settings"]["inky_saturation"] = float(form_data.get("inky_saturation", "0.5"))
        previous_buttons = get_button_config(device_config)
        previous_led = get_led_config(device_config)
        device_config.update_config(settings)

        current_app.config['REFRESH_TASK'].signal_config_change()
        if get_button_config(device_config) != previous_buttons:
            current_app.config['BUTTON_SERVICE'].reload()
        if get_led_config(device_config) != previous_led:
            current_app.config['LED'].reload()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": "Saved settings."})

@settings_bp.route('/shutdown', methods=['POST'])
def shutdown():
    data = request.get_json() or {}
    if data.get("reboot"):
        logger.info("Reboot requested")
        os.system("sudo reboot")
    else:
        logger.info("Shutdown requested")
        os.system("sudo shutdown -h now")
    return jsonify({"success": True})

@settings_bp.route('/download-logs')
def download_logs():
    try:
        buffer = io.StringIO()
        
        # Get 'hours' from query parameters, default to 2 if not provided or invalid
        hours_str = request.args.get('hours', '2')
        try:
            hours = int(hours_str)
        except ValueError:
            hours = 2
        since = datetime.now() - timedelta(hours=hours)

        if not JOURNAL_AVAILABLE:
            # Return a message when running in development mode without systemd
            buffer.write(f"Log download not available in development mode (cysystemd not installed).\n")
            buffer.write(f"Logs would normally show InkyPi service logs from the last {hours} hours.\n")
            buffer.write(f"\nTo see Flask development logs, check your terminal output.\n")
        else:
            reader = JournalReader()
            reader.open(JournalOpenMode.SYSTEM)
            reader.add_filter(Rule("_SYSTEMD_UNIT", "inkypi.service"))
            reader.seek_realtime_usec(int(since.timestamp() * 1_000_000))

            for record in reader:
                try:
                    ts = datetime.fromtimestamp(record.get_realtime_usec() / 1_000_000)
                    formatted_ts = ts.strftime("%b %d %H:%M:%S")
                except Exception:
                    formatted_ts = "??? ?? ??:??:??"

                data = record.data
                hostname = data.get("_HOSTNAME", "unknown-host")
                identifier = data.get("SYSLOG_IDENTIFIER") or data.get("_COMM", "?")
                pid = data.get("_PID", "?")
                msg = data.get("MESSAGE", "").rstrip()

                # Format the log entry similar to the journalctl default output
                buffer.write(f"{formatted_ts} {hostname} {identifier}[{pid}]: {msg}\n")

        buffer.seek(0)
        # Add date and time to the filename
        now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"inkypi_{now_str}.log"
        return Response(
            buffer.read(),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return Response(f"Error reading logs: {e}", status=500, mimetype="text/plain")

