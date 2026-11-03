#!/bin/sh
# Cross-compile Bananarchy multi-mode fcitx5-t9 engine (abc/Abc/ABC/Sym/Ctl/123) for armv7
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi

exec docker run --rm --network=host \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
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
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 >/dev/null

  echo "[1/3] Preparing ephemeral armv7 sysroot (musl-dev, fcitx5-dev)..."
  mkdir -p /tmp/armv7-sysroot/etc/apk/keys
  cp /etc/apk/repositories /tmp/armv7-sysroot/etc/apk/repositories
  cp /etc/apk/keys/* /tmp/armv7-sysroot/etc/apk/keys/
  apk --root /tmp/armv7-sysroot --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    fcitx5-dev >/dev/null

  echo "[2/3] Compiling Bananarchy multi-mode fcitx5-t9 engine for armv7..."
  mkdir -p \
    /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5 \
    /bananarchy/prebuilt/fcitx5-t9/usr/share/fcitx5/addon \
    /bananarchy/prebuilt/fcitx5-t9/usr/share/fcitx5/inputmethod

  armv7-alpine-linux-musleabihf-g++ \
    -shared -fPIC -O2 -std=c++20 \
    --sysroot=/tmp/armv7-sysroot \
    -I/tmp/armv7-sysroot/usr/include/Fcitx5/Core \
    -I/tmp/armv7-sysroot/usr/include/Fcitx5/Config \
    -I/tmp/armv7-sysroot/usr/include/Fcitx5/Utils \
    -I/bananarchy/src/fcitx5-t9/src \
    /bananarchy/src/fcitx5-t9/src/t9.cpp \
    -L/tmp/armv7-sysroot/usr/lib \
    -lFcitx5Core -lFcitx5Config -lFcitx5Utils \
    -o /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5/libt9.so

  armv7-alpine-linux-musleabihf-strip --strip-unneeded \
    /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5/libt9.so
  cp -f /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5/libt9.so \
    /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5/t9.so

  cp /bananarchy/src/fcitx5-t9/src/t9-addon.conf \
    /bananarchy/prebuilt/fcitx5-t9/usr/share/fcitx5/addon/t9.conf
  cp /bananarchy/src/fcitx5-t9/src/t9-inputmethod.conf \
    /bananarchy/prebuilt/fcitx5-t9/usr/share/fcitx5/inputmethod/t9.conf

  echo "[3/3] Updating overlay/rootfs and target rootfs with compiled fcitx5-t9..."
  cp -a /bananarchy/prebuilt/fcitx5-t9/usr/. /bananarchy/overlay/rootfs/usr/
  if [ -d /target-rootfs/usr/lib/fcitx5 ]; then
    cp -a /bananarchy/prebuilt/fcitx5-t9/usr/. /target-rootfs/usr/
  fi
  chown -R "$HOST_UID:$HOST_GID" /bananarchy/prebuilt/fcitx5-t9 /bananarchy/overlay/rootfs/usr
  ls -lh /bananarchy/prebuilt/fcitx5-t9/usr/lib/fcitx5/libt9.so
'
