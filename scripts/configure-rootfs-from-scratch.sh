#!/bin/sh
set -eu

BANANARCHY_DIR="${BANANARCHY_DIR:-/bananarchy}"
WORK="${WORK:-/work}"

echo "[1/4] Installing lightweight host helper tools in container..."
cat > /etc/apk/repositories <<EOF
https://mirror.nura.eco/postmarketos/v26.06
https://dl-cdn.alpinelinux.org/alpine/edge/main
https://dl-cdn.alpinelinux.org/alpine/edge/community
https://dl-cdn.alpinelinux.org/alpine/edge/testing
EOF
if [ -d "$BANANARCHY_DIR/keys" ]; then
  cp "$BANANARCHY_DIR/keys/"* /etc/apk/keys/
elif [ -d "$WORK/pmbootstrap/pmb/data/keys" ]; then
  cp "$WORK/pmbootstrap/pmb/data/keys/"* /etc/apk/keys/
fi

apk add --no-cache \
  busybox-static \
  busybox-extras \
  dbus \
  ca-certificates \
  fontconfig \
  glib \
  gtk+3.0 \
  gdk-pixbuf \
  librsvg \
  shared-mime-info \
  desktop-file-utils \
  gtk-update-icon-cache \
  kmod \
  eudev \
  postmarketos-mkinitfs \
  boot-deploy \
  mkbootimg \
  devicepkg-utils \
  e2fsprogs \
  e2fsprogs-extra \
  util-linux \
  sfdisk \
  android-tools \
  python3 \
  zstd \
  file

