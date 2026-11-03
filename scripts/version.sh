#!/bin/sh
# Compute and apply the Bananarchy Linux release version:
#   <year-letter><MM><DD>
# where <year-letter> is 'a'..'z' for (year - 2026) mod 26 (2026 = 'a'),
# followed by the two-digit local month and two-digit local day.
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

YEAR="$(date +%Y)"
MONTH="$(date +%m)"
DAY="$(date +%d)"

LETTER_IDX=$(( ((YEAR - 2026) % 26 + 26) % 26 ))
YEAR_LETTER="$(printf "\\$(printf '%03o' $(( 97 + LETTER_IDX )))")"
VERSION="${BANANARCHY_VERSION:-${YEAR_LETTER}${MONTH}${DAY}}"

if [ "${1:-}" = "--print" ] || [ "${1:-}" = "-p" ]; then
  printf '%s\n' "$VERSION"
  exit 0
fi

TARGET_ROOTFS="${1:-${ROOTFS_DIR:-$BASE_DIR/rootfs}}"

apply_template() {
  src_tpl="$1"
  dst_file="$2"
  if [ -f "$src_tpl" ]; then
    mkdir -p "$(dirname "$dst_file")"
    sed "s/@BANANARCHY_VERSION@/$VERSION/g" "$src_tpl" > "$dst_file"
  elif [ -f "$dst_file" ]; then
    sed -i -E \
      -e "s/@BANANARCHY_VERSION@/$VERSION/g" \
      -e "s/v0\.0\.1/$VERSION/g" \
      "$dst_file"
  fi
}

if [ -d "$TARGET_ROOTFS" ]; then
  for rel in etc/os-release etc/issue etc/motd; do
    apply_template "$BASE_DIR/overlay/rootfs/$rel" "$TARGET_ROOTFS/$rel"
  done
  echo "Applied Bananarchy version $VERSION to $TARGET_ROOTFS"
else
  printf '%s\n' "$VERSION"
fi
