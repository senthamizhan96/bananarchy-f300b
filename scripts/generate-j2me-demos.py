#!/usr/bin/env python3
"""Generate self-contained J2ME (MIDP 2.0 / CLDC 1.1) demo JAR and JAD suites.

Builds three complete, playable 240x320 MIDlet suites using a pure-Python
Java classfile (version 47.0 / CLDC 1.1) bytecode assembler and verifier:
  1. ReMEdy3DArena.jar + ReMEdy3DArena.jad (M3G JSR-184 3D Arena + 2D HUD)
  2. AstroBlaster240.jar + AstroBlaster240.jad (GameCanvas + RMS + MMAPI Tone)
  3. NetToolJ2ME.jar + NetToolJ2ME.jad (LCDUI Widgets + GCF Http/Socket Client)
"""

import os
import shutil
import struct
import sys
import zlib
import zipfile


BASE_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OUT_SHARE_DIR = os.path.join(
  BASE_DIR, "overlay", "rootfs", "usr", "share", "games", "j2me"
)
OUT_USER_DIR = os.path.join(
  BASE_DIR, "overlay", "rootfs", "home", "user", "Games"
)

# Access flags
ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_SUPER = 0x0020
ACC_SYNCHRONIZED = 0x0020

# newarray element type codes
T_BOOLEAN = 4
T_CHAR = 5
T_FLOAT = 6
T_DOUBLE = 7
T_BYTE = 8
T_SHORT = 9
T_INT = 10
T_LONG = 11


def type_slots(desc):
  """Return JVM stack/local slot width (0, 1, or 2) for a field/return type."""
  if desc == "V":
    return 0
  if desc in ("J", "D"):
    return 2
  return 1


def parse_method_desc(desc):
  """Parse a JVM method descriptor into (arg_descs, return_desc)."""
  if not desc.startswith("(") or ")" not in desc:
    raise ValueError(f"Invalid method descriptor: {desc}")
  end_args = desc.index(")")
  args_str = desc[1:end_args]
  ret_str = desc[end_args + 1:]
  args = []
  i = 0
  n = len(args_str)
  while i < n:
    start = i
    while i < n and args_str[i] == "[":
      i += 1
    if i >= n:
      raise ValueError(f"Malformed array in descriptor: {desc}")
    if args_str[i] == "L":
      semi = args_str.find(";", i)
      if semi < 0:
        raise ValueError(f"Unclosed object type in descriptor: {desc}")
      i = semi + 1
    elif args_str[i] in "BCDFIJSZ":
      i += 1
    else:
      raise ValueError(f"Unknown type char '{args_str[i]}' in {desc}")
    args.append(args_str[start:i])
  return args, ret_str


def method_stack_delta(desc, is_static=False):
  """Return (args_slots_including_this, net_stack_delta) for a call."""
  args, ret = parse_method_desc(desc)
  arg_slots = sum(type_slots(a) for a in args)
  if not is_static:
    arg_slots += 1
  ret_slots = type_slots(ret)
  return arg_slots, ret_slots - arg_slots


class ConstPool:
  """JVM Constant Pool builder with automatic deduplication."""

  def __init__(self):
    self.entries = [None]  # 1-based indexing
    self.lookup = {}

  def _add(self, key, raw_bytes, slots=1):
    if key in self.lookup:
      return self.lookup[key]
    idx = len(self.entries)
    self.entries.append((key[0], raw_bytes, key))
    if slots == 2:
      self.entries.append(None)
    self.lookup[key] = idx
    return idx

  def utf8(self, text):
    encoded = text.encode("utf-8")
    raw = struct.pack(">BH", 1, len(encoded)) + encoded
    return self._add(("Utf8", text), raw)

  def integer(self, val):
    raw = struct.pack(">Bi", 3, int(val))
    return self._add(("Integer", int(val)), raw)

  def float_val(self, val):
    packed = struct.pack(">f", float(val))
    bits = struct.unpack(">I", packed)[0]
    raw = b"\x04" + packed
    return self._add(("Float", bits), raw)

  def long_val(self, val):
    raw = struct.pack(">Bq", 5, int(val))
    return self._add(("Long", int(val)), raw, slots=2)

  def double_val(self, val):
    packed = struct.pack(">d", float(val))
    bits = struct.unpack(">Q", packed)[0]
    raw = b"\x06" + packed
    return self._add(("Double", bits), raw, slots=2)

  def class_ref(self, internal_name):
    name_idx = self.utf8(internal_name)
    raw = struct.pack(">BH", 7, name_idx)
    return self._add(("Class", internal_name), raw)

  def string(self, text):
    utf_idx = self.utf8(text)
    raw = struct.pack(">BH", 8, utf_idx)
    return self._add(("String", text), raw)

  def name_and_type(self, name, desc):
    n_idx = self.utf8(name)
    d_idx = self.utf8(desc)
    raw = struct.pack(">BHH", 12, n_idx, d_idx)
    return self._add(("NameAndType", name, desc), raw)

  def fieldref(self, cls, name, desc):
    c_idx = self.class_ref(cls)
    nt_idx = self.name_and_type(name, desc)
    raw = struct.pack(">BHH", 9, c_idx, nt_idx)
    return self._add(("Fieldref", cls, name, desc), raw)

  def methodref(self, cls, name, desc):
    c_idx = self.class_ref(cls)
    nt_idx = self.name_and_type(name, desc)
    raw = struct.pack(">BHH", 10, c_idx, nt_idx)
    return self._add(("Methodref", cls, name, desc), raw)

  def interface_methodref(self, cls, name, desc):
    c_idx = self.class_ref(cls)
    nt_idx = self.name_and_type(name, desc)
    raw = struct.pack(">BHH", 11, c_idx, nt_idx)
    return self._add(("InterfaceMethodref", cls, name, desc), raw)

  def serialize(self):
    out = [struct.pack(">H", len(self.entries))]
    for item in self.entries[1:]:
      if item is not None:
        out.append(item[1])
    return b"".join(out)


class CodeBuilder:
  """Symbolic JVM bytecode builder with automatic label and local resolution."""

  def __init__(self, cp, access_flags, desc):
    self.cp = cp
    self.is_static = bool(access_flags & ACC_STATIC)
    self.desc = desc
    self.code = bytearray()
    self.labels = {}
    self.fixups = []  # (opcode_pc, offset_pos, label_name)
    self.exceptions = []  # (start_lbl, end_lbl, handler_lbl, catch_cls)
    self.locals_map = {}
    self.next_local = 0

    args, _ = parse_method_desc(desc)
    if not self.is_static:
      self.locals_map["this"] = 0
      self.next_local = 1
    for idx, arg_desc in enumerate(args):
      self.locals_map[f"arg{idx}"] = self.next_local
      self.next_local += type_slots(arg_desc)

  def def_local(self, name, slots=1):
    if name in self.locals_map:
      return self.locals_map[name]
    slot = self.next_local
    self.locals_map[name] = slot
    self.next_local += slots
    return slot

  def alias_local(self, existing_name, alias_name):
    self.locals_map[alias_name] = self.locals_map[existing_name]

  def _slot(self, name_or_idx, slots=1):
    if isinstance(name_or_idx, int):
      if name_or_idx + slots > self.next_local:
        self.next_local = name_or_idx + slots
      return name_or_idx
    if name_or_idx not in self.locals_map:
      return self.def_local(name_or_idx, slots=slots)
    return self.locals_map[name_or_idx]

  def label(self, name):
    self.labels[name] = len(self.code)
    return self

  def try_catch(self, start_lbl, end_lbl, handler_lbl, catch_cls="java/lang/Throwable"):
    self.exceptions.append((start_lbl, end_lbl, handler_lbl, catch_cls))
    return self

  def emit(self, *bytes_list):
    for b in bytes_list:
      self.code.append(b & 0xFF)
    return self

  def emit_u2(self, val):
    self.code.extend(struct.pack(">H", val & 0xFFFF))
    return self

  def emit_i2(self, val):
    self.code.extend(struct.pack(">h", val))
    return self

  # Constant pushes
  def aconst_null(self):
    return self.emit(0x01)

  def iconst(self, val):
    val = int(val)
    if val == -1:
      return self.emit(0x02)
    if 0 <= val <= 5:
      return self.emit(0x03 + val)
    if -128 <= val <= 127:
      self.emit(0x10)
      self.code.extend(struct.pack(">b", val))
      return self
    if -32768 <= val <= 32767:
      self.emit(0x11)
      return self.emit_i2(val)
    return self.ldc_idx(self.cp.integer(val))

  def lconst(self, val):
    val = int(val)
    if val == 0:
      return self.emit(0x09)
    if val == 1:
      return self.emit(0x0A)
    idx = self.cp.long_val(val)
    self.emit(0x14)
    return self.emit_u2(idx)

  def fconst(self, val):
    val = float(val)
    if val == 0.0:
      return self.emit(0x0B)
    if val == 1.0:
      return self.emit(0x0C)
    if val == 2.0:
      return self.emit(0x0D)
    return self.ldc_idx(self.cp.float_val(val))

  def ldc_str(self, text):
    return self.ldc_idx(self.cp.string(text))

  def ldc_idx(self, idx):
    if idx <= 255:
      return self.emit(0x12, idx)
    self.emit(0x13)
    return self.emit_u2(idx)

  # Local loads/stores
  def iload(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x1A + s)
    return self.emit(0x15, s)

  def lload(self, local):
    s = self._slot(local, 2)
    if s <= 3:
      return self.emit(0x1E + s)
    return self.emit(0x16, s)

  def fload(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x22 + s)
    return self.emit(0x17, s)

  def aload(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x2A + s)
    return self.emit(0x19, s)

  def istore(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x3B + s)
    return self.emit(0x36, s)

  def lstore(self, local):
    s = self._slot(local, 2)
    if s <= 3:
      return self.emit(0x3F + s)
    return self.emit(0x37, s)

  def fstore(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x43 + s)
    return self.emit(0x38, s)

  def astore(self, local):
    s = self._slot(local, 1)
    if s <= 3:
      return self.emit(0x4B + s)
    return self.emit(0x3A, s)

  # Array loads/stores
  def iaload(self):
    return self.emit(0x2E)

  def baload(self):
    return self.emit(0x33)

  def saload(self):
    return self.emit(0x35)

  def iastore(self):
    return self.emit(0x4F)

  def fastore(self):
    return self.emit(0x51)

  def bastore(self):
    return self.emit(0x54)

  def sastore(self):
    return self.emit(0x56)

  # Stack manipulation
  def pop(self):
    return self.emit(0x57)

  def dup(self):
    return self.emit(0x59)

  # Arithmetic & bitwise
  def iadd(self):
    return self.emit(0x60)

  def ladd(self):
    return self.emit(0x61)

  def fadd(self):
    return self.emit(0x62)

  def isub(self):
    return self.emit(0x64)

  def lsub(self):
    return self.emit(0x65)

  def fsub(self):
    return self.emit(0x66)

  def imul(self):
    return self.emit(0x68)

  def lmul(self):
    return self.emit(0x69)

  def fmul(self):
    return self.emit(0x6A)

  def idiv(self):
    return self.emit(0x6C)

  def ldiv(self):
    return self.emit(0x6D)

  def fdiv(self):
    return self.emit(0x6E)

  def irem(self):
    return self.emit(0x70)

  def ineg(self):
    return self.emit(0x74)

  def fneg(self):
    return self.emit(0x76)

  def ishl(self):
    return self.emit(0x78)

  def ishr(self):
    return self.emit(0x7A)

  def iushr(self):
    return self.emit(0x7C)

  def iand(self):
    return self.emit(0x7E)

  def ior(self):
    return self.emit(0x80)

  def ixor(self):
    return self.emit(0x82)

  def iinc(self, local, delta):
    s = self._slot(local, 1)
    self.emit(0x84, s)
    self.code.extend(struct.pack(">b", delta))
    return self

  # Conversions & comparisons
  def i2l(self):
    return self.emit(0x85)

  def i2f(self):
    return self.emit(0x86)

  def l2i(self):
    return self.emit(0x88)

  def f2i(self):
    return self.emit(0x8B)

  def i2b(self):
    return self.emit(0x91)

  def i2c(self):
    return self.emit(0x92)

  def i2s(self):
    return self.emit(0x93)

  def lcmp(self):
    return self.emit(0x94)

  def fcmpl(self):
    return self.emit(0x95)

  def fcmpg(self):
    return self.emit(0x96)

  # Branches
  def _branch(self, opcode, label_name):
    op_pc = len(self.code)
    self.emit(opcode)
    self.fixups.append((op_pc, len(self.code), label_name))
    return self.emit_u2(0)

  def ifeq(self, lbl):
    return self._branch(0x99, lbl)

  def ifne(self, lbl):
    return self._branch(0x9A, lbl)

  def iflt(self, lbl):
    return self._branch(0x9B, lbl)

  def ifge(self, lbl):
    return self._branch(0x9C, lbl)

  def ifgt(self, lbl):
    return self._branch(0x9D, lbl)

  def ifle(self, lbl):
    return self._branch(0x9E, lbl)

  def if_icmpeq(self, lbl):
    return self._branch(0x9F, lbl)

  def if_icmpne(self, lbl):
    return self._branch(0xA0, lbl)

  def if_icmplt(self, lbl):
    return self._branch(0xA1, lbl)

  def if_icmpge(self, lbl):
    return self._branch(0xA2, lbl)

  def if_icmpgt(self, lbl):
    return self._branch(0xA3, lbl)

  def if_icmple(self, lbl):
    return self._branch(0xA4, lbl)

  def if_acmpeq(self, lbl):
    return self._branch(0xA5, lbl)

  def if_acmpne(self, lbl):
    return self._branch(0xA6, lbl)

  def goto(self, lbl):
    return self._branch(0xA7, lbl)

  def ifnull(self, lbl):
    return self._branch(0xC6, lbl)

  def ifnonnull(self, lbl):
    return self._branch(0xC7, lbl)

  # Returns
  def ireturn(self):
    return self.emit(0xAC)

  def lreturn(self):
    return self.emit(0xAD)

  def freturn(self):
    return self.emit(0xAE)

  def areturn(self):
    return self.emit(0xB0)

  def vreturn(self):
    return self.emit(0xB1)

  # Fields & Methods
  def getstatic(self, cls, name, desc):
    idx = self.cp.fieldref(cls, name, desc)
    self.emit(0xB2)
    return self.emit_u2(idx)

  def putstatic(self, cls, name, desc):
    idx = self.cp.fieldref(cls, name, desc)
    self.emit(0xB3)
    return self.emit_u2(idx)

  def getfield(self, cls, name, desc):
    idx = self.cp.fieldref(cls, name, desc)
    self.emit(0xB4)
    return self.emit_u2(idx)

  def putfield(self, cls, name, desc):
    idx = self.cp.fieldref(cls, name, desc)
    self.emit(0xB5)
    return self.emit_u2(idx)

  def invokevirtual(self, cls, name, desc):
    idx = self.cp.methodref(cls, name, desc)
    self.emit(0xB6)
    return self.emit_u2(idx)

  def invokespecial(self, cls, name, desc):
    idx = self.cp.methodref(cls, name, desc)
    self.emit(0xB7)
    return self.emit_u2(idx)

  def invokestatic(self, cls, name, desc):
    idx = self.cp.methodref(cls, name, desc)
    self.emit(0xB8)
    return self.emit_u2(idx)

  def invokeinterface(self, cls, name, desc):
    idx = self.cp.interface_methodref(cls, name, desc)
    arg_slots, _ = method_stack_delta(desc, is_static=False)
    self.emit(0xB9)
    self.emit_u2(idx)
    return self.emit(arg_slots, 0)

  # Objects & arrays
  def new_obj(self, cls):
    idx = self.cp.class_ref(cls)
    self.emit(0xBB)
    return self.emit_u2(idx)

  def newarray(self, atype):
    return self.emit(0xBC, atype)

  def anewarray(self, cls):
    idx = self.cp.class_ref(cls)
    self.emit(0xBD)
    return self.emit_u2(idx)

  def arraylength(self):
    return self.emit(0xBE)

  def athrow(self):
    return self.emit(0xBF)

  def checkcast(self, cls):
    idx = self.cp.class_ref(cls)
    self.emit(0xC0)
    return self.emit_u2(idx)

  def finalize(self):
    for op_pc, offset_pos, lbl in self.fixups:
      if lbl not in self.labels:
        raise ValueError(f"Undefined branch label: {lbl}")
      rel = self.labels[lbl] - op_pc
      if not (-32768 <= rel <= 32767):
        raise ValueError(f"Branch offset out of range for {lbl}: {rel}")
      struct.pack_into(">h", self.code, offset_pos, rel)

    exc_rows = []
    for s_lbl, e_lbl, h_lbl, catch_cls in self.exceptions:
      s_pc = self.labels[s_lbl]
      e_pc = self.labels[e_lbl]
      h_pc = self.labels[h_lbl]
      c_idx = 0 if catch_cls is None else self.cp.class_ref(catch_cls)
      exc_rows.append((s_pc, e_pc, h_pc, c_idx))

    max_stack, max_locals = compute_and_verify_code(
      bytes(self.code), exc_rows, self.cp, self.next_local
    )
    code_attr_name = self.cp.utf8("Code")
    exc_bytes = b"".join(
      struct.pack(">HHHH", s, e, h, c) for s, e, h, c in exc_rows
    )
    body = (
      struct.pack(">HHI", max_stack, max_locals, len(self.code))
      + bytes(self.code)
      + struct.pack(">H", len(exc_rows))
      + exc_bytes
      + struct.pack(">H", 0)
    )
    return struct.pack(">HI", code_attr_name, len(body)) + body


