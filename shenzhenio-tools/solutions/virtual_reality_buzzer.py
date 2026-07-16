from shzio import MC6000, Solution
from shzio.boards import Sz035


class VirtualRealityBuzzer(Solution):
    board = Sz035
    name = "Codex solution"

    def build(self) -> None:
        radio = self.board.radio
        buzzer = self.board.buzzer

        cpu = self.place(MC6000("cpu"), at=(11, 5))
        self.connect(radio.rx, cpu.x0, name="radio_to_cpu")
        self.connect(cpu.p1, buzzer.input, name="cpu_to_buzzer")

        p = cpu.program()
        buzz = p.label("buzz")

        p.tcp(cpu.x0, 0)
        p.plus.mov(1, cpu.dat)
        p.plus.jmp(buzz)
        p.minus.jmp(buzz)
        p.mov(0, cpu.dat)
        p.mark(buzz)
        p.teq(cpu.dat, 1)
        p.plus.not_()
        p.minus.mov(0, cpu.acc)
        p.mov(cpu.acc, cpu.p1)
        p.slp(1)