configure_common_rootfs() {
  ROOTFS="$1"
  UI_TYPE="$2"
  BOOT_UUID="$3"
  ROOT_UUID="$4"

  echo "=== Configuring $ROOTFS ($UI_TYPE) ==="

  cp /etc/apk/repositories "$ROOTFS/etc/apk/repositories"

  echo "  -> Ensuring fcitx5-gtk3 and Bananarchy development/scripting/privacy/multimedia packages are installed in $ROOTFS..."
  apk --root "$ROOTFS" --arch armv7 add --no-scripts \
    bash \
    fcitx5 \
    fcitx5-gtk3 \
    font-fira-code \
    font-noto \
    font-noto-emoji \
    git \
    gcc \
    g++ \
    musl-dev \
    make \
    mandoc \
    coreutils \
    bc \
    ed \
    rlwrap \
    less \
    nano \
    frotz \
    janet \
    janet-dev \
    jpm \
    lua5.4 \
    lua5.4-dev \
    lua5.5 \
    lua5.5-dev \
    yabasic \
    yabasic-doc \
    perl \
    perl-io-socket-ssl \
    tcl \
    tcl-lib \
    tcl-tls \
    sqlite \
    sqlite-tcl \
    tor \
    openvpn \
    wireguard-tools \
    wireguard-tools-wg \
    wireguard-tools-wg-quick \
    iptables \
    ip6tables \
    iproute2 \
    webkit2gtk-4.1 \
    libwpe \
    libwpebackend-fdo \
    gst-plugins-good \
    gst-plugins-bad \
    gst-libav \
    mesa-demos \
    mesa-utils \
    scrot \
    modemmanager \
    modemmanager-udev \
    mobile-broadband-provider-info \
    postmarketos-base-ui-modemmanager \
    postmarketos-base-ui-modemmanager-openrc \
    i2c-tools \
    v4l-utils

  ln -sf lua5.5 "$ROOTFS/usr/bin/lua"
  ln -sf luac5.5 "$ROOTFS/usr/bin/luac"

  echo "  -> Installing busybox and busybox-extras symlinks..."
  for applet in $(/bin/busybox --list-full); do
    target="$ROOTFS/$applet"
    mkdir -p "$(dirname "$target")"
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
      ln -s /bin/busybox "$target"
    fi
  done
  if [ -x /bin/busybox-extras ]; then
    for applet in $(/bin/busybox-extras --list-full); do
      target="$ROOTFS/$applet"
      mkdir -p "$(dirname "$target")"
      if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        ln -s /bin/busybox-extras "$target"
      fi
    done
  fi
  if [ -e "$ROOTFS/sbin/losetup" ] && [ ! -e "$ROOTFS/usr/sbin/losetup" ]; then
    ln -sf /sbin/losetup "$ROOTFS/usr/sbin/losetup"
  fi

  echo "  -> Setting up temporary x86_64 hostbin for chroot execution..."
  rm -rf "$ROOTFS/tmp/hostbin"
  mkdir -p "$ROOTFS/tmp/hostbin"
  cp /bin/busybox.static "$ROOTFS/tmp/hostbin/busybox.static"
  for app in $(/bin/busybox.static --list); do
    ln -sf /tmp/hostbin/busybox.static "$ROOTFS/tmp/hostbin/$app"
  done
  for bin in \
    /sbin/depmod \
    /usr/sbin/mkinitfs \
    /usr/bin/boot-deploy \
    /usr/bin/generate-kernel-cmdline \
    /usr/bin/mkbootimg \
    /usr/bin/mkbootimg-osm0sis \
    /usr/bin/zstd; do
    if [ -e "$bin" ]; then
      cp -L "$bin" "$ROOTFS/tmp/hostbin/"
    fi
  done
  rm -f "$ROOTFS/tmp/hostbin/df"
  cat > "$ROOTFS/tmp/hostbin/df" <<'EOF'
#!/tmp/hostbin/sh
printf "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/root 52428800 5242880 47185920 10%% /\n"
EOF
  chmod +x "$ROOTFS/tmp/hostbin/df"
  mkdir -p "$ROOTFS/proc/1/root"
  cp /lib/ld-musl-x86_64.so.1 "$ROOTFS/lib/"
  cp -a /usr/lib/libgcc_s.so* "$ROOTFS/tmp/hostbin/" 2>/dev/null || true
  cp -a /lib/libz.so* /usr/lib/libzstd.so* /usr/lib/liblzma.so* /lib/libcrypto.so* "$ROOTFS/tmp/hostbin/" 2>/dev/null || true

  ln -sf /tmp/hostbin/busybox.static "$ROOTFS/bin/sh"

  echo "  -> Creating system groups, users, and default user (UID 10000)..."
  chroot "$ROOTFS" /tmp/hostbin/busybox.static sh -e -c '
    export PATH="/tmp/hostbin:/usr/sbin:/usr/bin:/sbin:/bin"
    export LD_LIBRARY_PATH="/tmp/hostbin:/lib:/usr/lib"

    addgroup -S -g 42 shadow 2>/dev/null || true
    chown root:shadow /etc/shadow
    [ -e /etc/shadow- ] && chown root:shadow /etc/shadow-

    addgroup -S -g 86 avahi 2>/dev/null || true
    adduser -S -u 86 -D -H -h /dev/null -s /sbin/nologin -G avahi -g "Avahi System User" avahi 2>/dev/null || true

    addgroup -S klogd 2>/dev/null || true
    adduser -S -D -H -h /dev/null -s /sbin/nologin -G klogd -g klogd klogd 2>/dev/null || true

    addgroup -S chrony 2>/dev/null || true
    adduser -S -D -H -h /dev/null -s /sbin/nologin -G chrony -g chrony chrony 2>/dev/null || true

    addgroup -S messagebus 2>/dev/null || true
    adduser -S -D -H -h /dev/null -s /sbin/nologin -G messagebus -g messagebus messagebus 2>/dev/null || true

    addgroup -S dnsmasq 2>/dev/null || true
    adduser -S -D -H -h /dev/null -s /sbin/nologin -G dnsmasq -g dnsmasq dnsmasq 2>/dev/null || true

    addgroup -S geoclue 2>/dev/null || true
    adduser -S -D -h /var/lib/geoclue -s /sbin/nologin -G geoclue geoclue 2>/dev/null || true

    addgroup -S plugdev 2>/dev/null || true
    addgroup -S pipewire 2>/dev/null || true
    addgroup -S seat 2>/dev/null || true
    addgroup -S feedbackd 2>/dev/null || true

    addgroup -S polkitd 2>/dev/null || true
    adduser -S -D -H -h /var/empty -s /sbin/nologin -G polkitd -g polkitd polkitd 2>/dev/null || true

    addgroup -S colord 2>/dev/null || true
    adduser -S -D -H -h /var/lib/colord -s /sbin/nologin -G colord -g colord colord 2>/dev/null || true

    addgroup -S greetd 2>/dev/null || true
    adduser -S -D -H -h /var/lib/greetd -s /sbin/nologin -G greetd -g greetd greetd 2>/dev/null || true
    addgroup greetd video 2>/dev/null || true

    if ! grep -q "^user:" /etc/passwd 2>/dev/null; then
      adduser -D -u 10000 -s /bin/ash user 2>/dev/null || true
    fi

    for grp in audio input netdev plugdev video wheel seat feedbackd dialout tty pipewire; do
      addgroup -S "$grp" 2>/dev/null || true
      addgroup user "$grp" 2>/dev/null || true
    done

    echo "user:147147" | chpasswd
    passwd -l root >/dev/null 2>&1 || true
  '

  echo "  -> Configuring sudo/doas, hostname, locale, timezone, inittab, and sysctl..."
  mkdir -p "$ROOTFS/etc/doas.d" "$ROOTFS/etc/sudoers.d"
  rm -f "$ROOTFS/etc/doas.d/wheel.conf"

  cat > "$ROOTFS/etc/sudoers.d/wheel" <<EOF
%wheel ALL=(ALL:ALL) ALL
EOF
  chmod 440 "$ROOTFS/etc/sudoers.d/wheel"

  echo "nokia-argon" > "$ROOTFS/etc/hostname"
  cat > "$ROOTFS/etc/hosts" <<EOF
127.0.0.1	nokia-argon localhost.localdomain localhost
::1		localhost ipv6-localhost ipv6-loopback
fe00::0		ipv6-localnet
ff00::0		ipv6-mcastprefix
ff02::1		ipv6-allnodes
ff02::2		ipv6-allrouters
ff02::3		ipv6-allhosts
EOF

  echo "LANG=C.UTF-8" > "$ROOTFS/etc/locale.conf"
  if [ -f "$ROOTFS/usr/share/zoneinfo/UTC" ]; then
    cp "$ROOTFS/usr/share/zoneinfo/UTC" "$ROOTFS/etc/localtime"
    echo "UTC" > "$ROOTFS/etc/timezone"
  fi

  touch "$ROOTFS/etc/apk/interactive"
  mkdir -p "$ROOTFS/run/openrc"
  touch "$ROOTFS/run/openrc/shutdowntime"
  dbus-uuidgen --ensure="$ROOTFS/etc/machine-id"

  for mod in panel_mipi_dbi leds_msm8905_torch msm8905-cam; do
    if ! grep -qx "$mod" "$ROOTFS/etc/modules" 2>/dev/null; then
      echo "$mod" >> "$ROOTFS/etc/modules"
    fi
  done

  mkdir -p "$ROOTFS/etc/udev/rules.d"
  cat > "$ROOTFS/etc/udev/rules.d/90-nokia-argon-leds.rules" <<EOF
SUBSYSTEM=="leds", ACTION=="add", RUN+="/bin/chgrp video /sys/class/leds/%k/brightness", RUN+="/bin/chmod 0664 /sys/class/leds/%k/brightness"
SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod 0664 /sys/class/backlight/%k/brightness"
EOF

  mkdir -p "$ROOTFS/etc/sysctl.d"
  cat > "$ROOTFS/etc/sysctl.d/90-nokia-argon-lowram.conf" <<EOF
vm.swappiness = 180
vm.page-cluster = 0
vm.vfs_cache_pressure = 150
vm.min_free_kbytes = 16384
EOF

  echo "  -> Installing patched audio DTB, QDSP6/torch/camera kernel modules, UCM, and WirePlumber S16LE rules..."
  cp "$BANANARCHY_DIR/prebuilt/kernel-6.12.1-msm8916/dtbs/qcom-msm8905-nokia-argon.dtb" \
    "$ROOTFS/boot/dtbs/qcom-msm8905-nokia-argon.dtb"
  cp "$BANANARCHY_DIR/prebuilt/kernel-6.12.1-msm8916/qdsp6/"*.ko* \
    "$ROOTFS/lib/modules/6.12.1-msm8916/kernel/sound/soc/qcom/qdsp6/"
  mkdir -p "$ROOTFS/lib/modules/6.12.1-msm8916/extra"
  cp "$BANANARCHY_DIR/prebuilt/kernel-6.12.1-msm8916/leds/"*.ko* \
    "$ROOTFS/lib/modules/6.12.1-msm8916/extra/" 2>/dev/null || true
  cp "$BANANARCHY_DIR/prebuilt/kernel-6.12.1-msm8916/camera/"*.ko* \
    "$ROOTFS/lib/modules/6.12.1-msm8916/extra/" 2>/dev/null || true
  depmod -b "$ROOTFS" 6.12.1-msm8916

  sed -i 's/"CS-Voice"/"VoiceMMode1"/g' \
    "$ROOTFS/usr/share/alsa/ucm2/platforms/msm8916/qdsp6.conf"

  mkdir -p \
    "$ROOTFS/usr/share/wireplumber/wireplumber.conf.d" \
    "$ROOTFS/etc/wireplumber/wireplumber.conf.d" \
    "$ROOTFS/etc/skel/.config/wireplumber/wireplumber.conf.d" \
    "$ROOTFS/home/user/.config/wireplumber/wireplumber.conf.d"

  cat > "$ROOTFS/usr/share/wireplumber/wireplumber.conf.d/msm8916-s16le.conf" <<EOF
monitor.alsa.rules = [
  {
    matches = [
      {
        device.name = "alsa_card.comprC0"
      }
    ]
    actions = {
      update-props = {
        device.disabled = true
      }
    }
  },
  {
    matches = [
      {
        node.name = "~alsa_output.platform-7702000.*"
      },
      {
        node.name = "~alsa_output.platform-sound.*"
      },
      {
        node.name = "~alsa_input.platform-7702000.*"
      },
      {
        node.name = "~alsa_input.platform-sound.*"
      }
    ]
    actions = {
      update-props = {
        audio.format = "S16LE"
      }
    }
  }
]
EOF
  cp "$ROOTFS/usr/share/wireplumber/wireplumber.conf.d/msm8916-s16le.conf" \
    "$ROOTFS/etc/wireplumber/wireplumber.conf.d/msm8916-s16le.conf"
  cp "$ROOTFS/usr/share/wireplumber/wireplumber.conf.d/msm8916-s16le.conf" \
    "$ROOTFS/etc/skel/.config/wireplumber/wireplumber.conf.d/msm8916-s16le.conf"
  cp "$ROOTFS/usr/share/wireplumber/wireplumber.conf.d/msm8916-s16le.conf" \
    "$ROOTFS/home/user/.config/wireplumber/wireplumber.conf.d/msm8916-s16le.conf"

  echo "  -> Installing fcitx5-t9 addon and T9 profile..."
  cp -a "$BANANARCHY_DIR/prebuilt/fcitx5-t9/usr/"* "$ROOTFS/usr/"

  mkdir -p \
    "$ROOTFS/usr/share/superd/services" \
    "$ROOTFS/etc/skel/.config/superd/services/enabled" \
    "$ROOTFS/home/user/.config/superd/services/enabled" \
    "$ROOTFS/etc/skel/.config/fcitx5" \
    "$ROOTFS/home/user/.config/fcitx5"

  cat > "$ROOTFS/usr/share/superd/services/fcitx5.service" <<EOF
[Unit]
Description=fcitx5 input support

[Service]
Type=simple
ExecStart=/usr/bin/fcitx5
Restart=always
EOF

  ln -sf /usr/share/superd/services/fcitx5.service \
    "$ROOTFS/etc/skel/.config/superd/services/enabled/fcitx5.service"
  ln -sf /usr/share/superd/services/fcitx5.service \
    "$ROOTFS/home/user/.config/superd/services/enabled/fcitx5.service"

  cat > "$ROOTFS/etc/skel/.config/fcitx5/profile" <<EOF
[Groups/0]
# Group Name
Name=Default
# Layout
Default Layout=us
# Default Input Method
DefaultIM=t9

[Groups/0/Items/0]
# Name
Name=keyboard-us
# Layout
Layout=

[Groups/0/Items/1]
# Name
Name=t9
# Layout
Layout=

[GroupOrder]
0=Default
EOF
  cp "$ROOTFS/etc/skel/.config/fcitx5/profile" "$ROOTFS/home/user/.config/fcitx5/profile"

  cat > "$ROOTFS/etc/profile.d/fcitx5-t9.sh" <<'EOF'
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SXMO_MENU=wofi
export SXMO_BEMENU_PORTRAIT_LINES=11
export SXMO_BEMENU_LANDSCAPE_LINES=8
export BEMENU_OPTS='--fn "Sxmo 11.5" -H 24 -B 2 --hp 4 -s --no-overlap --fixed-height --scrollbar none --cw 2 --bdr "#005577" --tb "#383838" --tf "#aaaaaa" --fb "#383838" --ff "#ffffff" --cb "#ffffff" --cf "#383838" --nb "#1c1c1c" --nf "#ffffff" --ab "#1c1c1c" --af "#ffffff" --hb "#005577" --hf "#ffffff" -w -i'
EOF
  chmod 755 "$ROOTFS/etc/profile.d/fcitx5-t9.sh"

  if ! grep -q "GTK_IM_MODULE=fcitx" "$ROOTFS/etc/skel/.profile" 2>/dev/null; then
    cat >> "$ROOTFS/etc/skel/.profile" <<'EOF'

export PATH="$HOME/.local/bin:$PATH"
export WLR_RENDERER=pixman
export WLR_NO_HARDWARE_CURSORS=1
export SXMO_DISABLE_LEDS=1
export SXMO_NO_VIRTUAL_KEYBOARD=1
export NO_AT_BRIDGE=1
export GTK_A11Y=none
export GTK_USE_PORTAL=0
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SXMO_MENU=wofi
export SXMO_BEMENU_PORTRAIT_LINES=11
export SXMO_BEMENU_LANDSCAPE_LINES=8
export BEMENU_OPTS='--fn "Sxmo 11.5" -H 24 -B 2 --hp 4 -s --no-overlap --fixed-height --scrollbar none --cw 2 --bdr "#005577" --tb "#383838" --tf "#aaaaaa" --fb "#383838" --ff "#ffffff" --cb "#ffffff" --cf "#383838" --nb "#1c1c1c" --nf "#ffffff" --ab "#1c1c1c" --af "#ffffff" --hb "#005577" --hf "#ffffff" -w -i'
EOF
  fi
  cp "$ROOTFS/etc/skel/.profile" "$ROOTFS/home/user/.profile"

  echo "  -> Enabling OpenRC runlevel services..."
  mkdir -p \
    "$ROOTFS/etc/runlevels/sysinit" \
    "$ROOTFS/etc/runlevels/boot" \
    "$ROOTFS/etc/runlevels/default" \
    "$ROOTFS/etc/runlevels/shutdown"
  rm -f "$ROOTFS/etc/runlevels/boot/hwclock"

  for svc in devfs dmesg msm-firmware-loader udev udev-trigger udev-settle; do
    if [ -e "$ROOTFS/etc/init.d/$svc" ]; then
      ln -sf "/etc/init.d/$svc" "$ROOTFS/etc/runlevels/sysinit/$svc"
    fi
  done

  for svc in osclock swclock-offset-boot modules sysctl hostname bootmisc logbookd tmpfs msm-firmware-loader-unpack; do
    if [ -e "$ROOTFS/etc/init.d/$svc" ]; then
      ln -sf "/etc/init.d/$svc" "$ROOTFS/etc/runlevels/boot/$svc"
    fi
  done

  for svc in \
    udev-postmount \
    rmtfs \
    msm-modem-uim-selection \
    msm8909-modem-wwan-port \
    q6voiced \
    modemmanager \
    networkmanager \
    wpa_supplicant \
    bluetooth \
    dbus \
    cgroups \
    haveged \
    chronyd \
    rfkill \
    sleep-inhibitor \
    sshd \
    swapfile \
    local \
    postmarketos-zram-swap \
    nftables \
    iio-sensor-proxy \
    hkdm \
    kill-plymouth; do
    if [ -e "$ROOTFS/etc/init.d/$svc" ]; then
      ln -sf "/etc/init.d/$svc" "$ROOTFS/etc/runlevels/default/$svc"
    fi
  done

  for svc in mount-ro killprocs savecache swclock-offset-shutdown; do
    if [ -e "$ROOTFS/etc/init.d/$svc" ]; then
      ln -sf "/etc/init.d/$svc" "$ROOTFS/etc/runlevels/shutdown/$svc"
    fi
  done

  if [ "$UI_TYPE" = "sxmo" ]; then
    echo "  -> Applying Sxmo UI & User:Ybon keypad/RAM/display tweaks..."
    for svc in seatd tinydm; do
      if [ -e "$ROOTFS/etc/init.d/$svc" ]; then
        ln -sf "/etc/init.d/$svc" "$ROOTFS/etc/runlevels/default/$svc"
      fi
    done

    mkdir -p "$ROOTFS/var/lib/tinydm"
    ln -sf /usr/share/wayland-sessions/swmo.desktop "$ROOTFS/var/lib/tinydm/default-session.desktop"

    cat > "$ROOTFS/usr/bin/sxmo_deviceprofile_nokia,argon.sh" <<EOF
#!/bin/sh
export SXMO_WM=sway
export SXMO_ROTATION=normal

# Scaling factor for 240x320 QVGA screen
export SXMO_SWAY_SCALE=0.7

# Physical button mappings as determined via evtest
export SXMO_POWER_BUTTON="0:0:pm8916_pon"
export SXMO_VOLUME_UP_BUTTON="0:103:KEY_UP"
export SXMO_VOLUME_DOWN_BUTTON="0:108:KEY_DOWN"
export SXMO_MENU_LEFT_BUTTON="0:139:KEY_MENU"
export SXMO_MENU_RIGHT_BUTTON="0:158:KEY_BACK"
export SXMO_ENTER_BUTTON="0:352:KEY_OK"

# Nokia 8110 4G has a physical keypad and no touchscreen
export SXMO_NO_VIRTUAL_KEYBOARD=1
export SXMO_DISABLE_LEDS=1
export KEYBOARD_ARGS="-H 1 -L 1"
export WLR_RENDERER=pixman
export WLR_NO_HARDWARE_CURSORS=1
export NO_AT_BRIDGE=1
export GTK_A11Y=none
export GTK_USE_PORTAL=0
export SXMO_MENU=wofi
export SXMO_BEMENU_PORTRAIT_LINES=11
export SXMO_BEMENU_LANDSCAPE_LINES=8
EOF
    chmod 755 "$ROOTFS/usr/bin/sxmo_deviceprofile_nokia,argon.sh"

    cat > "$ROOTFS/usr/bin/sxmo_inputhandler.sh" <<'EOF'
#!/bin/sh
exec sxmo_hook_inputhandler.sh "$@"
EOF
    chmod 755 "$ROOTFS/usr/bin/sxmo_inputhandler.sh"

    sed -i 's|--input-device="$pwr" ||g' "$ROOTFS/usr/bin/sxmo_swayinitconf.sh"

    if ! grep -q "superctl start fcitx5" "$ROOTFS/usr/share/sxmo/default_hooks/sxmo_hook_start.sh"; then
      sed -i 's|superctl start bonsaid|[ "$(command -v bonsaid)" ] \&\& superctl start bonsaid\n\t\t[ "$(command -v fcitx5)" ] \&\& superctl start fcitx5|' \
        "$ROOTFS/usr/share/sxmo/default_hooks/sxmo_hook_start.sh"
    fi

    mkdir -p "$ROOTFS/etc/skel/.cache/sxmo" "$ROOTFS/home/user/.cache/sxmo"
    touch "$ROOTFS/etc/skel/.cache/sxmo/sxmo.nogesture" "$ROOTFS/home/user/.cache/sxmo/sxmo.nogesture"

    cat > "$ROOTFS/usr/share/sxmo/appcfg/wofi.config" <<EOF
# Space to setup configversion
# configversion: eb37989d3b93bf46e0df32db4ffd833e
matching=multi-contains
width=240
height=320
halign=fill
orientation=vertical
content_halign=fill
insensitive=true
prompt=
show=dmenu
no_actions=true
EOF

    cat > "$ROOTFS/usr/share/sxmo/appcfg/wofi.css" <<EOF
/* Space to setup configversion */
/* configversion: 2ecca20b604bc0b37d13aba5a0fabc07 */
* {
  font-family: monospace;
  font-size: 12px;
}

window {
  margin: 0px;
  border: 1px solid #005577;
  background-color: #222222;
}

#input {
  margin: 0px;
  border-radius: 0px;
  border: 1px solid #005577;
  background-color: #444444;
  color: white;
}