def compute_and_verify_code(code_bytes, exc_rows, cp, min_locals):
  """Disassemble, verify control-flow & stack depths, and return (max_stack, max_locals)."""
  n = len(code_bytes)
  if n == 0:
    raise ValueError("Method code cannot be empty")

  insns = {}  # pc -> (opcode, length, stack_delta, next_pcs, local_end)
  pc = 0
  max_local_used = min_locals

  while pc < n:
    op = code_bytes[pc]
    start = pc
    pc += 1
    delta = 0
    next_pcs = []
    falls_through = True
    local_end = 0

    if op == 0x01 or (0x02 <= op <= 0x08) or (0x0B <= op <= 0x0D):
      delta = 1
    elif op in (0x09, 0x0A):
      delta = 2
    elif op == 0x10:  # bipush
      pc += 1
      delta = 1
    elif op == 0x11:  # sipush
      pc += 2
      delta = 1
    elif op == 0x12:  # ldc
      idx = code_bytes[pc]
      pc += 1
      tag = cp.entries[idx][0]
      if tag not in ("Integer", "Float", "String"):
        raise ValueError(f"Invalid ldc CP tag {tag} at pc={start}")
      delta = 1
    elif op == 0x13:  # ldc_w
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      tag = cp.entries[idx][0]
      if tag not in ("Integer", "Float", "String"):
        raise ValueError(f"Invalid ldc_w CP tag {tag} at pc={start}")
      delta = 1
    elif op == 0x14:  # ldc2_w
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      tag = cp.entries[idx][0]
      if tag not in ("Long", "Double"):
        raise ValueError(f"Invalid ldc2_w CP tag {tag} at pc={start}")
      delta = 2
    elif op in (0x15, 0x17, 0x19):  # iload, fload, aload
      slot = code_bytes[pc]
      pc += 1
      local_end = slot + 1
      delta = 1
    elif op == 0x16:  # lload
      slot = code_bytes[pc]
      pc += 1
      local_end = slot + 2
      delta = 2
    elif 0x1A <= op <= 0x1D:  # iload_0..3
      local_end = (op - 0x1A) + 1
      delta = 1
    elif 0x1E <= op <= 0x21:  # lload_0..3
      local_end = (op - 0x1E) + 2
      delta = 2
    elif 0x22 <= op <= 0x25:  # fload_0..3
      local_end = (op - 0x22) + 1
      delta = 1
    elif 0x2A <= op <= 0x2D:  # aload_0..3
      local_end = (op - 0x2A) + 1
      delta = 1
    elif op in (0x2E, 0x33, 0x35):  # iaload, baload, saload
      delta = -1
    elif op in (0x36, 0x38, 0x3A):  # istore, fstore, astore
      slot = code_bytes[pc]
      pc += 1
      local_end = slot + 1
      delta = -1
    elif op == 0x37:  # lstore
      slot = code_bytes[pc]
      pc += 1
      local_end = slot + 2
      delta = -2
    elif 0x3B <= op <= 0x3E:  # istore_0..3
      local_end = (op - 0x3B) + 1
      delta = -1
    elif 0x3F <= op <= 0x42:  # lstore_0..3
      local_end = (op - 0x3F) + 2
      delta = -2
    elif 0x43 <= op <= 0x46:  # fstore_0..3
      local_end = (op - 0x43) + 1
      delta = -1
    elif 0x4B <= op <= 0x4E:  # astore_0..3
      local_end = (op - 0x4B) + 1
      delta = -1
    elif op in (0x4F, 0x51, 0x54, 0x56):  # iastore, fastore, bastore, sastore
      delta = -3
    elif op == 0x57:  # pop
      delta = -1
    elif op == 0x59:  # dup
      delta = 1
    elif op in (
      0x60, 0x62, 0x64, 0x66, 0x68, 0x6A, 0x6C, 0x6E, 0x70,
      0x78, 0x7A, 0x7C, 0x7E, 0x80, 0x82, 0x95, 0x96,
    ):
      delta = -1
    elif op in (0x61, 0x65, 0x69, 0x6D):  # ladd, lsub, lmul, ldiv
      delta = -2
    elif op in (0x74, 0x76, 0x86, 0x8B, 0x91, 0x92, 0x93, 0xBE):
      delta = 0
    elif op == 0x84:  # iinc
      slot = code_bytes[pc]
      pc += 2
      local_end = slot + 1
      delta = 0
    elif op == 0x85:  # i2l
      delta = 1
    elif op == 0x88:  # l2i
      delta = -1
    elif op == 0x94:  # lcmp
      delta = -3
    elif 0x99 <= op <= 0x9E or op in (0xC6, 0xC7):  # 1-operand branch
      rel = struct.unpack_from(">h", code_bytes, pc)[0]
      pc += 2
      delta = -1
      next_pcs.append(start + rel)
    elif 0x9F <= op <= 0xA6:  # 2-operand branch
      rel = struct.unpack_from(">h", code_bytes, pc)[0]
      pc += 2
      delta = -2
      next_pcs.append(start + rel)
    elif op == 0xA7:  # goto
      rel = struct.unpack_from(">h", code_bytes, pc)[0]
      pc += 2
      delta = 0
      falls_through = False
      next_pcs.append(start + rel)
    elif op in (0xAC, 0xAE, 0xB0, 0xBF):  # ireturn, freturn, areturn, athrow
      delta = -1
      falls_through = False
    elif op == 0xAD:  # lreturn
      delta = -2
      falls_through = False
    elif op == 0xB1:  # return
      delta = 0
      falls_through = False
    elif op in (0xB2, 0xB3, 0xB4, 0xB5):  # field ops
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      entry = cp.entries[idx]
      if entry[0] != "Fieldref":
        raise ValueError(f"Expected Fieldref at pc={start}, got {entry[0]}")
      fdesc = entry[2][3]
      w = type_slots(fdesc)
      if op == 0xB2:
        delta = w
      elif op == 0xB3:
        delta = -w
      elif op == 0xB4:
        delta = -1 + w
      else:
        delta = -1 - w
    elif op in (0xB6, 0xB7, 0xB8):  # invokevirtual, invokespecial, invokestatic
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      entry = cp.entries[idx]
      if entry[0] != "Methodref":
        raise ValueError(f"Expected Methodref at pc={start}, got {entry[0]}")
      mdesc = entry[2][3]
      _, delta = method_stack_delta(mdesc, is_static=(op == 0xB8))
    elif op == 0xB9:  # invokeinterface
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      count = code_bytes[pc + 2]
      zero = code_bytes[pc + 3]
      pc += 4
      entry = cp.entries[idx]
      if entry[0] != "InterfaceMethodref":
        raise ValueError(f"Expected InterfaceMethodref at pc={start}")
      mdesc = entry[2][3]
      expected_count, delta = method_stack_delta(mdesc, is_static=False)
      if count != expected_count or zero != 0:
        raise ValueError(
          f"invokeinterface count mismatch at pc={start}: {count} != {expected_count}"
        )
    elif op == 0xBB:  # new
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      if cp.entries[idx][0] != "Class":
        raise ValueError(f"Expected Class at pc={start}")
      delta = 1
    elif op == 0xBC:  # newarray
      pc += 1
      delta = 0
    elif op in (0xBD, 0xC0, 0xC1):  # anewarray, checkcast, instanceof
      idx = struct.unpack_from(">H", code_bytes, pc)[0]
      pc += 2
      if cp.entries[idx][0] != "Class":
        raise ValueError(f"Expected Class at pc={start}")
      delta = 0
    else:
      raise ValueError(f"Unsupported opcode 0x{op:02X} at pc={start}")

    if falls_through:
      next_pcs.append(pc)
    if local_end > max_local_used:
      max_local_used = local_end
    insns[start] = (op, pc - start, delta, next_pcs)

  # Verify all branch and exception targets land on instruction boundaries
  for start_pc, (_, _, _, targets) in insns.items():
    for t in targets:
      if t not in insns:
        raise ValueError(f"Invalid branch target pc={t} from pc={start_pc}")

  for s_pc, e_pc, h_pc, _ in exc_rows:
    if s_pc not in insns or h_pc not in insns or (e_pc != n and e_pc not in insns):
      raise ValueError(f"Invalid exception range [{s_pc}, {e_pc}) -> {h_pc}")

  # Control-flow stack depth verification
  depths = {0: 0}
  worklist = [0]
  for _, _, h_pc, _ in exc_rows:
    depths[h_pc] = 1
    worklist.append(h_pc)

  max_stack = 1 if exc_rows else 0
  while worklist:
    cur = worklist.pop()
    d_in = depths[cur]
    op, _, delta, targets = insns[cur]
    d_out = d_in + delta
    if d_out < 0:
      raise ValueError(f"Stack underflow at pc={cur} (op=0x{op:02X}, depth={d_in})")
    peak = max(d_in, d_out)
    if peak > max_stack:
      max_stack = peak
    for t in targets:
      if t in depths:
        if depths[t] != d_out:
          raise ValueError(
            f"Stack depth mismatch at merge pc={t}: {depths[t]} vs {d_out} (from pc={cur})"
          )
      else:
        depths[t] = d_out
        worklist.append(t)

  return max_stack + 2, max_local_used


class ClassBuilder:
  """Java .class file builder (version 47.0 / CLDC 1.1)."""

  def __init__(self, this_cls, super_cls="java/lang/Object", interfaces=None, flags=ACC_PUBLIC | ACC_SUPER):
    self.cp = ConstPool()
    self.flags = flags
    self.this_idx = self.cp.class_ref(this_cls)
    self.super_idx = self.cp.class_ref(super_cls)
    self.interface_idxs = [self.cp.class_ref(i) for i in (interfaces or [])]
    self.fields = []
    self.methods = []

  def add_field(self, name, desc, flags=ACC_PRIVATE):
    n_idx = self.cp.utf8(name)
    d_idx = self.cp.utf8(desc)
    self.fields.append((flags, n_idx, d_idx))

  def add_method(self, name, desc, flags=ACC_PUBLIC):
    cb = CodeBuilder(self.cp, flags, desc)
    self.methods.append((flags, name, desc, cb))
    return cb

  def to_bytes(self):
    method_blobs = []
    for flags, name, desc, cb in self.methods:
      n_idx = self.cp.utf8(name)
      d_idx = self.cp.utf8(desc)
      code_attr = cb.finalize()
      method_blobs.append(
        struct.pack(">HHHH", flags, n_idx, d_idx, 1) + code_attr
      )

    field_blobs = [
      struct.pack(">HHHH", flags, n_idx, d_idx, 0)
      for flags, n_idx, d_idx in self.fields
    ]

    header = struct.pack(">IHH", 0xCAFEBABE, 0, 47)
    cp_bytes = self.cp.serialize()
    cls_header = struct.pack(
      ">HHHH",
      self.flags,
      self.this_idx,
      self.super_idx,
      len(self.interface_idxs),
    )
    ifaces_bytes = b"".join(struct.pack(">H", i) for i in self.interface_idxs)
    fields_section = struct.pack(">H", len(field_blobs)) + b"".join(field_blobs)
    methods_section = struct.pack(">H", len(method_blobs)) + b"".join(method_blobs)
    attrs_section = struct.pack(">H", 0)
    raw = (
      header
      + cp_bytes
      + cls_header
      + ifaces_bytes
      + fields_section
      + methods_section
      + attrs_section
    )
    verify_class_bytes(raw)
    return raw


def verify_class_bytes(data):
  """Independent structural verifier for compiled Java 47.0 classfile bytes."""
  magic, minor, major, cp_count = struct.unpack_from(">IHHI", data, 0)[:4]
  cp_count = struct.unpack_from(">H", data, 8)[0]
  if magic != 0xCAFEBABE or minor != 0 or major != 47:
    raise ValueError(f"Invalid class header: magic=0x{magic:X}, v={major}.{minor}")

  pos = 10
  idx = 1
  cp_tags = {}
  while idx < cp_count:
    tag = data[pos]
    pos += 1
    cp_tags[idx] = tag
    if tag == 1:
      length = struct.unpack_from(">H", data, pos)[0]
      pos += 2 + length
      idx += 1
    elif tag in (3, 4):
      pos += 4
      idx += 1
    elif tag in (5, 6):
      pos += 8
      idx += 2
    elif tag in (7, 8):
      pos += 2
      idx += 1
    elif tag in (9, 10, 11, 12):
      pos += 4
      idx += 1
    else:
      raise ValueError(f"Unknown CP tag {tag} at index {idx}")

  flags, this_c, super_c, iface_count = struct.unpack_from(">HHHH", data, pos)
  pos += 8
  if cp_tags.get(this_c) != 7 or cp_tags.get(super_c) != 7:
    raise ValueError("Invalid this_class or super_class CP index")
  pos += 2 * iface_count

  fields_count = struct.unpack_from(">H", data, pos)[0]
  pos += 2
  for _ in range(fields_count):
    _, f_name, f_desc, f_attrs = struct.unpack_from(">HHHH", data, pos)
    pos += 8
    if cp_tags.get(f_name) != 1 or cp_tags.get(f_desc) != 1:
      raise ValueError("Invalid field name/descriptor CP index")
    for _ in range(f_attrs):
      _, a_len = struct.unpack_from(">HI", data, pos)
      pos += 6 + a_len

  methods_count = struct.unpack_from(">H", data, pos)[0]
  pos += 2
  for _ in range(methods_count):
    _, m_name, m_desc, m_attrs = struct.unpack_from(">HHHH", data, pos)
    pos += 8
    if cp_tags.get(m_name) != 1 or cp_tags.get(m_desc) != 1:
      raise ValueError("Invalid method name/descriptor CP index")
    for _ in range(m_attrs):
      _, a_len = struct.unpack_from(">HI", data, pos)
      pos += 6 + a_len

  cls_attrs = struct.unpack_from(">H", data, pos)[0]
  pos += 2
  for _ in range(cls_attrs):
    _, a_len = struct.unpack_from(">HI", data, pos)
    pos += 6 + a_len

  if pos != len(data):
    raise ValueError(f"Trailing bytes in classfile: {pos} != {len(data)}")


