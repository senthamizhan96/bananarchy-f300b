#!/bin/sh
# Apply Bananarchy prebuilt binaries, DTBs, panel firmwares, artwork, and Sxmo 240x320 overlay to a target rootfs
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_ROOTFS="${1:-${ROOTFS_DIR:-$BASE_DIR/rootfs}}"
DEVICE="${2:-nokia-argon}"

if [ ! -d "$TARGET_ROOTFS" ]; then
  echo "Error: Target rootfs directory does not exist: $TARGET_ROOTFS" >&2
  exit 1
fi

case "$DEVICE" in
  nokia-argon|argon)
    DEVICE="nokia-argon"
    DEVICE_DIR="nokia-argon"
    DEVICEINFO_FILE="deviceinfo"
    DTB_FILE="qcom-msm8905-nokia-argon.dtb"
    ;;
  nokia-beatles|beatles)
    DEVICE="nokia-beatles"
    DEVICE_DIR="nokia-beatles"
    DEVICEINFO_FILE="deviceinfo"
    DTB_FILE="qcom-msm8905-nokia-beatles.dtb"
    ;;
  nokia-tough|nokia-coconut|tough|coconut)
    DEVICE="nokia-tough"
    DEVICE_DIR="nokia-tough"
    DEVICEINFO_FILE="deviceinfo"
    DTB_FILE="qcom-msm8905-nokia-coconut.dtb"
    ;;
  nokia-sparkler|sparkler)
    DEVICE="nokia-sparkler"
    DEVICE_DIR="nokia-sparkler"
    DEVICEINFO_FILE="deviceinfo"
    DTB_FILE="qcom-msm8909-nokia-sparkler.dtb"
    ;;
  nokia-leo|leo)
    DEVICE="nokia-leo"
    DEVICE_DIR="nokia-sparkler"
    DEVICEINFO_FILE="deviceinfo.nokia-leo"
    DTB_FILE="qcom-msm8909-nokia-leo.dtb"
    ;;
  *)
    echo "Error: Unsupported device '$DEVICE'." >&2
    echo "Supported devices: nokia-argon, nokia-beatles, nokia-tough, nokia-sparkler, nokia-leo" >&2
    exit 1
    ;;
esac

if command -v docker >/dev/null 2>&1; then
  echo "[0/4] Verifying prebuilt artifacts..."
  if [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/lib/libneoui.so" ] || \
     [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/bin/dwm" ] || \
     [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/bin/st" ] || \
     [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so" ] || \
     [ ! -f "$BASE_DIR/prebuilt/sxmo-x11/usr/lib/dwm/plugins/privacy.so" ]; then
    echo "  -> Missing prebuilt sxmo-x11/NeoUI artifacts; running scripts/build-sxmo-x11.sh..."
    "$BASE_DIR/scripts/build-sxmo-x11.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/games/usr/bin/snake" ] || \
     [ ! -f "$BASE_DIR/prebuilt/games/usr/bin/tetris" ] || \
     [ ! -f "$BASE_DIR/prebuilt/games/usr/bin/scoundrel" ] || \
     [ ! -f "$BASE_DIR/prebuilt/games/usr/bin/gambatte" ] || \
     [ ! -f "$BASE_DIR/prebuilt/games/usr/bin/remedy" ]; then
    echo "  -> Missing prebuilt games/emulators; running scripts/build-games.sh..."
    "$BASE_DIR/scripts/build-games.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/gsocket/usr/bin/gs-netcat" ] || \
     [ ! -f "$BASE_DIR/prebuilt/gsocket/usr/lib/gsocket_dso.so.0" ]; then
    echo "  -> Missing prebuilt gsocket binaries; running scripts/build-gsocket.sh..."
    "$BASE_DIR/scripts/build-gsocket.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/fcitx5-t9/usr/lib/fcitx5/libt9.so" ]; then
    echo "  -> Missing prebuilt fcitx5-t9 engine; running scripts/build-fcitx5-t9.sh..."
    "$BASE_DIR/scripts/build-fcitx5-t9.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/xlibre/usr/bin/Xorg" ] || \
     [ ! -f "$BASE_DIR/prebuilt/xlibre/usr/lib/xorg/modules/xlibre-25/drivers/modesetting_drv.so" ]; then
    echo "  -> Missing prebuilt Xlibre server/drivers; running scripts/build-xlibre.sh..."
    "$BASE_DIR/scripts/build-xlibre.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/dtbs/$DTB_FILE" ] || \
     [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/drm/msm.ko.zst" ] || \
     [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/netfilter/xt_owner.ko.zst" ] || \
     [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/netfilter/nft_redir.ko.zst" ] || \
     [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/leds/leds-msm8905-torch.ko.zst" ] || \
     [ ! -f "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/camera/msm8905-cam.ko.zst" ]; then
    echo "  -> Missing prebuilt kernel modules or DTBs; running scripts/build-kernel-audio.sh..."
    "$BASE_DIR/scripts/build-kernel-audio.sh"
  fi
  if [ ! -f "$BASE_DIR/prebuilt/bantam/bantam" ]; then
    echo "  -> Missing prebuilt bantam binary; running scripts/build-bantam.sh..."
    "$BASE_DIR/scripts/build-bantam.sh"
  fi