#inner-box {
  margin: 0px;
}

#outer-box {
  margin: 1px;
}

#scroll {
  margin: 0px;
  color: yellow;
}

#text {
  margin: 1px;
  color: white;
}

#entry {
  padding: 0;
  margin: 0;
}

#entry:selected {
  background-color: #005577;
}
EOF

    cat > "$ROOTFS/usr/share/sxmo/appcfg/conky.conf" <<EOF
-- Space to setup configversion
-- configversion: 19ec5c7f9e83bc6534d657f6180feba2
conky.config = {
  out_to_x = false,
  out_to_wayland = true,
  alignment = 'middle_middle',
  own_window_argb_value = 0,
  own_window_type = 'desktop',
  default_shade_color = 'grey',
  draw_shades = true,
  font = 'Sxmo:size=11',
  use_xft = true,
  update_interval = 15,
}

conky.text = [[
\${alignc}\${font Sxmo:size=36:style=Bold}\${time %H}\${font Sxmo:size=36}:\${time %M}\${font}

\${alignc}\${font Sxmo:size=14}\${time %a %d %b %Y}\${font}
]]
EOF

    python3 - "$ROOTFS/usr/share/sxmo/appcfg/sway_template" <<'PYEOF'
import sys
path = sys.argv[1]
txt = open(path).read()

