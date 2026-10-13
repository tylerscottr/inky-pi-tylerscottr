#!/bin/bash

# Hardware PWM for the LED on Spectra 6 Inky Impressions (GPIO 13), so InkyPi can dim it.
# Sourced by install.sh, update.sh, and uninstall.sh. Expects VENV_PATH to be set.

LED_DIMMING_MARKER="# Added by InkyPi for Inky Impression LED dimming"
LED_DIMMING_OVERLAY="dtoverlay=pwm,pin=13,func=4"
# Set to true when config.txt changes, so the caller can offer a reboot.
BOOT_CONFIG_CHANGED=false

boot_config_file() {
  local file
  for file in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "$file" ]; then
      echo "$file"
      return
    fi
  done
}

# Prints the Inky EEPROM display variant, or nothing if there's no Inky board.
inky_display_variant() {
  "$VENV_PATH/bin/python" -c \
    "from inky.eeprom import read_eeprom; e = read_eeprom(); print(e.display_variant if e else '')" 2>/dev/null
}

backup_boot_config() {
  if [ ! -f "$1.inkypi-backup" ]; then
    cp "$1" "$1.inkypi-backup"
    echo "Backed up $1 to $1.inkypi-backup"
  fi
}

enable_led_dimming() {
  local config
  config=$(boot_config_file)
  if [ -z "$config" ] || grep -qxF "$LED_DIMMING_MARKER" "$config"; then
    return
  fi

  # Spectra 6 Impressions, the boards with the LED. Matches SPECTRA_VARIANTS in src/display/inky_display.py.
  case "$(inky_display_variant)" in
    21|22|25|26|27) ;;
    *) return ;;
  esac

  if [ ! -f "$(dirname "$config")/overlays/pwm.dtbo" ]; then
    echo "The pwm overlay isn't installed, so the LED will switch on and off without dimming."
    return
  fi
  if grep -Eq '^[[:space:]]*dtoverlay=pwm(-2chan)?([,[:space:]]|$)' "$config"; then
    echo "$config already loads a PWM overlay, so LED dimming wasn't added. The LED will switch on and off."
    return
  fi

  backup_boot_config "$config"
  # [all] makes the overlay apply to every model, whatever section the file ends in.
  printf '\n[all]\n%s\n%s\n' "$LED_DIMMING_MARKER" "$LED_DIMMING_OVERLAY" >> "$config"
  BOOT_CONFIG_CHANGED=true
  echo_success "Enabled LED dimming in $config."

  case "$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')" in
    ""|*Zero*|*"Pi 5"*|*"Compute Module"*) ;;
    *) echo "Note: analog audio from the headphone jack can share this PWM. If you use it, rerun with --no-led-dimming." ;;
  esac
}

disable_led_dimming() {
  local config
  config=$(boot_config_file)
  if [ -z "$config" ] || ! grep -qxF "$LED_DIMMING_MARKER" "$config"; then
    return
  fi

  backup_boot_config "$config"
  # Removes the marker and the overlay line after it. The [all] line stays, since later lines may rely on it.
  sed -i "/^${LED_DIMMING_MARKER}\$/,+1d" "$config"
  BOOT_CONFIG_CHANGED=true
  echo_success "Removed LED dimming from $config."
}
