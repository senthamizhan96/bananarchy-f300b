#!/bin/sh
# Fetch the latest master branch of Bantam AI agent from
# https://code.luxferre.top/luxferre/bantam-cpp and cross-compile for armv7
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BANTAM_REPO_URL="${BANTAM_REPO_URL:-https://code.luxferre.top/luxferre/bantam-cpp}"
BANTAM_BRANCH="${BANTAM_BRANCH:-master}"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi

exec docker run --rm --network=host \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  -e BANTAM_REPO_URL="$BANTAM_REPO_URL" \
  -e BANTAM_BRANCH="$BANTAM_BRANCH" \
  -v "$BASE_DIR:/bananarchy" \
  -v "$ROOTFS_DIR:/target-rootfs" \
  alpine:3.24 sh -eu -c '
  cat > /etc/apk/repositories <<EOF
https://mirror.nura.eco/postmarketos/v26.06
https://dl-cdn.alpinelinux.org/alpine/edge/main
https://dl-cdn.alpinelinux.org/alpine/edge/community
https://dl-cdn.alpinelinux.org/alpine/edge/testing
EOF
  cp /bananarchy/keys/* /etc/apk/keys/

  apk add --no-cache \
    build-base \
    git \
    ca-certificates \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 >/dev/null

  # Keep target-rootfs clean of -dev headers; use an ephemeral armv7 build sysroot
  if [ -d /target-rootfs/etc/apk ]; then
    apk --root /target-rootfs --arch armv7 del --no-scripts \
      curl-dev readline-dev ncurses-dev >/dev/null 2>&1 || true
  fi

  echo "[1/4] Preparing ephemeral armv7 sysroot (musl-dev, curl-dev, readline-dev)..."
  mkdir -p /tmp/armv7-sysroot/etc/apk/keys
  cp /etc/apk/repositories /tmp/armv7-sysroot/etc/apk/repositories
  cp /etc/apk/keys/* /tmp/armv7-sysroot/etc/apk/keys/
  apk --root /tmp/armv7-sysroot --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    curl-dev \
    readline-dev \
    ncurses-dev >/dev/null

  echo "[2/4] Cloning latest $BANTAM_BRANCH from $BANTAM_REPO_URL..."
  rm -rf /tmp/bantam-cpp
  git clone --depth 1 --branch "$BANTAM_BRANCH" "$BANTAM_REPO_URL" /tmp/bantam-cpp
  cd /tmp/bantam-cpp
  echo "      Commit: $(git rev-parse --short HEAD) ($(git log -1 --format=%s))"

  echo "[3/4] Cross-compiling Bantam AI agent for armv7..."
  mkdir -p \
    /bananarchy/prebuilt/bantam \
    /bananarchy/overlay/rootfs/usr/bin \
    /bananarchy/overlay/rootfs/usr/local/bin

  armv7-alpine-linux-musleabihf-g++ \
    -std=c++20 -Wall -Wextra -Wpedantic -O2 \
    -D_POSIX_C_SOURCE=200809L -D_DEFAULT_SOURCE \
    -D_FILE_OFFSET_BITS=64 -D_LARGEFILE_SOURCE -D_XOPEN_SOURCE=700 \
    --sysroot=/tmp/armv7-sysroot \
    -I. \
    -I/tmp/armv7-sysroot/usr/include \
    main.cpp config.cpp util.cpp json.cpp llm.cpp tools.cpp session.cpp render.cpp editor.cpp \
    -L/tmp/armv7-sysroot/usr/lib \
    -lcurl -lreadline -lncursesw \
    -o /bananarchy/prebuilt/bantam/bantam

  armv7-alpine-linux-musleabihf-strip --strip-unneeded /bananarchy/prebuilt/bantam/bantam
  chmod 755 /bananarchy/prebuilt/bantam/bantam

  echo "[4/4] Installing Bantam into prebuilt/, overlay/rootfs/, and target rootfs..."
  cp -a /bananarchy/prebuilt/bantam/bantam /bananarchy/overlay/rootfs/usr/bin/bantam
  cp -a /bananarchy/prebuilt/bantam/bantam /bananarchy/overlay/rootfs/usr/local/bin/bantam
  if [ -d /target-rootfs/usr/bin ]; then
    cp -a /bananarchy/prebuilt/bantam/bantam /target-rootfs/usr/bin/bantam
    cp -a /bananarchy/prebuilt/bantam/bantam /target-rootfs/usr/local/bin/bantam
  fi

  # Ensure no default .bantam.cfg is included anywhere
  rm -f \
    /bananarchy/overlay/rootfs/home/user/.bantam.cfg \
    /bananarchy/overlay/rootfs/etc/skel/.bantam.cfg \
    /bananarchy/overlay/rootfs/root/.bantam.cfg \
    /target-rootfs/home/user/.bantam.cfg \
    /target-rootfs/etc/skel/.bantam.cfg \
    /target-rootfs/root/.bantam.cfg

  chown "$HOST_UID:$HOST_GID" /target-rootfs/usr/bin/bantam /target-rootfs/usr/local/bin/bantam 2>/dev/null || true
  chown -R "$HOST_UID:$HOST_GID" /bananarchy/prebuilt/bantam /bananarchy/overlay/rootfs/usr
  ls -lh /bananarchy/prebuilt/bantam/bantam
'