def make_png_icon(fg_rgb, accent_rgb, bg_rgb=(10, 14, 24)):
  """Create a valid 16x16 RGB PNG icon without external dependencies."""
  width, height = 16, 16
  rows = []
  for y in range(height):
    row = bytearray([0])  # filter type 0
    for x in range(width):
      border = x in (0, 15) or y in (0, 15)
      diag = abs(x - 7.5) + abs(y - 7.5)
      if border:
        c = accent_rgb
      elif diag < 4.2:
        c = fg_rgb
      elif 4.2 <= diag < 6.0:
        c = accent_rgb
      else:
        c = bg_rgb
      row.extend(c)
    rows.append(bytes(row))

  raw_data = b"".join(rows)

  def chunk(tag, body):
    crc = zlib.crc32(tag + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

  ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
  return (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", ihdr)
    + chunk(b"IDAT", zlib.compress(raw_data, 9))
    + chunk(b"IEND", b"")
  )


# =============================================================================
# Suite 1: ReMEdy 3D Arena (ReMEdy3DArena.jar + ReMEdy3DArena.jad)
# =============================================================================

def build_arena_midlet_class():
  cls = ClassBuilder(
    "Arena3DMIDlet",
    "javax/microedition/midlet/MIDlet",
    ["java/lang/Runnable", "javax/microedition/lcdui/CommandListener"],
  )
  cls.add_field("canvas", "LArena3DCanvas;", ACC_PRIVATE)
  cls.add_field("exitCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("modeCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("running", "Z", ACC_PUBLIC)

  # <init>()V
  cb = cls.add_method("<init>", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokespecial("javax/microedition/midlet/MIDlet", "<init>", "()V")
  cb.aload("this")
  cb.new_obj("Arena3DCanvas")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("Arena3DCanvas", "<init>", "(LArena3DMIDlet;)V")
  cb.putfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Command")
  cb.dup()
  cb.ldc_str("Exit")
  cb.iconst(7)  # Command.EXIT
  cb.iconst(1)
  cb.invokespecial("javax/microedition/lcdui/Command", "<init>", "(Ljava/lang/String;II)V")
  cb.putfield("Arena3DMIDlet", "exitCmd", "Ljavax/microedition/lcdui/Command;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Command")
  cb.dup()
  cb.ldc_str("Mode")
  cb.iconst(1)  # Command.SCREEN
  cb.iconst(2)
  cb.invokespecial("javax/microedition/lcdui/Command", "<init>", "(Ljava/lang/String;II)V")
  cb.putfield("Arena3DMIDlet", "modeCmd", "Ljavax/microedition/lcdui/Command;")

  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "exitCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")

  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "modeCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")

  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")
  cb.vreturn()

  # startApp()V
  cb = cls.add_method("startApp", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokestatic(
    "javax/microedition/lcdui/Display",
    "getDisplay",
    "(Ljavax/microedition/midlet/MIDlet;)Ljavax/microedition/lcdui/Display;",
  )
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "running", "Z")
  cb.ifne("already_running")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("Arena3DMIDlet", "running", "Z")
  cb.new_obj("java/lang/Thread")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("java/lang/Thread", "<init>", "(Ljava/lang/Runnable;)V")
  cb.invokevirtual("java/lang/Thread", "start", "()V")
  cb.label("already_running")
  cb.vreturn()

  # pauseApp()V
  cb = cls.add_method("pauseApp", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("Arena3DMIDlet", "running", "Z")
  cb.vreturn()

  # destroyApp(Z)V
  cb = cls.add_method("destroyApp", "(Z)V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("Arena3DMIDlet", "running", "Z")
  cb.vreturn()

  # commandAction(Command, Displayable)V
  cb = cls.add_method(
    "commandAction",
    "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Displayable;)V",
    ACC_PUBLIC,
  )
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "exitCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("check_mode")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("Arena3DMIDlet", "running", "Z")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/midlet/MIDlet", "notifyDestroyed", "()V")
  cb.vreturn()
  cb.label("check_mode")
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "modeCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("cmd_done")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.invokevirtual("Arena3DCanvas", "cycleMode", "()V")
  cb.label("cmd_done")
  cb.vreturn()

  # run()V
  cb = cls.add_method("run", "()V", ACC_PUBLIC)
  cb.label("loop")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "running", "Z")
  cb.ifeq("exit_loop")
  cb.label("try_s")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.invokevirtual("Arena3DCanvas", "step", "()V")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "repaint", "()V")
  cb.aload("this")
  cb.getfield("Arena3DMIDlet", "canvas", "LArena3DCanvas;")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "serviceRepaints", "()V")
  cb.lconst(33)
  cb.invokestatic("java/lang/Thread", "sleep", "(J)V")
  cb.label("try_e")
  cb.goto("loop")
  cb.label("catch_e")
  cb.pop()
  cb.goto("loop")
  cb.try_catch("try_s", "try_e", "catch_e", "java/lang/Throwable")
  cb.label("exit_loop")
  cb.vreturn()

  return cls.to_bytes()


def _emit_short_array_init(cb, values):
  cb.iconst(len(values))
  cb.newarray(T_SHORT)
  for idx, val in enumerate(values):
    cb.dup()
    cb.iconst(idx)
    cb.iconst(val)
    cb.sastore()


def _emit_byte_array_init(cb, values):
  cb.iconst(len(values))
  cb.newarray(T_BYTE)
  for idx, val in enumerate(values):
    cb.dup()
    cb.iconst(idx)
    cb.iconst(val)
    cb.bastore()


def _emit_int_array_init(cb, values):
  cb.iconst(len(values))
  cb.newarray(T_INT)
  for idx, val in enumerate(values):
    cb.dup()
    cb.iconst(idx)
    cb.iconst(val)
    cb.iastore()


