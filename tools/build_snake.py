#!/usr/bin/env python3
"""Build the RTM32 Snake program with the old STX4 instruction encoding.

The supplied RTM32-0.5 emulator implements the original STX4 encoding, while
rtm32.asm 1.2.0 emits a newer instruction encoding for several mnemonics.  This
module therefore assembles the small source language used by ``snake.rmt``
into numeric ``.word`` directives and delegates only the final MDBG packaging
to the official assembler.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

# Original STX4 opcodes.
OP_R = 0
OP_ADDI = 1
OP_J = 2
OP_JAL = 3
OP_ANDI = 4
OP_ORI = 5
OP_XORI = 6
OP_LUI = 7
OP_LW = 8
OP_SW = 9
OP_SH = 10
OP_SB = 11
OP_LH = 12
OP_LHU = 13
OP_LB = 14
OP_LBU = 15
OP_BEQ = 16
OP_BNE = 17
OP_BLT = 18
OP_BGT = 19
OP_BLE = 20
OP_BGE = 21
OP_SLTI = 22
OP_SLTIU = 23

FUNC_SLL = 0
FUNC_SRL = 1
FUNC_SRA = 2
FUNC_SLLR = 3
FUNC_SRLR = 4
FUNC_SRAR = 5
FUNC_CFS = 6
FUNC_CTS = 7
FUNC_AND = 8
FUNC_OR = 9
FUNC_XOR = 10
FUNC_NOR = 11
FUNC_SLT = 12
FUNC_SLTU = 13
FUNC_JR = 14
FUNC_JALR = 15
FUNC_LHX = 16
FUNC_LHUX = 17
FUNC_LBX = 18
FUNC_LBUX = 19
FUNC_LWX = 20
FUNC_MUL = 21
FUNC_MULH = 22
FUNC_MULHU = 23
FUNC_DIV = 24
FUNC_DIVU = 25
FUNC_REST = 26
FUNC_RESTU = 27
FUNC_ADD = 28
FUNC_SUB = 29
FUNC_TRAP = 32
FUNC_RFT = 33

REGISTERS = {
    "zero": 0,
    "ra": 1,
    "t0": 2,
    "t1": 3,
    "t2": 4,
    "t3": 5,
    "t4": 6,
    "t5": 7,
    "k0": 8,
    "k1": 9,
    "a0": 10,
    "a1": 11,
    "a2": 12,
    "a3": 13,
    "v0": 14,
    "v1": 15,
    "t6": 16,
    "t7": 17,
    "t8": 18,
    "t9": 19,
    "s0": 20,
    "s1": 21,
    "s2": 22,
    "s3": 23,
    "s4": 24,
    "s5": 25,
    "s6": 26,
    "s7": 27,
    "fp": 28,
    "gp": 29,
    "sp": 30,
    "at": 31,
}

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.']*$")
_MEM_RE = re.compile(r"^(.+)\((\$[^)]+)\)$")


class EncodedProgram:
    def __init__(self, words: list[int], assembler_source: str, data: bytes) -> None:
        self.words = words
        self.assembler_source = assembler_source
        self.data = data


def _mask32(value: int) -> int:
    return value & 0xFFFFFFFF


def _check_signed(value: int, bits: int, what: str) -> None:
    low = -(1 << (bits - 1))
    high = (1 << (bits - 1)) - 1
    if not low <= value <= high:
        raise ValueError(f"{what}={value} does not fit signed {bits}-bit immediate")


def encode_i(opcode: int, rs: int, rt: int, imm: int) -> int:
    """Encode an original STX4 I-format instruction."""

    _check_signed(imm, 17, "immediate")
    return _mask32((opcode << 27) | (rs << 22) | (rt << 17) | (imm & 0x1FFFF))


def encode_l(opcode: int, rs: int, rt: int, imm: int, high: bool = False) -> int:
    if not 0 <= imm <= 0xFFFF:
        raise ValueError(f"logical immediate={imm} does not fit 16 bits")
    return (
        (opcode << 27)
        | (rs << 22)
        | (rt << 17)
        | ((1 if high else 0) << 16)
        | imm
    ) & 0xFFFFFFFF


def encode_r(
    func: int,
    rs: int = 0,
    rt: int = 0,
    rd: int = 0,
    aux: int = 0,
) -> int:
    if not 0 <= aux <= 31:
        raise ValueError(f"aux={aux} does not fit 5 bits")
    return _mask32((rs << 22) | (rt << 17) | (rd << 12) | (aux << 7) | (func & 0x3F))


def encode_branch(opcode: int, pc: int, rs: int, target: int, rt: int = 0) -> int:
    """Encode a branch whose target is an absolute byte address.

    The optional ``rt`` parameter is last so the compact four-argument form is
    convenient for tests and for one-register comparisons against ``$zero``.
    """

    delta = target - (pc + 4)
    if delta % 4:
        raise ValueError("branch target must be word-aligned")
    offset = delta // 4
    _check_signed(offset, 17, "branch offset")
    return _mask32((opcode << 27) | (rs << 22) | (rt << 17) | (offset & 0x1FFFF))


def encode_jump(opcode: int, target: int) -> int:
    if target % 4:
        raise ValueError("jump target must be word-aligned")
    return _mask32((opcode << 27) | ((target >> 2) & 0x07FFFFFF))


def _strip_comment(line: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if char == '"' and not escaped:
            in_string = not in_string
        if not in_string and line[index : index + 2] == "//":
            return line[:index]
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    return line


def _split_args(text: str) -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if char == '"' and not escaped:
            in_string = not in_string
        elif not in_string and char == "(":
            depth += 1
        elif not in_string and char == ")":
            depth -= 1
        elif not in_string and depth == 0 and char == ",":
            result.append(text[start:index].strip())
            start = index + 1
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    tail = text[start:].strip()
    if tail:
        result.append(tail)
    return result


def _split_values(text: str) -> list[str]:
    """Split comma- or whitespace-separated numeric directive arguments."""

    if "," in text:
        return _split_args(text)
    return [item for item in text.split() if item]


def _parse_register(token: str) -> int:
    token = token.strip()
    if not token.startswith("$"):
        raise ValueError(f"expected register, got {token!r}")
    name = token[1:].lower()
    if name.isdigit():
        value = int(name, 10)
        if not 0 <= value < 32:
            raise ValueError(f"register out of range: {token}")
        return value
    try:
        return REGISTERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown register: {token}") from exc


def _parse_literal(token: str) -> int:
    token = token.strip()
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        value = ast.literal_eval(token)
        if not isinstance(value, str) or len(value) != 1:
            raise ValueError(f"expected one-character literal: {token}")
        return ord(value)
    return int(token, 0)


def _resolve(token: str, labels: dict[str, int]) -> int:
    token = token.strip()
    try:
        return _parse_literal(token)
    except (ValueError, TypeError):
        pass

    match = re.fullmatch(
        r"([A-Za-z_][A-Za-z0-9_.']*)(?:\s*([+-])\s*(0x[0-9a-fA-F]+|0b[01]+|0o[0-7]+|[0-9]+))?",
        token,
    )
    if not match or match.group(1) not in labels:
        raise ValueError(f"unknown immediate or label: {token}")
    value = labels[match.group(1)]
    if match.group(2):
        adjustment = int(match.group(3), 0)
        value += adjustment if match.group(2) == "+" else -adjustment
    return value


def _decode_string(token: str) -> bytes:
    try:
        value = ast.literal_eval(token.strip())
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"invalid string literal: {token}") from exc
    if not isinstance(value, str):
        raise ValueError(f"expected string literal: {token}")
    return value.encode("utf-8")


def _parse_pad(text: str, offset: int = 0) -> int:
    values = _split_values(text)
    total = 0
    alignment = 1
    for value in values:
        match = re.fullmatch(r"([0-9]+)(?:\(([0-9]+)\))?", value)
        if not match:
            raise ValueError(f"invalid .pad argument: {value}")
        total += int(match.group(1), 0)
        if match.group(2):
            alignment = int(match.group(2), 0)
    if alignment > 1:
        total += (-(offset + total)) % alignment
    return total


def _section_name(directive: str) -> str:
    value = directive.strip()
    if value.startswith('"'):
        return ast.literal_eval(value)
    return value


def _normalise_lines(source: str) -> list[tuple[int, str]]:
    lines = []
    for number, raw in enumerate(source.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if line:
            lines.append((number, line))
    return lines


def _split_labels(line: str) -> tuple[list[str], str]:
    labels: list[str] = []
    rest = line.strip()
    while ":" in rest:
        candidate, possible_rest = rest.split(":", 1)
        candidate = candidate.strip()
        if not _LABEL_RE.fullmatch(candidate):
            break
        labels.append(candidate)
        rest = possible_rest.strip()
        if not rest:
            break
    return labels, rest


def _directive_size(
    directive: str, section: str, labels: dict[str, int], offset: int
) -> int:
    name, _, args = directive.partition(" ")
    name = name.lower()
    if name in (".text", ".data", ".section"):
        return 0
    if name == ".word":
        return (-offset) % 4 + 4 * len(_split_values(args))
    if name == ".short":
        return (-offset) % 2 + 2 * len(_split_values(args))
    if name == ".byte":
        return len(_split_values(args))
    if name == ".asciiz":
        return len(_decode_string(args)) + 1
    if name == ".pad":
        return _parse_pad(args, offset)
    if name in (".ptr", ".lo16", ".hi16"):
        return (-offset) % 4 + 4
    raise ValueError(f"unsupported directive {name}")


def _iter_source_entries(source: str) -> Iterable[tuple[int, list[str], str]]:
    for line_number, line in _normalise_lines(source):
        labels, statement = _split_labels(line)
        yield line_number, labels, statement


def _first_pass(
    source: str,
) -> tuple[dict[str, int], list[tuple[int, str, str, int]], int, int]:
    labels_by_section: dict[str, int] = {}
    entries: list[tuple[int, str, str, int]] = []
    section = "text"
    text_offset = 0
    data_offset = 0

    for line_number, source_labels, statement in _iter_source_entries(source):
        for label in source_labels:
            if label in labels_by_section:
                raise ValueError(f"duplicate label {label!r} on line {line_number}")
            # Temporarily store section-relative offsets. They become absolute
            # after the text size is known.
            labels_by_section[label] = (0 if section == "text" else 1) << 31 | (
                text_offset if section == "text" else data_offset
            )
        if not statement:
            continue
        if statement.startswith("."):
            name, _, args = statement.partition(" ")
            lower = name.lower()
            if lower in (".text", ".data", ".section"):
                requested = (
                    "text"
                    if lower == ".text"
                    else "data"
                    if lower == ".data"
                    else _section_name(args)
                )
                if requested not in ("text", ".text", "data", ".data"):
                    raise ValueError(
                        f"unsupported section {requested!r} on line {line_number}"
                    )
                section = "text" if requested in ("text", ".text") else "data"
                continue
            size = _directive_size(
                statement,
                section,
                {},
                text_offset if section == "text" else data_offset,
            )
            entries.append(
                (
                    line_number,
                    section,
                    statement,
                    text_offset if section == "text" else data_offset,
                )
            )
            if section == "text":
                text_offset += size
            else:
                data_offset += size
        else:
            entries.append(
                (
                    line_number,
                    section,
                    statement,
                    text_offset if section == "text" else data_offset,
                )
            )
            if section == "text":
                text_offset += 4
            else:
                raise ValueError(f"instruction outside .text on line {line_number}")

    data_base = (text_offset + 3) & ~3
    labels: dict[str, int] = {}
    for name, encoded in labels_by_section.items():
        is_data = bool(encoded & (1 << 31))
        relative = encoded & 0x7FFFFFFF
        labels[name] = data_base + relative if is_data else relative
    return labels, entries, text_offset, data_offset


def _parse_memory_operand(token: str, labels: dict[str, int]) -> tuple[int, int]:
    match = _MEM_RE.fullmatch(token.strip())
    if not match:
        raise ValueError(f"expected memory operand offset(base), got {token!r}")
    return _resolve(match.group(1), labels), _parse_register(match.group(2))


def _encode_instruction(statement: str, pc: int, labels: dict[str, int]) -> int:
    name, _, rest = statement.partition(" ")
    name = name.lower()
    args = _split_args(rest)

    if name == "nop":
        return encode_i(OP_ADDI, 0, 0, 0)
    if name == "la":
        if len(args) != 2:
            raise ValueError("la expects destination and label")
        value = _resolve(args[1], labels)
        return encode_i(OP_ADDI, 0, _parse_register(args[0]), value)
    if name == "li":
        if len(args) != 2:
            raise ValueError("li expects destination and immediate")
        return encode_i(OP_ADDI, 0, _parse_register(args[0]), _resolve(args[1], labels))
    if name == "addi":
        return encode_i(
            OP_ADDI,
            _parse_register(args[1]),
            _parse_register(args[0]),
            _resolve(args[2], labels),
        )
    if name in ("andi", "ori", "xori"):
        opcode = {"andi": OP_ANDI, "ori": OP_ORI, "xori": OP_XORI}[name]
        return encode_l(
            opcode,
            _parse_register(args[1]),
            _parse_register(args[0]),
            _resolve(args[2], labels),
        )
    if name == "lui":
        return encode_l(OP_LUI, 0, _parse_register(args[0]), _resolve(args[1], labels))
    if name in ("lw", "sw", "sh", "sb", "lh", "lhu", "lb", "lbu"):
        opcode = {
            "lw": OP_LW,
            "sw": OP_SW,
            "sh": OP_SH,
            "sb": OP_SB,
            "lh": OP_LH,
            "lhu": OP_LHU,
            "lb": OP_LB,
            "lbu": OP_LBU,
        }[name]
        offset, base = _parse_memory_operand(args[1], labels)
        # STX4 encodes the effective-address register in `rs` and the
        # load destination/store value in `rt`.
        return encode_i(opcode, base, _parse_register(args[0]), offset)
    if name in ("beq", "bne", "blt", "bgt", "ble", "bge"):
        opcode = {
            "beq": OP_BEQ,
            "bne": OP_BNE,
            "blt": OP_BLT,
            "bgt": OP_BGT,
            "ble": OP_BLE,
            "bge": OP_BGE,
        }[name]
        return encode_branch(
            opcode,
            pc,
            _parse_register(args[0]),
            _resolve(args[2], labels),
            _parse_register(args[1]),
        )
    if name in ("j", "jal"):
        return encode_jump(OP_J if name == "j" else OP_JAL, _resolve(args[0], labels))
    if name == "jalx":
        # Original JALX is represented as JAL with an explicit link register
        # by the source dialect; the game does not need it, but accepting it
        # makes the parser useful for small diagnostics.
        return encode_jump(OP_JAL, _resolve(args[1], labels))
    if name in ("sll", "srl", "sra"):
        func = {"sll": FUNC_SLL, "srl": FUNC_SRL, "sra": FUNC_SRA}[name]
        return encode_r(
            func,
            rt=_parse_register(args[1]),
            rd=_parse_register(args[0]),
            aux=_resolve(args[2], labels),
        )
    if name in ("sllr", "srlr", "srar"):
        func = {"sllr": FUNC_SLLR, "srlr": FUNC_SRLR, "srar": FUNC_SRAR}[name]
        return encode_r(
            func,
            rs=_parse_register(args[2]),
            rt=_parse_register(args[1]),
            rd=_parse_register(args[0]),
        )
    if name in ("and", "or", "xor", "nor", "slt", "sltu", "add", "sub"):
        func = {
            "and": FUNC_AND,
            "or": FUNC_OR,
            "xor": FUNC_XOR,
            "nor": FUNC_NOR,
            "slt": FUNC_SLT,
            "sltu": FUNC_SLTU,
            "add": FUNC_ADD,
            "sub": FUNC_SUB,
        }[name]
        return encode_r(
            func,
            rs=_parse_register(args[1]),
            rt=_parse_register(args[2]),
            rd=_parse_register(args[0]),
        )
    if name == "jr":
        return encode_r(FUNC_JR, rs=_parse_register(args[0]))
    if name == "jalr":
        return encode_r(
            FUNC_JALR, rs=_parse_register(args[1]), rt=_parse_register(args[0])
        )
    if name == "trap":
        return encode_r(FUNC_TRAP, aux=_resolve(args[0], labels))
    if name == "rft":
        return encode_r(FUNC_RFT)
    raise ValueError(f"unsupported instruction {name!r} at 0x{pc:08x}")


def _encode_data(
    entries: list[tuple[int, str, str, int]], labels: dict[str, int]
) -> bytes:
    output = bytearray()
    for line_number, section, statement, _ in entries:
        if section != "data":
            continue
        name, _, args = statement.partition(" ")
        name = name.lower()
        if name == ".byte":
            for token in _split_values(args):
                value = _parse_literal(token)
                if not 0 <= value <= 0xFF:
                    raise ValueError(f"byte out of range on line {line_number}")
                output.append(value)
        elif name == ".asciiz":
            output.extend(_decode_string(args))
            output.append(0)
        elif name == ".short":
            while len(output) % 2:
                output.append(0)
            for token in _split_values(args):
                output.extend((_resolve(token, labels) & 0xFFFF).to_bytes(2, "little"))
        elif name == ".word":
            while len(output) % 4:
                output.append(0)
            for token in _split_values(args):
                output.extend(
                    (_resolve(token, labels) & 0xFFFFFFFF).to_bytes(4, "little")
                )
        elif name == ".pad":
            output.extend(b"\0" * _parse_pad(args, len(output)))
        elif name in (".ptr", ".lo16", ".hi16"):
            while len(output) % 4:
                output.append(0)
            value = _resolve(args, labels)
            if name == ".lo16":
                value &= 0xFFFF
            elif name == ".hi16":
                value = (value >> 16) & 0xFFFF
            output.extend((value & 0xFFFFFFFF).to_bytes(4, "little"))
        else:
            raise ValueError(
                f"unsupported data directive {name!r} on line {line_number}"
            )
    return bytes(output)


def _bytes_as_directives(data: bytes) -> str:
    if not data:
        return "    .byte 0\n"
    lines = []
    for start in range(0, len(data), 16):
        chunk = data[start : start + 16]
        lines.append("    .byte " + " ".join(f"0x{value:02x}" for value in chunk))
    return "\n".join(lines) + "\n"


def encode_source(source: str) -> EncodedProgram:
    labels, entries, text_size, _ = _first_pass(source)
    words: list[int] = []
    for line_number, section, statement, offset in entries:
        if section != "text":
            continue
        if statement.startswith("."):
            name, _, args = statement.partition(" ")
            if name.lower() == ".word":
                for token in _split_values(args):
                    words.append(_mask32(_resolve(token, labels)))
            elif name.lower() in (".pad", ".byte", ".short", ".asciiz"):
                raise ValueError(
                    f"data directive {name} is not valid in .text on line {line_number}"
                )
            else:
                raise ValueError(
                    f"unsupported text directive {name!r} on line {line_number}"
                )
        else:
            encoded = _encode_instruction(statement, offset, labels)
            words.append(encoded)
    if len(words) * 4 != text_size:
        raise AssertionError(
            f"internal text layout mismatch: {len(words) * 4} != {text_size}"
        )

    data = _encode_data(entries, labels)
    assembler_lines = ['.section ".text"']
    assembler_lines.extend(f"    .word 0x{word:08x}" for word in words)
    assembler_lines.append('.section ".data"')
    assembler_lines.append(_bytes_as_directives(data).rstrip())
    assembler_source = "\n".join(assembler_lines) + "\n"
    return EncodedProgram(words=words, assembler_source=assembler_source, data=data)


def stage_assembler(source: Path, destination: Path) -> Path:
    """Copy the supplied assembler and make only the copy executable."""

    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    mode = stat.S_IMODE(destination.stat().st_mode)
    destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination


def build(
    source_path: Path,
    output_path: Path,
    assembler_path: Path,
    generated_path: Path | None = None,
) -> Path:
    source_path = source_path.resolve()
    output_path = output_path.resolve()
    program = encode_source(source_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rtm32-snake-") as temporary:
        temporary_path = Path(temporary)
        staged = stage_assembler(assembler_path, temporary_path / "rtm32.asm")
        assembled_source = (
            Path(generated_path).resolve()
            if generated_path
            else temporary_path / "snake.generated.rtm"
        )
        assembled_source.parent.mkdir(parents=True, exist_ok=True)
        assembled_source.write_text(program.assembler_source, encoding="utf-8")
        command = [str(staged), str(assembled_source), "-o", str(output_path)]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(
                "assembler failed with exit code "
                f"{result.returncode}:\n{result.stdout}{result.stderr}"
            )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=root / "snake.rmt")
    parser.add_argument("--output", type=Path, default=root / "build" / "snake.bin")
    parser.add_argument(
        "--assembler",
        type=Path,
        default=root / "rtm32.asm-1.2.0" / "x86_64-linux-musl-rtm32.asm",
    )
    parser.add_argument(
        "--generated",
        type=Path,
        help="keep the generated numeric assembler source at this path",
    )
    args = parser.parse_args(argv)
    try:
        output = build(args.source, args.output, args.assembler, args.generated)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"build_snake: error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
