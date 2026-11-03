#!/bin/sh
# Cross-compile Linux 6.12.1-msm8916 QDSP6/Q6Voice audio modules and MSM8905/MSM8909 DTBs
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOTFS_DIR="${ROOTFS_DIR:-$BASE_DIR/rootfs}"
if [ -e "$ROOTFS_DIR" ]; then
  ROOTFS_DIR="$(realpath "$ROOTFS_DIR")"
fi
KERNEL_TAG="v6.12.1-msm8916"
KERNEL_URL="https://github.com/msm8916-mainline/linux/archive/refs/tags/${KERNEL_TAG}.tar.gz"

KERNEL_CACHE_DIR="${KERNEL_CACHE_DIR:-/tmp/linux-msm8916}"
DOCKER_CACHE_ARGS=""
if [ -d "$KERNEL_CACHE_DIR" ]; then
  DOCKER_CACHE_ARGS="-v $KERNEL_CACHE_DIR:/kcache"
fi

# shellcheck disable=SC2086
exec docker run --rm --network=host \
  -e HOST_UID="$(id -u)" \
  -e HOST_GID="$(id -g)" \
  -e KERNEL_TAG="$KERNEL_TAG" \
  -e KERNEL_URL="$KERNEL_URL" \
  -v "$BASE_DIR:/bananarchy" \
  -v "$ROOTFS_DIR:/target-rootfs:ro" \
  $DOCKER_CACHE_ARGS \
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
    bc \
    bison \
    flex \
    openssl-dev \
    elfutils-dev \
    perl \
    python3 \
    dtc \
    findutils \
    patch \
    curl \
    tar \
    xz \
    zstd \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7

  mkdir -p /bananarchy/prebuilt/kernel-6.12.1-msm8916
  if [ -d /kcache ] && [ -f /kcache/Makefile ]; then
    echo "[1/5] Using cached kernel tree in /kcache..."
    cd /kcache
    if ! grep -q "VOICEMMODE1" include/dt-bindings/sound/qcom,q6voice.h 2>/dev/null; then
      echo "[2/5] Applying Bananarchy QDSP6/Q6Voice and MSM8905/MSM8909 DTS patches..."
      patch -p1 < /bananarchy/patches/kernel/0001-msm8909-qdsp6-voice-sound-nokia-argon-6.12.patch
    fi
    if ! grep -q "adreno_is_a304" drivers/gpu/drm/msm/adreno/adreno_gpu.h 2>/dev/null; then
      echo "[2b/5] Applying Adreno 304 GPU and MSM8909 IOMMU/GPU DT patch..."
      patch -p1 < /bananarchy/patches/kernel/0003-msm8909-adreno-304-gpu-support.patch
    fi
    if [ ! -f include/config/auto.conf ] || ! grep -q "CONFIG_NETFILTER_XT_MATCH_OWNER=m" .config 2>/dev/null; then
      echo "[3/5] Preparing kernel configuration..."
      cp /bananarchy/patches/kernel/config-bananarchy-msm8909.armv7 .config
      make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- olddefconfig
      make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- modules_prepare
    fi
  else
    mkdir -p /tmp/kbuild
    CACHE_TAR="/bananarchy/prebuilt/kernel-6.12.1-msm8916/linux-${KERNEL_TAG#v}.tar.gz"
    cd /tmp/kbuild
    if [ -f "$CACHE_TAR" ] && [ "$(wc -c < "$CACHE_TAR")" -gt 1000000 ]; then
      echo "[1/5] Using cached Linux ${KERNEL_TAG} tarball..."
      cp "$CACHE_TAR" linux.tar.gz
    else
      echo "[1/5] Downloading Linux ${KERNEL_TAG}..."
      curl -f -sSL "$KERNEL_URL" -o linux.tar.gz
      cp linux.tar.gz "$CACHE_TAR" || true
    fi
    tar -xzf linux.tar.gz
    cd "linux-${KERNEL_TAG#v}"

    echo "[2/5] Applying Bananarchy QDSP6/Q6Voice and MSM8905/MSM8909 DTS patches..."
    patch -p1 < /bananarchy/patches/kernel/0001-msm8909-qdsp6-voice-sound-nokia-argon-6.12.patch
    patch -p1 < /bananarchy/patches/kernel/0003-msm8909-adreno-304-gpu-support.patch

    echo "[3/5] Preparing kernel configuration and Module.symvers..."
    cp /bananarchy/patches/kernel/config-bananarchy-msm8909.armv7 .config
    make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- olddefconfig
    make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- modules_prepare
  fi

  echo "[4/5] Building QDSP6/Q6Voice sound modules, Adreno 304 DRM module, torch LED module, camera module, and Nokia MSM8905/MSM8909 DTBs..."
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    KBUILD_MODPOST_WARN=1 M=sound/soc/qcom/qdsp6 modules
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    KBUILD_MODPOST_WARN=1 M=drivers/gpu/drm/msm modules
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    KBUILD_MODPOST_WARN=1 M=net/netfilter modules
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    KBUILD_MODPOST_WARN=1 M=/bananarchy/src/leds-msm8905-torch modules
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    KBUILD_MODPOST_WARN=1 M=/bananarchy/src/msm8905-cam modules
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- -j"$(nproc)" \
    qcom/qcom-msm8905-nokia-argon.dtb \
    qcom/qcom-msm8909-nokia-sparkler.dtb \
    qcom/qcom-msm8909-nokia-leo.dtb

  echo "[5/5] Exporting built modules and DTBs to prebuilt/kernel-6.12.1-msm8916..."
  mkdir -p /bananarchy/prebuilt/kernel-6.12.1-msm8916/qdsp6 \
           /bananarchy/prebuilt/kernel-6.12.1-msm8916/drm \
           /bananarchy/prebuilt/kernel-6.12.1-msm8916/netfilter \
           /bananarchy/prebuilt/kernel-6.12.1-msm8916/leds \
           /bananarchy/prebuilt/kernel-6.12.1-msm8916/camera \
           /bananarchy/prebuilt/kernel-6.12.1-msm8916/dtbs
  cp -a sound/soc/qcom/qdsp6/*.ko /bananarchy/prebuilt/kernel-6.12.1-msm8916/qdsp6/
  cp -a drivers/gpu/drm/msm/msm.ko /bananarchy/prebuilt/kernel-6.12.1-msm8916/drm/
  cp -a net/netfilter/xt_owner.ko net/netfilter/nft_redir.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/netfilter/
  cp -a /bananarchy/src/leds-msm8905-torch/leds-msm8905-torch.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/leds/
  cp -a /bananarchy/src/msm8905-cam/msm8905-cam.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/camera/
  armv7-alpine-linux-musleabihf-strip --strip-unneeded \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/qdsp6/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/drm/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/netfilter/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/leds/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/camera/*.ko
  for ko in \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/qdsp6/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/drm/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/netfilter/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/leds/*.ko \
    /bananarchy/prebuilt/kernel-6.12.1-msm8916/camera/*.ko; do
    zstd -f -q -k "$ko" -o "${ko}.zst"
  done
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- \
    M=/bananarchy/src/leds-msm8905-torch clean
  make ARCH=arm CROSS_COMPILE=armv7-alpine-linux-musleabihf- \
    M=/bananarchy/src/msm8905-cam clean
  for dts in \
    /bananarchy/devices/nokia-argon/qcom-msm8905-nokia-argon.dts \
    /bananarchy/devices/nokia-beatles/qcom-msm8905-nokia-beatles.dts \
    /bananarchy/devices/nokia-tough/qcom-msm8905-nokia-coconut.dts \
    /bananarchy/devices/nokia-sparkler/qcom-msm8909-nokia-sparkler.dts \
    /bananarchy/devices/nokia-sparkler/qcom-msm8909-nokia-leo.dts; do
    dtb="${dts%.dts}.dtb"
    dtc -I dts -O dtb -o "$dtb" "$dts" 2>/dev/null
    cp -a "$dtb" /bananarchy/prebuilt/kernel-6.12.1-msm8916/dtbs/
  done

  chown -R "$HOST_UID:$HOST_GID" /bananarchy/prebuilt/kernel-6.12.1-msm8916 \
    /bananarchy/src/leds-msm8905-torch \
    /bananarchy/src/msm8905-cam
  echo "Kernel audio modules, torch LED module, camera module, and DTBs built successfully."
'
