#!/bin/bash

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

set -euo pipefail

THEME="${1:-}"
MANEKI_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/maneki"

case "$THEME" in
    0) VARIANT="colors-${FAMILY}.css"; LABEL="$FAMILY_LABEL Dark" ;;
    1) VARIANT="colors-${FAMILY}-light.css"; LABEL="$FAMILY_LABEL Light" ;;
    *) echo "Usage: ./set-theme.sh <0|1>"; exit 1 ;;
esac

if [ ! -d "$MANEKI_DIR" ]; then
    echo "⚠ maneki is not set up, skipping"
    exit 0
fi

if [ ! -f "$MANEKI_DIR/$VARIANT" ]; then
    echo "✗ Missing maneki colors: $MANEKI_DIR/$VARIANT"
    exit 1
fi

TMP="$(mktemp "$MANEKI_DIR/.colors.XXXXXX")"
cp "$MANEKI_DIR/$VARIANT" "$TMP"
chmod 644 "$TMP"
mv -f "$TMP" "$MANEKI_DIR/colors.css"
echo "✓ maneki theme set to: $LABEL (reloads by itself)"