def build_arena_canvas_class():
  cls = ClassBuilder("Arena3DCanvas", "javax/microedition/lcdui/Canvas")
  cls.add_field("midlet", "LArena3DMIDlet;", ACC_PRIVATE)
  cls.add_field("g3d", "Ljavax/microedition/m3g/Graphics3D;", ACC_PRIVATE)
  cls.add_field("camera", "Ljavax/microedition/m3g/Camera;", ACC_PRIVATE)
  cls.add_field("camTrans", "Ljavax/microedition/m3g/Transform;", ACC_PRIVATE)
  cls.add_field("meshTrans", "Ljavax/microedition/m3g/Transform;", ACC_PRIVATE)
  cls.add_field("satTrans", "Ljavax/microedition/m3g/Transform;", ACC_PRIVATE)
  cls.add_field("cubeVB", "Ljavax/microedition/m3g/VertexBuffer;", ACC_PRIVATE)
  cls.add_field("cubeIB", "Ljavax/microedition/m3g/TriangleStripArray;", ACC_PRIVATE)
  cls.add_field("crystalVB", "Ljavax/microedition/m3g/VertexBuffer;", ACC_PRIVATE)
  cls.add_field("crystalIB", "Ljavax/microedition/m3g/TriangleStripArray;", ACC_PRIVATE)
  cls.add_field("app", "Ljavax/microedition/m3g/Appearance;", ACC_PRIVATE)
  cls.add_field("polyMode", "Ljavax/microedition/m3g/PolygonMode;", ACC_PRIVATE)
  cls.add_field("bg", "Ljavax/microedition/m3g/Background;", ACC_PRIVATE)
  cls.add_field("light", "Ljavax/microedition/m3g/Light;", ACC_PRIVATE)
  cls.add_field("yaw", "F", ACC_PRIVATE)
  cls.add_field("pitch", "F", ACC_PRIVATE)
  cls.add_field("roll", "F", ACC_PRIVATE)
  cls.add_field("camZ", "F", ACC_PRIVATE)
  cls.add_field("autoSpin", "Z", ACC_PRIVATE)
  cls.add_field("mode", "I", ACC_PRIVATE)
  cls.add_field("frameCount", "I", ACC_PRIVATE)
  cls.add_field("fps", "I", ACC_PRIVATE)
  cls.add_field("lastFpsTime", "J", ACC_PRIVATE)
  cls.add_field("starX", "[I", ACC_PRIVATE)
  cls.add_field("starY", "[I", ACC_PRIVATE)
  cls.add_field("starZ", "[I", ACC_PRIVATE)

  # <init>(LArena3DMIDlet;)V
  cb = cls.add_method("<init>", "(LArena3DMIDlet;)V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokespecial("javax/microedition/lcdui/Canvas", "<init>", "()V")
  cb.aload("this")
  cb.aload("arg0")
  cb.putfield("Arena3DCanvas", "midlet", "LArena3DMIDlet;")
  cb.aload("this")
  cb.iconst(1)
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "setFullScreenMode", "(Z)V")

  cb.aload("this")
  cb.fconst(25.0)
  cb.putfield("Arena3DCanvas", "yaw", "F")
  cb.aload("this")
  cb.fconst(20.0)
  cb.putfield("Arena3DCanvas", "pitch", "F")
  cb.aload("this")
  cb.fconst(0.0)
  cb.putfield("Arena3DCanvas", "roll", "F")
  cb.aload("this")
  cb.fconst(5.5)
  cb.putfield("Arena3DCanvas", "camZ", "F")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("Arena3DCanvas", "autoSpin", "Z")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("Arena3DCanvas", "mode", "I")
  cb.aload("this")
  cb.iconst(30)
  cb.putfield("Arena3DCanvas", "fps", "I")
  cb.aload("this")
  cb.invokestatic("java/lang/System", "currentTimeMillis", "()J")
  cb.putfield("Arena3DCanvas", "lastFpsTime", "J")

  # Initialize 24 3D starfield coordinates
  cb.aload("this")
  cb.iconst(24)
  cb.newarray(T_INT)
  cb.putfield("Arena3DCanvas", "starX", "[I")
  cb.aload("this")
  cb.iconst(24)
  cb.newarray(T_INT)
  cb.putfield("Arena3DCanvas", "starY", "[I")
  cb.aload("this")
  cb.iconst(24)
  cb.newarray(T_INT)
  cb.putfield("Arena3DCanvas", "starZ", "[I")

  cb.iconst(0)
  cb.istore("i")
  cb.label("star_init_loop")
  cb.iload("i")
  cb.iconst(24)
  cb.if_icmpge("star_init_done")

  # starX[i] = ((i * 37 + 13) % 200) - 100
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starX", "[I")
  cb.iload("i")
  cb.iload("i")
  cb.iconst(37)
  cb.imul()
  cb.iconst(13)
  cb.iadd()
  cb.iconst(200)
  cb.irem()
  cb.iconst(100)
  cb.isub()
  cb.iastore()

  # starY[i] = ((i * 53 + 29) % 200) - 100
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starY", "[I")
  cb.iload("i")
  cb.iload("i")
  cb.iconst(53)
  cb.imul()
  cb.iconst(29)
  cb.iadd()
  cb.iconst(200)
  cb.irem()
  cb.iconst(100)
  cb.isub()
  cb.iastore()

  # starZ[i] = (i * 3) + 8
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starZ", "[I")
  cb.iload("i")
  cb.iload("i")
  cb.iconst(3)
  cb.imul()
  cb.iconst(8)
  cb.iadd()
  cb.iastore()

  cb.iinc("i", 1)
  cb.goto("star_init_loop")
  cb.label("star_init_done")

  cb.aload("this")
  cb.invokevirtual("Arena3DCanvas", "init3D", "()V")
  cb.vreturn()

  # init3D()V
  cb = cls.add_method("init3D", "()V", ACC_PRIVATE)
  cb.aload("this")
  cb.invokestatic(
    "javax/microedition/m3g/Graphics3D",
    "getInstance",
    "()Ljavax/microedition/m3g/Graphics3D;",
  )
  cb.putfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/Camera")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/Camera", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "camera", "Ljavax/microedition/m3g/Camera;")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camera", "Ljavax/microedition/m3g/Camera;")
  cb.fconst(55.0)
  cb.fconst(0.75)  # 240.0 / 320.0
  cb.fconst(0.5)
  cb.fconst(50.0)
  cb.invokevirtual("javax/microedition/m3g/Camera", "setPerspective", "(FFFF)V")

  for tf_field in ("camTrans", "meshTrans", "satTrans"):
    cb.aload("this")
    cb.new_obj("javax/microedition/m3g/Transform")
    cb.dup()
    cb.invokespecial("javax/microedition/m3g/Transform", "<init>", "()V")
    cb.putfield("Arena3DCanvas", tf_field, "Ljavax/microedition/m3g/Transform;")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/Background")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/Background", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(-16315626)  # 0xFF070B16
  cb.invokevirtual("javax/microedition/m3g/Background", "setColor", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(1)
  cb.invokevirtual("javax/microedition/m3g/Background", "setColorClearEnable", "(Z)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(1)
  cb.invokevirtual("javax/microedition/m3g/Background", "setDepthClearEnable", "(Z)V")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/PolygonMode")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/PolygonMode", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(162)  # PolygonMode.CULL_NONE
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setCulling", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(165)  # PolygonMode.SHADE_SMOOTH
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setShading", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(168)  # PolygonMode.WINDING_CCW
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setWinding", "(I)V")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/Appearance")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/Appearance", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "app", "Ljavax/microedition/m3g/Appearance;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "app", "Ljavax/microedition/m3g/Appearance;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.invokevirtual(
    "javax/microedition/m3g/Appearance",
    "setPolygonMode",
    "(Ljavax/microedition/m3g/PolygonMode;)V",
  )

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/Light")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/Light", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "light", "Ljavax/microedition/m3g/Light;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "light", "Ljavax/microedition/m3g/Light;")
  cb.iconst(129)  # Light.DIRECTIONAL
  cb.invokevirtual("javax/microedition/m3g/Light", "setMode", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "light", "Ljavax/microedition/m3g/Light;")
  cb.iconst(-1)  # 0xFFFFFFFF
  cb.invokevirtual("javax/microedition/m3g/Light", "setColor", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "light", "Ljavax/microedition/m3g/Light;")
  cb.fconst(1.25)
  cb.invokevirtual("javax/microedition/m3g/Light", "setIntensity", "(F)V")

  # Build Cube VertexBuffer (8 vertices)
  cube_verts = [
    -1, -1,  1,   1, -1,  1,  -1,  1,  1,   1,  1,  1,
    -1, -1, -1,   1, -1, -1,  -1,  1, -1,   1,  1, -1,
  ]
  cube_cols = [
      0, -27,  -1,  -1,  40, -96,  64,  -1, -80,  -1, -40,   0,
     40, 120,  -1, -80,  20,  -1,   0, -60, 120,  -1,  -1,  -1,
  ]
  cube_norms = [
    -70, -70,  70,  70, -70,  70, -70,  70,  70,  70,  70,  70,
    -70, -70, -70,  70, -70, -70, -70,  70, -70,  70,  70, -70,
  ]
  cube_indices = [
    0, 1, 2, 3,  # Front
    5, 4, 7, 6,  # Back
    4, 0, 6, 2,  # Left
    1, 5, 3, 7,  # Right
    2, 3, 6, 7,  # Top
    4, 5, 0, 1,  # Bottom
  ]
  cube_strips = [4, 4, 4, 4, 4, 4]

  cb.new_obj("javax/microedition/m3g/VertexArray")
  cb.dup()
  cb.iconst(8)
  cb.iconst(3)
  cb.iconst(2)
  cb.invokespecial("javax/microedition/m3g/VertexArray", "<init>", "(III)V")
  cb.astore("vaPos")
  cb.aload("vaPos")
  cb.iconst(0)
  cb.iconst(8)
  _emit_short_array_init(cb, cube_verts)
  cb.invokevirtual("javax/microedition/m3g/VertexArray", "set", "(II[S)V")

  cb.new_obj("javax/microedition/m3g/VertexArray")
  cb.dup()
  cb.iconst(8)
  cb.iconst(3)
  cb.iconst(1)
  cb.invokespecial("javax/microedition/m3g/VertexArray", "<init>", "(III)V")
  cb.astore("vaCol")
  cb.aload("vaCol")
  cb.iconst(0)
  cb.iconst(8)
  _emit_byte_array_init(cb, cube_cols)
  cb.invokevirtual("javax/microedition/m3g/VertexArray", "set", "(II[B)V")

  cb.new_obj("javax/microedition/m3g/VertexArray")
  cb.dup()
  cb.iconst(8)
  cb.iconst(3)
  cb.iconst(1)
  cb.invokespecial("javax/microedition/m3g/VertexArray", "<init>", "(III)V")
  cb.astore("vaNorm")
  cb.aload("vaNorm")
  cb.iconst(0)
  cb.iconst(8)
  _emit_byte_array_init(cb, cube_norms)
  cb.invokevirtual("javax/microedition/m3g/VertexArray", "set", "(II[B)V")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/VertexBuffer")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/VertexBuffer", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("vaPos")
  cb.fconst(1.0)
  cb.aconst_null()
  cb.invokevirtual(
    "javax/microedition/m3g/VertexBuffer",
    "setPositions",
    "(Ljavax/microedition/m3g/VertexArray;F[F)V",
  )
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("vaCol")
  cb.invokevirtual(
    "javax/microedition/m3g/VertexBuffer",
    "setColors",
    "(Ljavax/microedition/m3g/VertexArray;)V",
  )
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("vaNorm")
  cb.invokevirtual(
    "javax/microedition/m3g/VertexBuffer",
    "setNormals",
    "(Ljavax/microedition/m3g/VertexArray;)V",
  )
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.iconst(-16718337)  # 0xFF00E5FF
  cb.invokevirtual("javax/microedition/m3g/VertexBuffer", "setDefaultColor", "(I)V")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/TriangleStripArray")
  cb.dup()
  _emit_int_array_init(cb, cube_indices)
  _emit_int_array_init(cb, cube_strips)
  cb.invokespecial("javax/microedition/m3g/TriangleStripArray", "<init>", "([I[I)V")
  cb.putfield("Arena3DCanvas", "cubeIB", "Ljavax/microedition/m3g/TriangleStripArray;")

  # Build Octahedron Crystal VertexBuffer (6 vertices)
  cryst_verts = [
     0,  2,  0,
    -1,  0,  1,
     1,  0,  1,
     1,  0, -1,
    -1,  0, -1,
     0, -2,  0,
  ]
  cryst_cols = [
    -1, -30,  80,
     0,  -1, -40,
    80, 120,  -1,
    -1,  40, -60,
    40,  -1, 100,
    -1, -80,   0,
  ]
  cryst_indices = [
    0, 1, 2,
    0, 2, 3,
    0, 3, 4,
    0, 4, 1,
    5, 2, 1,
    5, 3, 2,
    5, 4, 3,
    5, 1, 4,
  ]
  cryst_strips = [3, 3, 3, 3, 3, 3, 3, 3]

  cb.new_obj("javax/microedition/m3g/VertexArray")
  cb.dup()
  cb.iconst(6)
  cb.iconst(3)
  cb.iconst(2)
  cb.invokespecial("javax/microedition/m3g/VertexArray", "<init>", "(III)V")
  cb.astore("cPos")
  cb.aload("cPos")
  cb.iconst(0)
  cb.iconst(6)
  _emit_short_array_init(cb, cryst_verts)
  cb.invokevirtual("javax/microedition/m3g/VertexArray", "set", "(II[S)V")

  cb.new_obj("javax/microedition/m3g/VertexArray")
  cb.dup()
  cb.iconst(6)
  cb.iconst(3)
  cb.iconst(1)
  cb.invokespecial("javax/microedition/m3g/VertexArray", "<init>", "(III)V")
  cb.astore("cCol")
  cb.aload("cCol")
  cb.iconst(0)
  cb.iconst(6)
  _emit_byte_array_init(cb, cryst_cols)
  cb.invokevirtual("javax/microedition/m3g/VertexArray", "set", "(II[B)V")

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/VertexBuffer")
  cb.dup()
  cb.invokespecial("javax/microedition/m3g/VertexBuffer", "<init>", "()V")
  cb.putfield("Arena3DCanvas", "crystalVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("cPos")
  cb.fconst(0.85)
  cb.aconst_null()
  cb.invokevirtual(
    "javax/microedition/m3g/VertexBuffer",
    "setPositions",
    "(Ljavax/microedition/m3g/VertexArray;F[F)V",
  )
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("cCol")
  cb.invokevirtual(
    "javax/microedition/m3g/VertexBuffer",
    "setColors",
    "(Ljavax/microedition/m3g/VertexArray;)V",
  )

  cb.aload("this")
  cb.new_obj("javax/microedition/m3g/TriangleStripArray")
  cb.dup()
  _emit_int_array_init(cb, cryst_indices)
  _emit_int_array_init(cb, cryst_strips)
  cb.invokespecial("javax/microedition/m3g/TriangleStripArray", "<init>", "([I[I)V")
  cb.putfield("Arena3DCanvas", "crystalIB", "Ljavax/microedition/m3g/TriangleStripArray;")
  cb.vreturn()

  # cycleMode()V
  cb = cls.add_method("cycleMode", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "mode", "I")
  cb.iconst(1)
  cb.iadd()
  cb.iconst(3)
  cb.irem()
  cb.putfield("Arena3DCanvas", "mode", "I")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "mode", "I")
  cb.ifne("mode_not_0")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(-16315626)  # 0xFF070B16
  cb.invokevirtual("javax/microedition/m3g/Background", "setColor", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(165)  # SHADE_SMOOTH
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setShading", "(I)V")
  cb.vreturn()

  cb.label("mode_not_0")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "mode", "I")
  cb.iconst(1)
  cb.if_icmpne("mode_2")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(-15464420)  # 0xFF14081C
  cb.invokevirtual("javax/microedition/m3g/Background", "setColor", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(164)  # SHADE_FLAT
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setShading", "(I)V")
  cb.vreturn()

  cb.label("mode_2")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.iconst(-16443884)  # 0xFF051614
  cb.invokevirtual("javax/microedition/m3g/Background", "setColor", "(I)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "polyMode", "Ljavax/microedition/m3g/PolygonMode;")
  cb.iconst(165)
  cb.invokevirtual("javax/microedition/m3g/PolygonMode", "setShading", "(I)V")
  cb.vreturn()

  # step()V
  cb = cls.add_method("step", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "autoSpin", "Z")
  cb.ifeq("skip_spin")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "yaw", "F")
  cb.fconst(2.8)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "yaw", "F")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "pitch", "F")
  cb.fconst(1.4)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "pitch", "F")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "roll", "F")
  cb.fconst(0.9)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "roll", "F")
  cb.label("skip_spin")

  # Advance starfield Z
  cb.iconst(0)
  cb.istore("i")
  cb.label("star_step_loop")
  cb.iload("i")
  cb.iconst(24)
  cb.if_icmpge("star_step_done")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starZ", "[I")
  cb.iload("i")
  cb.iaload()
  cb.iconst(2)
  cb.isub()
  cb.istore("nz")
  cb.iload("nz")
  cb.iconst(4)
  cb.if_icmpgt("store_nz")
  cb.iconst(72)
  cb.istore("nz")
  cb.label("store_nz")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starZ", "[I")
  cb.iload("i")
  cb.iload("nz")
  cb.iastore()
  cb.iinc("i", 1)
  cb.goto("star_step_loop")
  cb.label("star_step_done")

  # FPS update
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "frameCount", "I")
  cb.iconst(1)
  cb.iadd()
  cb.putfield("Arena3DCanvas", "frameCount", "I")
  cb.invokestatic("java/lang/System", "currentTimeMillis", "()J")
  cb.lstore("now")
  cb.lload("now")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "lastFpsTime", "J")
  cb.lsub()
  cb.lconst(1000)
  cb.lcmp()
  cb.iflt("fps_done")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "frameCount", "I")
  cb.putfield("Arena3DCanvas", "fps", "I")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("Arena3DCanvas", "frameCount", "I")
  cb.aload("this")
  cb.lload("now")
  cb.putfield("Arena3DCanvas", "lastFpsTime", "J")
  cb.label("fps_done")
  cb.vreturn()

  # keyPressed(I)V
  cb = cls.add_method("keyPressed", "(I)V", ACC_PROTECTED)
  cb.iload("arg0")
  cb.iconst(42)  # KEY_STAR '*'
  cb.if_icmpne("not_star")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "autoSpin", "Z")
  cb.iconst(1)
  cb.ixor()
  cb.putfield("Arena3DCanvas", "autoSpin", "Z")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "repaint", "()V")
  cb.vreturn()

  cb.label("not_star")
  cb.iload("arg0")
  cb.iconst(35)  # KEY_POUND '#'
  cb.if_icmpne("not_pound")
  cb.aload("this")
  cb.fconst(25.0)
  cb.putfield("Arena3DCanvas", "yaw", "F")
  cb.aload("this")
  cb.fconst(20.0)
  cb.putfield("Arena3DCanvas", "pitch", "F")
  cb.aload("this")
  cb.fconst(5.5)
  cb.putfield("Arena3DCanvas", "camZ", "F")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "repaint", "()V")
  cb.vreturn()

  cb.label("not_pound")
  cb.iload("arg0")
  cb.iconst(49)  # KEY_NUM1 zoom in
  cb.if_icmpne("not_key1")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camZ", "F")
  cb.fconst(3.0)
  cb.fcmpg()
  cb.ifle("key_done")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camZ", "F")
  cb.fconst(0.5)
  cb.fsub()
  cb.putfield("Arena3DCanvas", "camZ", "F")
  cb.goto("key_done")

  cb.label("not_key1")
  cb.iload("arg0")
  cb.iconst(51)  # KEY_NUM3 zoom out
  cb.if_icmpne("check_ga")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camZ", "F")
  cb.fconst(11.0)
  cb.fcmpg()
  cb.ifge("key_done")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camZ", "F")
  cb.fconst(0.5)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "camZ", "F")
  cb.goto("key_done")

  cb.label("check_ga")
  cb.aload("this")
  cb.iload("arg0")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "getGameAction", "(I)I")
  cb.istore("ga")

  # LEFT (ga==2 or key==52)
  cb.iload("ga")
  cb.iconst(2)
  cb.if_icmpeq("do_left")
  cb.iload("arg0")
  cb.iconst(52)
  cb.if_icmpne("not_left")
  cb.label("do_left")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "yaw", "F")
  cb.fconst(10.0)
  cb.fsub()
  cb.putfield("Arena3DCanvas", "yaw", "F")
  cb.goto("key_done")

  cb.label("not_left")
  # RIGHT (ga==5 or key==54)
  cb.iload("ga")
  cb.iconst(5)
  cb.if_icmpeq("do_right")
  cb.iload("arg0")
  cb.iconst(54)
  cb.if_icmpne("not_right")
  cb.label("do_right")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "yaw", "F")
  cb.fconst(10.0)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "yaw", "F")
  cb.goto("key_done")

  cb.label("not_right")
  # UP (ga==1 or key==50)
  cb.iload("ga")
  cb.iconst(1)
  cb.if_icmpeq("do_up")
  cb.iload("arg0")
  cb.iconst(50)
  cb.if_icmpne("not_up")
  cb.label("do_up")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "pitch", "F")
  cb.fconst(10.0)
  cb.fsub()
  cb.putfield("Arena3DCanvas", "pitch", "F")
  cb.goto("key_done")

  cb.label("not_up")
  # DOWN (ga==6 or key==56)
  cb.iload("ga")
  cb.iconst(6)
  cb.if_icmpeq("do_down")
  cb.iload("arg0")
  cb.iconst(56)
  cb.if_icmpne("not_down")
  cb.label("do_down")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "pitch", "F")
  cb.fconst(10.0)
  cb.fadd()
  cb.putfield("Arena3DCanvas", "pitch", "F")
  cb.goto("key_done")

  cb.label("not_down")
  # FIRE (ga==8 or key==53)
  cb.iload("ga")
  cb.iconst(8)
  cb.if_icmpeq("do_fire")
  cb.iload("arg0")
  cb.iconst(53)
  cb.if_icmpne("key_done")
  cb.label("do_fire")
  cb.aload("this")
  cb.invokevirtual("Arena3DCanvas", "cycleMode", "()V")

  cb.label("key_done")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "repaint", "()V")
  cb.vreturn()

  # paint(Ljavax/microedition/lcdui/Graphics;)V
  cb = cls.add_method("paint", "(Ljavax/microedition/lcdui/Graphics;)V", ACC_PROTECTED)
  cb.label("m3g_try_s")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("arg0")
  cb.invokevirtual("javax/microedition/m3g/Graphics3D", "bindTarget", "(Ljava/lang/Object;)V")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.iconst(0)
  cb.iconst(0)
  cb.iconst(240)
  cb.iconst(320)
  cb.invokevirtual("javax/microedition/m3g/Graphics3D", "setViewport", "(IIII)V")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "bg", "Ljavax/microedition/m3g/Background;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "clear",
    "(Ljavax/microedition/m3g/Background;)V",
  )

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual("javax/microedition/m3g/Transform", "setIdentity", "()V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camTrans", "Ljavax/microedition/m3g/Transform;")
  cb.fconst(0.0)
  cb.fconst(0.0)
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camZ", "F")
  cb.invokevirtual("javax/microedition/m3g/Transform", "postTranslate", "(FFF)V")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camera", "Ljavax/microedition/m3g/Camera;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "setCamera",
    "(Ljavax/microedition/m3g/Camera;Ljavax/microedition/m3g/Transform;)V",
  )

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.invokevirtual("javax/microedition/m3g/Graphics3D", "resetLights", "()V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "light", "Ljavax/microedition/m3g/Light;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "camTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "addLight",
    "(Ljavax/microedition/m3g/Light;Ljavax/microedition/m3g/Transform;)I",
  )
  cb.pop()

  # Prepare meshTrans
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual("javax/microedition/m3g/Transform", "setIdentity", "()V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "yaw", "F")
  cb.fconst(0.0)
  cb.fconst(1.0)
  cb.fconst(0.0)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postRotate", "(FFFF)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "pitch", "F")
  cb.fconst(1.0)
  cb.fconst(0.0)
  cb.fconst(0.0)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postRotate", "(FFFF)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "roll", "F")
  cb.fconst(0.0)
  cb.fconst(0.0)
  cb.fconst(1.0)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postRotate", "(FFFF)V")

  # Render main mesh (mode == 1 renders crystal as primary, else cube)
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "mode", "I")
  cb.iconst(1)
  cb.if_icmpeq("render_crystal_main")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "cubeIB", "Ljavax/microedition/m3g/TriangleStripArray;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "app", "Ljavax/microedition/m3g/Appearance;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "render",
    "(Ljavax/microedition/m3g/VertexBuffer;Ljavax/microedition/m3g/IndexBuffer;Ljavax/microedition/m3g/Appearance;Ljavax/microedition/m3g/Transform;)V",
  )
  cb.goto("render_satellites")

  cb.label("render_crystal_main")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalIB", "Ljavax/microedition/m3g/TriangleStripArray;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "app", "Ljavax/microedition/m3g/Appearance;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "meshTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "render",
    "(Ljavax/microedition/m3g/VertexBuffer;Ljavax/microedition/m3g/IndexBuffer;Ljavax/microedition/m3g/Appearance;Ljavax/microedition/m3g/Transform;)V",
  )

  # Render orbiting crystal satellite
  cb.label("render_satellites")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "satTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual("javax/microedition/m3g/Transform", "setIdentity", "()V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "satTrans", "Ljavax/microedition/m3g/Transform;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "yaw", "F")
  cb.fconst(1.5)
  cb.fmul()
  cb.fconst(0.0)
  cb.fconst(1.0)
  cb.fconst(0.0)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postRotate", "(FFFF)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "satTrans", "Ljavax/microedition/m3g/Transform;")
  cb.fconst(2.0)
  cb.fconst(0.4)
  cb.fconst(0.0)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postTranslate", "(FFF)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "satTrans", "Ljavax/microedition/m3g/Transform;")
  cb.fconst(0.42)
  cb.fconst(0.42)
  cb.fconst(0.42)
  cb.invokevirtual("javax/microedition/m3g/Transform", "postScale", "(FFF)V")

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalVB", "Ljavax/microedition/m3g/VertexBuffer;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "crystalIB", "Ljavax/microedition/m3g/TriangleStripArray;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "app", "Ljavax/microedition/m3g/Appearance;")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "satTrans", "Ljavax/microedition/m3g/Transform;")
  cb.invokevirtual(
    "javax/microedition/m3g/Graphics3D",
    "render",
    "(Ljavax/microedition/m3g/VertexBuffer;Ljavax/microedition/m3g/IndexBuffer;Ljavax/microedition/m3g/Appearance;Ljavax/microedition/m3g/Transform;)V",
  )

  cb.aload("this")
  cb.getfield("Arena3DCanvas", "g3d", "Ljavax/microedition/m3g/Graphics3D;")
  cb.invokevirtual("javax/microedition/m3g/Graphics3D", "releaseTarget", "()V")
  cb.label("m3g_try_e")
  cb.goto("draw_hud")

  cb.label("m3g_catch")
  cb.pop()
  cb.try_catch("m3g_try_s", "m3g_try_e", "m3g_catch", "java/lang/Throwable")

  # Draw 2D starfield & HUD via javax.microedition.lcdui.Graphics
  cb.label("draw_hud")
  cb.aload("arg0")
  cb.iconst(0x70D6FF)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")

  cb.iconst(0)
  cb.istore("si")
  cb.label("star_draw_loop")
  cb.iload("si")
  cb.iconst(24)
  cb.if_icmpge("star_draw_done")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starZ", "[I")
  cb.iload("si")
  cb.iaload()
  cb.istore("sz")
  # sx = 120 + (starX[si] * 64) / sz
  cb.iconst(120)
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starX", "[I")
  cb.iload("si")
  cb.iaload()
  cb.iconst(64)
  cb.imul()
  cb.iload("sz")
  cb.idiv()
  cb.iadd()
  cb.istore("sx")
  # sy = 160 + (starY[si] * 64) / sz
  cb.iconst(160)
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "starY", "[I")
  cb.iload("si")
  cb.iaload()
  cb.iconst(64)
  cb.imul()
  cb.iload("sz")
  cb.idiv()
  cb.iadd()
  cb.istore("sy")

  cb.aload("arg0")
  cb.iload("sx")
  cb.iload("sy")
  cb.iconst(2)
  cb.iconst(2)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.iinc("si", 1)
  cb.goto("star_draw_loop")
  cb.label("star_draw_done")

  # Top HUD bar (240x22)
  cb.aload("arg0")
  cb.iconst(0x0D1528)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("arg0")
  cb.iconst(0)
  cb.iconst(0)
  cb.iconst(240)
  cb.iconst(22)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")

  cb.aload("arg0")
  cb.iconst(0x00E5FF)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("arg0")
  cb.ldc_str("240x320 M3G 3D")
  cb.iconst(4)
  cb.iconst(3)
  cb.iconst(20)  # TOP | LEFT
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")

  # FPS string on top-right
  cb.new_obj("java/lang/StringBuffer")
  cb.dup()
  cb.ldc_str("FPS:")
  cb.invokespecial("java/lang/StringBuffer", "<init>", "(Ljava/lang/String;)V")
  cb.aload("this")
  cb.getfield("Arena3DCanvas", "fps", "I")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.invokevirtual("java/lang/StringBuffer", "toString", "()Ljava/lang/String;")
  cb.astore("fpsStr")

  cb.aload("arg0")
  cb.iconst(0xFFD740)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("arg0")
  cb.aload("fpsStr")
  cb.iconst(236)
  cb.iconst(3)
  cb.iconst(24)  # TOP | RIGHT
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")

  # Bottom HUD bar (Y=298..320)
  cb.aload("arg0")
  cb.iconst(0x0D1528)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("arg0")
  cb.iconst(0)
  cb.iconst(298)
  cb.iconst(240)
  cb.iconst(22)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")

  cb.aload("arg0")
  cb.iconst(0xB2FF59)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("arg0")
  cb.ldc_str("2468:Rot 13:Zoom 5:Mode *:Spin")
  cb.iconst(120)
  cb.iconst(302)
  cb.iconst(17)  # TOP | HCENTER
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")
  cb.vreturn()

  return cls.to_bytes()


# =============================================================================
# Suite 2: Astro Blaster 240 (AstroBlaster240.jar + AstroBlaster240.jad)
# =============================================================================

def build_astro_midlet_class():
  cls = ClassBuilder(
    "AstroMIDlet",
    "javax/microedition/midlet/MIDlet",
    ["java/lang/Runnable"],
  )
  cls.add_field("canvas", "LAstroCanvas;", ACC_PRIVATE)
  cls.add_field("running", "Z", ACC_PUBLIC)

  # <init>()V
  cb = cls.add_method("<init>", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokespecial("javax/microedition/midlet/MIDlet", "<init>", "()V")
  cb.aload("this")
  cb.new_obj("AstroCanvas")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("AstroCanvas", "<init>", "(LAstroMIDlet;)V")
  cb.putfield("AstroMIDlet", "canvas", "LAstroCanvas;")
  cb.vreturn()

  # startApp()V
  cb = cls.add_method("startApp", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokestatic(
    "javax/microedition/lcdui/Display",
    "getDisplay",
    "(Ljavax/microedition/midlet/MIDlet;)Ljavax/microedition/lcdui/Display;",
  )
  cb.aload("this")
  cb.getfield("AstroMIDlet", "canvas", "LAstroCanvas;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.aload("this")
  cb.getfield("AstroMIDlet", "running", "Z")
  cb.ifne("already_started")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("AstroMIDlet", "running", "Z")
  cb.new_obj("java/lang/Thread")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("java/lang/Thread", "<init>", "(Ljava/lang/Runnable;)V")
  cb.invokevirtual("java/lang/Thread", "start", "()V")
  cb.label("already_started")
  cb.vreturn()

  # pauseApp()V
  cb = cls.add_method("pauseApp", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroMIDlet", "running", "Z")
  cb.vreturn()

  # destroyApp(Z)V
  cb = cls.add_method("destroyApp", "(Z)V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroMIDlet", "running", "Z")
  cb.aload("this")
  cb.getfield("AstroMIDlet", "canvas", "LAstroCanvas;")
  cb.invokevirtual("AstroCanvas", "saveHighScore", "()V")
  cb.vreturn()

  # run()V
  cb = cls.add_method("run", "()V", ACC_PUBLIC)
  cb.label("game_loop")
  cb.aload("this")
  cb.getfield("AstroMIDlet", "running", "Z")
  cb.ifeq("game_done")
  cb.label("try_s")
  cb.aload("this")
  cb.getfield("AstroMIDlet", "canvas", "LAstroCanvas;")
  cb.invokevirtual("AstroCanvas", "updateFrame", "()V")
  cb.aload("this")
  cb.getfield("AstroMIDlet", "canvas", "LAstroCanvas;")
  cb.invokevirtual("AstroCanvas", "renderFrame", "()V")
  cb.lconst(33)
  cb.invokestatic("java/lang/Thread", "sleep", "(J)V")
  cb.label("try_e")
  cb.goto("game_loop")
  cb.label("catch_e")
  cb.pop()
  cb.goto("game_loop")
  cb.try_catch("try_s", "try_e", "catch_e", "java/lang/Throwable")
  cb.label("game_done")
  cb.vreturn()

  return cls.to_bytes()


def build_astro_canvas_class():
  cls = ClassBuilder("AstroCanvas", "javax/microedition/lcdui/game/GameCanvas")
  cls.add_field("midlet", "LAstroMIDlet;", ACC_PRIVATE)
  cls.add_field("screenW", "I", ACC_PRIVATE)
  cls.add_field("screenH", "I", ACC_PRIVATE)
  cls.add_field("playerX", "I", ACC_PRIVATE)
  cls.add_field("playerY", "I", ACC_PRIVATE)
  cls.add_field("shield", "I", ACC_PRIVATE)
  cls.add_field("score", "I", ACC_PRIVATE)
  cls.add_field("highScore", "I", ACC_PRIVATE)
  cls.add_field("wave", "I", ACC_PRIVATE)
  cls.add_field("cooldown", "I", ACC_PRIVATE)
  cls.add_field("gameOver", "Z", ACC_PRIVATE)
  cls.add_field("rawMask", "I", ACC_PRIVATE)
  cls.add_field("starX", "[I", ACC_PRIVATE)
  cls.add_field("starY", "[I", ACC_PRIVATE)
  cls.add_field("laserX", "[I", ACC_PRIVATE)
  cls.add_field("laserY", "[I", ACC_PRIVATE)
  cls.add_field("enemyX", "[I", ACC_PRIVATE)
  cls.add_field("enemyY", "[I", ACC_PRIVATE)
  cls.add_field("enemyDX", "[I", ACC_PRIVATE)

  # <init>(LAstroMIDlet;)V
  cb = cls.add_method("<init>", "(LAstroMIDlet;)V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(0)  # suppressKeyEvents = false so keyPressed also fires
  cb.invokespecial("javax/microedition/lcdui/game/GameCanvas", "<init>", "(Z)V")
  cb.aload("this")
  cb.aload("arg0")
  cb.putfield("AstroCanvas", "midlet", "LAstroMIDlet;")
  cb.aload("this")
  cb.iconst(1)
  cb.invokevirtual("javax/microedition/lcdui/Canvas", "setFullScreenMode", "(Z)V")

  cb.aload("this")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "getWidth", "()I")
  cb.putfield("AstroCanvas", "screenW", "I")
  cb.aload("this")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "getHeight", "()I")
  cb.putfield("AstroCanvas", "screenH", "I")

  cb.aload("this")
  cb.iconst(20)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "starX", "[I")
  cb.aload("this")
  cb.iconst(20)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "starY", "[I")

  cb.aload("this")
  cb.iconst(6)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "laserX", "[I")
  cb.aload("this")
  cb.iconst(6)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "laserY", "[I")

  cb.aload("this")
  cb.iconst(5)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "enemyX", "[I")
  cb.aload("this")
  cb.iconst(5)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "enemyY", "[I")
  cb.aload("this")
  cb.iconst(5)
  cb.newarray(T_INT)
  cb.putfield("AstroCanvas", "enemyDX", "[I")

  cb.aload("this")
  cb.invokevirtual("AstroCanvas", "loadHighScore", "()V")
  cb.aload("this")
  cb.invokevirtual("AstroCanvas", "resetGame", "()V")
  cb.vreturn()

  # resetGame()V
  cb = cls.add_method("resetGame", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.iconst(120)
  cb.putfield("AstroCanvas", "playerX", "I")
  cb.aload("this")
  cb.iconst(268)
  cb.putfield("AstroCanvas", "playerY", "I")
  cb.aload("this")
  cb.iconst(100)
  cb.putfield("AstroCanvas", "shield", "I")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroCanvas", "score", "I")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("AstroCanvas", "wave", "I")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroCanvas", "cooldown", "I")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroCanvas", "gameOver", "Z")

  # Stars
  cb.iconst(0)
  cb.istore("i")
  cb.label("st_loop")
  cb.iload("i")
  cb.iconst(20)
  cb.if_icmpge("st_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "starX", "[I")
  cb.iload("i")
  cb.iload("i")
  cb.iconst(43)
  cb.imul()
  cb.iconst(17)
  cb.iadd()
  cb.iconst(232)
  cb.irem()
  cb.iconst(4)
  cb.iadd()
  cb.iastore()
  cb.aload("this")
  cb.getfield("AstroCanvas", "starY", "[I")
  cb.iload("i")
  cb.iload("i")
  cb.iconst(15)
  cb.imul()
  cb.iconst(24)
  cb.iadd()
  cb.iastore()
  cb.iinc("i", 1)
  cb.goto("st_loop")
  cb.label("st_done")

  # Clear lasers
  cb.iconst(0)
  cb.istore("j")
  cb.label("ls_loop")
  cb.iload("j")
  cb.iconst(6)
  cb.if_icmpge("ls_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("j")
  cb.iconst(-1)
  cb.iastore()
  cb.iinc("j", 1)
  cb.goto("ls_loop")
  cb.label("ls_done")

  # Enemies
  cb.iconst(0)
  cb.istore("k")
  cb.label("en_loop")
  cb.iload("k")
  cb.iconst(5)
  cb.if_icmpge("en_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyX", "[I")
  cb.iload("k")
  cb.iload("k")
  cb.iconst(44)
  cb.imul()
  cb.iconst(24)
  cb.iadd()
  cb.iastore()
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyY", "[I")
  cb.iload("k")
  cb.iload("k")
  cb.iconst(28)
  cb.imul()
  cb.iconst(30)
  cb.iadd()
  cb.iastore()
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyDX", "[I")
  cb.iload("k")
  cb.iload("k")
  cb.iconst(2)
  cb.irem()
  cb.iconst(2)
  cb.imul()
  cb.iconst(1)
  cb.isub()
  cb.iconst(2)
  cb.imul()
  cb.iastore()
  cb.iinc("k", 1)
  cb.goto("en_loop")
  cb.label("en_done")
  cb.vreturn()

  # loadHighScore()V via javax.microedition.rms.RecordStore("astro_hi")
  cb = cls.add_method("loadHighScore", "()V", ACC_PUBLIC)
  cb.label("rms_load_s")
  cb.ldc_str("astro_hi")
  cb.iconst(1)
  cb.invokestatic(
    "javax/microedition/rms/RecordStore",
    "openRecordStore",
    "(Ljava/lang/String;Z)Ljavax/microedition/rms/RecordStore;",
  )
  cb.astore("rs")
  cb.aload("rs")
  cb.invokevirtual("javax/microedition/rms/RecordStore", "getNumRecords", "()I")
  cb.ifle("rms_load_close")
  cb.aload("rs")
  cb.iconst(1)
  cb.invokevirtual("javax/microedition/rms/RecordStore", "getRecord", "(I)[B")
  cb.astore("buf")
  cb.aload("buf")
  cb.ifnull("rms_load_close")
  cb.aload("buf")
  cb.arraylength()
  cb.iconst(4)
  cb.if_icmplt("rms_load_close")
  # highScore = ((buf[0]&0xFF)<<24) | ((buf[1]&0xFF)<<16) | ((buf[2]&0xFF)<<8) | (buf[3]&0xFF)
  cb.aload("this")
  cb.aload("buf")
  cb.iconst(0)
  cb.baload()
  cb.iconst(0xFF)
  cb.iand()
  cb.iconst(24)
  cb.ishl()
  cb.aload("buf")
  cb.iconst(1)
  cb.baload()
  cb.iconst(0xFF)
  cb.iand()
  cb.iconst(16)
  cb.ishl()
  cb.ior()
  cb.aload("buf")
  cb.iconst(2)
  cb.baload()
  cb.iconst(0xFF)
  cb.iand()
  cb.iconst(8)
  cb.ishl()
  cb.ior()
  cb.aload("buf")
  cb.iconst(3)
  cb.baload()
  cb.iconst(0xFF)
  cb.iand()
  cb.ior()
  cb.putfield("AstroCanvas", "highScore", "I")
  cb.label("rms_load_close")
  cb.aload("rs")
  cb.invokevirtual("javax/microedition/rms/RecordStore", "closeRecordStore", "()V")
  cb.label("rms_load_e")
  cb.vreturn()
  cb.label("rms_load_catch")
  cb.pop()
  cb.vreturn()
  cb.try_catch("rms_load_s", "rms_load_e", "rms_load_catch", "java/lang/Throwable")

  # saveHighScore()V via javax.microedition.rms.RecordStore("astro_hi")
  cb = cls.add_method("saveHighScore", "()V", ACC_PUBLIC)
  cb.label("rms_save_s")
  cb.ldc_str("astro_hi")
  cb.iconst(1)
  cb.invokestatic(
    "javax/microedition/rms/RecordStore",
    "openRecordStore",
    "(Ljava/lang/String;Z)Ljavax/microedition/rms/RecordStore;",
  )
  cb.astore("rs")
  cb.iconst(4)
  cb.newarray(T_BYTE)
  cb.astore("buf")
  cb.aload("this")
  cb.getfield("AstroCanvas", "highScore", "I")
  cb.istore("hs")
  cb.aload("buf")
  cb.iconst(0)
  cb.iload("hs")
  cb.iconst(24)
  cb.ishr()
  cb.i2b()
  cb.bastore()
  cb.aload("buf")
  cb.iconst(1)
  cb.iload("hs")
  cb.iconst(16)
  cb.ishr()
  cb.i2b()
  cb.bastore()
  cb.aload("buf")
  cb.iconst(2)
  cb.iload("hs")
  cb.iconst(8)
  cb.ishr()
  cb.i2b()
  cb.bastore()
  cb.aload("buf")
  cb.iconst(3)
  cb.iload("hs")
  cb.i2b()
  cb.bastore()

  cb.aload("rs")
  cb.invokevirtual("javax/microedition/rms/RecordStore", "getNumRecords", "()I")
  cb.ifgt("rms_update_existing")
  cb.aload("rs")
  cb.aload("buf")
  cb.iconst(0)
  cb.iconst(4)
  cb.invokevirtual("javax/microedition/rms/RecordStore", "addRecord", "([BII)I")
  cb.pop()
  cb.goto("rms_save_close")
  cb.label("rms_update_existing")
  cb.aload("rs")
  cb.iconst(1)
  cb.aload("buf")
  cb.iconst(0)
  cb.iconst(4)
  cb.invokevirtual("javax/microedition/rms/RecordStore", "setRecord", "(I[BII)V")
  cb.label("rms_save_close")
  cb.aload("rs")
  cb.invokevirtual("javax/microedition/rms/RecordStore", "closeRecordStore", "()V")
  cb.label("rms_save_e")
  cb.vreturn()
  cb.label("rms_save_catch")
  cb.pop()
  cb.vreturn()
  cb.try_catch("rms_save_s", "rms_save_e", "rms_save_catch", "java/lang/Throwable")

  # playSound(III)V wrapper around javax.microedition.media.Manager.playTone
  cb = cls.add_method("playSound", "(III)V", ACC_PRIVATE)
  cb.label("tone_s")
  cb.iload("arg0")
  cb.iload("arg1")
  cb.iload("arg2")
  cb.invokestatic("javax/microedition/media/Manager", "playTone", "(III)V")
  cb.label("tone_e")
  cb.vreturn()
  cb.label("tone_catch")
  cb.pop()
  cb.vreturn()
  cb.try_catch("tone_s", "tone_e", "tone_catch", "java/lang/Throwable")

  # keyPressed(I)V for keypad 2/4/6/8/5/#
  cb = cls.add_method("keyPressed", "(I)V", ACC_PROTECTED)
  cb.iload("arg0")
  cb.iconst(35)  # '#' exits and saves high score
  cb.if_icmpne("kp_check_num")
  cb.aload("this")
  cb.invokevirtual("AstroCanvas", "saveHighScore", "()V")
  cb.aload("this")
  cb.getfield("AstroCanvas", "midlet", "LAstroMIDlet;")
  cb.iconst(0)
  cb.putfield("AstroMIDlet", "running", "Z")
  cb.aload("this")
  cb.getfield("AstroCanvas", "midlet", "LAstroMIDlet;")
  cb.invokevirtual("javax/microedition/midlet/MIDlet", "notifyDestroyed", "()V")
  cb.vreturn()

  cb.label("kp_check_num")
  cb.iload("arg0")
  cb.iconst(50)  # '2' UP_PRESSED (2)
  cb.if_icmpne("kp_not2")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.iconst(2)
  cb.ior()
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.vreturn()
  cb.label("kp_not2")
  cb.iload("arg0")
  cb.iconst(56)  # '8' DOWN_PRESSED (64)
  cb.if_icmpne("kp_not8")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.iconst(64)
  cb.ior()
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.vreturn()
  cb.label("kp_not8")
  cb.iload("arg0")
  cb.iconst(52)  # '4' LEFT_PRESSED (4)
  cb.if_icmpne("kp_not4")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.iconst(4)
  cb.ior()
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.vreturn()
  cb.label("kp_not4")
  cb.iload("arg0")
  cb.iconst(54)  # '6' RIGHT_PRESSED (32)
  cb.if_icmpne("kp_not6")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.iconst(32)
  cb.ior()
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.vreturn()
  cb.label("kp_not6")
  cb.iload("arg0")
  cb.iconst(53)  # '5' FIRE_PRESSED (256)
  cb.if_icmpne("kp_done")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.iconst(256)
  cb.ior()
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.label("kp_done")
  cb.vreturn()

  # keyReleased(I)V
  cb = cls.add_method("keyReleased", "(I)V", ACC_PROTECTED)
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroCanvas", "rawMask", "I")
  cb.vreturn()

  # updateFrame()V
  cb = cls.add_method("updateFrame", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/game/GameCanvas", "getKeyStates", "()I")
  cb.aload("this")
  cb.getfield("AstroCanvas", "rawMask", "I")
  cb.ior()
  cb.istore("keys")

  # If gameOver and FIRE pressed -> resetGame
  cb.aload("this")
  cb.getfield("AstroCanvas", "gameOver", "Z")
  cb.ifeq("not_over")
  cb.iload("keys")
  cb.iconst(256)
  cb.iand()
  cb.ifeq("ret_early")
  cb.aload("this")
  cb.invokevirtual("AstroCanvas", "resetGame", "()V")
  cb.label("ret_early")
  cb.vreturn()

  cb.label("not_over")
  # Move player ship with LEFT(4), RIGHT(32), UP(2), DOWN(64)
  cb.iload("keys")
  cb.iconst(4)
  cb.iand()
  cb.ifeq("chk_right")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.iconst(18)
  cb.if_icmple("chk_right")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.iconst(5)
  cb.isub()
  cb.putfield("AstroCanvas", "playerX", "I")

  cb.label("chk_right")
  cb.iload("keys")
  cb.iconst(32)
  cb.iand()
  cb.ifeq("chk_up")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.iconst(222)
  cb.if_icmpge("chk_up")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.iconst(5)
  cb.iadd()
  cb.putfield("AstroCanvas", "playerX", "I")

  cb.label("chk_up")
  cb.iload("keys")
  cb.iconst(2)
  cb.iand()
  cb.ifeq("chk_down")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.iconst(60)
  cb.if_icmple("chk_down")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.iconst(4)
  cb.isub()
  cb.putfield("AstroCanvas", "playerY", "I")

  cb.label("chk_down")
  cb.iload("keys")
  cb.iconst(64)
  cb.iand()
  cb.ifeq("chk_fire")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.iconst(282)
  cb.if_icmpge("chk_fire")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.iconst(4)
  cb.iadd()
  cb.putfield("AstroCanvas", "playerY", "I")

  cb.label("chk_fire")
  cb.aload("this")
  cb.getfield("AstroCanvas", "cooldown", "I")
  cb.ifle("can_fire")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "cooldown", "I")
  cb.iconst(1)
  cb.isub()
  cb.putfield("AstroCanvas", "cooldown", "I")
  cb.goto("scroll_stars")

  cb.label("can_fire")
  cb.iload("keys")
  cb.iconst(256)
  cb.iand()
  cb.ifeq("scroll_stars")
  # Spawn laser in first free slot
  cb.iconst(0)
  cb.istore("li")
  cb.label("find_laser")
  cb.iload("li")
  cb.iconst(6)
  cb.if_icmpge("scroll_stars")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iaload()
  cb.ifge("next_laser_slot")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserX", "[I")
  cb.iload("li")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.iastore()
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.iconst(10)
  cb.isub()
  cb.iastore()
  cb.aload("this")
  cb.iconst(5)
  cb.putfield("AstroCanvas", "cooldown", "I")
  cb.aload("this")
  cb.iconst(84)
  cb.iconst(30)
  cb.iconst(70)
  cb.invokevirtual("AstroCanvas", "playSound", "(III)V")
  cb.goto("scroll_stars")
  cb.label("next_laser_slot")
  cb.iinc("li", 1)
  cb.goto("find_laser")

  # Scroll stars
  cb.label("scroll_stars")
  cb.iconst(0)
  cb.istore("si")
  cb.label("st_upd")
  cb.iload("si")
  cb.iconst(20)
  cb.if_icmpge("st_upd_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "starY", "[I")
  cb.iload("si")
  cb.iaload()
  cb.iconst(3)
  cb.iadd()
  cb.istore("sy")
  cb.iload("sy")
  cb.iconst(296)
  cb.if_icmplt("st_keep")
  cb.iconst(24)
  cb.istore("sy")
  cb.label("st_keep")
  cb.aload("this")
  cb.getfield("AstroCanvas", "starY", "[I")
  cb.iload("si")
  cb.iload("sy")
  cb.iastore()
  cb.iinc("si", 1)
  cb.goto("st_upd")
  cb.label("st_upd_done")

  # Advance lasers
  cb.iconst(0)
  cb.istore("li")
  cb.label("ls_upd")
  cb.iload("li")
  cb.iconst(6)
  cb.if_icmpge("ls_upd_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iaload()
  cb.istore("ly")
  cb.iload("ly")
  cb.iflt("ls_next")
  cb.iload("ly")
  cb.iconst(9)
  cb.isub()
  cb.istore("ly")
  cb.iload("ly")
  cb.iconst(22)
  cb.if_icmplt("ls_kill")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iload("ly")
  cb.iastore()
  cb.goto("ls_next")
  cb.label("ls_kill")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iconst(-1)
  cb.iastore()
  cb.label("ls_next")
  cb.iinc("li", 1)
  cb.goto("ls_upd")
  cb.label("ls_upd_done")

  # Update enemies & collisions
  cb.iconst(0)
  cb.istore("ei")
  cb.label("en_upd")
  cb.iload("ei")
  cb.iconst(5)
  cb.if_icmpge("en_upd_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyX", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyDX", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.iadd()
  cb.istore("ex")

  cb.iload("ex")
  cb.iconst(16)
  cb.if_icmplt("flip_dx")
  cb.iload("ex")
  cb.iconst(224)
  cb.if_icmpgt("flip_dx")
  cb.goto("move_ey")
  cb.label("flip_dx")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyDX", "[I")
  cb.iload("ei")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyDX", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.ineg()
  cb.iastore()

  cb.label("move_ey")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyX", "[I")
  cb.iload("ei")
  cb.iload("ex")
  cb.iastore()

  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyY", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.iconst(2)
  cb.iadd()
  cb.istore("ey")

  # Check laser hits on enemy ei
  cb.iconst(0)
  cb.istore("li")
  cb.label("hit_loop")
  cb.iload("li")
  cb.iconst(6)
  cb.if_icmpge("check_bottom")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iaload()
  cb.istore("ly")
  cb.iload("ly")
  cb.iflt("hit_next")

  cb.aload("this")
  cb.getfield("AstroCanvas", "laserX", "[I")
  cb.iload("li")
  cb.iaload()
  cb.iload("ex")
  cb.isub()
  cb.invokestatic("java/lang/Math", "abs", "(I)I")
  cb.iconst(14)
  cb.if_icmpgt("hit_next")

  cb.iload("ly")
  cb.iload("ey")
  cb.isub()
  cb.invokestatic("java/lang/Math", "abs", "(I)I")
  cb.iconst(12)
  cb.if_icmpgt("hit_next")

  # Hit!
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iconst(-1)
  cb.iastore()
  cb.iconst(28)
  cb.istore("ey")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "score", "I")
  cb.iconst(25)
  cb.iadd()
  cb.putfield("AstroCanvas", "score", "I")
  cb.aload("this")
  cb.iconst(62)
  cb.iconst(65)
  cb.iconst(85)
  cb.invokevirtual("AstroCanvas", "playSound", "(III)V")

  cb.aload("this")
  cb.getfield("AstroCanvas", "score", "I")
  cb.aload("this")
  cb.getfield("AstroCanvas", "highScore", "I")
  cb.if_icmple("check_bottom")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "score", "I")
  cb.putfield("AstroCanvas", "highScore", "I")
  cb.goto("check_bottom")

  cb.label("hit_next")
  cb.iinc("li", 1)
  cb.goto("hit_loop")

  cb.label("check_bottom")
  cb.iload("ey")
  cb.iconst(290)
  cb.if_icmplt("store_ey")
  cb.iconst(28)
  cb.istore("ey")
  cb.aload("this")
  cb.aload("this")
  cb.getfield("AstroCanvas", "shield", "I")
  cb.iconst(20)
  cb.isub()
  cb.putfield("AstroCanvas", "shield", "I")
  cb.aload("this")
  cb.iconst(45)
  cb.iconst(140)
  cb.iconst(90)
  cb.invokevirtual("AstroCanvas", "playSound", "(III)V")

  cb.aload("this")
  cb.getfield("AstroCanvas", "shield", "I")
  cb.ifgt("store_ey")
  cb.aload("this")
  cb.iconst(0)
  cb.putfield("AstroCanvas", "shield", "I")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("AstroCanvas", "gameOver", "Z")
  cb.aload("this")
  cb.invokevirtual("AstroCanvas", "saveHighScore", "()V")

  cb.label("store_ey")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyY", "[I")
  cb.iload("ei")
  cb.iload("ey")
  cb.iastore()
  cb.iinc("ei", 1)
  cb.goto("en_upd")
  cb.label("en_upd_done")
  cb.vreturn()

  # renderFrame()V
  cb = cls.add_method("renderFrame", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokevirtual(
    "javax/microedition/lcdui/game/GameCanvas",
    "getGraphics",
    "()Ljavax/microedition/lcdui/Graphics;",
  )
  cb.astore("g")

  # Clear 240x320 background
  cb.aload("g")
  cb.iconst(0x060A14)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iconst(0)
  cb.iconst(0)
  cb.iconst(240)
  cb.iconst(320)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")

  # Draw starfield
  cb.aload("g")
  cb.iconst(0x88B4E8)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.iconst(0)
  cb.istore("si")
  cb.label("dr_stars")
  cb.iload("si")
  cb.iconst(20)
  cb.if_icmpge("dr_stars_done")
  cb.aload("g")
  cb.aload("this")
  cb.getfield("AstroCanvas", "starX", "[I")
  cb.iload("si")
  cb.iaload()
  cb.aload("this")
  cb.getfield("AstroCanvas", "starY", "[I")
  cb.iload("si")
  cb.iaload()
  cb.iconst(2)
  cb.iconst(2)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.iinc("si", 1)
  cb.goto("dr_stars")
  cb.label("dr_stars_done")

  # Draw player fighter ship (triangle + wings)
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerX", "I")
  cb.istore("px")
  cb.aload("this")
  cb.getfield("AstroCanvas", "playerY", "I")
  cb.istore("py")

  cb.aload("g")
  cb.iconst(0x00E5FF)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iload("px")
  cb.iload("py")
  cb.iconst(10)
  cb.isub()
  cb.iload("px")
  cb.iconst(10)
  cb.isub()
  cb.iload("py")
  cb.iconst(8)
  cb.iadd()
  cb.iload("px")
  cb.iconst(10)
  cb.iadd()
  cb.iload("py")
  cb.iconst(8)
  cb.iadd()
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillTriangle", "(IIIIII)V")

  # Draw lasers
  cb.aload("g")
  cb.iconst(0xFFEA00)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.iconst(0)
  cb.istore("li")
  cb.label("dr_lasers")
  cb.iload("li")
  cb.iconst(6)
  cb.if_icmpge("dr_lasers_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserY", "[I")
  cb.iload("li")
  cb.iaload()
  cb.istore("ly")
  cb.iload("ly")
  cb.iflt("dr_laser_next")
  cb.aload("g")
  cb.aload("this")
  cb.getfield("AstroCanvas", "laserX", "[I")
  cb.iload("li")
  cb.iaload()
  cb.iconst(1)
  cb.isub()
  cb.iload("ly")
  cb.iconst(3)
  cb.iconst(9)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.label("dr_laser_next")
  cb.iinc("li", 1)
  cb.goto("dr_lasers")
  cb.label("dr_lasers_done")

  # Draw enemies
  cb.aload("g")
  cb.iconst(0xFF3D71)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.iconst(0)
  cb.istore("ei")
  cb.label("dr_enemies")
  cb.iload("ei")
  cb.iconst(5)
  cb.if_icmpge("dr_enemies_done")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyX", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.istore("ex")
  cb.aload("this")
  cb.getfield("AstroCanvas", "enemyY", "[I")
  cb.iload("ei")
  cb.iaload()
  cb.istore("ey")
  cb.aload("g")
  cb.iload("ex")
  cb.iconst(9)
  cb.isub()
  cb.iload("ey")
  cb.iconst(7)
  cb.isub()
  cb.iconst(18)
  cb.iconst(14)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.iinc("ei", 1)
  cb.goto("dr_enemies")
  cb.label("dr_enemies_done")

  # Top HUD bar
  cb.aload("g")
  cb.iconst(0x101A30)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iconst(0)
  cb.iconst(0)
  cb.iconst(240)
  cb.iconst(22)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")

  # Score & High score text
  cb.new_obj("java/lang/StringBuffer")
  cb.dup()
  cb.ldc_str("SC:")
  cb.invokespecial("java/lang/StringBuffer", "<init>", "(Ljava/lang/String;)V")
  cb.aload("this")
  cb.getfield("AstroCanvas", "score", "I")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.ldc_str(" HI:")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(Ljava/lang/String;)Ljava/lang/StringBuffer;")
  cb.aload("this")
  cb.getfield("AstroCanvas", "highScore", "I")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.invokevirtual("java/lang/StringBuffer", "toString", "()Ljava/lang/String;")
  cb.astore("hudStr")

  cb.aload("g")
  cb.iconst(0xFFFFFF)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.aload("hudStr")
  cb.iconst(4)
  cb.iconst(3)
  cb.iconst(20)  # TOP | LEFT
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")

  # Shield bar on top right
  cb.aload("g")
  cb.iconst(0x00E676)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iconst(184)
  cb.iconst(6)
  cb.aload("this")
  cb.getfield("AstroCanvas", "shield", "I")
  cb.iconst(2)
  cb.idiv()
  cb.iconst(10)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")

  # Game over banner if gameOver
  cb.aload("this")
  cb.getfield("AstroCanvas", "gameOver", "Z")
  cb.ifeq("draw_footer")
  cb.aload("g")
  cb.iconst(0x280818)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iconst(24)
  cb.iconst(128)
  cb.iconst(192)
  cb.iconst(54)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.aload("g")
  cb.iconst(0xFF5252)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.ldc_str("GAME OVER - PRESS 5")
  cb.iconst(120)
  cb.iconst(148)
  cb.iconst(17)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")

  cb.label("draw_footer")
  cb.aload("g")
  cb.iconst(0x101A30)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.iconst(0)
  cb.iconst(298)
  cb.iconst(240)
  cb.iconst(22)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "fillRect", "(IIII)V")
  cb.aload("g")
  cb.iconst(0x80D8FF)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "setColor", "(I)V")
  cb.aload("g")
  cb.ldc_str("2468:Move 5:Laser #:Exit")
  cb.iconst(120)
  cb.iconst(302)
  cb.iconst(17)
  cb.invokevirtual("javax/microedition/lcdui/Graphics", "drawString", "(Ljava/lang/String;III)V")

  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/game/GameCanvas", "flushGraphics", "()V")
  cb.vreturn()

  return cls.to_bytes()


# =============================================================================
# Suite 3: J2ME Net & UI Tool (NetToolJ2ME.jar + NetToolJ2ME.jad)
# =============================================================================

def build_net_tool_midlet_class():
  cls = ClassBuilder(
    "NetToolMIDlet",
    "javax/microedition/midlet/MIDlet",
    ["javax/microedition/lcdui/CommandListener", "java/lang/Runnable"],
  )
  cls.add_field("display", "Ljavax/microedition/lcdui/Display;", ACC_PRIVATE)
  cls.add_field("menu", "Ljavax/microedition/lcdui/List;", ACC_PRIVATE)
  cls.add_field("httpForm", "Ljavax/microedition/lcdui/Form;", ACC_PRIVATE)
  cls.add_field("tcpForm", "Ljavax/microedition/lcdui/Form;", ACC_PRIVATE)
  cls.add_field("uiForm", "Ljavax/microedition/lcdui/Form;", ACC_PRIVATE)
  cls.add_field("notesBox", "Ljavax/microedition/lcdui/TextBox;", ACC_PRIVATE)
  cls.add_field("urlField", "Ljavax/microedition/lcdui/TextField;", ACC_PRIVATE)
  cls.add_field("hostField", "Ljavax/microedition/lcdui/TextField;", ACC_PRIVATE)
  cls.add_field("statusItem", "Ljavax/microedition/lcdui/StringItem;", ACC_PRIVATE)
  cls.add_field("tcpItem", "Ljavax/microedition/lcdui/StringItem;", ACC_PRIVATE)
  cls.add_field("modeChoice", "Ljavax/microedition/lcdui/ChoiceGroup;", ACC_PRIVATE)
  cls.add_field("gauge", "Ljavax/microedition/lcdui/Gauge;", ACC_PRIVATE)
  cls.add_field("exitCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("backCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("fetchCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("probeCmd", "Ljavax/microedition/lcdui/Command;", ACC_PRIVATE)
  cls.add_field("taskType", "I", ACC_PRIVATE)

  # <init>()V
  cb = cls.add_method("<init>", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.invokespecial("javax/microedition/midlet/MIDlet", "<init>", "()V")

  # Commands
  for field, label, ctype, prio in (
    ("exitCmd", "Exit", 7, 1),
    ("backCmd", "Back", 2, 1),
    ("fetchCmd", "Fetch", 4, 1),
    ("probeCmd", "Probe", 4, 1),
  ):
    cb.aload("this")
    cb.new_obj("javax/microedition/lcdui/Command")
    cb.dup()
    cb.ldc_str(label)
    cb.iconst(ctype)
    cb.iconst(prio)
    cb.invokespecial("javax/microedition/lcdui/Command", "<init>", "(Ljava/lang/String;II)V")
    cb.putfield("NetToolMIDlet", field, "Ljavax/microedition/lcdui/Command;")

  # Main List (Choice.IMPLICIT = 3)
  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/List")
  cb.dup()
  cb.ldc_str("J2ME Net & UI Tool")
  cb.iconst(3)
  cb.invokespecial("javax/microedition/lcdui/List", "<init>", "(Ljava/lang/String;I)V")
  cb.putfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")

  for item_text in (
    "1. HTTP GET Client",
    "2. TCP Socket Probe",
    "3. NeoUI Form & Gauge",
    "4. Scratchpad TextBox",
    "5. Runtime Alert Info",
  ):
    cb.aload("this")
    cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
    cb.ldc_str(item_text)
    cb.aconst_null()
    cb.invokevirtual(
      "javax/microedition/lcdui/List",
      "append",
      "(Ljava/lang/String;Ljavax/microedition/lcdui/Image;)I",
    )
    cb.pop()

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "exitCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")

  # HTTP Form
  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Form")
  cb.dup()
  cb.ldc_str("HTTP GET Client")
  cb.invokespecial("javax/microedition/lcdui/Form", "<init>", "(Ljava/lang/String;)V")
  cb.putfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/TextField")
  cb.dup()
  cb.ldc_str("URL:")
  cb.ldc_str("http://example.com/")
  cb.iconst(128)
  cb.iconst(4)  # TextField.URL
  cb.invokespecial(
    "javax/microedition/lcdui/TextField",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;II)V",
  )
  cb.putfield("NetToolMIDlet", "urlField", "Ljavax/microedition/lcdui/TextField;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/StringItem")
  cb.dup()
  cb.ldc_str("Status:")
  cb.ldc_str("Ready - press Fetch")
  cb.invokespecial(
    "javax/microedition/lcdui/StringItem",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;)V",
  )
  cb.putfield("NetToolMIDlet", "statusItem", "Ljavax/microedition/lcdui/StringItem;")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "urlField", "Ljavax/microedition/lcdui/TextField;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "statusItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "fetchCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "backCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")

  # TCP Socket Form
  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Form")
  cb.dup()
  cb.ldc_str("TCP Socket Probe")
  cb.invokespecial("javax/microedition/lcdui/Form", "<init>", "(Ljava/lang/String;)V")
  cb.putfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/TextField")
  cb.dup()
  cb.ldc_str("Host:Port")
  cb.ldc_str("example.com:80")
  cb.iconst(64)
  cb.iconst(0)  # TextField.ANY
  cb.invokespecial(
    "javax/microedition/lcdui/TextField",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;II)V",
  )
  cb.putfield("NetToolMIDlet", "hostField", "Ljavax/microedition/lcdui/TextField;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/StringItem")
  cb.dup()
  cb.ldc_str("Socket:")
  cb.ldc_str("Idle")
  cb.invokespecial(
    "javax/microedition/lcdui/StringItem",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;)V",
  )
  cb.putfield("NetToolMIDlet", "tcpItem", "Ljavax/microedition/lcdui/StringItem;")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "hostField", "Ljavax/microedition/lcdui/TextField;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "probeCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "backCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")

  # NeoUI Widget Showcase Form (ChoiceGroup + Gauge)
  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Form")
  cb.dup()
  cb.ldc_str("NeoUI LCDUI Widgets")
  cb.invokespecial("javax/microedition/lcdui/Form", "<init>", "(Ljava/lang/String;)V")
  cb.putfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/ChoiceGroup")
  cb.dup()
  cb.ldc_str("Renderer:")
  cb.iconst(1)  # Choice.EXCLUSIVE
  cb.invokespecial(
    "javax/microedition/lcdui/ChoiceGroup",
    "<init>",
    "(Ljava/lang/String;I)V",
  )
  cb.putfield("NetToolMIDlet", "modeChoice", "Ljavax/microedition/lcdui/ChoiceGroup;")

  for opt in ("Adreno 304 Glamor", "X11 Pixmap", "M3G JSR-184"):
    cb.aload("this")
    cb.getfield("NetToolMIDlet", "modeChoice", "Ljavax/microedition/lcdui/ChoiceGroup;")
    cb.ldc_str(opt)
    cb.aconst_null()
    cb.invokevirtual(
      "javax/microedition/lcdui/ChoiceGroup",
      "append",
      "(Ljava/lang/String;Ljavax/microedition/lcdui/Image;)I",
    )
    cb.pop()

  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/Gauge")
  cb.dup()
  cb.ldc_str("Gain:")
  cb.iconst(1)  # interactive
  cb.iconst(10)
  cb.iconst(8)
  cb.invokespecial("javax/microedition/lcdui/Gauge", "<init>", "(Ljava/lang/String;ZII)V")
  cb.putfield("NetToolMIDlet", "gauge", "Ljavax/microedition/lcdui/Gauge;")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "modeChoice", "Ljavax/microedition/lcdui/ChoiceGroup;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "gauge", "Ljavax/microedition/lcdui/Gauge;")
  cb.invokevirtual("javax/microedition/lcdui/Form", "append", "(Ljavax/microedition/lcdui/Item;)I")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "backCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")

  # Scratchpad TextBox
  cb.aload("this")
  cb.new_obj("javax/microedition/lcdui/TextBox")
  cb.dup()
  cb.ldc_str("Scratchpad")
  cb.ldc_str("Bananarchy MIDP 2.0 / CLDC 1.1 Notes")
  cb.iconst(256)
  cb.iconst(0)
  cb.invokespecial(
    "javax/microedition/lcdui/TextBox",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;II)V",
  )
  cb.putfield("NetToolMIDlet", "notesBox", "Ljavax/microedition/lcdui/TextBox;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "notesBox", "Ljavax/microedition/lcdui/TextBox;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "backCmd", "Ljavax/microedition/lcdui/Command;")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "addCommand", "(Ljavax/microedition/lcdui/Command;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "notesBox", "Ljavax/microedition/lcdui/TextBox;")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/lcdui/Displayable", "setCommandListener", "(Ljavax/microedition/lcdui/CommandListener;)V")
  cb.vreturn()

  # startApp()V
  cb = cls.add_method("startApp", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.aload("this")
  cb.invokestatic(
    "javax/microedition/lcdui/Display",
    "getDisplay",
    "(Ljavax/microedition/midlet/MIDlet;)Ljavax/microedition/lcdui/Display;",
  )
  cb.putfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  # pauseApp()V
  cb = cls.add_method("pauseApp", "()V", ACC_PUBLIC)
  cb.vreturn()

  # destroyApp(Z)V
  cb = cls.add_method("destroyApp", "(Z)V", ACC_PUBLIC)
  cb.vreturn()

  # commandAction(Command, Displayable)V
  cb = cls.add_method(
    "commandAction",
    "(Ljavax/microedition/lcdui/Command;Ljavax/microedition/lcdui/Displayable;)V",
    ACC_PUBLIC,
  )
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "exitCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("not_exit")
  cb.aload("this")
  cb.invokevirtual("javax/microedition/midlet/MIDlet", "notifyDestroyed", "()V")
  cb.vreturn()

  cb.label("not_exit")
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "backCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("not_back")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  cb.label("not_back")
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "fetchCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("not_fetch")
  cb.aload("this")
  cb.iconst(1)
  cb.putfield("NetToolMIDlet", "taskType", "I")
  cb.new_obj("java/lang/Thread")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("java/lang/Thread", "<init>", "(Ljava/lang/Runnable;)V")
  cb.invokevirtual("java/lang/Thread", "start", "()V")
  cb.vreturn()

  cb.label("not_fetch")
  cb.aload("arg0")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "probeCmd", "Ljavax/microedition/lcdui/Command;")
  cb.if_acmpne("check_list_select")
  cb.aload("this")
  cb.iconst(2)
  cb.putfield("NetToolMIDlet", "taskType", "I")
  cb.new_obj("java/lang/Thread")
  cb.dup()
  cb.aload("this")
  cb.invokespecial("java/lang/Thread", "<init>", "(Ljava/lang/Runnable;)V")
  cb.invokevirtual("java/lang/Thread", "start", "()V")
  cb.vreturn()

  cb.label("check_list_select")
  cb.aload("arg1")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.if_acmpne("cmd_end")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.invokevirtual("javax/microedition/lcdui/List", "getSelectedIndex", "()I")
  cb.istore("sel")

  cb.iload("sel")
  cb.ifne("sel_not_0")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "httpForm", "Ljavax/microedition/lcdui/Form;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  cb.label("sel_not_0")
  cb.iload("sel")
  cb.iconst(1)
  cb.if_icmpne("sel_not_1")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpForm", "Ljavax/microedition/lcdui/Form;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  cb.label("sel_not_1")
  cb.iload("sel")
  cb.iconst(2)
  cb.if_icmpne("sel_not_2")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "uiForm", "Ljavax/microedition/lcdui/Form;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  cb.label("sel_not_2")
  cb.iload("sel")
  cb.iconst(3)
  cb.if_icmpne("sel_alert")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "notesBox", "Ljavax/microedition/lcdui/TextBox;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Displayable;)V",
  )
  cb.vreturn()

  cb.label("sel_alert")
  cb.new_obj("javax/microedition/lcdui/Alert")
  cb.dup()
  cb.ldc_str("Runtime Info")
  cb.ldc_str("Bananarchy J2ME MIDP-2.0 / CLDC-1.1 / M3G-1.1 (240x320)")
  cb.aconst_null()
  cb.getstatic("javax/microedition/lcdui/AlertType", "INFO", "Ljavax/microedition/lcdui/AlertType;")
  cb.invokespecial(
    "javax/microedition/lcdui/Alert",
    "<init>",
    "(Ljava/lang/String;Ljava/lang/String;Ljavax/microedition/lcdui/Image;Ljavax/microedition/lcdui/AlertType;)V",
  )
  cb.astore("alert")
  cb.aload("alert")
  cb.iconst(3000)
  cb.invokevirtual("javax/microedition/lcdui/Alert", "setTimeout", "(I)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "display", "Ljavax/microedition/lcdui/Display;")
  cb.aload("alert")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "menu", "Ljavax/microedition/lcdui/List;")
  cb.invokevirtual(
    "javax/microedition/lcdui/Display",
    "setCurrent",
    "(Ljavax/microedition/lcdui/Alert;Ljavax/microedition/lcdui/Displayable;)V",
  )

  cb.label("cmd_end")
  cb.vreturn()

  # run()V - executes HttpConnection or SocketConnection
  cb = cls.add_method("run", "()V", ACC_PUBLIC)
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "taskType", "I")
  cb.iconst(1)
  cb.if_icmpne("do_tcp_task")

  # Task 1: HttpConnection GET
  cb.label("http_try_s")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "statusItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.ldc_str("Connecting...")
  cb.invokevirtual("javax/microedition/lcdui/StringItem", "setText", "(Ljava/lang/String;)V")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "urlField", "Ljavax/microedition/lcdui/TextField;")
  cb.invokevirtual("javax/microedition/lcdui/TextField", "getString", "()Ljava/lang/String;")
  cb.invokestatic(
    "javax/microedition/io/Connector",
    "open",
    "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
  )
  cb.checkcast("javax/microedition/io/HttpConnection")
  cb.astore("hc")

  cb.aload("hc")
  cb.ldc_str("GET")
  cb.invokeinterface("javax/microedition/io/HttpConnection", "setRequestMethod", "(Ljava/lang/String;)V")

  cb.aload("hc")
  cb.invokeinterface("javax/microedition/io/HttpConnection", "getResponseCode", "()I")
  cb.istore("rc")

  cb.aload("hc")
  cb.invokeinterface("javax/microedition/io/InputConnection", "openInputStream", "()Ljava/io/InputStream;")
  cb.astore("in")

  cb.iconst(128)
  cb.newarray(T_BYTE)
  cb.astore("buf")
  cb.aload("in")
  cb.aload("buf")
  cb.invokevirtual("java/io/InputStream", "read", "([B)I")
  cb.istore("nread")

  cb.aload("in")
  cb.invokevirtual("java/io/InputStream", "close", "()V")
  cb.aload("hc")
  cb.invokeinterface("javax/microedition/io/Connection", "close", "()V")

  cb.new_obj("java/lang/StringBuffer")
  cb.dup()
  cb.ldc_str("HTTP ")
  cb.invokespecial("java/lang/StringBuffer", "<init>", "(Ljava/lang/String;)V")
  cb.iload("rc")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.ldc_str(" (")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(Ljava/lang/String;)Ljava/lang/StringBuffer;")
  cb.iload("nread")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.ldc_str(" bytes)")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(Ljava/lang/String;)Ljava/lang/StringBuffer;")
  cb.invokevirtual("java/lang/StringBuffer", "toString", "()Ljava/lang/String;")
  cb.astore("msg")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "statusItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.aload("msg")
  cb.invokevirtual("javax/microedition/lcdui/StringItem", "setText", "(Ljava/lang/String;)V")
  cb.label("http_try_e")
  cb.vreturn()

  cb.label("http_catch")
  cb. pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "statusItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.ldc_str("HTTP error / offline")
  cb.invokevirtual("javax/microedition/lcdui/StringItem", "setText", "(Ljava/lang/String;)V")
  cb.vreturn()
  cb.try_catch("http_try_s", "http_try_e", "http_catch", "java/lang/Throwable")

  # Task 2: SocketConnection probe
  cb.label("do_tcp_task")
  cb.label("tcp_try_s")
  cb.new_obj("java/lang/StringBuffer")
  cb.dup()
  cb.ldc_str("socket://")
  cb.invokespecial("java/lang/StringBuffer", "<init>", "(Ljava/lang/String;)V")
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "hostField", "Ljavax/microedition/lcdui/TextField;")
  cb.invokevirtual("javax/microedition/lcdui/TextField", "getString", "()Ljava/lang/String;")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(Ljava/lang/String;)Ljava/lang/StringBuffer;")
  cb.invokevirtual("java/lang/StringBuffer", "toString", "()Ljava/lang/String;")
  cb.invokestatic(
    "javax/microedition/io/Connector",
    "open",
    "(Ljava/lang/String;)Ljavax/microedition/io/Connection;",
  )
  cb.checkcast("javax/microedition/io/SocketConnection")
  cb.astore("sc")

  cb.aload("sc")
  cb.invokeinterface("javax/microedition/io/OutputConnection", "openOutputStream", "()Ljava/io/OutputStream;")
  cb.astore("out")
  cb.aload("out")
  cb.ldc_str("HEAD / HTTP/1.0\r\n\r\n")
  cb.invokevirtual("java/lang/String", "getBytes", "()[B")
  cb.invokevirtual("java/io/OutputStream", "write", "([B)V")
  cb.aload("out")
  cb.invokevirtual("java/io/OutputStream", "flush", "()V")

  cb.aload("sc")
  cb.invokeinterface("javax/microedition/io/InputConnection", "openInputStream", "()Ljava/io/InputStream;")
  cb.astore("sin")
  cb.iconst(64)
  cb.newarray(T_BYTE)
  cb.astore("sbuf")
  cb.aload("sin")
  cb.aload("sbuf")
  cb.invokevirtual("java/io/InputStream", "read", "([B)I")
  cb.istore("sn")

  cb.aload("sin")
  cb.invokevirtual("java/io/InputStream", "close", "()V")
  cb.aload("out")
  cb.invokevirtual("java/io/OutputStream", "close", "()V")
  cb.aload("sc")
  cb.invokeinterface("javax/microedition/io/Connection", "close", "()V")

  cb.new_obj("java/lang/StringBuffer")
  cb.dup()
  cb.ldc_str("Connected, rx=")
  cb.invokespecial("java/lang/StringBuffer", "<init>", "(Ljava/lang/String;)V")
  cb.iload("sn")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(I)Ljava/lang/StringBuffer;")
  cb.ldc_str("B")
  cb.invokevirtual("java/lang/StringBuffer", "append", "(Ljava/lang/String;)Ljava/lang/StringBuffer;")
  cb.invokevirtual("java/lang/StringBuffer", "toString", "()Ljava/lang/String;")
  cb.astore("tmsg")

  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.aload("tmsg")
  cb.invokevirtual("javax/microedition/lcdui/StringItem", "setText", "(Ljava/lang/String;)V")
  cb.label("tcp_try_e")
  cb.vreturn()

  cb.label("tcp_catch")
  cb.pop()
  cb.aload("this")
  cb.getfield("NetToolMIDlet", "tcpItem", "Ljavax/microedition/lcdui/StringItem;")
  cb.ldc_str("Socket error / refused")
  cb.invokevirtual("javax/microedition/lcdui/StringItem", "setText", "(Ljava/lang/String;)V")
  cb.vreturn()
  cb.try_catch("tcp_try_s", "tcp_try_e", "tcp_catch", "java/lang/Throwable")

  return cls.to_bytes()