fi

echo "[1/4] Installing Bananarchy rootfs overlay (artwork, branding, Sxmo 240x320, UCM, udev)..."
rm -f "$TARGET_ROOTFS/usr/bin/bantam" "$TARGET_ROOTFS/usr/local/bin/bantam" "$TARGET_ROOTFS/etc/doas.d/wheel.conf"
cp -af "$BASE_DIR/overlay/rootfs/." "$TARGET_ROOTFS/"
if [ -x "$BASE_DIR/scripts/version.sh" ]; then
  "$BASE_DIR/scripts/version.sh" "$TARGET_ROOTFS"
fi
# Pre-create the user's modem/SMS log and blocklist directories so that the
# root-run dual-SIM daemon never creates them root-owned on a fresh image.
mkdir -p "$TARGET_ROOTFS/home/user/.local/share/sxmo/modem" \
  "$TARGET_ROOTFS/home/user/.local/share/sxmo/block"
chown -R 10000:10000 "$TARGET_ROOTFS/home/user" 2>/dev/null || true
chown 0:0 "$TARGET_ROOTFS/etc/doas.d/50-sxmo.conf" 2>/dev/null || true
chmod 640 "$TARGET_ROOTFS/etc/doas.d/50-sxmo.conf" 2>/dev/null || true
mkdir -p "$TARGET_ROOTFS/etc/bananarchy"
chown 0:0 "$TARGET_ROOTFS/etc/bananarchy" 2>/dev/null || true
chmod 0700 "$TARGET_ROOTFS/etc/bananarchy" 2>/dev/null || true
if [ -f "$TARGET_ROOTFS/etc/group" ]; then
  grep -q '^openvpn:' "$TARGET_ROOTFS/etc/group" || echo 'openvpn:x:113:openvpn' >> "$TARGET_ROOTFS/etc/group"
  grep -q '^tor:' "$TARGET_ROOTFS/etc/group" || echo 'tor:x:114:tor' >> "$TARGET_ROOTFS/etc/group"
fi
if [ -f "$TARGET_ROOTFS/etc/passwd" ]; then
  grep -q '^openvpn:' "$TARGET_ROOTFS/etc/passwd" || echo 'openvpn:x:108:113:OpenVPN:/dev/null:/sbin/nologin' >> "$TARGET_ROOTFS/etc/passwd"
  grep -q '^tor:' "$TARGET_ROOTFS/etc/passwd" || echo 'tor:x:109:114:tor:/var/lib/tor:/sbin/nologin' >> "$TARGET_ROOTFS/etc/passwd"