if "xwayland disable" not in txt:
  txt = txt.replace('font "Sxmo 11"\n', 'font "Sxmo 11"\nxwayland disable\n', 1)

old_menu = """mode "menu" {
    # This is slower than remapping the keys using a keymap, but we don't need
    # to know the device names, so use it as a fallback so this functionality
    # keeps working if a deviceprofile doesn't exist.
    # TODO: the correct way to do this is to implement custom keybindings in
    # bemenu & wofi
    bindsym XF86PowerOff exec wtype -k Return
    bindsym XF86AudioRaiseVolume exec wtype -k Up
    bindsym XF86AudioLowerVolume exec wtype -k Down
}"""

new_menu = """mode "menu" {
    bindsym XF86PowerOff exec sxmo_dmenu.sh close
    bindsym XF86PickupPhone exec wtype -k Return
    bindsym XF86KbdLcdMenu1 exec sxmo_dmenu.sh close
    bindsym XF86KbdLcdMenu2 exec sxmo_dmenu.sh close
    bindsym XF86NumericStar exec wtype "*"
    bindsym XF86NumericPound exec fcitx5-remote -t
    bindsym XF86AudioRaiseVolume exec wtype -k Up
    bindsym XF86AudioLowerVolume exec wtype -k Down
}"""
if old_menu in txt:
  txt = txt.replace(old_menu, new_menu, 1)

