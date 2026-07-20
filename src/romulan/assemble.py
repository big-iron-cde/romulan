"""Minimal two-pass 6502/65C02 assembler.

Parses mnemonic assembly source into a mapping of CPU address -> byte —
the same shape produced by :func:`romulan.build_rom.parse_hex_file` — so
:func:`romulan.build_rom.build_rom` can consume either input format. The
input format is detected by :func:`romulan.build_rom.detect_format`.

Supported syntax (all origins are CPU addresses, not file offsets)::

    ; comment
    label:                      ; alone on a line or before an instruction
    .org $8000                  ; set origin (must be in $8000-$FFFF)
    .byte $EA, 5, label         ; raw bytes, comma separated
    .word $8000, label          ; little-endian words (interrupt vectors)
    CLC                         ; implied
    ASL / ASL A                 ; accumulator
    LDA #$05                    ; immediate
    LDA $10                     ; zero page (numeric operand < $100)
    STA $4000                   ; absolute (numeric operand >= $100)
    LDA $10,X                   ; zero page,X   LDA $1234,Y  absolute,Y
    JMP ($1234)                 ; indirect (JMP only)
    LDA ($10,X) / ($10),Y       ; (zp,X) and (zp),Y
    LDA ($10)                   ; 65C02 (zp) indirect
    BNE loop                    ; relative branch to a label or address

Numbers may be written as ``$hex``, ``0xhex`` or decimal. When an operand
is a label it is always encoded in absolute (16-bit) form, because labels
can only refer to ROM addresses ($8000-$FFFF). A numeric operand narrower
than $100 uses the zero-page form when the mnemonic provides one, and the
absolute form otherwise (so ``JMP $0000`` encodes as 4C 00 00).

Instruction set: all 56 official NMOS 6502 mnemonics plus the W65C02
additions used in the course (BRA, PHX, PHY, PLX, PLY, STZ, TRB, TSB,
STP, WAI, accumulator INC/DEC, BIT immediate/indexed, (zp) indirect).

If no ``.org`` precedes the first byte, assembly starts at $8000. All
emitted bytes must land in the ROM region $8000-$FFFF; operand *values*
(e.g. the target of ``JMP $0000``) are unrestricted.

Errors raise :class:`ValueError` with a line number, mirroring
:func:`romulan.build_rom.parse_hex_file`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROM_BASE_ADDR = 0x8000  # ROM starts at CPU address $8000 (as in build_rom)

# Addressing mode identifiers
_IMP = "imp"    # implied
_ACC = "acc"    # accumulator
_IMM = "imm"    # #immediate
_ZP = "zp"      # zero page
_ZPX = "zpx"    # zero page,X
_ZPY = "zpy"    # zero page,Y
_ABS = "abs"    # absolute
_ABSX = "absx"  # absolute,X
_ABSY = "absy"  # absolute,Y
_IND = "ind"    # (absolute) — JMP only
_IZX = "izx"    # (zero page,X)
_IZY = "izy"    # (zero page),Y
_IZP = "izp"    # (zero page) — 65C02 only
_REL = "rel"    # relative (branches)

_MODE_SIZE = {
    _IMP: 1, _ACC: 1,
    _IMM: 2, _ZP: 2, _ZPX: 2, _ZPY: 2, _IZX: 2, _IZY: 2, _IZP: 2, _REL: 2,
    _ABS: 3, _ABSX: 3, _ABSY: 3, _IND: 3,
}

# Opcode table: mnemonic -> {addressing mode: opcode}.
# Official NMOS 6502 set plus the W65C02 additions listed above.
_OPCODES: dict[str, dict[str, int]] = {
    "ADC": {_IMM: 0x69, _ZP: 0x65, _ZPX: 0x75, _ABS: 0x6D, _ABSX: 0x7D,
            _ABSY: 0x79, _IZX: 0x61, _IZY: 0x71, _IZP: 0x72},
    "AND": {_IMM: 0x29, _ZP: 0x25, _ZPX: 0x35, _ABS: 0x2D, _ABSX: 0x3D,
            _ABSY: 0x39, _IZX: 0x21, _IZY: 0x31, _IZP: 0x32},
    "ASL": {_ACC: 0x0A, _ZP: 0x06, _ZPX: 0x16, _ABS: 0x0E, _ABSX: 0x1E},
    "BCC": {_REL: 0x90},
    "BCS": {_REL: 0xB0},
    "BEQ": {_REL: 0xF0},
    "BIT": {_IMM: 0x89, _ZP: 0x24, _ZPX: 0x34, _ABS: 0x2C, _ABSX: 0x3C},
    "BMI": {_REL: 0x30},
    "BNE": {_REL: 0xD0},
    "BPL": {_REL: 0x10},
    "BRA": {_REL: 0x80},
    "BRK": {_IMP: 0x00},
    "BVC": {_REL: 0x50},
    "BVS": {_REL: 0x70},
    "CLC": {_IMP: 0x18},
    "CLD": {_IMP: 0xD8},
    "CLI": {_IMP: 0x58},
    "CLV": {_IMP: 0xB8},
    "CMP": {_IMM: 0xC9, _ZP: 0xC5, _ZPX: 0xD5, _ABS: 0xCD, _ABSX: 0xDD,
            _ABSY: 0xD9, _IZX: 0xC1, _IZY: 0xD1, _IZP: 0xD2},
    "CPX": {_IMM: 0xE0, _ZP: 0xE4, _ABS: 0xEC},
    "CPY": {_IMM: 0xC0, _ZP: 0xC4, _ABS: 0xCC},
    "DEC": {_ACC: 0x3A, _ZP: 0xC6, _ZPX: 0xD6, _ABS: 0xCE, _ABSX: 0xDE},
    "DEX": {_IMP: 0xCA},
    "DEY": {_IMP: 0x88},
    "EOR": {_IMM: 0x49, _ZP: 0x45, _ZPX: 0x55, _ABS: 0x4D, _ABSX: 0x5D,
            _ABSY: 0x59, _IZX: 0x41, _IZY: 0x51, _IZP: 0x52},
    "INC": {_ACC: 0x1A, _ZP: 0xE6, _ZPX: 0xF6, _ABS: 0xEE, _ABSX: 0xFE},
    "INX": {_IMP: 0xE8},
    "INY": {_IMP: 0xC8},
    "JMP": {_ABS: 0x4C, _IND: 0x6C},
    "JSR": {_ABS: 0x20},
    "LDA": {_IMM: 0xA9, _ZP: 0xA5, _ZPX: 0xB5, _ABS: 0xAD, _ABSX: 0xBD,
            _ABSY: 0xB9, _IZX: 0xA1, _IZY: 0xB1, _IZP: 0xB2},
    "LDX": {_IMM: 0xA2, _ZP: 0xA6, _ZPY: 0xB6, _ABS: 0xAE, _ABSY: 0xBE},
    "LDY": {_IMM: 0xA0, _ZP: 0xA4, _ZPX: 0xB4, _ABS: 0xAC, _ABSX: 0xBC},
    "LSR": {_ACC: 0x4A, _ZP: 0x46, _ZPX: 0x56, _ABS: 0x4E, _ABSX: 0x5E},
    "NOP": {_IMP: 0xEA},
    "ORA": {_IMM: 0x09, _ZP: 0x05, _ZPX: 0x15, _ABS: 0x0D, _ABSX: 0x1D,
            _ABSY: 0x19, _IZX: 0x01, _IZY: 0x11, _IZP: 0x12},
    "PHA": {_IMP: 0x48},
    "PHP": {_IMP: 0x08},
    "PHX": {_IMP: 0xDA},
    "PHY": {_IMP: 0x5A},
    "PLA": {_IMP: 0x68},
    "PLP": {_IMP: 0x28},
    "PLX": {_IMP: 0xFA},
    "PLY": {_IMP: 0x7A},
    "ROL": {_ACC: 0x2A, _ZP: 0x26, _ZPX: 0x36, _ABS: 0x2E, _ABSX: 0x3E},
    "ROR": {_ACC: 0x6A, _ZP: 0x66, _ZPX: 0x76, _ABS: 0x6E, _ABSX: 0x7E},
    "RTI": {_IMP: 0x40},
    "RTS": {_IMP: 0x60},
    "SBC": {_IMM: 0xE9, _ZP: 0xE5, _ZPX: 0xF5, _ABS: 0xED, _ABSX: 0xFD,
            _ABSY: 0xF9, _IZX: 0xE1, _IZY: 0xF1, _IZP: 0xF2},
    "SEC": {_IMP: 0x38},
    "SED": {_IMP: 0xF8},
    "SEI": {_IMP: 0x78},
    "STA": {_ZP: 0x85, _ZPX: 0x95, _ABS: 0x8D, _ABSX: 0x9D, _ABSY: 0x99,
            _IZX: 0x81, _IZY: 0x91, _IZP: 0x92},
    "STP": {_IMP: 0xDB},
    "STX": {_ZP: 0x86, _ZPY: 0x96, _ABS: 0x8E},
    "STY": {_ZP: 0x84, _ZPX: 0x94, _ABS: 0x8C},
    "STZ": {_ZP: 0x64, _ZPX: 0x74, _ABS: 0x9C, _ABSX: 0x9E},
    "TAX": {_IMP: 0xAA},
    "TAY": {_IMP: 0xA8},
    "TRB": {_ZP: 0x14, _ABS: 0x1C},
    "TSB": {_ZP: 0x04, _ABS: 0x0C},
    "TSX": {_IMP: 0xBA},
    "TXA": {_IMP: 0x8A},
    "TXS": {_IMP: 0x9A},
    "TYA": {_IMP: 0x98},
    "WAI": {_IMP: 0xCB},
}

_LABEL_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*")
_LABEL_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_number(token: str) -> int | None:
    """Parse ``$hex``, ``0xhex`` or decimal; return None for non-numbers.

    Args:
        token: The operand token to parse.

    Returns:
        The integer value, or ``None`` when the token is not numeric.
    """
    try:
        if token.startswith("$"):
            return int(token[1:], 16)
        if token.lower().startswith("0x"):
            return int(token, 16)
        if token.isdigit():
            return int(token, 10)
    except ValueError:
        return None
    return None


def _classify_operand(operand: str, line_num: int) -> tuple[str, str | None]:
    """Classify an operand string into a (kind, token) pair.

    Kinds: ``none`` (no operand), ``acc`` (``A``), ``imm`` (``#value``),
    ``plain`` (address or label), ``x``/``y`` (``,X``/``,Y`` indexed),
    ``paren`` (``(value)``), ``izx`` (``(zp,X)``), ``izy`` (``(zp),Y``).

    Args:
        operand: The operand text with all whitespace already removed.
        line_num: Source line number for error messages.

    Returns:
        A (kind, token) tuple; token is ``None`` for ``none``/``acc``.

    Raises:
        ValueError: If the operand cannot be parsed.
    """
    if not operand:
        return "none", None
    if operand.upper() == "A":
        return "acc", None
    if operand.startswith("#"):
        return "imm", operand[1:]
    if operand.startswith("("):
        match = re.match(r"^\((.+),[Xx]\)$", operand)
        if match:
            return "izx", match.group(1)
        match = re.match(r"^\((.+)\),[Yy]$", operand)
        if match:
            return "izy", match.group(1)
        match = re.match(r"^\((.+)\)$", operand)
        if match:
            return "paren", match.group(1)
        raise ValueError(f"Line {line_num}: cannot parse operand {operand!r}")
    if operand[-2:].upper() == ",X":
        return "x", operand[:-2]
    if operand[-2:].upper() == ",Y":
        return "y", operand[:-2]
    return "plain", operand


def _resolve_mode(mnemonic: str, kind: str, token: str | None, line_num: int) -> str:
    """Choose the addressing mode for a mnemonic/operand pair.

    A numeric operand narrower than $100 selects the zero-page mode when
    the mnemonic provides one, otherwise the absolute mode (a valid 16-bit
    encoding of a small address). Labels always use absolute modes.

    Args:
        mnemonic: Uppercase instruction mnemonic.
        kind: Operand kind from :func:`_classify_operand`.
        token: Operand token (value text or label name), if any.
        line_num: Source line number for error messages.

    Returns:
        The addressing mode identifier.

    Raises:
        ValueError: If the mnemonic does not support the addressing mode.
    """
    modes = _OPCODES[mnemonic]

    def _invalid() -> ValueError:
        return ValueError(
            f"Line {line_num}: {mnemonic} does not support this addressing mode"
        )

    if kind == "none":
        if _IMP in modes:
            return _IMP
        if _ACC in modes:  # e.g. ASL with no operand means accumulator
            return _ACC
        raise _invalid()
    if kind == "acc":
        if _ACC in modes:
            return _ACC
        raise _invalid()
    if kind == "imm":
        if _IMM in modes:
            return _IMM
        raise _invalid()
    if kind == "izx":
        if _IZX in modes:
            return _IZX
        raise _invalid()
    if kind == "izy":
        if _IZY in modes:
            return _IZY
        raise _invalid()
    if kind == "paren":
        if _IND in modes:
            return _IND
        if _IZP in modes:
            return _IZP
        raise _invalid()
    if kind in ("x", "y"):
        zp_mode = _ZPX if kind == "x" else _ZPY
        abs_mode = _ABSX if kind == "x" else _ABSY
        number = _parse_number(token)
        if number is not None and number < 0x100 and zp_mode in modes:
            return zp_mode
        if abs_mode in modes:
            return abs_mode
        if zp_mode in modes:
            return zp_mode  # range-checked again in pass 2
        raise _invalid()
    # plain operand: address or label (branch mnemonics land here too)
    if _REL in modes:
        return _REL
    number = _parse_number(token)
    if number is not None and number < 0x100 and _ZP in modes:
        return _ZP
    if _ABS in modes:
        return _ABS
    if _ZP in modes:
        return _ZP
    raise _invalid()


def _resolve_value(token: str, labels: dict[str, int], line_num: int) -> int:
    """Resolve an operand token to its numeric value.

    Args:
        token: A number (``$hex``/``0xhex``/decimal) or a label name.
        labels: The label table from pass 1.
        line_num: Source line number for error messages.

    Returns:
        The numeric value.

    Raises:
        ValueError: If the token is neither a number nor a defined label.
    """
    value = _parse_number(token)
    if value is not None:
        return value
    if not _LABEL_REF.match(token):
        raise ValueError(f"Line {line_num}: cannot parse operand {token!r}")
    if token not in labels:
        raise ValueError(f"Line {line_num}: undefined label {token!r}")
    return labels[token]


def _emit(out: dict[int, int], addr: int, byte: int, line_num: int) -> None:
    """Place one byte, enforcing the ROM region and no overwrites.

    Args:
        out: The CPU address -> byte map under construction.
        addr: CPU address to write.
        byte: Byte value to write.
        line_num: Source line number for error messages.

    Raises:
        ValueError: If ``addr`` is outside $8000-$FFFF or already written.
    """
    if not (ROM_BASE_ADDR <= addr <= 0xFFFF):
        raise ValueError(
            f"Line {line_num}: address ${addr:04X} is outside the ROM region "
            f"($8000-$FFFF)"
        )
    if addr in out:
        raise ValueError(
            f"Line {line_num}: address ${addr:04X} is written more than once"
        )
    out[addr] = byte


def parse_asm_file(path: Path) -> dict[int, int]:
    """Assemble a 6502 assembly source file into a CPU address -> byte map.

    Two passes: the first collects labels and computes statement addresses;
    the second emits bytes, resolving label operands and checking ranges.

    Args:
        path: Path to the assembly source file.

    Returns:
        A mapping from CPU address ($8000-$FFFF) to byte value.

    Raises:
        ValueError: On syntax errors, unknown mnemonics, unsupported
            addressing modes, undefined or duplicate labels, branches out
            of range, or bytes emitted outside the ROM region.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()

    labels: dict[str, int] = {}
    statements: list[dict] = []
    pc: int | None = None

    # ---- pass 1: labels, statement sizes and addresses ----
    for line_num, raw in enumerate(lines, start=1):
        line = raw.split(";")[0].strip()
        if not line:
            continue

        # label definitions (several may precede one statement)
        while match := _LABEL_DEF.match(line):
            name = match.group(1)
            if name in labels:
                raise ValueError(f"Line {line_num}: duplicate label {name!r}")
            if pc is None:
                pc = ROM_BASE_ADDR
            labels[name] = pc
            line = line[match.end():].strip()

        if not line:
            continue

        if line.startswith("."):
            parts = line.split(None, 1)
            directive = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if directive == ".org":
                value = _parse_number(arg)
                if value is None:
                    raise ValueError(
                        f"Line {line_num}: .org requires a numeric address, "
                        f"got {arg!r}"
                    )
                if not (ROM_BASE_ADDR <= value <= 0xFFFF):
                    raise ValueError(
                        f"Line {line_num}: .org address ${value:04X} is outside "
                        f"the ROM region ($8000-$FFFF)"
                    )
                pc = value
                continue
            if directive not in (".byte", ".word"):
                raise ValueError(f"Line {line_num}: unknown directive {directive!r}")
            items = [item.strip() for item in arg.split(",") if item.strip()]
            if not items:
                raise ValueError(
                    f"Line {line_num}: {directive} requires at least one value"
                )
            if pc is None:
                pc = ROM_BASE_ADDR
            statements.append({
                "kind": "byte" if directive == ".byte" else "word",
                "line_num": line_num,
                "pc": pc,
                "items": items,
            })
            pc += len(items) * (1 if directive == ".byte" else 2)
            continue

        # instruction
        parts = line.split(None, 1)
        mnemonic = parts[0].upper()
        operand = re.sub(r"\s+", "", parts[1]) if len(parts) > 1 else ""
        if mnemonic not in _OPCODES:
            raise ValueError(f"Line {line_num}: unknown mnemonic {mnemonic!r}")
        kind, token = _classify_operand(operand, line_num)
        mode = _resolve_mode(mnemonic, kind, token, line_num)
        if pc is None:
            pc = ROM_BASE_ADDR
        statements.append({
            "kind": "instr",
            "line_num": line_num,
            "pc": pc,
            "mnemonic": mnemonic,
            "mode": mode,
            "token": token,
        })
        pc += _MODE_SIZE[mode]

    # ---- pass 2: emit bytes ----
    out: dict[int, int] = {}
    for stmt in statements:
        line_num = stmt["line_num"]
        pc = stmt["pc"]

        if stmt["kind"] in ("byte", "word"):
            size = 1 if stmt["kind"] == "byte" else 2
            limit = 0xFF if size == 1 else 0xFFFF
            for index, item in enumerate(stmt["items"]):
                value = _resolve_value(item, labels, line_num)
                if not (0 <= value <= limit):
                    raise ValueError(
                        f"Line {line_num}: value {item!r} does not fit in "
                        f"{size * 8} bits"
                    )
                _emit(out, pc + index * size, value & 0xFF, line_num)
                if size == 2:
                    _emit(out, pc + index * size + 1, (value >> 8) & 0xFF, line_num)
            continue

        mnemonic = stmt["mnemonic"]
        mode = stmt["mode"]
        _emit(out, pc, _OPCODES[mnemonic][mode], line_num)
        if mode in (_IMP, _ACC):
            continue

        value = _resolve_value(stmt["token"], labels, line_num)
        if mode == _REL:
            offset = value - (pc + 2)
            if not (-128 <= offset <= 127):
                raise ValueError(
                    f"Line {line_num}: branch target {stmt['token']!r} is out "
                    f"of range ({offset:+d} bytes)"
                )
            _emit(out, pc + 1, offset & 0xFF, line_num)
        elif mode in (_IMM, _ZP, _ZPX, _ZPY, _IZX, _IZY, _IZP):
            if not (0 <= value <= 0xFF):
                raise ValueError(
                    f"Line {line_num}: operand {stmt['token']!r} does not fit "
                    f"in a byte"
                )
            _emit(out, pc + 1, value, line_num)
        else:  # abs, absx, absy, ind
            if not (0 <= value <= 0xFFFF):
                raise ValueError(
                    f"Line {line_num}: operand {stmt['token']!r} is out of range"
                )
            _emit(out, pc + 1, value & 0xFF, line_num)
            _emit(out, pc + 2, (value >> 8) & 0xFF, line_num)

    return out
