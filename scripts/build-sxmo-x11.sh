#!/bin/sh
# Cross-compile Bananarchy X11 UI components for armv7 (Cortex-A7 + NEON-VFPv4):
# - sxmo-st (patched with HarfBuzz ligatures for Fira Code + XIM)
# - sxmo-dmenu (patched with XIM + 240x320 vertical search bar + Fira Code)
# - sxmo-dwm (configured for 240x320 + Fira Code + Pango statusbar + Nokia keypad)
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

  echo "[1/5] Installing host cross-compilation toolchain..."
  apk add --no-cache \
    build-base \
    gcc-armv7 \
    g++-armv7 \
    binutils-armv7 \
    pkgconf >/dev/null

  echo "[2/5] Preparing armv7 sysroot (X11, Xft, HarfBuzz, Pango, FreeType, Fontconfig)..."
  SYSROOT="/tmp/armv7-sysroot"
  mkdir -p "$SYSROOT/etc/apk/keys"
  cp /etc/apk/repositories "$SYSROOT/etc/apk/repositories"
  cp /etc/apk/keys/* "$SYSROOT/etc/apk/keys/"
  apk --root "$SYSROOT" --initdb --arch armv7 add --no-scripts \
    alpine-baselayout \
    musl-dev \
    linux-headers \
    alsa-lib-dev \
    libx11-dev \
    libxfixes-dev \
    libxft-dev \
    libxinerama-dev \
    libxext-dev \
    libxrender-dev \
    libxcb-dev \
    xcb-util-dev \
    fontconfig-dev \
    freetype-dev \
    harfbuzz-dev \
    pango-dev \
    glib-dev \
    cairo-dev \
    libjpeg-turbo-dev \
    libpng-dev \
    openssl-dev \
    opus-dev \
    gsm-dev \
    toxcore-dev \
    gtk+3.0-dev \
    webkit2gtk-4.1-dev \
    libwpe-dev \
    libwpebackend-fdo-dev >/dev/null

  export PKG_CONFIG_SYSROOT_DIR="$SYSROOT"
  export PKG_CONFIG_LIBDIR="$SYSROOT/usr/lib/pkgconfig:$SYSROOT/usr/share/pkgconfig"
  CC="armv7-alpine-linux-musleabihf-gcc --sysroot=$SYSROOT"
  CXX="armv7-alpine-linux-musleabihf-g++ --sysroot=$SYSROOT"
  STRIP="armv7-alpine-linux-musleabihf-strip"
  OPT_CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -fno-plt -fomit-frame-pointer"

  mkdir -p /bananarchy/prebuilt/sxmo-x11/usr/bin

  echo "[3/5] Compiling patched sxmo-st (with HarfBuzz Fira Code ligatures + XIM)..."
  cd /bananarchy/src/sxmo-st
  cp -f config.def.h config.h
  ST_INCS="$(pkgconf --cflags x11 xft fontconfig freetype2 harfbuzz)"
  ST_LIBS="$(pkgconf --libs x11 xft fontconfig freetype2 harfbuzz) -lm -lrt -lutil"
  $CC $OPT_CFLAGS -DVERSION=\"0.8.4.1-bananarchy\" -D_XOPEN_SOURCE=600 $ST_INCS \
    st.c x.c hb.c $ST_LIBS -o /bananarchy/prebuilt/sxmo-x11/usr/bin/st
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/st

  echo "[4/6] Compiling patched sxmo-dmenu plugin (dmenu.so with XIM + 240x320 vertical layout + Fira Code)..."
  mkdir -p /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins
  cd /bananarchy/src/sxmo-dmenu
  cp -f config.def.h config.h
  DMENU_INCS="$(pkgconf --cflags x11 xfixes xinerama xft fontconfig freetype2)"
  DMENU_LIBS="$(pkgconf --libs x11 xfixes xinerama xft fontconfig freetype2)"
  $CC $OPT_CFLAGS -fPIC -shared -D_DEFAULT_SOURCE -D_BSD_SOURCE -D_XOPEN_SOURCE=700 -D_POSIX_C_SOURCE=200809L -DVERSION=\"5.0.14-bananarchy\" -DXINERAMA $DMENU_INCS \
    dmenu.c drw.c util.c $DMENU_LIBS -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so
  $CC $OPT_CFLAGS -D_DEFAULT_SOURCE -DVERSION=\"5.0.14-bananarchy\" \
    stest.c -o /bananarchy/prebuilt/sxmo-x11/usr/bin/stest
  cp -f dmenu_path dmenu_run /bananarchy/prebuilt/sxmo-x11/usr/bin/
  chmod 755 /bananarchy/prebuilt/sxmo-x11/usr/bin/dmenu_path /bananarchy/prebuilt/sxmo-x11/usr/bin/dmenu_run
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dmenu.so /bananarchy/prebuilt/sxmo-x11/usr/bin/stest

  echo "[5/6] Compiling patched sxmo-dwm (240x320 + Fira Code + Pango statusbar + Plugin API)..."
  cd /bananarchy/src/sxmo-dwm
  cp -f config.def.h config.h
  DWM_INCS="$(pkgconf --cflags x11 xfixes xinerama xft fontconfig freetype2 pango pangoxft xcb xcb-res)"
  DWM_LIBS="$(pkgconf --libs x11 xfixes xinerama xft fontconfig freetype2 pango pangoxft xcb xcb-res) -lX11-xcb -lrt -ldl"
  $CC $OPT_CFLAGS -std=c99 -Wno-deprecated-declarations -D_DEFAULT_SOURCE -D_BSD_SOURCE -D_POSIX_C_SOURCE=2 -DVERSION=\"6.3.1-bananarchy\" -DXINERAMA -DNOBONSAI $DWM_INCS \
    dwm.c drw.c util.c $DWM_LIBS -o /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm

  echo "[6/7] Compiling NeoUI SDK shared library (libneoui.so + /usr/include/neoui.h)..."
  mkdir -p /bananarchy/prebuilt/sxmo-x11/usr/lib /bananarchy/prebuilt/sxmo-x11/usr/include
  PLUG_INCS="$(pkgconf --cflags x11 xfixes xft fontconfig freetype2 pango pangoxft alsa libjpeg libpng) -I/bananarchy/src/neoui"
  PLUG_LIBS="$(pkgconf --libs x11 xfixes xft fontconfig freetype2 pango pangoxft alsa)"
  MEDIA_LIBS="$(pkgconf --libs libjpeg libpng)"
  CXX_PLUG_FLAGS="$OPT_CFLAGS -fPIC -shared -std=c++20 -Wall -Wextra -Wno-psabi"

  $CXX $CXX_PLUG_FLAGS -Wl,-soname,libneoui.so $PLUG_INCS \
    /bananarchy/src/neoui/neoui.cpp $PLUG_LIBS $MEDIA_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so
  cp -f /bananarchy/src/neoui/neoui.h /bananarchy/prebuilt/sxmo-x11/usr/include/neoui.h
  cp -f /bananarchy/src/neoui/neoui.h "$SYSROOT/usr/include/neoui.h"
  cp -f /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so "$SYSROOT/usr/lib/libneoui.so"
  NEOUI_LIBS="-L/bananarchy/prebuilt/sxmo-x11/usr/lib -lneoui"

  echo "[7/7] Compiling DWM Plugins (linked against libneoui.so) and dwm-plugin runner..."
  mkdir -p /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins
  pids=""

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/xdrawer/xdrawer.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/xdrawer.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/dialer_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dialer.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/calllog_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/calllog.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/callscreen_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/callscreen.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/contacts_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/contacts.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/sms_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/sms.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/files_plugin.cpp $NEOUI_LIBS $PLUG_LIBS $MEDIA_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/files.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/camera_plugin.cpp $NEOUI_LIBS $PLUG_LIBS $MEDIA_LIBS -lpthread \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/camera.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/themes_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/themes.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/modem_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/modem.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/networks_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/networks.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/ringtones_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/ringtones.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/wallpapers_plugin.cpp $NEOUI_LIBS $PLUG_LIBS $MEDIA_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/wallpapers.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/music_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/music.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/locker_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -lcrypt \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/locker.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/sip_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -lssl -lcrypto -lopus -lgsm -lpthread \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/sip.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/email_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -lssl -lcrypto \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/email.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/irc_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -lssl -lcrypto \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/irc.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/tox_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -ltoxcore -lpthread \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/tox.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/smallweb_plugin.cpp $NEOUI_LIBS $PLUG_LIBS -lssl -lcrypto \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/smallweb.so &
  pids="$pids $!"

  WEBARCHY_INCS="$(pkgconf --cflags webkit2gtk-4.1 gtk+-3.0 wpe-1.0 wpebackend-fdo-1.0)"
  WEBARCHY_LIBS="$(pkgconf --libs webkit2gtk-4.1 gtk+-3.0 wpe-1.0 wpebackend-fdo-1.0)"
  $CXX $CXX_PLUG_FLAGS $PLUG_INCS $WEBARCHY_INCS \
    /bananarchy/src/dwm-plugins/webarchy_plugin.cpp $NEOUI_LIBS $PLUG_LIBS $WEBARCHY_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/webarchy.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/gps_plugin.cpp $NEOUI_LIBS $PLUG_LIBS $MEDIA_LIBS -lssl -lcrypto -lpthread \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/gps.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/passcode_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/passcode.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/engmenu_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/engmenu.so &
  pids="$pids $!"

  $CXX $CXX_PLUG_FLAGS $PLUG_INCS \
    /bananarchy/src/dwm-plugins/privacy_plugin.cpp $NEOUI_LIBS $PLUG_LIBS \
    -o /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/privacy.so &
  pids="$pids $!"

  for pid in $pids; do
    wait "$pid"
  done

  $STRIP --strip-unneeded "$SYSROOT"/usr/lib/libtoxcore.so.*.* || true
  cp -a "$SYSROOT"/usr/lib/libtoxcore.so* /bananarchy/prebuilt/sxmo-x11/usr/lib/

  $STRIP --strip-unneeded \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/xdrawer.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/dialer.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/calllog.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/callscreen.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/contacts.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/sms.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/files.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/camera.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/themes.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/modem.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/networks.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/ringtones.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/wallpapers.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/music.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/locker.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/sip.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/email.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/irc.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/tox.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/smallweb.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/webarchy.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/gps.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/passcode.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/engmenu.so \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/privacy.so

  $CC $OPT_CFLAGS -std=c99 -D_DEFAULT_SOURCE -Wall -Wextra \
    /bananarchy/src/dwm-plugins/dwm_plugin_runner.c -ldl \
    -o /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm-plugin
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm-plugin

  $CC $OPT_CFLAGS -std=c99 -D_DEFAULT_SOURCE -Wall -Wextra \
    /bananarchy/src/dwm-plugins/sxmo_vibrate.c \
    -o /bananarchy/prebuilt/sxmo-x11/usr/bin/sxmo_vibrate
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/sxmo_vibrate

  $CC $OPT_CFLAGS -std=c99 -D_DEFAULT_SOURCE -Wall -Wextra \
    /bananarchy/src/dwm-plugins/verify_passcode.c -lcrypt \
    -o /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-verify-passcode
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-verify-passcode

  $CC $OPT_CFLAGS -std=c99 -D_DEFAULT_SOURCE -Wall -Wextra \
    /bananarchy/src/dwm-plugins/engmenu_exec.c \
    -o /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-engmenu-exec
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-engmenu-exec
  chmod 4755 /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-engmenu-exec

  $CXX $OPT_CFLAGS -std=c++20 -Wall -Wextra \
    /bananarchy/src/dwm-plugins/bananarchy_melody.cpp -lm \
    -o /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-melody
  $STRIP --strip-unneeded /bananarchy/prebuilt/sxmo-x11/usr/bin/bananarchy-melody

  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/dmenu
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xdmenu
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xlocker
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xdrawer
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/XDrawer
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xdialer
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xcalllog
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xcallscreen
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xcontacts
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xsms
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xfiles
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xcamera
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xthemes
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xmodem
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xnetworks
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xbluetooth
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xringtones
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xwallpapers
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xbackground
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xmusic
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xplayer
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xsip
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xemail
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xmail
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xirc
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xtox
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xkopher
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/kopher
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xsmallweb
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xwebarchy
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/webarchy
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xgps
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xmaps
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xnav
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xpasscode
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xengmenu
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/xprivacy
  ln -sf dwm-plugin /bananarchy/prebuilt/sxmo-x11/usr/bin/privacy

  mkdir -p /bananarchy/overlay/rootfs/usr/bin /bananarchy/overlay/rootfs/usr/lib/dwm/plugins /bananarchy/overlay/rootfs/usr/include
  cp -a /bananarchy/prebuilt/sxmo-x11/usr/bin/. /bananarchy/overlay/rootfs/usr/bin/
  cp -f /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so /bananarchy/overlay/rootfs/usr/lib/libneoui.so
  cp -a /bananarchy/prebuilt/sxmo-x11/usr/lib/libtoxcore.so* /bananarchy/overlay/rootfs/usr/lib/
  cp -f /bananarchy/prebuilt/sxmo-x11/usr/include/neoui.h /bananarchy/overlay/rootfs/usr/include/neoui.h
  cp -a /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/. /bananarchy/overlay/rootfs/usr/lib/dwm/plugins/
  if [ -d /target-rootfs/usr/bin ]; then
    mkdir -p /target-rootfs/usr/lib/dwm/plugins /target-rootfs/usr/include
    cp -a /bananarchy/prebuilt/sxmo-x11/usr/bin/. /target-rootfs/usr/bin/
    cp -f /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so /target-rootfs/usr/lib/libneoui.so
    cp -a /bananarchy/prebuilt/sxmo-x11/usr/lib/libtoxcore.so* /target-rootfs/usr/lib/
    cp -f /bananarchy/prebuilt/sxmo-x11/usr/include/neoui.h /target-rootfs/usr/include/neoui.h
    cp -a /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/. /target-rootfs/usr/lib/dwm/plugins/
  fi

  chown -R "$HOST_UID:$HOST_GID" \
    /bananarchy/prebuilt/sxmo-x11 \
    /bananarchy/overlay/rootfs/usr/bin \
    /bananarchy/overlay/rootfs/usr/lib \
    /bananarchy/overlay/rootfs/usr/include \
    /bananarchy/src/neoui \
    /bananarchy/src/sxmo-st \
    /bananarchy/src/sxmo-dmenu \
    /bananarchy/src/sxmo-dwm \
    /bananarchy/src/xdrawer \
    /bananarchy/src/dwm-plugins
  ls -lh /bananarchy/prebuilt/sxmo-x11/usr/bin/st \
    /bananarchy/prebuilt/sxmo-x11/usr/bin/dmenu \
    /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm \
    /bananarchy/prebuilt/sxmo-x11/usr/bin/dwm-plugin \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/libneoui.so \
    /bananarchy/prebuilt/sxmo-x11/usr/include/neoui.h \
    /bananarchy/prebuilt/sxmo-x11/usr/lib/dwm/plugins/*.so
'
