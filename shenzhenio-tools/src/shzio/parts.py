from __future__ import annotations

from .model import Part, PartSpec, PinDirection, PinKind, PinSpec, Side


def _pin(
    name: str,
    kind: PinKind,
    index: int,
    width: int,
    height: int,
    direction: PinDirection = PinDirection.BIDIRECTIONAL,
) -> PinSpec:
    if index < 0 or index >= height * 2:
        raise ValueError(f"pin index {index} is invalid for a {width}x{height} part")
    if index < height:
        side = Side.LEFT
        offset = index
        contact = (0, height - 1 - index)
        rotated_contact = (width - 1, index)
    else:
        side = Side.RIGHT
        offset = height * 2 - 1 - index
        contact = (width - 1, index - height)
        rotated_contact = (0, height - 1 - (index - height))
    return PinSpec(
        name=name,
        kind=kind,
        side=side,
        offset=offset,
        index=index,
        direction=direction,
        contact_dx=contact[0],
        contact_dy=contact[1],
        rotated_contact_dx=rotated_contact[0],
        rotated_contact_dy=rotated_contact[1],
    )


MC4000_SPEC = PartSpec(
    type_name="UC4",
    width=3,
    height=2,
    cost=3,
    max_code_lines=9,
    registers=("acc",),
    pins={
        "x0": _pin("x0", PinKind.XBUS, 0, 3, 2),
        "p0": _pin("p0", PinKind.SIMPLE, 1, 3, 2),
        "x1": _pin("x1", PinKind.XBUS, 2, 3, 2),
        "p1": _pin("p1", PinKind.SIMPLE, 3, 3, 2),
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
        "x0": _pin("x0", PinKind.XBUS, 0, 3, 3),
        "x1": _pin("x1", PinKind.XBUS, 1, 3, 3),
        "p0": _pin("p0", PinKind.SIMPLE, 2, 3, 3),
        "x2": _pin("x2", PinKind.XBUS, 3, 3, 3),
        "x3": _pin("x3", PinKind.XBUS, 4, 3, 3),
        "p1": _pin("p1", PinKind.SIMPLE, 5, 3, 3),
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
        "tx": _pin("tx", PinKind.XBUS, 2, 3, 2, PinDirection.INPUT),
        "rx": _pin("rx", PinKind.XBUS, 3, 3, 2, PinDirection.OUTPUT),
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
