#!/bin/bash

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

set -euo pipefail

THEME="${1:-}"
PERCH_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/perch"

case "$THEME" in
    0) VARIANT="colors-${FAMILY}.css"; LABEL="$FAMILY_LABEL Dark" ;;
    1) VARIANT="colors-${FAMILY}-light.css"; LABEL="$FAMILY_LABEL Light" ;;
    *) echo "Usage: ./set-theme.sh <0|1>"; exit 1 ;;
esac

if [ ! -d "$PERCH_DIR" ]; then
    echo "⚠ perch is not set up, skipping"
    exit 0
fi

if [ ! -f "$PERCH_DIR/$VARIANT" ]; then
    echo "✗ Missing perch colors: $PERCH_DIR/$VARIANT"
    exit 1
fi

TMP="$(mktemp "$PERCH_DIR/.colors.XXXXXX")"
cp "$PERCH_DIR/$VARIANT" "$TMP"
chmod 644 "$TMP"
mv -f "$TMP" "$PERCH_DIR/colors.css"
echo "✓ perch theme set to: $LABEL (reloads by itself)"
