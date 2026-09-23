import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build_snake.py"
SPEC = importlib.util.spec_from_file_location("build_snake", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
build_snake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_snake)


class EncoderTests(unittest.TestCase):
    def test_old_stx4_immediate_encoding_keeps_addi_on_opcode_one(self):
        word = build_snake.encode_i(build_snake.OP_ADDI, 0, 22, -256)

        self.assertEqual(word >> 27, 1)
        self.assertEqual(word & 0x1FFFF, 0x1FF00)
        self.assertNotEqual(word >> 27, 3)

    def test_old_stx4_trap_uses_function_thirty_two(self):
        word = build_snake.encode_r(build_snake.FUNC_TRAP, aux=1)

        self.assertEqual(word & 0x3F, 32)
        self.assertEqual(word >> 27, 0)

    def test_branch_and_jump_offsets_are_pc_relative_and_word_aligned(self):
        branch = build_snake.encode_branch(build_snake.OP_BNE, 0x20, 0, 0x40)
        jump = build_snake.encode_jump(build_snake.OP_J, 0x100)

        self.assertEqual(branch & 0x1FFFF, 0x7)
        self.assertEqual(jump & 0x07FFFFFF, 0x40)
        with self.assertRaises(ValueError):
            build_snake.encode_branch(build_snake.OP_BEQ, 0x20, 0, 0x22)

    def test_source_uses_old_instruction_encoding_and_emits_no_mnemonic_payload(self):
        source = """
        .text
        start:
          addi $t0, $zero, 7
          trap 1
        .data
        message:
          .asciiz "X"
        """

        encoded = build_snake.encode_source(source)

        self.assertEqual(encoded.words[0] >> 27, build_snake.OP_ADDI)
        self.assertEqual(encoded.words[1] & 0x3F, build_snake.FUNC_TRAP)
        self.assertIn(".word 0x", encoded.assembler_source)
        self.assertNotIn("addi", encoded.assembler_source)
        self.assertNotIn("trap", encoded.assembler_source)
        self.assertEqual(encoded.data, b"X\x00")

    def test_staged_assembler_is_executable_without_changing_supplied_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "assembler"
            original.write_bytes(b"not executed")
            original.chmod(stat.S_IRUSR | stat.S_IWUSR)
            before = stat.S_IMODE(original.stat().st_mode)

            staged = build_snake.stage_assembler(original, Path(tmp) / "stage")

            self.assertEqual(stat.S_IMODE(original.stat().st_mode), before)
            self.assertTrue(os.access(staged, os.X_OK))
            self.assertEqual(staged.read_bytes(), original.read_bytes())

    def test_repository_source_declares_game_features(self):
        source = (ROOT / "snake.rmt").read_text()

        for feature in ("poll_input", "render", "spawn_food", "delay", "game_over"):
            self.assertIn(feature, source)
        self.assertIn("0xFFFFFF00", source)
        self.assertIn("0xFFFFFF04", source)

    def test_arithmetic_mul_div_rest_use_old_r_type_functions(self):
        cases = {
            "mul": build_snake.FUNC_MUL,
            "div": build_snake.FUNC_DIV,
            "rest": build_snake.FUNC_REST,
        }

        for mnemonic, func in cases.items():
            word = build_snake._encode_instruction(f"{mnemonic} $t0, $t1, $t2", 0, {})

            self.assertEqual(word & 0x3F, func)
            self.assertEqual(word >> 27, 0)
            self.assertEqual((word >> 12) & 0x1F, 2)
            self.assertEqual((word >> 22) & 0x1F, 3)
            self.assertEqual((word >> 17) & 0x1F, 4)


    def test_word_label_after_unaligned_data_points_at_aligned_words(self):
        template = """
        .text
        start:
          la $t0, arreglo
          lw $t1, 0($t0)
        .data
        msg: .asciiz "AB"
        {label} .word 5 6
        """

        # text = 2 words -> data_base 8; msg = 3 bytes -> pad 1 byte,
        # so arreglo must sit at 12, not at the unaligned offset 11.
        for label in ("arreglo:", "arreglo:\n   "):
            with self.subTest(label=label):
                encoded = build_snake.encode_source(template.format(label=label))

                self.assertEqual(encoded.words[0] & 0x1FFFF, 12)
                self.assertEqual(len(encoded.data), 12)
                self.assertEqual(
                    encoded.data[-8:],
                    (5).to_bytes(4, "little") + (6).to_bytes(4, "little"),
                )


if __name__ == "__main__":
    unittest.main()
