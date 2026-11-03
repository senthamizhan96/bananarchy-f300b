#!/bin/sh
# Cross-compile the patched sxmo-dmenu plugin (dmenu.so) for armv7.
# Mirrors the dmenu section of scripts/build-sxmo-x11.sh.
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
http://mirror.nura.eco/postmarketos/v26.06
https://dl-cdn.alpinelinux.org/alpine/edge/main
https://dl-cdn.alpinelinux.org/alpine/edge/community
https://dl-cdn.alpinelinux.org/alpine/edge/testing
EOF
  cp /bananarchy/keys/* /etc/apk/keys/

  echo "[1/3] Installing host cross-compilation toolchain..."
  apk add --no-cache \
    build-base \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 \
    pkgconf >/dev/null

  echo "[2/3] Preparing armv7 sysroot (X11 + xinerama/xft/fontconfig)..."
  SYSROOT="/tmp/armv7-sysroot"
  mkdir -p "$SYSROOT/etc/apk/keys"
  cp /etc/apk/repositories "$SYSROOT/etc/apk/repositories"
  cp /etc/apk/keys/* "$SYSROOT/etc/apk/keys/"
  apk --root "$SYSROOT" --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    libx11-dev \
    libxfixes-dev \
    libxinerama-dev \
    libxft-dev \
    fontconfig-dev \
    freetype-dev >/dev/null

  export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
  export PKG_CONFIG_LIBDIR="$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig"
  CC="armv7-alpine-linux-musleabihf-gcc --sysroot=$SYSROOT"
  STRIP="armv7-alpine-linux-musleabihf-strip"
  OPT_CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -fno-plt -fomit-frame-pointer"

  mkdir -p /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins

  echo "[3/3] Compiling dmenu.so..."
  cd /bananarchy/src/sxmo-dmenu
  cp -f config.def.h config.h
  DMENU_INCS="$(pkgconf --cflags x11 xfixes xinerama xft fontconfig freetype2)"
  DMENU_LIBS="$(pkgconf --libs x11 xfixes xinerama xft fontconfig freetype2)"
  $CC $OPT_CFLAGS -fPIC -shared -D_DEFAULT_SOURCE -D_BSD_SOURCE -D_XOPEN_SOURCE=700 -D_POSIX_C_SOURCE=200809L -DVERSION=\"5.0.14-bananarchy\" -DXINERAMA $DMENU_INCS \
    dmenu.c drw.c util.c $DMENU_LIBS -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so

  echo "Installing into overlay and (optional) target rootfs..."
  mkdir -p /bananarchy/overlay/rootfs/usr/lib/dwm/plugins
  cp -f /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so /bananarchy/overlay/rootfs/usr/lib/dwm/plugins/dmenu.so
  if [ -d /target-rootfs/usr/lib/dwm/plugins ]; then
    cp -f /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so /target-rootfs/usr/lib/dwm/plugins/dmenu.so
  fi

  chown -R "$HOST_UID:$HOST_GID" \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins \
    /bananarchy/overlay/rootfs/usr/lib/dwm/plugins \
    /bananarchy/src/sxmo-dmenu
  ls -lh /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so
'