fi
TOR_UID="$(awk -F: '/^tor:/ {print $3}' "$TARGET_ROOTFS/etc/passwd" 2>/dev/null || true)"
TOR_GID="$(awk -F: '/^tor:/ {print $4}' "$TARGET_ROOTFS/etc/passwd" 2>/dev/null || true)"
mkdir -p "$TARGET_ROOTFS/var/lib/tor"
chown -R "${TOR_UID:-109}:${TOR_GID:-114}" "$TARGET_ROOTFS/var/lib/tor" 2>/dev/null || true
chmod 0700 "$TARGET_ROOTFS/var/lib/tor" 2>/dev/null || true
chown 0:0 "$TARGET_ROOTFS/usr/bin/sudo" "$TARGET_ROOTFS/usr/bin/doas" "$TARGET_ROOTFS/bin/su" 2>/dev/null || true
chmod 4755 "$TARGET_ROOTFS/usr/bin/sudo" "$TARGET_ROOTFS/usr/bin/doas" "$TARGET_ROOTFS/bin/su" 2>/dev/null || true

echo "[2/4] Installing prebuilt QDSP6/Q6Voice kernel modules, Adreno 304 DRM module, torch LED module, camera module, and DTBs..."
if [ -d "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/sound/soc/qcom/qdsp6" ]; then
  cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/qdsp6/"*.ko* \
    "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/sound/soc/qcom/qdsp6/"
fi
if [ -d "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/drm" ] && \
   [ -d "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/gpu/drm/msm" ]; then
  rm -f "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/gpu/drm/msm/msm.ko"
  cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/drm/msm.ko.zst" \
    "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/gpu/drm/msm/msm.ko.zst"
fi
if [ -d "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/leds" ] && \
   [ -d "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/leds" ]; then
  cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/leds/"*.ko* \
    "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/leds/"
fi
if [ -d "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/camera" ] && \
   [ -d "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers" ]; then
  mkdir -p "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/media/platform/qcom/camss"
  cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/camera/"*.ko* \
    "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/drivers/media/platform/qcom/camss/"
fi
if [ -d "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/netfilter" ] && \
   [ -d "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/net/netfilter" ]; then
  cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/netfilter/"*.ko.zst \
    "$TARGET_ROOTFS/lib/modules/6.12.1-msm8916/kernel/net/netfilter/"
fi
mkdir -p "$TARGET_ROOTFS/boot/dtbs"
cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/dtbs/"*.dtb "$TARGET_ROOTFS/boot/dtbs/"
cp -af "$BASE_DIR/prebuilt/kernel-6.12.1-msm8916/dtbs/$DTB_FILE" "$TARGET_ROOTFS/boot/$DTB_FILE"

echo "[3/4] Installing prebuilt Xlibre, Sxmo X11 (dwm/dmenu/st), fcitx5-t9, and Bantam AI agent..."
if [ -d "$BASE_DIR/prebuilt/xlibre/usr" ]; then
  cp -af "$BASE_DIR/prebuilt/xlibre/usr/bin/." "$TARGET_ROOTFS/usr/bin/"
  cp -af "$BASE_DIR/prebuilt/xlibre/usr/lib/xorg/." "$TARGET_ROOTFS/usr/lib/xorg/"
  cp -af "$BASE_DIR/prebuilt/xlibre/usr/share/X11/xorg.conf.d/." "$TARGET_ROOTFS/usr/share/X11/xorg.conf.d/"
fi
if [ -d "$BASE_DIR/prebuilt/sxmo-x11/usr" ]; then
  cp -af "$BASE_DIR/prebuilt/sxmo-x11/usr/." "$TARGET_ROOTFS/usr/"
fi
chown 0:0 "$TARGET_ROOTFS/usr/bin/bananarchy-engmenu-exec" 2>/dev/null || true
chmod 4755 "$TARGET_ROOTFS/usr/bin/bananarchy-engmenu-exec" 2>/dev/null || true
if [ -d "$BASE_DIR/prebuilt/games/usr" ]; then
  cp -af "$BASE_DIR/prebuilt/games/usr/." "$TARGET_ROOTFS/usr/"
