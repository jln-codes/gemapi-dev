#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p icons

# The pictogram is transparent; every installable icon has the same opaque
# ivory backdrop as the cartouche so it works on light and dark launchers.
background='#F5F3E7'

make_icon() {
  local size="$1" destination="$2" mark_size="${3:-$1}"
  magick logo-seul.png -resize "${mark_size}x${mark_size}" \
    -background "$background" -gravity center -extent "${size}x${size}" \
    -alpha remove -alpha off -strip "$destination"
}

make_icon 16 favicon-16x16.png
make_icon 32 favicon-32x32.png
make_icon 48 icons/favicon-48x48.png
magick favicon-16x16.png favicon-32x32.png icons/favicon-48x48.png favicon.ico

make_icon 180 apple-touch-icon.png
make_icon 192 icons/icon-192.png
make_icon 512 icons/icon-512.png

# Maskable icons need the entire pictogram inside the central safe circle.
# The mark occupies about 82% of its source square, so a source scaled to 70%
# of the canvas keeps the visible artwork within a 58% square.
make_icon 192 icons/icon-maskable-192.png 134
make_icon 512 icons/icon-maskable-512.png 358
