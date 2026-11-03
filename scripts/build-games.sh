#!/bin/sh
# Cross-compile Bananarchy standalone X11 games (Snake, Tetris) for armv7.
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi

if [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/lib/libneoui.so" ]; then
  echo "[auto-deps] Missing libneoui.so; running scripts/build-sxmo-x11.sh first..."
  "$BASE_DIR/scripts/build-sxmo-x11.sh"
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

  echo "[1/4] Installing host cross-compilation toolchain..."
  apk add --no-cache \
    build-base \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 \
    pkgconf >/dev/null

  echo "[2/4] Preparing armv7 sysroot (X11 + NeoUI + EGL/GLESv2 + ALSA + Media/Net)..."
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
    libxft-dev \
    pango-dev \
    glib-dev \
    alsa-lib-dev \
    zlib-dev \
    libpng-dev \
    libjpeg-turbo-dev \
    curl-dev \
    mesa-dev >/dev/null

  cp /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so "$SYSROOT/usr/lib/libneoui.so"
  cp /bananarchy/src/neoui/neoui.h "$SYSROOT/usr/include/neoui.h"

  export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
  export PKG_CONFIG_LIBDIR="$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig"
  CC="armv7-alpine-linux-musleabihf-gcc --sysroot=$SYSROOT"
  CXX="armv7-alpine-linux-musleabihf-g++ --sysroot=$SYSROOT"
  STRIP="armv7-alpine-linux-musleabihf-strip"
  OPT_CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -fno-plt -fomit-frame-pointer"

  mkdir -p /bananarchy/prebuilt/games/usr/bin

  echo "[3/4] Compiling Snake, Tetris, Scoundrel, Gambatte (GB/GBC), and ReMEdy (J2ME MIDP 2.0 / M3G 3D)..."
  $CXX $OPT_CFLAGS -std=c++17 -Wall -Wextra \
    /bananarchy/src/games/snake.cpp $(pkgconf --libs x11) \
    -o /bananarchy/prebuilt/games/usr/bin/snake
  $CXX $OPT_CFLAGS -std=c++17 -Wall -Wextra \
    /bananarchy/src/games/tetris.cpp $(pkgconf --libs x11) \
    -o /bananarchy/prebuilt/games/usr/bin/tetris
  $CXX $OPT_CFLAGS -std=c++20 -Wall -Wextra \
    $(pkgconf --cflags x11 xft pangoxft pango glib-2.0 gobject-2.0) \
    /bananarchy/src/games/scoundrel.cpp \
    -L"$SYSROOT/usr/lib" -lneoui \
    $(pkgconf --libs x11 xft pangoxft pango glib-2.0 gobject-2.0) \
    -o /bananarchy/prebuilt/games/usr/bin/scoundrel

  GAMBATTE_DIR="/bananarchy/src/games/gambatte"
  $CXX $OPT_CFLAGS -std=c++17 -DHAVE_STDINT_H -D__LIBRETRO__ \
    $(pkgconf --cflags alsa) \
    -I"$GAMBATTE_DIR" \
    -I"$GAMBATTE_DIR/common" \
    -I"$GAMBATTE_DIR/include" \
    -I"$GAMBATTE_DIR/src" \
    "$GAMBATTE_DIR"/src/*.cpp \
    "$GAMBATTE_DIR"/src/mem/*.cpp \
    "$GAMBATTE_DIR"/src/sound/*.cpp \
    "$GAMBATTE_DIR"/src/video/*.cpp \
    /bananarchy/src/games/gambatte_phone.cpp \
    $(pkgconf --libs x11 xfixes alsa) -lpthread -lm \
    -o /bananarchy/prebuilt/games/usr/bin/gambatte

  ln -sf gambatte /bananarchy/prebuilt/games/usr/bin/gbemu

  $CXX $OPT_CFLAGS -std=c++20 -Wall -Wextra \
    $(pkgconf --cflags x11 xft pangoxft pango glib-2.0 gobject-2.0 alsa zlib libpng libjpeg libcurl egl glesv2) \
    /bananarchy/src/games/remedy/remedy_main.cpp \
    -L"$SYSROOT/usr/lib" -lneoui \
    $(pkgconf --libs x11 xft pangoxft pango glib-2.0 gobject-2.0 alsa zlib libpng libjpeg libcurl egl glesv2) \
    -lpthread -lm \
    -o /bananarchy/prebuilt/games/usr/bin/remedy

  ln -sf remedy /bananarchy/prebuilt/games/usr/bin/j2me

  $STRIP --strip-unneeded \
    /bananarchy/prebuilt/games/usr/bin/snake \
    /bananarchy/prebuilt/games/usr/bin/tetris \
    /bananarchy/prebuilt/games/usr/bin/scoundrel \
    /bananarchy/prebuilt/games/usr/bin/gambatte \
    /bananarchy/prebuilt/games/usr/bin/remedy

  echo "[4/4] Installing into overlay and (optional) target rootfs..."
  mkdir -p /bananarchy/overlay/rootfs/usr/bin
  cp -a /bananarchy/prebuilt/games/usr/bin/. /bananarchy/overlay/rootfs/usr/bin/
  if [ -d /target-rootfs/usr/bin ]; then
    cp -a /bananarchy/prebuilt/games/usr/bin/. /target-rootfs/usr/bin/
  fi

  chown -R "$HOST_UID:$HOST_GID" \
    /bananarchy/prebuilt/games \
    /bananarchy/overlay/rootfs/usr/bin \
    /bananarchy/src/games
  ls -lh /bananarchy/prebuilt/games/usr/bin/snake /bananarchy/prebuilt/games/usr/bin/tetris /bananarchy/prebuilt/games/usr/bin/scoundrel /bananarchy/prebuilt/games/usr/bin/gambatte /bananarchy/prebuilt/games/usr/bin/remedy
'
