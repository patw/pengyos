#!/usr/bin/env bash
# Generate the PengyOS wallpaper candidates listed in prompts.toml.
#
# One mascot block + one style block is prepended to every scene prompt so the
# whole set reads as one family; the scene text comes from prompts.toml verbatim.
# Output: candidates/<name>-16x9.png (16:9, 2K).
#
# Usage: ./generate.sh [name ...]        no args = all entries
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
GEN="$HOME/Personal/skills/image_gen/gen_image.py"
OUT="$HERE/candidates"
LOG="$HERE/generate.log"
mkdir -p "$OUT" "$HERE/.prompts"

# Shared mascot + style block. Keep Pengy identical across the set.
STYLE='Pengy the penguin: a cute round cartoon penguin with a glossy black head, back and flippers, a white face patch and white belly, small round black eyes, a small orange beak, orange webbed feet, and a small black-and-white diagonally striped necktie. Flat modern vector illustration, clean thick dark outlines, soft cel shading, subtle rim glow, rich detailed background. Widescreen 16:9 desktop wallpaper, full-bleed scene edge to edge, no text, no letters, no words, no watermark, no logo, no signature, no border, no frame, no vignette, Pengy is the only character.'

mapfile -t ENTRIES < <(python3 - "$HERE/prompts.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    m = tomllib.load(f)
for w in m["wallpaper"]:
    print(f'{w["name"]}\t{w["prompt"]}')
PY
)

want=("$@")
for e in "${ENTRIES[@]}"; do
  name="${e%%$'\t'*}"
  scene="${e#*$'\t'}"
  if [ ${#want[@]} -gt 0 ]; then
    hit=0; for w in "${want[@]}"; do [ "$w" = "$name" ] && hit=1; done
    [ $hit -eq 0 ] && continue
  fi
  dest="$OUT/$name-16x9.png"
  [ -f "$dest" ] && { echo "skip $name (exists)"; continue; }

  pf="$HERE/.prompts/$name.txt"
  printf '%s\n\n%s\n' "$STYLE" "$scene" > "$pf"

  echo ">> $name"
  for attempt in 1 2 3; do
    if ( cd "$(dirname "$GEN")" && uv run gen_image.py -f "$pf" --ar 16:9 --res 2K -o "$name-16x9.png" ) >>"$LOG" 2>&1; then
      if [ -f "$HOME/Pictures/$name-16x9.png" ]; then
        mv -f "$HOME/Pictures/$name-16x9.png" "$dest"
        grep -o 'https://img.catbee.ca/[A-Za-z0-9._-]*' "$LOG" | tail -1 > "$OUT/$name.url"
        echo "   ok $(python3 -c "import sys;from PIL import Image;print(Image.open(sys.argv[1]).size)" "$dest" 2>/dev/null)"
        break
      fi
    fi
    echo "   attempt $attempt failed"
    sleep 3
  done
done
echo DONE