old_lid = """bindswitch lid:on exec sxmo_wm.sh display off
bindswitch lid:off exec sxmo_wm.sh display on"""

new_lid = """bindswitch lid:on exec sh -c "brightnessctl -d white:kbd_backlight set 0; sxmo_wm.sh display off"
bindswitch lid:off exec sh -c "brightnessctl -d white:kbd_backlight set 255; sxmo_wm.sh display on"

# Nokia 8110 4G hardware keypad bindings (User:Ybon)
bindsym --no-repeat XF86KbdLcdMenu1 exec sxmo_hook_inputhandler.sh menuleft_one
bindsym --no-repeat XF86KbdLcdMenu2 exec sxmo_hook_inputhandler.sh menuright_one
bindsym --no-repeat XF86PickupPhone exec sxmo_hook_inputhandler.sh enter_one
bindsym --no-repeat XF86NumericStar exec wtype "*"
bindsym --no-repeat XF86NumericPound exec sh -c 'fcitx5-remote -t && sxmo_notify_user.sh -t 1500 "Input: $(fcitx5-remote -n)"'"""

if old_lid in txt:
  txt = txt.replace(old_lid, new_lid, 1)
open(path, "w").write(txt)
PYEOF

    python3 - "$ROOTFS/usr/share/sxmo/default_hooks/sxmo_hook_inputhandler.sh" <<'PYEOF'
