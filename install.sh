#!/bin/bash
# Install script for Axis Twist Compensation Beacon
# Symlinks the module into Klipper's extras directory

set -e

KLIPPER_DIR="${HOME}/klipper"
EXTRAS_DIR="${KLIPPER_DIR}/klippy/extras"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE="axis_twist_compensation_beacon.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Axis Twist Compensation Beacon — Installer ===${NC}"
echo ""

# Check Klipper directory exists
if [ ! -d "${KLIPPER_DIR}" ]; then
    echo -e "${RED}Error: Klipper directory not found at ${KLIPPER_DIR}${NC}"
    echo "If Klipper is installed elsewhere, set KLIPPER_DIR:"
    echo "  KLIPPER_DIR=/path/to/klipper ./install.sh"
    exit 1
fi

# Check extras directory exists
if [ ! -d "${EXTRAS_DIR}" ]; then
    echo -e "${RED}Error: Klipper extras directory not found at ${EXTRAS_DIR}${NC}"
    exit 1
fi

# Check module file exists
if [ ! -f "${SCRIPT_DIR}/${MODULE}" ]; then
    echo -e "${RED}Error: ${MODULE} not found in ${SCRIPT_DIR}${NC}"
    exit 1
fi

# Create symlink (or replace existing)
TARGET="${EXTRAS_DIR}/${MODULE}"
if [ -L "${TARGET}" ]; then
    echo -e "${YELLOW}Updating existing symlink...${NC}"
    rm "${TARGET}"
elif [ -f "${TARGET}" ]; then
    echo -e "${YELLOW}Backing up existing ${MODULE} to ${MODULE}.bak${NC}"
    mv "${TARGET}" "${TARGET}.bak"
fi

ln -s "${SCRIPT_DIR}/${MODULE}" "${TARGET}"

echo -e "${GREEN}✓ Installed ${MODULE} → ${TARGET}${NC}"
echo ""

# Restart Klipper
echo "Restarting Klipper..."
if command -v systemctl &> /dev/null && systemctl is-active --quiet klipper 2>/dev/null; then
    sudo systemctl restart klipper
elif command -v service &> /dev/null; then
    sudo service klipper restart
else
    echo -e "${YELLOW}Could not detect init system — please restart Klipper manually.${NC}"
fi

echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Add to your printer.cfg:"
echo ""
echo "     [axis_twist_compensation]"
echo "     calibrate_start_x: 20"
echo "     calibrate_end_x: 200"
echo "     calibrate_y: 112.5"
echo ""
echo "     [axis_twist_compensation_beacon]"
echo ""
echo "  2. Run calibration:"
echo "     AXIS_TWIST_COMPENSATION_BEACON"
echo "     SAVE_CONFIG"
echo ""
echo -e "${GREEN}Done!${NC}"
