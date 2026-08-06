from __future__ import annotations

from pathlib import Path
from typing import Any

from .chip_catalog import DEFAULT_OUTPUT as DEFAULT_CHIP_CATALOG
from .chip_catalog import load_chip_catalog
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
    chip_kind_value=4,
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
    chip_kind_value=4,
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
    chip_kind_value=17,
    pins={
        "tx": _pin("tx", PinKind.XBUS, 2, 3, 2, PinDirection.INPUT),
        "rx": _pin("rx", PinKind.XBUS, 3, 3, 2, PinDirection.OUTPUT),
    },
)

BRIDGE_SPEC = PartSpec(
    type_name="BRIDGE",
    width=1,
    height=3,
    cost=0,
    max_code_lines=None,
    registers=(),
    pins={},
    chip_kind_value=2,
)

PART_SPECS = {
    MC4000_SPEC.type_name: MC4000_SPEC,
    MC6000_SPEC.type_name: MC6000_SPEC,
    RADIO_SPEC.type_name: RADIO_SPEC,
    BRIDGE_SPEC.type_name: BRIDGE_SPEC,
}

_CATALOG_PART_SPECS: dict[str, PartSpec] | None = None

# The manual describes MC4000X as the XBus-only MC4000 variant but does not
# repeat its program-memory/register table. Keep that inference explicit rather
# than silently treating the extracted package as a non-programmable device.
INFERRED_PROGRAMMABLE_BEHAVIOR = {
    "UC4X": {
        "max_code_lines": 9,
        "registers": ("acc",),
    },
}


class MC4000(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(MC4000_SPEC, name=name)


class MC6000(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(MC6000_SPEC, name=name)


class MC4000X(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(part_spec_for_type("UC4X"), name=name)


class Radio(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(RADIO_SPEC, name=name, provided=True)

    @property
    def rx(self):
        return self.pin("rx")

    @property
    def tx(self):
        return self.pin("tx")


class Bridge(Part):
    def __init__(self, name: str | None = None) -> None:
        super().__init__(BRIDGE_SPEC, name=name)


def part_from_type(type_name: str, name: str | None = None) -> Part:
    spec = part_spec_for_type(type_name)
    return Part(spec, name=name)


def part_spec_for_type(
    type_name: str,
    catalog_path: str | Path = DEFAULT_CHIP_CATALOG,
) -> PartSpec:
    if type_name in PART_SPECS:
        return PART_SPECS[type_name]
    specs = load_catalog_part_specs(catalog_path)
    try:
        return specs[type_name]
    except KeyError as exc:
        raise KeyError(f"unknown part type {type_name!r}") from exc


def load_catalog_part_specs(
    catalog_path: str | Path = DEFAULT_CHIP_CATALOG,
) -> dict[str, PartSpec]:
    global _CATALOG_PART_SPECS
    if Path(catalog_path).resolve() == Path(DEFAULT_CHIP_CATALOG).resolve():
        if _CATALOG_PART_SPECS is None:
            payload = load_chip_catalog(catalog_path)
            _CATALOG_PART_SPECS = {
                chip["type"]: part_spec_from_catalog_record(chip)
                for chip in payload["chips"]
            }
        return _CATALOG_PART_SPECS
    payload = load_chip_catalog(catalog_path)
    return {
        chip["type"]: part_spec_from_catalog_record(chip)
        for chip in payload["chips"]
    }


def part_spec_from_catalog_record(record: dict[str, Any]) -> PartSpec:
    type_name = record.get("type")
    size = record.get("size")
    if not isinstance(type_name, str) or not type_name:
        raise ValueError(f"chip record has invalid type {type_name!r}")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(isinstance(value, int) and value > 0 for value in size)
    ):
        raise ValueError(f"chip {type_name} has invalid size {size!r}")

    pins = {}
    for raw_pin in record.get("pins", []):
        direction_value = raw_pin.get("direction")
        direction = (
            PinDirection(direction_value)
            if direction_value in {item.value for item in PinDirection}
            else PinDirection.BIDIRECTIONAL
        )
        contact = raw_pin.get("contact_offset")
        rotated_contact = raw_pin.get("rotated_contact_offset")
        if (
            not isinstance(contact, list)
            or len(contact) != 2
            or not isinstance(rotated_contact, list)
            or len(rotated_contact) != 2
        ):
            raise ValueError(
                f"chip {type_name} pin {raw_pin.get('name')!r} has invalid contacts"
            )
        pin = PinSpec(
            name=raw_pin["name"],
            kind=PinKind(raw_pin["kind"]),
            side=Side(raw_pin["side"]),
            offset=raw_pin["side_offset"],
            index=raw_pin["index"],
            direction=direction,
            contact_dx=contact[0],
            contact_dy=contact[1],
            rotated_contact_dx=rotated_contact[0],
            rotated_contact_dy=rotated_contact[1],
        )
        if pin.name in pins:
            raise ValueError(f"chip {type_name} has duplicate pin {pin.name!r}")
        pins[pin.name] = pin

    manual = record.get("manual") or {}
    inferred_behavior = INFERRED_PROGRAMMABLE_BEHAVIOR.get(type_name, {})
    chip_kind_value = next(
        (
            field.get("value")
            for field in record.get("scalar_fields", [])
            if field.get("field_token") == "0x040008B5"
        ),
        None,
    )
    return PartSpec(
        type_name=type_name,
        width=size[0],
        height=size[1],
        cost=record.get("price", 0),
        max_code_lines=manual.get(
            "program_lines", inferred_behavior.get("max_code_lines")
        ),
        registers=tuple(
            manual.get(
                "internal_registers", inferred_behavior.get("registers", ())
            )
        ),
        pins=pins,
        chip_kind_value=chip_kind_value,
    )