import sys
path = sys.argv[1]
txt = open(path).read()

old_power = """	"powerbutton_one")
		if echo "$WMCLASS" | grep -i "megapixels"; then
			sxmo_type.sh -k space
		else
			sxmo_state.sh click
		fi
		exit 0
		;;"""

new_power = """	"menuright"*|"menuright_one")
		if sxmo_dmenu.sh isopen; then
			sxmo_dmenu.sh close
		else
			sxmo_appmenu.sh "$WMCLASS" "$WMNAME"
		fi
		exit 0
		;;
	"menuleft"*|"menuleft_one")
		if sxmo_dmenu.sh isopen; then
			sxmo_dmenu.sh close
		else
			sxmo_appmenu.sh sys
		fi
		exit 0
		;;
	"enter_one")
		if pidof -q sxmo_RepeatRing.sh; then
			sxmo_hook_pickup.sh
		else
			sxmo_appmenu.sh "$WMCLASS" "$WMNAME"
		fi
		exit 0
		;;
	"powerbutton_one")
		if echo "$WMCLASS" | grep -i "megapixels"; then
			sxmo_type.sh -k space
		else
			sxmo_appmenu.sh power
		fi
		exit 0
		;;"""

if "menuright_one" not in txt:
  assert old_power in txt, "old_power block not found in sxmo_hook_inputhandler.sh"
  txt = txt.replace(old_power, new_power, 1)