# =============================================================================
# Packaging & JAD generation
# =============================================================================

def package_midlet_suite(
  base_name,
  midlet_name,
  vendor,
  version,
  main_class,
  description,
  classes_dict,
  icon_bytes,
):
  """Write deterministic .jar and .jad files to OUT_SHARE_DIR and OUT_USER_DIR."""
  manifest = (
    "Manifest-Version: 1.0\r\n"
    f"MIDlet-Name: {midlet_name}\r\n"
    f"MIDlet-Version: {version}\r\n"
    f"MIDlet-Vendor: {vendor}\r\n"
    f"MIDlet-Description: {description}\r\n"
    "MIDlet-Icon: /icon.png\r\n"
    f"MIDlet-1: {midlet_name}, /icon.png, {main_class}\r\n"
    "MicroEdition-Profile: MIDP-2.0\r\n"
    "MicroEdition-Configuration: CLDC-1.1\r\n"
  )

  jar_filename = f"{base_name}.jar"
  jad_filename = f"{base_name}.jad"
  jar_path = os.path.join(OUT_SHARE_DIR, jar_filename)
  jad_path = os.path.join(OUT_SHARE_DIR, jad_filename)

  fixed_dt = (2026, 1, 1, 0, 0, 0)
  with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zi_mf = zipfile.ZipInfo("META-INF/MANIFEST.MF", date_time=fixed_dt)
    zi_mf.compress_type = zipfile.ZIP_DEFLATED
    zi_mf.external_attr = 0o644 << 16
    zf.writestr(zi_mf, manifest.encode("utf-8"))

    zi_ic = zipfile.ZipInfo("icon.png", date_time=fixed_dt)
    zi_ic.compress_type = zipfile.ZIP_DEFLATED
    zi_ic.external_attr = 0o644 << 16
    zf.writestr(zi_ic, icon_bytes)

    for cls_file, cls_bytes in classes_dict.items():
      verify_class_bytes(cls_bytes)
      zi = zipfile.ZipInfo(cls_file, date_time=fixed_dt)
      zi.compress_type = zipfile.ZIP_DEFLATED
      zi.external_attr = 0o644 << 16
      zf.writestr(zi, cls_bytes)

  jar_size = os.path.getsize(jar_path)
  jad_content = (
    f"MIDlet-Name: {midlet_name}\r\n"
    f"MIDlet-Version: {version}\r\n"
    f"MIDlet-Vendor: {vendor}\r\n"
    f"MIDlet-Description: {description}\r\n"
    "MIDlet-Icon: /icon.png\r\n"
    f"MIDlet-1: {midlet_name}, /icon.png, {main_class}\r\n"
    f"MIDlet-Jar-URL: {jar_filename}\r\n"
    f"MIDlet-Jar-Size: {jar_size}\r\n"
    "MicroEdition-Profile: MIDP-2.0\r\n"
    "MicroEdition-Configuration: CLDC-1.1\r\n"
  )
  with open(jad_path, "wb") as f:
    f.write(jad_content.encode("utf-8"))

  shutil.copy2(jar_path, os.path.join(OUT_USER_DIR, jar_filename))
  shutil.copy2(jad_path, os.path.join(OUT_USER_DIR, jad_filename))
  print(f"Generated {jar_filename} ({jar_size} bytes) and {jad_filename}")


