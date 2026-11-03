#!/usr/bin/env python3
"""Generate Bananarchy OS artwork assets from artwork/icon-large.png."""

import math
import os
import sys
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ARTWORK_DIR = os.path.join(BASE_DIR, "artwork")
SOURCE_ICON = os.path.join(ARTWORK_DIR, "icon-large.png")


def load_font(size, bold=True):
  candidates = [
    "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/gnu-free/FreeMonoBold.ttf",
  ]
  for path in candidates:
    if os.path.isfile(path):
      try:
        return ImageFont.truetype(path, size)
      except Exception:
        pass
  return ImageFont.load_default()


def make_transparent_icon(src_rgb):
  """Create an RGBA version with the outer black background keyed to alpha."""
  w, h = src_rgb.size
  cx, cy = w / 2.0, h / 2.0
  # The circular emblem spans radius ~495px on a 1024x1024 canvas
  outer_r = min(w, h) * 0.486
  feather = min(w, h) * 0.012

  rgba = src_rgb.convert("RGBA")
  px = rgba.load()
  for y in range(h):
    dy = y - cy
    for x in range(w):
      dx = x - cx
      dist = math.hypot(dx, dy)
      r, g, b, _ = px[x, y]
      lum = max(r, g, b)
      if dist > outer_r + feather and lum < 18:
        px[x, y] = (0, 0, 0, 0)
      elif dist > outer_r - feather and lum < 28:
        # Smooth alpha ramp near outer ring
        t = (outer_r + feather - dist) / (2.0 * feather)
        t = max(0.0, min(1.0, t))
        alpha = int(round(255.0 * max(t, min(1.0, (lum - 2) / 26.0))))
        px[x, y] = (r, g, b, alpha)
  return rgba


def make_canvas(width, height, bg=(1, 1, 1)):
  return Image.new("RGB", (width, height), bg)


def paste_centered(canvas, icon_rgb, box_size, center_x, center_y):
  resized = icon_rgb.resize((box_size, box_size), Image.Resampling.LANCZOS)
  x0 = int(round(center_x - box_size / 2.0))
  y0 = int(round(center_y - box_size / 2.0))
  canvas.paste(resized, (x0, y0))
  return canvas


