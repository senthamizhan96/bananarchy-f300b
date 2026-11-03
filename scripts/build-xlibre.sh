#!/bin/sh
# Cross-compile Xlibre (X11Libre/xserver) + xf86-input-libinput + xf86-video-fbdev
# optimized for Qualcomm MSM8905/MSM8909 (ARMv7 Cortex-A7 + NEON-VFPv4, 240x320 KMS/SPI)
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi

exec docker run --rm --network=host \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  -e FORCE_REBUILD="${FORCE_REBUILD:-0}" \
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

  echo "[1/5] Installing host cross-compilation toolchain and meson/ninja..."
  apk add --no-cache \
    build-base \
    git \
    ca-certificates \
    patch \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 \
    meson \
    ninja \
    pkgconf \
    python3 >/dev/null

  XLIBRE_SRC="/bananarchy/src/xlibre"
  if [ ! -f "$XLIBRE_SRC/meson.build" ]; then
    echo "[1b/5] Cloning X11Libre/xserver and applying Bananarchy MSM8905 SPI DRM patch..."
    XLIBRE_SRC="/tmp/xlibre-src"
    rm -rf "$XLIBRE_SRC"
    git clone --depth 1 https://github.com/X11Libre/xserver.git "$XLIBRE_SRC"
    patch -d "$XLIBRE_SRC" -p1 < /bananarchy/patches/xlibre/0001-xlibre-msm8905-spi-drm-and-shared-vt.patch
  fi

  echo "[2/5] Preparing armv7 sysroot for Xlibre + DDX drivers..."
  SYSROOT="/tmp/armv7-sysroot"
  mkdir -p "$SYSROOT/etc/apk/keys"
  cp /etc/apk/repositories "$SYSROOT/etc/apk/repositories"
  cp /etc/apk/keys/* "$SYSROOT/etc/apk/keys/"
  apk --root "$SYSROOT" --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    xorgproto \
    xtrans \
    font-util-dev \
    pixman-dev \
    libx11-dev \
    libxext-dev \
    libxfont2-dev \
    libxkbfile-dev \
    libxshmfence-dev \
    libxdmcp-dev \
    libxau-dev \
    libxcvt-dev \
    libdrm-dev \
    libepoxy-dev \
    mesa-dev \
    eudev-dev \
    dbus-dev \
    libpciaccess-dev \
    openssl-dev \
    libinput-dev \
    mtdev-dev >/dev/null

  OPT_CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -fno-plt -fomit-frame-pointer"

  cat > /tmp/armv7-cross.ini <<EOF
[binaries]
c = '\''armv7-alpine-linux-musleabihf-gcc'\''
cpp = '\''armv7-alpine-linux-musleabihf-g++'\''
ar = '\''armv7-alpine-linux-musleabihf-ar'\''
strip = '\''armv7-alpine-linux-musleabihf-strip'\''
pkg-config = '\''pkgconf'\''

[built-in options]
c_args = ['\''--sysroot=/tmp/armv7-sysroot'\'', '\''-O2'\'', '\''-march=armv7-a'\'', '\''-mtune=cortex-a7'\'', '\''-mfpu=neon-vfpv4'\'', '\''-mfloat-abi=hard'\'', '\''-fno-plt'\'', '\''-fomit-frame-pointer'\'']
c_link_args = ['\''--sysroot=/tmp/armv7-sysroot'\'', '\''-Wl,-O1,--as-needed'\'']

[properties]
sys_root = '\''/tmp/armv7-sysroot'\''
pkg_config_libdir = '\''/tmp/armv7-sysroot/usr/lib/pkgconfig:/tmp/armv7-sysroot/usr/share/pkgconfig'\''

[host_machine]
system = '\''linux'\''
cpu_family = '\''arm'\''
cpu = '\''armv7l'\''
endian = '\''little'\''
EOF

  ABI_MOD_DIR="/usr/lib/xorg/modules/xlibre-25"
  if [ "${FORCE_REBUILD:-0}" = "1" ] || [ ! -f /bananarchy/prebuilt/xlibre/usr/bin/Xorg ] || [ ! -f "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/extensions/libglx.so" ] || [ ! -f "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/libglamoregl.so" ] || ! grep -q "continuing in shared VT mode" /bananarchy/prebuilt/xlibre/usr/bin/Xorg 2>/dev/null; then
    echo "[3/5] Configuring and compiling Xlibre (X11Libre/xserver) for armv7..."
    rm -rf /tmp/build-xlibre
    meson setup /tmp/build-xlibre "$XLIBRE_SRC" \
      --cross-file=/tmp/armv7-cross.ini \
      --prefix=/usr \
      --libdir=lib \
      --libexecdir=libexec \
      --sysconfdir=/etc \
      --localstatedir=/var \
      --buildtype=release \
      -Dxorg=true \
      -Dxorg-sdk=true \
      -Dglamor=true \
      -Dglx=true \
      -Ddri1=false \
      -Ddri2=true \
      -Ddri3=true \
      -Ddrm=true \
      -Dgbm=true \
      -Dudev=true \
      -Dudev_kms=true \
      -Dsystemd_logind=true \
      -Dseatd_libseat=false \
      -Dsuid_wrapper=false \
      -Dpciaccess=true \
      -Dvgahw=false \
      -Dint10=false \
      -Ddga=false \
      -Dxv=true \
      -Dxvmc=false \
      -Dxephyr=false \
      -Dxnest=false \
      -Dxvfb=false \
      -Dxfake=false \
      -Dxfbdev=false \
      -Dxmodesetting=false \
      -Dxf86-input-inputtest=false \
      -Dxf86-input-null=false \
      -Dlegacy_nvidia_340x=false \
      -Dlegacy_nvidia_padding=false \
      -Ddocs=false \
      -Ddevel-docs=false \
      -Dtests=false \
      -Dlibunwind=false \
      -Dsha1=libcrypto \
      -Dinput_thread=true \
      -Ddefault_font_path=/usr/share/fonts/misc,/usr/share/fonts/TTF,/usr/share/fonts/OTF \
      -Dxkb_dir=/usr/share/X11/xkb \
      -Dxkb_output_dir=/tmp \
      -Dxkb_bin_dir=/usr/bin \
      -Dlog_dir=/tmp

    ninja -C /tmp/build-xlibre -j"$(nproc)"
    rm -rf /bananarchy/prebuilt/xlibre
    mkdir -p /bananarchy/prebuilt/xlibre
    DESTDIR="/bananarchy/prebuilt/xlibre" ninja -C /tmp/build-xlibre install
  elif ! grep -q "Failed to allocate cursor BOs" "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/drivers/modesetting_drv.so" 2>/dev/null; then
    echo "[3/5] Rebuilding patched modesetting_drv.so in Xlibre..."
    rm -rf /tmp/build-xlibre
    meson setup /tmp/build-xlibre "$XLIBRE_SRC" \
      --cross-file=/tmp/armv7-cross.ini \
      --prefix=/usr \
      --libdir=lib \
      --libexecdir=libexec \
      --sysconfdir=/etc \
      --localstatedir=/var \
      --buildtype=release \
      -Dxorg=true \
      -Dxorg-sdk=true \
      -Dglamor=true \
      -Dglx=true \
      -Ddri1=false \
      -Ddri2=true \
      -Ddri3=true \
      -Ddrm=true \
      -Dgbm=true \
      -Dudev=true \
      -Dudev_kms=true \
      -Dsystemd_logind=true \
      -Dseatd_libseat=false \
      -Dsuid_wrapper=false \
      -Dpciaccess=true \
      -Dvgahw=false \
      -Dint10=false \
      -Ddga=false \
      -Dxv=true \
      -Dxvmc=false \
      -Dxephyr=false \
      -Dxnest=false \
      -Dxvfb=false \
      -Dxfake=false \
      -Dxfbdev=false \
      -Dxmodesetting=false \
      -Dxf86-input-inputtest=false \
      -Dxf86-input-null=false \
      -Dlegacy_nvidia_340x=false \
      -Dlegacy_nvidia_padding=false \
      -Ddocs=false \
      -Ddevel-docs=false \
      -Dtests=false \
      -Dlibunwind=false \
      -Dsha1=libcrypto \
      -Dinput_thread=true \
      -Dxkb_dir=/usr/share/X11/xkb \
      -Dxkb_output_dir=/tmp \
      -Dxkb_bin_dir=/usr/bin \
      -Dlog_dir=/tmp
    ninja -C /tmp/build-xlibre -j"$(nproc)" hw/xfree86/drivers/video/modesetting/modesetting_drv.so
    cp -f /tmp/build-xlibre/hw/xfree86/drivers/video/modesetting/modesetting_drv.so "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/drivers/modesetting_drv.so"
  else
    echo "[3/5] Reusing compiled Xlibre in /bananarchy/prebuilt/xlibre..."
  fi

  if [ -d /bananarchy/prebuilt/xlibre/tmp/armv7-sysroot/usr ]; then
    cp -a /bananarchy/prebuilt/xlibre/tmp/armv7-sysroot/usr/. /bananarchy/prebuilt/xlibre/usr/
    rm -rf /bananarchy/prebuilt/xlibre/tmp
  fi

  cp -a /bananarchy/prebuilt/xlibre/usr/. "$SYSROOT/usr/"

  echo "[4/5] Compiling X11Libre/xf86-input-libinput and xf86-video-fbdev against Xlibre SDK..."
  ABI_MOD_DIR="/usr/lib/xorg/modules/xlibre-25"
  if [ ! -f "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/input/libinput_drv.so" ]; then
    rm -rf /tmp/build-libinput
    meson setup /tmp/build-libinput /bananarchy/src/xf86-input-libinput \
      --cross-file=/tmp/armv7-cross.ini \
      --prefix=/usr \
      --libdir=lib \
      --sysconfdir=/etc \
      --buildtype=release \
      -Dxorg-module-dir="$ABI_MOD_DIR/input" \
      -Dsdkdir=/usr/include/xorg \
      -Dxorg-conf-dir=/usr/share/X11/xorg.conf.d
    ninja -C /tmp/build-libinput -j"$(nproc)"
    DESTDIR="/bananarchy/prebuilt/xlibre" ninja -C /tmp/build-libinput install
  fi

  mkdir -p "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/drivers" /tmp/fbdev-inc
  cat > /tmp/fbdev-inc/config.h <<EOF
#include "xorg-server.h"
#define PACKAGE_VERSION_MAJOR 0
#define PACKAGE_VERSION_MINOR 5
#define PACKAGE_VERSION_PATCHLEVEL 0
EOF
  armv7-alpine-linux-musleabihf-gcc \
    --sysroot="$SYSROOT" \
    $OPT_CFLAGS -shared -fPIC \
    -I/tmp/fbdev-inc \
    -I"$SYSROOT/usr/include/xorg" \
    -I"$SYSROOT/usr/include/pixman-1" \
    -I"$SYSROOT/usr/include/X11/dri" \
    -I"$SYSROOT/usr/include/libdrm" \
    /bananarchy/src/xf86-video-fbdev/src/fbdev.c \
    -o "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/drivers/fbdev_drv.so"

  echo "[5/5] Stripping Xlibre binaries and installing into prebuilt, overlay, and rootfs..."
  find /bananarchy/prebuilt/xlibre/usr/bin /bananarchy/prebuilt/xlibre/usr/lib -type f \( -name "Xorg" -o -name "gtf" -o -name "*.so" \) -exec armv7-alpine-linux-musleabihf-strip --strip-unneeded {} +

  rm -rf /bananarchy/prebuilt/xlibre/usr/share/X11/xorg.conf.d/10-nvidia*.conf \
         /bananarchy/prebuilt/xlibre/etc/X11/xorg.conf.d/10-nvidia*

  mkdir -p /bananarchy/overlay/rootfs/usr/bin \
           /bananarchy/overlay/rootfs/usr/lib/xorg \
           /bananarchy/overlay/rootfs/usr/share/X11/xorg.conf.d
  cp -a /bananarchy/prebuilt/xlibre/usr/bin/. /bananarchy/overlay/rootfs/usr/bin/
  cp -a /bananarchy/prebuilt/xlibre/usr/lib/xorg/. /bananarchy/overlay/rootfs/usr/lib/xorg/
  cp -a /bananarchy/prebuilt/xlibre/usr/share/X11/xorg.conf.d/. /bananarchy/overlay/rootfs/usr/share/X11/xorg.conf.d/

  if [ -d /target-rootfs/usr ]; then
    cp -a /bananarchy/overlay/rootfs/usr/bin/Xorg /bananarchy/overlay/rootfs/usr/bin/X /bananarchy/overlay/rootfs/usr/bin/gtf /target-rootfs/usr/bin/
    cp -a /bananarchy/overlay/rootfs/usr/lib/xorg/. /target-rootfs/usr/lib/xorg/
    cp -a /bananarchy/overlay/rootfs/usr/share/X11/xorg.conf.d/. /target-rootfs/usr/share/X11/xorg.conf.d/
  fi

  chown -R "$HOST_UID:$HOST_GID" /bananarchy/prebuilt/xlibre /bananarchy/overlay/rootfs/usr /target-rootfs/usr/lib/xorg /target-rootfs/usr/share/X11/xorg.conf.d /target-rootfs/usr/bin/Xorg /target-rootfs/usr/bin/X /target-rootfs/usr/bin/gtf 2>/dev/null || true
  ls -lh /bananarchy/prebuilt/xlibre/usr/bin/Xorg "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/drivers/"*.so "/bananarchy/prebuilt/xlibre$ABI_MOD_DIR/input/"*.so
'
