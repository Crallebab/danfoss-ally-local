#!/bin/sh
# Copy the integration to Home Assistant over SSH and optionally restart Core.
#   tools/deploy.sh [ssh-host] [--restart]
# ssh-host defaults to "ha" (an SSH config alias for the HA "Terminal & SSH" add-on).
set -eu

HOST=ha
RESTART=
for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=--restart ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) HOST="$arg" ;;
  esac
done
cd "$(dirname "$0")/.."

tar -C custom_components --exclude='__pycache__' -cf - danfoss_ally_local \
  | ssh -- "$HOST" 'mkdir -p /config/custom_components \
      && rm -rf /config/custom_components/danfoss_ally_local \
      && tar -C /config/custom_components -xf - \
      && ls /config/custom_components/danfoss_ally_local'

if [ "$RESTART" = "--restart" ]; then
  ssh -- "$HOST" 'ha core check && ha core restart'
  echo "Home Assistant restarted."
else
  echo "Copied. Restart Home Assistant to load the new version."
fi