def main():
  os.makedirs(OUT_SHARE_DIR, exist_ok=True)
  os.makedirs(OUT_USER_DIR, exist_ok=True)

  # 1. ReMEdy3DArena
  package_midlet_suite(
    base_name="ReMEdy3DArena",
    midlet_name="ReMEdy 3D Arena",
    vendor="Bananarchy",
    version="1.0.0",
    main_class="Arena3DMIDlet",
    description="240x320 M3G (JSR-184) hardware-accelerated 3D cube/crystal arena",
    classes_dict={
      "Arena3DMIDlet.class": build_arena_midlet_class(),
      "Arena3DCanvas.class": build_arena_canvas_class(),
    },
    icon_bytes=make_png_icon((0, 229, 255), (180, 64, 255)),
  )

  # 2. AstroBlaster240
  package_midlet_suite(
    base_name="AstroBlaster240",
    midlet_name="Astro Blaster 240",
    vendor="Bananarchy",
    version="1.0.0",
    main_class="AstroMIDlet",
    description="240x320 MIDP 2.0 GameCanvas vertical space shooter with RMS & MMAPI",
    classes_dict={
      "AstroMIDlet.class": build_astro_midlet_class(),
      "AstroCanvas.class": build_astro_canvas_class(),
    },
    icon_bytes=make_png_icon((255, 234, 0), (255, 61, 113)),
  )

  # 3. NetToolJ2ME
  package_midlet_suite(
    base_name="NetToolJ2ME",
    midlet_name="J2ME Net & UI Tool",
    vendor="Bananarchy",
    version="1.0.0",
    main_class="NetToolMIDlet",
    description="NeoUI-themed LCDUI widget suite with GCF HTTP and TCP Socket tools",
    classes_dict={
      "NetToolMIDlet.class": build_net_tool_midlet_class(),
    },
    icon_bytes=make_png_icon((0, 230, 118), (64, 196, 255)),
  )


if __name__ == "__main__":
  main()
