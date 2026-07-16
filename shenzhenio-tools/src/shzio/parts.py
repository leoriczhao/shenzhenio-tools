from __future__ import annotations

from .model import Part, PartSpec, PinDirection, PinKind, PinSpec, Side


def _pin(
    name: str,
    kind: PinKind,
    side: Side,
    offset: int,
    direction: PinDirection = PinDirection.BIDIRECTIONAL,
    contact_dx: int | None = None,
    contact_dy: int | None = None,
) -> PinSpec:
    return PinSpec(
        name=name,
        kind=kind,
        side=side,
        offset=offset,
        direction=direction,
        contact_dx=contact_dx,
        contact_dy=contact_dy,
    )


MC4000_SPEC = PartSpec(
    type_name="UC4",
    width=3,
    height=2,
    cost=3,
    max_code_lines=9,
    registers=("acc",),
    pins={
        "x0": _pin("x0", PinKind.XBUS, Side.LEFT, 0, contact_dx=0, contact_dy=2),
        "p0": _pin("p0", PinKind.SIMPLE, Side.LEFT, 1, contact_dx=0, contact_dy=3),
        "p1": _pin("p1", PinKind.SIMPLE, Side.RIGHT, 0, contact_dx=2, contact_dy=2),
        "x1": _pin("x1", PinKind.XBUS, Side.RIGHT, 1, contact_dx=2, contact_dy=3),
    },
)

MC6000_SPEC = PartSpec(
    type_name="UC6",
    width=3,
    height=3,
    cost=5,
    max_code_lines=14,
    registers=("acc", "dat"),
    pins={
        "x0": _pin("x0", PinKind.XBUS, Side.LEFT, 0, contact_dx=0, contact_dy=2),
        "x1": _pin("x1", PinKind.XBUS, Side.LEFT, 1, contact_dx=0, contact_dy=3),
        "p0": _pin("p0", PinKind.SIMPLE, Side.LEFT, 2, contact_dx=0, contact_dy=4),
        "p1": _pin("p1", PinKind.SIMPLE, Side.RIGHT, 0, contact_dx=2, contact_dy=2),
        "x3": _pin("x3", PinKind.XBUS, Side.RIGHT, 1, contact_dx=2, contact_dy=3),
        "x2": _pin("x2", PinKind.XBUS, Side.RIGHT, 2, contact_dx=2, contact_dy=4),
    },
)

RADIO_SPEC = PartSpec(
    type_name="RADIO",
    width=3,
    height=2,
    cost=0,
    max_code_lines=None,
    registers=(),
    pins={
        "rx": _pin("rx", PinKind.XBUS, Side.RIGHT, 0, PinDirection.OUTPUT, contact_dx=1, contact_dy=1),
        "tx": _pin("tx", PinKind.XBUS, Side.RIGHT, 1, PinDirection.INPUT, contact_dx=1, contact_dy=2),
    },
)

PART_SPECS = {
    MC4000_SPEC.type_name: MC4000_SPEC,
    MC6000_SPEC.type_name: MC6000_SPEC,
    RADIO_SPEC.type_name: RADIO_SPEC,
}


class MC4000(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(MC4000_SPEC, name=name)


class MC6000(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(MC6000_SPEC, name=name)


class Radio(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(RADIO_SPEC, name=name, provided=True)

    @property
    def rx(self):
        return self.pin("rx")

    @property
    def tx(self):
        return self.pin("tx")


def part_from_type(type_name: str, name: str | None = None) -> Part:
    try:
        spec = PART_SPECS[type_name]
    except KeyError as exc:
        raise KeyError(f"unknown part type {type_name!r}") from exc
    return Part(spec, name=name)