open(path, "w").write(txt)
PYEOF

    for target_home in "$ROOTFS/etc/skel" "$ROOTFS/home/user"; do
      mkdir -p \
        "$target_home/.config/sxmo/hooks" \
        "$target_home/.config/fontconfig/conf.d"
      cp "$ROOTFS/usr/share/sxmo/appcfg/profile_template" "$target_home/.config/sxmo/profile"
      cp "$ROOTFS/usr/share/sxmo/appcfg/xinit_template" "$target_home/.config/sxmo/xinit"
      cp "$ROOTFS/usr/share/sxmo/appcfg/conky.conf" "$target_home/.config/sxmo/conky.conf"
      cp "$ROOTFS/usr/share/sxmo/appcfg/Xresources" "$target_home/.Xresources"
      cp "$ROOTFS/usr/share/sxmo/appcfg/fontconfig.conf" "$target_home/.config/fontconfig/conf.d/50-sxmo.conf"
      cp "$ROOTFS/usr/share/sxmo/default_hooks/sxmo_hook_inputhandler.sh" "$target_home/.config/sxmo/hooks/sxmo_hook_inputhandler.sh"
      chmod 755 "$target_home/.config/sxmo/hooks/sxmo_hook_inputhandler.sh" "$target_home/.config/sxmo/xinit" "$target_home/.config/sxmo/profile"
    done
  fi

  sed -i "/^P:sxmo-utils-common$/,/^$/ s/ vim//" "$ROOTFS/lib/apk/db/installed" 2>/dev/null || true
  apk --root "$ROOTFS" --arch armv7 del --no-scripts vim vim-common nftables-vim protobuf-vim 2>/dev/null || true

  if [ -x /bananarchy/scripts/apply-bananarchy-overlay.sh ]; then
    /bananarchy/scripts/apply-bananarchy-overlay.sh "$ROOTFS" nokia-argon
    depmod -b "$ROOTFS" 6.12.1-msm8916
  fi

  chown -R 10000:10000 "$ROOTFS/home/user"

  echo "  -> Generating initramfs, boot.img, and extlinux.conf via mkinitfs..."
  chroot "$ROOTFS" /tmp/hostbin/busybox.static sh -e -c '
    export PATH="/tmp/hostbin:/usr/sbin:/usr/bin:/sbin:/bin"
    export LD_LIBRARY_PATH="/tmp/hostbin:/lib:/usr/lib"
    /tmp/hostbin/mkinitfs
  '

  echo "  -> Restoring armv7 /bin/sh symlink and cleaning up temporary hostbin..."
  ln -sf /bin/busybox "$ROOTFS/bin/sh"
  rm -rf "$ROOTFS/tmp/hostbin" "$ROOTFS/lib/ld-musl-x86_64.so.1" "$ROOTFS/proc/1"

  echo "  -> Verifying $ROOTFS boot artifacts:"
  ls -lh "$ROOTFS/boot/initramfs" "$ROOTFS/boot/boot.img" "$ROOTFS/boot/extlinux/extlinux.conf"
  cat "$ROOTFS/boot/extlinux/extlinux.conf"
}

BOOT_UUID_SXMO="a1b2c3d4-8110-4000-8000-000000000001"
ROOT_UUID_SXMO="a1b2c3d4-8110-4000-8000-000000000002"

configure_common_rootfs "$WORK/rootfs-sxmo" "sxmo" "$BOOT_UUID_SXMO" "$ROOT_UUID_SXMO"

echo "BANANARCHY ROOTFS CONFIGURATION COMPLETED SUCCESSFULLY!"