fi
if [ -d "$BASE_DIR/prebuilt/gsocket/usr" ]; then
  cp -af "$BASE_DIR/prebuilt/gsocket/usr/." "$TARGET_ROOTFS/usr/"
fi
if [ -d "$BASE_DIR/prebuilt/gsocket/etc" ]; then
  cp -af "$BASE_DIR/prebuilt/gsocket/etc/." "$TARGET_ROOTFS/etc/"
fi
cp -af "$BASE_DIR/prebuilt/fcitx5-t9/usr/." "$TARGET_ROOTFS/usr/"
cp -af "$BASE_DIR/prebuilt/bantam/bantam" "$TARGET_ROOTFS/usr/bin/bantam"
cp -af "$BASE_DIR/prebuilt/bantam/bantam" "$TARGET_ROOTFS/usr/local/bin/bantam"
python3 - "$TARGET_ROOTFS" <<'PYEOF'
import glob, os, struct, sys

rootfs = sys.argv[1]
for path in glob.glob(os.path.join(rootfs, "usr/lib/libgallium-*.so")):
  if os.path.islink(path):
    continue
  with open(path, "rb") as f:
    data = bytearray(f.read())
  changed = False
  str_off = data.find(b"FD305B\x00")
  if str_off != -1:
    data[str_off:str_off + 7] = b"FD304\x00\x00"
    changed = True
  rec_old = struct.pack("<IIQ", 0, 0, 0x03000512)
  rec_new = struct.pack("<IIQ", 304, 0, 0x030004ff)
  rec_off = data.find(rec_old)
  if rec_off != -1:
    data[rec_off:rec_off + 16] = rec_new
    changed = True
  if changed:
    with open(path, "wb") as f:
      f.write(data)
    print(f"Patched {os.path.basename(path)} with Adreno 304 (FD304) device descriptor.")
