#!/usr/bin/env bash
cd "$(dirname "$0")"

# --- DualSense udev rules (Linux only) ---
# Without these rules the kernel blocks non-root access to the HID device.
RULES_SRC="$(pwd)/packaging/linux/70-dualsense.rules"
RULES_DEST="/etc/udev/rules.d/70-dualsense.rules"

if [ -f "$RULES_SRC" ] && [ ! -f "$RULES_DEST" ]; then
    echo ""
    echo "DualSense udev rules are not installed."
    echo "Without them the app cannot talk to the controller unless run as root."
    echo ""
    if command -v sudo &>/dev/null; then
        printf "Install now? (Y/n): "
        read -r ans
        case "${ans:-Y}" in
            [Yy]*)
                sudo cp "$RULES_SRC" "$RULES_DEST"
                sudo udevadm control --reload-rules
                sudo udevadm trigger
                echo "Rules installed. Unplug and replug your DualSense if it is already connected."
                ;;
            *)
                echo "Skipped. To install manually:"
                echo "  sudo cp packaging/linux/70-dualsense.rules /etc/udev/rules.d/"
                echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
                ;;
        esac
        echo ""
    else
        echo "sudo not found. Install manually:"
        echo "  sudo cp packaging/linux/70-dualsense.rules /etc/udev/rules.d/"
        echo "  sudo udevadm control --reload-rules && sudo udevadm trigger"
        echo ""
    fi
fi

# --- uv ---
if ! command -v uv &>/dev/null; then
    echo "uv not found -- installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

uv run main.py
