#!/bin/bash

# Formatting stuff
bold=$(tput bold)
normal=$(tput sgr0)
green=$(tput setaf 2)
red=$(tput setaf 1)

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )

APPNAME="inkypi"
INSTALL_PATH="/usr/local/$APPNAME"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"

SERVICE_FILE="$APPNAME.service"
SERVICE_FILE_SOURCE="$SCRIPT_DIR/$SERVICE_FILE"
SERVICE_FILE_TARGET="/etc/systemd/system/$SERVICE_FILE"

APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PIP_REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

PULL=false
LED_DIMMING=true
# Options other than --pull, passed on when the script reruns itself after pulling.
FORWARD_ARGS=()

source "$SCRIPT_DIR/led_dimming.sh"

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_error() {
  echo -e "$1 [\e[31m\xE2\x9C\x98\e[0m]\n"
}

usage() {
  echo "Usage: sudo bash install/update.sh [--pull] [--no-led-dimming]"
  echo "  --pull            Pull the latest code with 'git pull --rebase' before updating."
  echo "  --no-led-dimming  Don't set up (or remove) hardware PWM for the Inky Impression LED in config.txt."
}

parse_arguments() {
  for arg in "$@"; do
    case "$arg" in
      --pull) PULL=true ;;
      --no-led-dimming) LED_DIMMING=false; FORWARD_ARGS+=("$arg") ;;
      -h|--help) usage; exit 0 ;;
      *) echo_error "ERROR: Unknown option '$arg'."; usage; exit 1 ;;
    esac
  done
}

ask_for_reboot() {
  echo "A reboot is needed for the config.txt change to take effect."
  if [ ! -t 0 ]; then
    echo "Reboot later with 'sudo reboot now'."
    return
  fi
  read -p "Would you like to restart your Raspberry Pi now? [Y/N] " userInput
  if [[ "${userInput,,}" == "y" ]]; then
    echo_success "Rebooting now..."
    sleep 2
    sudo reboot now
  else
    echo "Reboot later with 'sudo reboot now'."
  fi
}

# Run git as the checkout's owner so root never owns files under .git.
repo_git() {
  sudo -u "$REPO_OWNER" -H git -C "$REPO_DIR" "$@"
}

pull_latest() {
  REPO_DIR=$( cd "$SCRIPT_DIR/.." && pwd )
  REPO_OWNER=$(stat -c '%U' "$REPO_DIR")

  if ! repo_git diff --quiet HEAD; then
    echo_error "ERROR: $REPO_DIR has uncommitted changes. Commit or stash them, then rerun."
    exit 1
  fi

  echo "Pulling latest code into $REPO_DIR..."
  # Rebasing keeps the checkout in sync even when the remote branch was force-pushed.
  if ! repo_git pull --rebase; then
    repo_git rebase --abort > /dev/null 2>&1
    echo_error "ERROR: 'git pull --rebase' failed. Fix it in $REPO_DIR, then rerun."
    exit 1
  fi
  echo_success "Now at $(repo_git log --oneline -1)."
}

setup_zramswap_service() {
  echo "Enabling and starting zramswap service."
  sudo apt-get install -y zram-tools > /dev/null
  echo -e "ALGO=zstd\nPERCENT=60" | sudo tee /etc/default/zramswap > /dev/null
  sudo systemctl enable --now zramswap
}

setup_earlyoom_service() {
  echo "Enabling and starting earlyoom service."
  sudo apt-get install -y earlyoom > /dev/null
  sudo systemctl enable --now earlyoom
}

update_app_service() {
  echo "Updating $APPNAME systemd service."
  if [ -f "$SERVICE_FILE_SOURCE" ]; then
    cp "$SERVICE_FILE_SOURCE" "$SERVICE_FILE_TARGET"
    echo "Restarting $APPNAME service."
    sudo systemctl daemon-reload
    sudo systemctl restart $SERVICE_FILE
  else
    echo_error "ERROR: Service file $SERVICE_FILE_SOURCE not found!"
    exit 1
  fi
}

update_cli() {
  cp -r "$SCRIPT_DIR/cli" "$INSTALL_PATH/"
  sudo chmod +x "$INSTALL_PATH/cli/"*
}

# Get OS release number, e.g. 11=Bullseye, 12=Bookworm, 13=Trixe
get_os_version() {
  echo "$(lsb_release -sr)"
}

parse_arguments "$@"

# Ensure script is run with sudo
if [ "$EUID" -ne 0 ]; then
  echo_error "ERROR: This script requires root privileges. Please run it with sudo."
  exit 1
fi

if [ "$PULL" = true ]; then
  pull_latest
  # Rerun the pulled copy of this script so the update uses its latest steps.
  exec bash "$SCRIPT_DIR/update.sh" "${FORWARD_ARGS[@]}"
fi

apt-get update -y > /dev/null
if [ -f "$APT_REQUIREMENTS_FILE" ]; then
  echo "Installing system dependencies... "
  xargs -a "$APT_REQUIREMENTS_FILE" sudo apt-get install -y > /dev/null && echo_success "Installed system dependencies."
else
  echo_error "ERROR: System dependencies file $APT_REQUIREMENTS_FILE not found!"
  exit 1
fi

# check OS version for Bookworm to setup zramswap
if [[ $(get_os_version) = "12" ]] ; then
  echo "OS version is Bookworm - setting up zramswap"
  setup_zramswap_service
else
  echo "OS version is not Bookworm - skipping zramswap setup."
fi
setup_earlyoom_service

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
  echo_error "ERROR: Virtual environment not found at $VENV_PATH. Run the installation script first."
  exit 1
fi

# Activate the virtual environment
source "$VENV_PATH/bin/activate"

# Upgrade pip
echo "Upgrading pip..."
$VENV_PATH/bin/python -m pip install --upgrade pip setuptools wheel > /dev/null && echo_success "Pip upgraded successfully."

# Install or update Python dependencies
if [ -f "$PIP_REQUIREMENTS_FILE" ]; then
  echo "Updating Python dependencies..."
  $VENV_PATH/bin/python -m pip install --upgrade -r "$PIP_REQUIREMENTS_FILE" -qq > /dev/null && echo_success "Dependencies updated successfully."
else
  echo_error "ERROR: Requirements file $PIP_REQUIREMENTS_FILE not found!"
  exit 1
fi

if [ "$LED_DIMMING" = true ]; then
  enable_led_dimming
else
  disable_led_dimming
fi

echo "Updating executable in ${BINPATH}/$APPNAME"
cp $SCRIPT_DIR/inkypi $BINPATH/
sudo chmod +x $BINPATH/$APPNAME

echo "Update JS and CSS files"
bash $SCRIPT_DIR/update_vendors.sh > /dev/null

update_app_service
update_cli

echo_success "Update completed."

if [ "$BOOT_CONFIG_CHANGED" = true ]; then
  ask_for_reboot
fi