PYEOF
rm -rf \
  "$TARGET_ROOTFS/usr/bin/sxmo_swayinitconf.sh" \
  "$TARGET_ROOTFS/usr/bin/sxmo_swayinit.sh" \
  "$TARGET_ROOTFS/usr/bin/sxmo_riverinit.sh" \
  "$TARGET_ROOTFS/usr/bin/sxmo_riverbar.sh" \
  "$TARGET_ROOTFS/usr/bin/bemenu" \
  "$TARGET_ROOTFS/usr/bin/bemenu-run" \
  "$TARGET_ROOTFS/usr/lib/bemenu" \
  "$TARGET_ROOTFS/usr/share/wayland-sessions" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/sway_template" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/wob.ini" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/river" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/wofi.config" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/wofi.css" \
  "$TARGET_ROOTFS/usr/share/sxmo/appcfg/foot.ini" \
  "$TARGET_ROOTFS/usr/share/sxmo/sessions/sxmo_river.desktop" \
  "$TARGET_ROOTFS/usr/share/sxmo/services/sxmo_riverbar.service" \
  "$TARGET_ROOTFS/usr/share/sxmo/services/sxmo_wob.service" \
  "$TARGET_ROOTFS/usr/share/sxmo/appscripts/sxmo_record.sh" \
  "$TARGET_ROOTFS/usr/share/sxmo/appscripts/sxmo_websearch.sh" \
  "$TARGET_ROOTFS/usr/share/sxmo/appscripts/sxmo_youtube.sh" \
  "$TARGET_ROOTFS/usr/share/sxmo/appscripts/sxmo_youtube_audio.sh" \
  "$TARGET_ROOTFS/usr/lib/fcitx5/libwayland.so" \
  "$TARGET_ROOTFS/usr/lib/fcitx5/libwaylandim.so" \
  "$TARGET_ROOTFS/usr/share/fcitx5/addon/wayland.conf" \
  "$TARGET_ROOTFS/usr/share/fcitx5/addon/waylandim.conf" \
  "$TARGET_ROOTFS/usr/bin/vim"* \
  "$TARGET_ROOTFS/usr/share/vim" \
  "$TARGET_ROOTFS/home/user/.config/fcitx5/conf/waylandim.conf" \
  "$TARGET_ROOTFS/etc/skel/.config/fcitx5/conf/waylandim.conf" \
  "$TARGET_ROOTFS/home/user/.config/wob" \
  "$TARGET_ROOTFS/etc/skel/.config/wob" \
  "$TARGET_ROOTFS/home/user/.config/wofi" \
  "$TARGET_ROOTFS/etc/skel/.config/wofi" \
  "$TARGET_ROOTFS/home/user/.config/foot" \
  "$TARGET_ROOTFS/etc/skel/.config/foot" \
  "$TARGET_ROOTFS/home/user/.config/sxmo/sway" \
  "$TARGET_ROOTFS/etc/skel/.config/sxmo/sway" \
  "$TARGET_ROOTFS/home/user/.bantam.cfg" \
  "$TARGET_ROOTFS/etc/skel/.bantam.cfg" \
  "$TARGET_ROOTFS/root/.bantam.cfg" \
  "$TARGET_ROOTFS/usr/share/wallpapers/postmarketos" \
  "$TARGET_ROOTFS/usr/share/wallpapers/postmarketos.jpg" \
  "$TARGET_ROOTFS/usr/share/wallpapers/bananarchy.jpg" \
  "$TARGET_ROOTFS/usr/share/wallpapers/bananarchy-240x240.png" \
  "$TARGET_ROOTFS/usr/share/wallpapers/bananarchy-landscape.png" \
  "$TARGET_ROOTFS/etc/runlevels/default/hkdm" \
  "$TARGET_ROOTFS/etc/runlevels/default/haveged" \
  "$TARGET_ROOTFS/etc/runlevels/default/iio-sensor-proxy" \
  "$TARGET_ROOTFS/etc/runlevels/default/sleep-inhibitor" \
  "$TARGET_ROOTFS/etc/runlevels/default/swapfile" \
  "$TARGET_ROOTFS/etc/runlevels/default/nftables" \
  "$TARGET_ROOTFS/etc/runlevels/default/tor" \
  "$TARGET_ROOTFS/etc/runlevels/default/openvpn" \
  "$TARGET_ROOTFS/etc/runlevels/default/iptables" \
  "$TARGET_ROOTFS/etc/runlevels/default/ip6tables" \
  "$TARGET_ROOTFS/etc/runlevels/boot/logbookd" \
  "$TARGET_ROOTFS/usr/share/superd/services/sxmo_battery_monitor.service" \
  "$TARGET_ROOTFS/usr/share/superd/services/sxmo_autosuspend.service" \
  "$TARGET_ROOTFS/usr/share/superd/services/sxmo-x11-status.service" \
  "$TARGET_ROOTFS/swapfile" \
  "$TARGET_ROOTFS/var/log/logbookd.db"*
ln -sf /etc/init.d/kill-plymouth "$TARGET_ROOTFS/etc/runlevels/default/kill-plymouth"
mkdir -p "$TARGET_ROOTFS/var/lib/tinydm"
ln -sf /usr/share/xsessions/sxmo.desktop "$TARGET_ROOTFS/var/lib/tinydm/default-session.desktop"

echo "[4/4] Installing deviceinfo for $DEVICE ($DTB_FILE)..."
if [ -f "$BASE_DIR/devices/$DEVICE_DIR/$DEVICEINFO_FILE" ]; then
  cp -a "$BASE_DIR/devices/$DEVICE_DIR/$DEVICEINFO_FILE" "$TARGET_ROOTFS/etc/deviceinfo"
fi
if [ -f "$TARGET_ROOTFS/boot/extlinux/extlinux.conf" ]; then
  sed -i "s|^[[:space:]]*fdt .*|\tfdt /$DTB_FILE|" "$TARGET_ROOTFS/boot/extlinux/extlinux.conf"
fi

echo "Bananarchy overlay applied to $TARGET_ROOTFS for device $DEVICE."