def main():
  if not os.path.isfile(SOURCE_ICON):
    print(f"Error: Source icon not found at {SOURCE_ICON}", file=sys.stderr)
    sys.exit(1)

  os.makedirs(ARTWORK_DIR, exist_ok=True)
  src = Image.open(SOURCE_ICON).convert("RGB")
  src_rgba = make_transparent_icon(src)

  # 1. Standard square RGB icons
  square_sizes = [512, 256, 192, 128, 64, 48, 32, 24, 16]
  for sz in square_sizes:
    out_path = os.path.join(ARTWORK_DIR, f"icon-{sz}.png")
    src.resize((sz, sz), Image.Resampling.LANCZOS).save(out_path, "PNG", optimize=True)

  # 2. Transparent RGBA icons for freedesktop / UI themes
  for sz in [512, 256, 128, 64, 48, 32]:
    out_path = os.path.join(ARTWORK_DIR, f"icon-transparent-{sz}.png")
    src_rgba.resize((sz, sz), Image.Resampling.LANCZOS).save(out_path, "PNG", optimize=True)

  # 3. Multi-size favicon.ico
  ico_path = os.path.join(ARTWORK_DIR, "favicon.ico")
  src_rgba.save(
    ico_path,
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
  )

  # 4. Sxmo 240x320 portrait wallpaper (emblem placed below Conky clock area)
  # Top bar is 24px (0..24), Conky clock occupies y=28..118, emblem sits in y=126..314
  wp_portrait = make_canvas(240, 320, (1, 1, 1))
  paste_centered(wp_portrait, src, 188, 120, 220)
  wp_portrait.save(os.path.join(ARTWORK_DIR, "wallpaper-240x320.png"), "PNG", optimize=True)
  wp_portrait.save(os.path.join(ARTWORK_DIR, "wallpaper-240x320.jpg"), "JPEG", quality=95, subsampling=0)

  # 5. Centered 240x320 portrait wallpaper
  wp_centered = make_canvas(240, 320, (1, 1, 1))
  paste_centered(wp_centered, src, 216, 120, 160)
  wp_centered.save(os.path.join(ARTWORK_DIR, "wallpaper-240x320-centered.png"), "PNG", optimize=True)
  wp_centered.save(os.path.join(ARTWORK_DIR, "wallpaper-240x320-centered.jpg"), "JPEG", quality=95, subsampling=0)

  # 6. Landscape 320x240 QVGA wallpaper
  wp_landscape = make_canvas(320, 240, (1, 1, 1))
  paste_centered(wp_landscape, src, 208, 160, 120)
  wp_landscape.save(os.path.join(ARTWORK_DIR, "wallpaper-320x240.png"), "PNG", optimize=True)
  wp_landscape.save(os.path.join(ARTWORK_DIR, "wallpaper-320x240.jpg"), "JPEG", quality=95, subsampling=0)

  # 7. Square 240x240 wallpaper (Nokia 2720 Flip external screen)
  wp_square = make_canvas(240, 240, (1, 1, 1))
  paste_centered(wp_square, src, 216, 120, 120)
  wp_square.save(os.path.join(ARTWORK_DIR, "wallpaper-240x240.png"), "PNG", optimize=True)

  # 8. Boot splash 240x320 (PNG + raw PPM for framebuffer/initramfs)
  splash = make_canvas(240, 320, (1, 1, 1))
  paste_centered(splash, src, 196, 120, 134)
  draw = ImageDraw.Draw(splash)
  font_title = load_font(20, bold=True)
  font_sub = load_font(11, bold=False)

  title_text = "BANANARCHY"
  tb = draw.textbbox((0, 0), title_text, font=font_title)
  tw = tb[2] - tb[0]
  draw.text(((240 - tw) // 2, 248), title_text, fill=(255, 214, 10), font=font_title)

  sub_text = "MSM8909 LINUX // SXMO"
  sb = draw.textbbox((0, 0), sub_text, font=font_sub)
  sw = sb[2] - sb[0]
  draw.text(((240 - sw) // 2, 276), sub_text, fill=(180, 180, 180), font=font_sub)

  splash.save(os.path.join(ARTWORK_DIR, "splash-240x320.png"), "PNG", optimize=True)
  splash.save(os.path.join(ARTWORK_DIR, "splash-240x320.ppm"), "PPM")

  # 9. Plymouth rotating boot splash layers (stationary central A + rotating outer ring)
  import numpy as np
  from scipy import ndimage
  src_rgba = src.convert("RGBA")
  arr = np.array(src_rgba)
  rgb = arr[:, :, :3].astype(np.float32)
  r_ch, g_ch, b_ch = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
  lum = rgb.max(axis=2)

  # Separate central A bananas from outer banana circle via distance transform
  is_yellow = (r_ch > 140) & (g_ch > 90) & (b_ch < 90)
  labeled, num = ndimage.label(is_yellow)
  center_yellow = np.zeros_like(is_yellow)
  outer_yellow = np.zeros_like(is_yellow)
  for i in range(1, num + 1):
    pts = np.argwhere(labeled == i)
    if len(pts) < 50:
      continue
    ymin, xmin = pts.min(axis=0)
    ymax, xmax = pts.max(axis=0)
    yc, xc = pts.mean(axis=0)
    rc = np.hypot(xc - 512.0, yc - 512.0)
    if (xmax - xmin) > 700 or rc > 280.0:
      outer_yellow |= (labeled == i)
    else:
      center_yellow |= (labeled == i)

  dist_center = ndimage.distance_transform_edt(~center_yellow)
  dist_outer = ndimage.distance_transform_edt(~outer_yellow)
  y_grid, x_grid = np.ogrid[:1024, :1024]
  rad = np.hypot(x_grid - 512.0, y_grid - 512.0)
  fg_alpha = np.clip((lum - 10.0) / 28.0, 0.0, 1.0)

  in_inner_core = np.clip((290.0 - rad) / 4.0, 0.0, 1.0) * np.clip((718.0 - y_grid) / 4.0, 0.0, 1.0)
  near_center_tip = (
    np.clip((dist_outer - dist_center + 4.0) / 4.0, 0.0, 1.0)
    * np.clip((27.0 - dist_center) / 3.0, 0.0, 1.0)
    * np.clip((y_grid - 91.0) / 2.5, 0.0, 1.0)
    * np.clip((722.0 - y_grid) / 3.0, 0.0, 1.0)
  )
  center_weight = np.maximum(in_inner_core, near_center_tip)
  ring_weight = 1.0 - center_weight

  center_rgba = arr.copy()
  center_rgba[:, :, 3] = (fg_alpha * center_weight * 255.0).astype(np.uint8)
  ring_rgba = arr.copy()
  ring_rgba[:, :, 3] = (fg_alpha * ring_weight * 255.0).astype(np.uint8)

  err_rgba = arr.copy()
  err_rgba[:, :, 0] = np.clip(rgb[:, :, 0] * 1.1, 0, 255).astype(np.uint8)
  err_rgba[:, :, 1] = (rgb[:, :, 1] * 0.22).astype(np.uint8)
  err_rgba[:, :, 2] = (rgb[:, :, 2] * 0.18).astype(np.uint8)
  err_rgba[:, :, 3] = (fg_alpha * 255.0).astype(np.uint8)

  ply_size = 168
  ply_center_img = Image.fromarray(center_rgba).resize((ply_size, ply_size), Image.Resampling.LANCZOS)
  ply_ring_img = Image.fromarray(ring_rgba).resize((ply_size, ply_size), Image.Resampling.LANCZOS)
  ply_error_img = Image.fromarray(err_rgba).resize((ply_size, ply_size), Image.Resampling.LANCZOS)

  # Title image for Plymouth splash ("BANANARCHY")
  ply_title_img = Image.new("RGBA", (180, 28), (0, 0, 0, 0))
  ptdraw = ImageDraw.Draw(ply_title_img)
  ptb = ptdraw.textbbox((0, 0), title_text, font=font_title)
  ptw = ptb[2] - ptb[0]
  ptdraw.text(((180 - ptw) // 2, 2), title_text, fill=(255, 214, 10, 255), font=font_title)

  ply_center_img.save(os.path.join(ARTWORK_DIR, "plymouth-center.png"), "PNG", optimize=True)
  ply_ring_img.save(os.path.join(ARTWORK_DIR, "plymouth-ring.png"), "PNG", optimize=True)
  ply_error_img.save(os.path.join(ARTWORK_DIR, "plymouth-error.png"), "PNG", optimize=True)
  ply_title_img.save(os.path.join(ARTWORK_DIR, "plymouth-title.png"), "PNG", optimize=True)

  for root_base in (
    os.path.join(BASE_DIR, "overlay", "rootfs"),
    os.path.join(BASE_DIR, "rootfs"),
  ):
    if not os.path.isdir(root_base):
      continue
    ply_dir = os.path.join(root_base, "usr", "share", "plymouth")
    theme_dir = os.path.join(ply_dir, "themes", "pmos-spin")
    os.makedirs(theme_dir, exist_ok=True)
    ply_center_img.save(os.path.join(theme_dir, "center.png"), "PNG", optimize=True)
    ply_ring_img.save(os.path.join(theme_dir, "ring.png"), "PNG", optimize=True)
    ply_error_img.save(os.path.join(theme_dir, "error.png"), "PNG", optimize=True)
    ply_title_img.save(os.path.join(theme_dir, "title.png"), "PNG", optimize=True)
    for stale in ("right.png", "top.png", "left.png"):
      stale_path = os.path.join(theme_dir, stale)
      if os.path.exists(stale_path):
        os.remove(stale_path)
    icon_rgba = Image.fromarray(arr.copy())
    icon_rgba.putalpha(Image.fromarray((fg_alpha * 255.0).astype(np.uint8)))
    icon_rgba.resize((128, 128), Image.Resampling.LANCZOS).save(
      os.path.join(ply_dir, "bizcom.png"), "PNG", optimize=True
    )

  # 10. Project documentation banner (800x240)
  banner = make_canvas(800, 240, (1, 1, 1))
  paste_centered(banner, src, 204, 118, 120)
  bdraw = ImageDraw.Draw(banner)
  font_btitle = load_font(44, bold=True)
  font_bsub = load_font(16, bold=True)
  font_bdesc = load_font(12, bold=False)

  bdraw.text((238, 46), "BANANARCHY", fill=(255, 214, 10), font=font_btitle)
  bdraw.text(
    (240, 108),
    "Keypad-First Linux Hardfork for Qualcomm MSM8909",
    fill=(240, 240, 240),
    font=font_bsub,
  )
  bdraw.text(
    (240, 142),
    "Nokia 8110 4G  |  Nokia 2720 Flip  |  Nokia 800 Tough  |  Nokia 8000 4G",
    fill=(255, 140, 26),
    font=font_bdesc,
  )
  bdraw.text(
    (240, 170),
    "240x320 Native Sxmo  *  Multi-Mode T9 Input  *  QDSP6 Voice & Modem",
    fill=(170, 170, 170),
    font=font_bdesc,
  )
  banner.save(os.path.join(ARTWORK_DIR, "banner-800x240.png"), "PNG", optimize=True)

  print(f"Generated Bananarchy artwork suite in {ARTWORK_DIR}:")
  for name in sorted(os.listdir(ARTWORK_DIR)):
    p = os.path.join(ARTWORK_DIR, name)
    if os.path.isfile(p):
      print(f"  {name:32s} {os.stat(p).st_size:8d} bytes")


if __name__ == "__main__":
  main()
