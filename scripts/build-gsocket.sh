#!/bin/sh
# Cross-compile Global Socket (gsocket / gs-netcat) for armv7 (Cortex-A7 + NEON-VFPv4)
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi

mkdir -p "$BASE_DIR/prebuilt/gsocket"
if [ ! -f "$BASE_DIR/prebuilt/gsocket/gsocket-1.4.43.tar.gz" ] && [ -f "/home/lux/Downloads/gsocket-1.4.43.tar.gz" ]; then
  cp -f "/home/lux/Downloads/gsocket-1.4.43.tar.gz" "$BASE_DIR/prebuilt/gsocket/gsocket-1.4.43.tar.gz"
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

  echo "[1/4] Installing host cross-compilation toolchain and autotools..."
  apk add --no-cache \
    build-base \
    autoconf \
    automake \
    libtool \
    curl \
    tar \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 \
    pkgconf >/dev/null

  echo "[2/4] Preparing armv7 sysroot (musl, openssl)..."
  SYSROOT="/tmp/armv7-sysroot"
  mkdir -p "$SYSROOT/etc/apk/keys"
  cp /etc/apk/repositories "$SYSROOT/etc/apk/repositories"
  cp /etc/apk/keys/* "$SYSROOT/etc/apk/keys/"
  apk --root "$SYSROOT" --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    openssl-dev >/dev/null

  TARBALL="/bananarchy/prebuilt/gsocket/gsocket-1.4.43.tar.gz"
  if [ ! -f "$TARBALL" ]; then
    echo "Fetching gsocket-1.4.43.tar.gz..."
    curl -f -sSL "https://github.com/hackerschoice/gsocket/archive/refs/tags/v1.4.43.tar.gz" -o "$TARBALL"
  fi

  echo "[3/4] Cross-compiling gsocket for armv7..."
  mkdir -p /tmp/gsbuild
  tar -xzf "$TARBALL" -C /tmp/gsbuild
  cd /tmp/gsbuild/gsocket-1.4.43
  sed -i "s/ENGINE_init/OPENSSL_init_crypto/g" configure.ac
  sed -i "s/for xincdir in.*/for xincdir in ; do/" configure.ac
  sed -i "s/for xlibdir in.*/for xlibdir in ; do/" configure.ac
  ./bootstrap

  export CC="armv7-alpine-linux-musleabihf-gcc --sysroot=$SYSROOT"
  export AR="armv7-alpine-linux-musleabihf-ar"
  export RANLIB="armv7-alpine-linux-musleabihf-ranlib"
  export STRIP="armv7-alpine-linux-musleabihf-strip"
  export CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -fno-plt -fomit-frame-pointer -I$SYSROOT/usr/include"
  export CPPFLAGS="-I$SYSROOT/usr/include"
  export LDFLAGS="-L$SYSROOT/usr/lib -L$SYSROOT/lib"

  ./configure \
    --host=armv7-alpine-linux-musleabihf \
    --prefix=/usr \
    --sysconfdir=/etc \
    --datarootdir=/usr/share >/dev/null

  make -j"$(nproc)" >/dev/null
  rm -rf /tmp/gs-dest
  make install DESTDIR=/tmp/gs-dest >/dev/null

  $STRIP --strip-unneeded \
    /tmp/gs-dest/usr/bin/gs-netcat \
    /tmp/gs-dest/usr/lib/gsocket_dso.so.0 \
    /tmp/gs-dest/usr/lib/gsocket_uchroot_dso.so.0

  echo "[4/4] Exporting gsocket binaries to prebuilt/, overlay/rootfs/, and rootfs/..."
  rm -rf /bananarchy/prebuilt/gsocket/usr /bananarchy/prebuilt/gsocket/etc
  mkdir -p /bananarchy/prebuilt/gsocket/usr /bananarchy/prebuilt/gsocket/etc
  cp -a /tmp/gs-dest/usr/bin /bananarchy/prebuilt/gsocket/usr/
  cp -a /tmp/gs-dest/usr/lib /bananarchy/prebuilt/gsocket/usr/
  cp -a /tmp/gs-dest/usr/share /bananarchy/prebuilt/gsocket/usr/
  cp -a /tmp/gs-dest/etc/. /bananarchy/prebuilt/gsocket/etc/

  mkdir -p /bananarchy/overlay/rootfs/usr/bin \
           /bananarchy/overlay/rootfs/usr/lib \
           /bananarchy/overlay/rootfs/usr/share \
           /bananarchy/overlay/rootfs/etc
  cp -a /bananarchy/prebuilt/gsocket/usr/bin/. /bananarchy/overlay/rootfs/usr/bin/
  cp -a /bananarchy/prebuilt/gsocket/usr/lib/. /bananarchy/overlay/rootfs/usr/lib/
  cp -a /bananarchy/prebuilt/gsocket/usr/share/gsocket /bananarchy/overlay/rootfs/usr/share/
  cp -a /bananarchy/prebuilt/gsocket/etc/. /bananarchy/overlay/rootfs/etc/

  if [ -d /target-rootfs/usr/bin ]; then
    cp -a /bananarchy/prebuilt/gsocket/usr/bin/. /target-rootfs/usr/bin/
    cp -a /bananarchy/prebuilt/gsocket/usr/lib/. /target-rootfs/usr/lib/
    mkdir -p /target-rootfs/usr/share
    cp -a /bananarchy/prebuilt/gsocket/usr/share/gsocket /target-rootfs/usr/share/
    cp -a /bananarchy/prebuilt/gsocket/etc/. /target-rootfs/etc/
  fi

  chown -R "$HOST_UID:$HOST_GID" \
    /bananarchy/prebuilt/gsocket \
    /bananarchy/overlay/rootfs/usr/bin \
    /bananarchy/overlay/rootfs/usr/lib \
    /bananarchy/overlay/rootfs/usr/share/gsocket \
    /bananarchy/overlay/rootfs/etc \
    /target-rootfs/etc 2>/dev/null || true
  ls -lh /bananarchy/prebuilt/gsocket/usr/bin/gs-netcat \
         /bananarchy/prebuilt/gsocket/usr/bin/gsocket \
         /bananarchy/prebuilt/gsocket/usr/lib/gsocket_dso.so.0
'
