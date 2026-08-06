# Simulator semantics and evidence

The simulator is intentionally split into a CPU core, circuit scheduler,
networks, and device models. A passing CPU or Simple-network unit test is not
evidence that XBus timing matches the game.

## Current support

| Area | Status | Evidence |
|---|---|---|
| MCxxxx parsing, labels, comments, and conditional prefixes | Implemented | English manual pages 13-16 |
| `acc`, `dat`, `null`, immediate range, and arithmetic saturation | Implemented | English manual pages 14-15 |
| `nop`, `mov`, `jmp`, arithmetic, tests, `not`, `dgt`, and `dst` | Implemented | Manual plus independent edge tests |
| Conditional instructions initially disabled and skipped with zero power | Implemented | English manual page 14 |
| End-of-program wrap | Implemented | Verified by the completed in-game `Sz035` design |
| `slp` and `gen` local temporal state | Implemented | Manual behavior plus community edge tests |
| Simple I/O mode switching and continuous `0..100` levels | Implemented | English manual pages 9, 18, and 19 |
| Multiple Simple drivers compose by maximum value | Provisional | Avas network tests; needs game differential probe |
| Deterministic multi-MCU tick scheduler | Implemented, provisional ordering | Independent implementation; needs game differential probe |
| Logical networks built from `Solution.nets` | Implemented | Checked against typed design IR |
| Active/passive XBus rendezvous and blocking | Implemented | English manual page 9 plus independent tests |
| Non-blocking terminal empty value `-999` | Implemented | Extracted terminal type and in-game device convention |
| Multi-candidate XBus arbitration | Deterministic, provisional | Avas uses random selection; game differential required |
| Standalone queued XBus probe I/O | Implemented | `MemoryPinIO` test adapter only |

`MemoryPinIO` exists for deterministic instruction tests. It accepts XBus
writes into a capture list and therefore must not be treated as the final XBus
network implementation.

## Tick phases

`CircuitSimulator.tick()` currently performs these phases:

1. Use board input levels previously set with `drive_port()`.
2. Visit programmable parts in solution order, executing at most one
   instruction per machine per round.
3. Let each XBus network perform at most one active/passive rendezvous.
4. Repeat fair rounds while a machine or XBus endpoint changes state.
5. Stop when all machines are sleeping/idle or a full round makes no progress.
6. Snapshot Simple levels, XBus transfers, and status into `TickReport`.
7. Advance each `slp`/`gen` temporal state by one time unit.

The scheduler has independent round and instruction budgets. A program that
loops forever without sleeping or blocking raises `SimulationBudgetExceeded`
with tick, machine, program-counter, and opcode context.

The ordering of zero-time effects between different MCxxxx chips is not stated
by the local manual. Solution-order round-robin is deterministic but remains a
provisional scheduling rule until a two-chip game probe confirms it. For the
same reason, `TickReport.simple_levels` is explicitly the level during the tick,
captured before the final time advance.

An MCU XBus operation has two VM-visible phases. Its first attempt registers an
active read or write and remains blocked. After a network rendezvous, retrying
the same instruction commits the cached operand or received packet and consumes
power. Passive puzzle inputs only transmit when an active reader exists, while
passive puzzle outputs only receive from an active writer. All-passive networks
do not transfer. `slx` checks for an available transmitter but does not register
a receiving operation or consume a packet.

## Sources

Primary behavior source:

- `Content/SHENZHEN IO Manual (English).pdf`, language reference pages 13-16
  and MC4000/MC6000 data sheets on pages 18-19.
- `Content/SHENZHEN IO Manual (Chinese).pdf` is the local translation used for
  terminology cross-checks.
- The locally completed `Sz035` game run verifies that execution wraps from
  the final instruction to the first instruction.

Secondary conformance source:

- `avas/ShenzhenIOEmulator`, MIT licensed, commit
  `b025006c8a6d54b658fd331ef81908896a45082d`. Its command tests were used as
  behavioral hypotheses for negative `dgt`, signed `dst`, partial operand
  reads, `slp`, `gen`, and maximum-value Simple network composition. The Python
  implementation and tests in this project were written independently.

The secondary source is not authoritative. Signed `dst` and exact cross-tick
`slp`/`gen` phase boundaries, Simple multi-driver composition, cross-chip
instruction ordering, and XBus multi-candidate arbitration remain provisional
until small generated probes are compared with the current game executable.

MC4000X's four-XBus package is extracted from the game. Its nine program lines
and `acc` register are an explicit inference from the English manual page 18,
which calls it the XBus-only MC4000 variant but does not repeat the full resource
table. This inference remains marked in `INFERRED_PROGRAMMABLE_BEHAVIOR`.
