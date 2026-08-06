from __future__ import annotations

import unittest
from pathlib import Path

from shzio import MC4000, MC6000
from shzio.loader import load_solution
from shzio.program import parse_program
from shzio.vm import MemoryPinIO, MicrocontrollerVM, StepStatus, VMError


def vm_for(lines: list[str], *, mc6000: bool = False) -> MicrocontrollerVM:
    part = MC6000("cpu") if mc6000 else MC4000("cpu")
    return MicrocontrollerVM(part, parse_program(lines))


class MicrocontrollerVMTests(unittest.TestCase):
    def test_existing_sz035_program_runs_from_builder_ir(self) -> None:
        root = Path(__file__).resolve().parents[1]
        solution = load_solution(root / "solutions" / "virtual_reality_buzzer.py")
        cpu = next(part for part in solution.parts if part.name == "cpu")
        io = MemoryPinIO({name: pin.kind for name, pin in cpu.spec.pins.items()})
        io.push_xbus_input("x0", 1)
        vm = MicrocontrollerVM(cpu, pin_io=io)

        results = vm.run_until_blocked()

        self.assertEqual(StepStatus.SLEEPING, results[-1].status)
        self.assertEqual(1, vm.read_register("dat"))
        self.assertEqual(100, vm.read_register("acc"))
        self.assertEqual(100, io.simple_outputs["p1"])
        self.assertEqual(7, vm.power_used)

    def test_arithmetic_saturates_register_range_and_program_wraps(self) -> None:
        vm = vm_for(["mov 800 acc", "add 400", "mul -2"])

        self.assertEqual(StepStatus.EXECUTED, vm.step().status)
        self.assertEqual(800, vm.read_register("acc"))
        vm.step()
        self.assertEqual(999, vm.read_register("acc"))
        vm.step()
        self.assertEqual(-999, vm.read_register("acc"))
        self.assertEqual(0, vm.pc)
        self.assertEqual(3, vm.power_used)

    def test_conditions_start_disabled_and_skips_use_no_power(self) -> None:
        vm = vm_for(
            [
                "+ mov 5 acc",
                "teq acc 0",
                "+ mov 7 acc",
                "- mov 9 acc",
                "tcp acc 7",
                "+ mov 8 acc",
                "- mov 6 acc",
            ]
        )

        results = [vm.step() for _ in range(7)]

        self.assertEqual(
            [
                StepStatus.SKIPPED,
                StepStatus.EXECUTED,
                StepStatus.EXECUTED,
                StepStatus.SKIPPED,
                StepStatus.EXECUTED,
                StepStatus.SKIPPED,
                StepStatus.SKIPPED,
            ],
            [result.status for result in results],
        )
        self.assertEqual(7, vm.read_register("acc"))
        self.assertEqual(3, vm.power_used)
        self.assertEqual(3, vm.instructions_executed)

    def test_jump_targets_instruction_after_label(self) -> None:
        vm = vm_for(["jmp update", "mov 99 acc", "update:", "add 1"])

        vm.step()
        self.assertEqual(2, vm.pc)
        vm.step()
        self.assertEqual(1, vm.read_register("acc"))

    def test_dgt_handles_negative_values_and_out_of_range_digits(self) -> None:
        cases = [
            (-123, 0, -3),
            (-123, 1, -2),
            (-123, 2, -1),
            (596, 3, 0),
            (596, -1, 0),
        ]
        for initial, digit, expected in cases:
            with self.subTest(initial=initial, digit=digit):
                vm = vm_for([f"mov {initial} acc", f"dgt {digit}"])
                vm.step()
                vm.step()
                self.assertEqual(expected, vm.read_register("acc"))

    def test_dst_uses_low_digit_and_negative_source_sign(self) -> None:
        cases = [
            (596, 0, 7, 597),
            (596, 1, 7, 576),
            (596, 2, 7, 796),
            (123, 4, 5, 123),
            (123, 1, 45, 153),
            (123, 1, -45, -153),
        ]
        for initial, digit, source, expected in cases:
            with self.subTest(initial=initial, digit=digit, source=source):
                vm = vm_for(
                    [f"mov {initial} acc", f"dst {digit} {source}"]
                )
                vm.step()
                vm.step()
                self.assertEqual(expected, vm.read_register("acc"))

    def test_xbus_read_is_cached_while_destination_write_blocks(self) -> None:
        part = MC4000("cpu")
        io = MemoryPinIO({name: pin.kind for name, pin in part.spec.pins.items()})
        io.push_xbus_input("x0", 42)
        io.set_xbus_write_ready("x1", False)
        vm = MicrocontrollerVM(part, parse_program(["mov x0 x1"]), io)

        self.assertEqual(StepStatus.BLOCKED, vm.step().status)
        self.assertFalse(io.xbus_inputs["x0"])
        self.assertEqual(0, vm.power_used)

        io.set_xbus_write_ready("x1", True)
        self.assertEqual(StepStatus.EXECUTED, vm.step().status)
        self.assertEqual([42], io.xbus_outputs["x1"])
        self.assertEqual(1, vm.power_used)

    def test_slx_waits_without_consuming_xbus_value(self) -> None:
        part = MC4000("cpu")
        io = MemoryPinIO({name: pin.kind for name, pin in part.spec.pins.items()})
        vm = MicrocontrollerVM(part, parse_program(["slx x0", "mov x0 acc"]), io)

        self.assertEqual(StepStatus.BLOCKED, vm.step().status)
        io.push_xbus_input("x0", 9)
        self.assertEqual(StepStatus.EXECUTED, vm.step().status)
        self.assertEqual(1, len(io.xbus_inputs["x0"]))
        vm.step()
        self.assertEqual(9, vm.read_register("acc"))

    def test_sleep_finishes_only_after_requested_time_units(self) -> None:
        vm = vm_for(["slp 2", "mov 5 acc"])

        first = vm.step()
        self.assertEqual(StepStatus.SLEEPING, first.status)
        self.assertEqual(0, vm.pc)
        self.assertEqual(1, vm.power_used)
        self.assertEqual(0, vm.step().power_used)

        vm.advance_time(1)
        self.assertTrue(vm.sleeping)
        self.assertEqual(0, vm.pc)
        vm.advance_time(1)
        self.assertFalse(vm.sleeping)
        self.assertEqual(1, vm.pc)
        vm.step()
        self.assertEqual(5, vm.read_register("acc"))

    def test_gen_drives_high_then_low_and_blocks_program_counter(self) -> None:
        part = MC4000("cpu")
        io = MemoryPinIO({name: pin.kind for name, pin in part.spec.pins.items()})
        vm = MicrocontrollerVM(part, parse_program(["gen p0 2 1", "nop"]), io)

        self.assertEqual(StepStatus.SLEEPING, vm.step().status)
        self.assertEqual(100, io.simple_outputs["p0"])
        vm.advance_time(1)
        self.assertEqual(100, io.simple_outputs["p0"])
        vm.advance_time(1)
        self.assertEqual(0, io.simple_outputs["p0"])
        self.assertEqual(0, vm.pc)
        vm.advance_time(1)
        self.assertFalse(vm.sleeping)
        self.assertEqual(1, vm.pc)

    def test_simple_output_and_internal_registers_are_clamped(self) -> None:
        part = MC6000("cpu")
        io = MemoryPinIO({name: pin.kind for name, pin in part.spec.pins.items()})
        vm = MicrocontrollerVM(
            part,
            parse_program(["mov 999 p0", "mov -999 p1", "mov 0 null"]),
            io,
        )

        vm.step()
        vm.step()
        vm.step()
        self.assertEqual(100, io.simple_outputs["p0"])
        self.assertEqual(0, io.simple_outputs["p1"])
        self.assertEqual(0, vm.read_register("null"))

    def test_run_until_blocked_has_an_instruction_budget(self) -> None:
        vm = vm_for(["nop"])
        with self.assertRaisesRegex(VMError, "within 3 steps"):
            vm.run_until_blocked(max_steps=3)


if __name__ == "__main__":
    unittest.main()
