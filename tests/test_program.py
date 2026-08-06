from __future__ import annotations

import unittest

from shzio import MC4000, MC4000X, MC6000
from shzio.program import (
    Condition,
    Immediate,
    ProgramError,
    RegisterOperand,
    parse_program,
    validate_program_for_part,
)


class ProgramIRTests(unittest.TestCase):
    def test_parser_builds_typed_ir_and_preserves_semantic_text(self) -> None:
        program = parse_program(
            [
                "# setup",
                "loop: teq acc 10 # reached limit",
                "+ jmp end",
                "  add 1",
                "  jmp loop",
                "end: mov 0 acc",
            ]
        )

        self.assertEqual(5, program.instruction_count)
        self.assertEqual({"loop": 0, "end": 4}, program.labels)
        first = program.instructions[0]
        self.assertEqual("teq", first.opcode)
        self.assertEqual(RegisterOperand("acc"), first.operands[0])
        self.assertEqual(Immediate(10), first.operands[1])
        self.assertEqual(Condition.POSITIVE, program.instructions[1].condition)
        self.assertEqual(
            [
                "# setup",
                "loop: teq acc 10  # reached limit",
                "+ jmp end",
                "  add 1",
                "  jmp loop",
                "end: mov 0 acc",
            ],
            program.render_lines(),
        )

    def test_parser_rejects_invalid_immediates_and_labels(self) -> None:
        with self.assertRaisesRegex(ProgramError, "outside -999..999"):
            parse_program(["mov 1000 acc"])
        with self.assertRaisesRegex(ProgramError, "undefined label"):
            parse_program(["jmp nowhere"])
        with self.assertRaisesRegex(ProgramError, "duplicate label"):
            parse_program(["loop: nop", "loop: nop"])
        with self.assertRaisesRegex(ProgramError, "does not precede an instruction"):
            parse_program(["jmp end", "end:"])

    def test_builder_keeps_existing_python_api_and_attaches_ir(self) -> None:
        cpu = MC6000("cpu")
        builder = cpu.program()
        done = builder.label("done")

        builder.teq(cpu.acc, 0)
        builder.plus.jmp(done)
        builder.minus.mov(1, cpu.dat)
        builder.mark(done)
        builder.slp(1)

        self.assertIsNotNone(cpu.program_ir)
        self.assertEqual(4, cpu.program_ir.instruction_count)
        self.assertEqual(
            [
                "  teq acc 0",
                "+ jmp done",
                "- mov 1 dat",
                "done:",
                "  slp 1",
            ],
            cpu.code_lines,
        )
        validate_program_for_part(cpu.program_ir, cpu)

    def test_part_validation_enforces_model_registers_and_pin_kinds(self) -> None:
        mc4000 = MC4000("small")
        with self.assertRaisesRegex(ProgramError, "unavailable register 'dat'"):
            validate_program_for_part(parse_program(["mov 1 dat"]), mc4000)
        with self.assertRaisesRegex(ProgramError, "slx requires a xbus pin"):
            validate_program_for_part(parse_program(["slx p0"]), mc4000)
        with self.assertRaisesRegex(ProgramError, "gen requires a simple pin"):
            validate_program_for_part(parse_program(["gen x0 1 1"]), mc4000)

    def test_mc4000x_uses_mc4000_cpu_resources_with_four_xbus_pins(self) -> None:
        cpu = MC4000X("xbus_cpu")
        program = parse_program(["mov x0 acc", "mov acc x3"])

        self.assertEqual(("acc",), cpu.spec.registers)
        self.assertEqual(9, cpu.spec.max_code_lines)
        self.assertEqual({"x0", "x1", "x2", "x3"}, set(cpu.spec.pins))
        validate_program_for_part(program, cpu)

    def test_part_validation_counts_instructions_not_labels_or_comments(self) -> None:
        cpu = MC4000("cpu")
        valid = parse_program(
            ["start:", "# comment", *("nop" for _ in range(9))]
        )
        validate_program_for_part(valid, cpu)

        too_long = parse_program(["nop" for _ in range(10)])
        with self.assertRaisesRegex(ProgramError, "limit is 9"):
            validate_program_for_part(too_long, cpu)


if __name__ == "__main__":
    unittest.main()
