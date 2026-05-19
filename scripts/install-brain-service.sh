#!/bin/bash
# Install + enable + start the sos-brain systemd unit.
set -e
cp /mnt/HC_Volume_104325311/SOS/sos/services/brain/sos-brain.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable sos-brain.service
systemctl --user start sos-brain.service
systemctl --user status sos-brain.service --no-pager
