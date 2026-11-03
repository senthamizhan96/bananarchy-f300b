#!/usr/bin/env python3
"""Compile MIPI-DBI panel init command text files into binary firmware blobs."""

import sys


MAGIC_HEADER = b"MIPI DBI\x00\x00\x00\x00\x00\x00\x00\x01"


def compile_mipi_dbi(txt_path, bin_path):
  out = bytearray(MAGIC_HEADER)
  with open(txt_path, "r", encoding="utf-8") as f:
    for lineno, raw_line in enumerate(f, 1):
      line = raw_line.split("#", 1)[0].strip()
      if not line:
        continue
      parts = line.split()
      op = parts[0].lower()
      if op == "command":
        if len(parts) < 2:
          raise ValueError(f"{txt_path}:{lineno}: 'command' requires at least 1 byte")
        cmd = int(parts[1], 0)
        args = [int(x, 0) for x in parts[2:]]
        out.append(cmd & 0xFF)
        out.append(len(args) & 0xFF)
        for a in args:
          out.append(a & 0xFF)
      elif op == "delay":
        if len(parts) != 2:
          raise ValueError(f"{txt_path}:{lineno}: 'delay' requires 1 integer argument (ms)")
        ms = int(parts[1], 0)
        out.append(0x00)
        out.append(0x01)
        out.append(ms & 0xFF)
      else:
        raise ValueError(f"{txt_path}:{lineno}: unknown directive '{op}'")

  with open(bin_path, "wb") as wf:
    wf.write(out)


def main():
  if len(sys.argv) != 3:
    print(f"Usage: {sys.argv[0]} <output.bin> <input.txt>", file=sys.stderr)
    sys.exit(1)
  compile_mipi_dbi(sys.argv[2], sys.argv[1])


if __name__ == "__main__":
  main()
