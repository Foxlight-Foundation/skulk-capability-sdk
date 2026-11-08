"""Independent synthetic inputs/expectations for the SDK 1.0 candidate profile."""

import hashlib
import json
import math
import random
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, final

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue

ROOT = Path(__file__).resolve().parents[1]
ContractName = Literal["Descriptor", "ContractEnvelope"]


class Oracle(Protocol):
    """The independent producer uses only this draft-validator operation."""

    def is_valid(self, instance: JsonValue) -> bool: ...


def _oracle(definition: dict[str, JsonValue] | bool) -> Oracle:
    """Expose the stable producer operation rather than deprecated overloads."""
    return Draft202012Validator(definition)


class Frozen(BaseModel):
    """Strict maintainer-fixture records, never runtime authority."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


@final
class Response(Frozen):
    """One bounded probe outcome; canonical content is synthetic fixture data."""

    canonical: str | None = None
    digest: str | None = None
    valid: bool | None = None
    error: str | None = None


@final
class CodecCase(Frozen):
    """An independently specified canonical JSON or refusal."""

    source: str
    canonical: str | None = None
    error: str | None = None


@final
class SchemaCase(Frozen):
    """A closed-profile schema, exact input text and expected decision."""

    definition: str = Field(alias="schema")
    instance: str
    valid: bool | None = None
    error: str | None = None


@final
class ContractCase(Frozen):
    """One identity/default/negotiation witness."""

    name: ContractName
    source: str
    canonical: str | None = None
    digest: str | None = None
    error: str | None = None


@final
class Fixture(Frozen):
    """Normative examples with independent standard-library digest production."""

    profile: str
    profile_digest: str
    scope: str
    codec: list[CodecCase]
    schemas: list[SchemaCase]
    contracts: list[ContractCase]


@final
@dataclass(frozen=True)
class Case:
    """A synthetic probe request plus its independent expectation."""

    identity: str
    operation: Literal["canonical", "schema", "contract"]
    source: str
    expected: Response
    definition: str = ""
    name: ContractName = "Descriptor"

    def request(self) -> dict[str, JsonValue]:
        """Return only maintainer probe inputs, without expected results."""
        return {
            "operation": self.operation,
            "source": self.source,
            "definition": self.definition,
            "name": self.name,
        }


def fixture() -> Fixture:
    """Read checked-in expectations and verify their independent profile hash."""
    value = Fixture.model_validate_json(
        (ROOT / "kernel/contracts/portable-v1-cases.json").read_bytes()
    )
    specification: JsonValue = json.loads(
        (ROOT / "kernel/contracts/portable-v1.json").read_text()
    )
    canonical = json.dumps(specification, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        b"skulk.portable.profile\0" + canonical.encode()
    ).hexdigest()
    if digest != value.profile_digest:
        raise ValueError("independent portable profile fingerprint differs")
    return value


def cases() -> list[Case]:
    """Produce finite JSON and ordinary draft cases without using native policy."""
    reference = fixture()
    result: list[Case] = []
    for index, row in enumerate(reference.codec):
        result.append(
            Case(
                f"codec/{index}",
                "canonical",
                row.source,
                Response(canonical=row.canonical, error=row.error),
            )
        )
    for index, row in enumerate(reference.schemas):
        result.append(
            Case(
                f"schema/{index}",
                "schema",
                row.instance,
                Response(valid=row.valid, error=row.error),
                row.definition,
            )
        )
    for index, row in enumerate(reference.contracts):
        result.append(
            Case(
                f"contract/{index}",
                "contract",
                row.source,
                Response(canonical=row.canonical, digest=row.digest, error=row.error),
                name=row.name,
            )
        )

    random_source = random.Random(731_529)
    numbers = [0.0, -0.0, 1.0, -1.0, 0.1, -0.1, 1e-6, 1e-7, 5e-324]
    while len(numbers) < 393:
        number = struct.unpack(">d", random_source.getrandbits(64).to_bytes(8, "big"))[
            0
        ]
        if math.isfinite(number) and abs(number) <= 9_007_199_254_740_991:
            numbers.append(number)
    for index, number in enumerate(numbers):
        expected = json.dumps(int(number) if number.is_integer() else number)
        result.append(
            Case(
                f"number/{index}",
                "canonical",
                json.dumps(number),
                Response(canonical=expected),
            )
        )

    definitions: list[JsonValue] = [True, False]
    definitions.extend(
        {"type": kind}
        for kind in [
            "null",
            "boolean",
            "object",
            "array",
            "number",
            "integer",
            "string",
        ]
    )
    definitions.extend(
        [
            {"type": ["string", "null"]},
            {"minimum": 0},
            {"exclusiveMinimum": 1},
            {"maximum": 2},
            {"exclusiveMaximum": 2},
            {"multipleOf": 2},
            {"enum": [None, True, 1, "x"]},
            {"const": {"a": 1}},
            {"minLength": 2, "maxLength": 4},
            {"pattern": "[a-z]+"},
            {"items": {"type": "integer"}, "minItems": 1, "maxItems": 3},
            {"uniqueItems": True},
            {"prefixItems": [{"type": "string"}], "items": False},
            {"contains": {"type": "integer"}, "minContains": 1, "maxContains": 2},
            {
                "properties": {"a": {"type": "integer"}},
                "required": ["a"],
                "additionalProperties": False,
            },
            {
                "patternProperties": {"[ab]": {"type": "integer"}},
                "additionalProperties": False,
            },
            {"propertyNames": {"minLength": 2}, "minProperties": 1, "maxProperties": 2},
            {"dependentRequired": {"a": ["b"]}},
            {"dependentSchemas": {"a": {"required": ["b"]}}},
            {"allOf": [{"type": "number"}, {"minimum": 1}]},
            {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            {"oneOf": [{"type": "number"}, {"type": "integer"}]},
            {"not": {"type": "array"}},
            {
                "if": {"type": "number"},
                "then": {"minimum": 0},
                "else": {"type": "string"},
            },
            {
                "$defs": {"n": {"type": "integer"}},
                "properties": {"a": {"$ref": "#/$defs/n"}},
            },
        ]
    )
    instances: list[JsonValue] = [
        None,
        False,
        True,
        -2,
        0,
        1,
        2,
        1.5,
        0.3,
        "",
        "abc",
        "a\n",
        "é",
        "🦊",
        [],
        [1],
        [1, 1.0],
        ["x", 2],
        {},
        {"a": 1},
        {"a": "x"},
        {"b": 2},
        {"a": 1, "b": True},
    ]
    for schema_index, definition in enumerate(definitions):
        if not isinstance(definition, dict | bool):
            raise ValueError("independent producer requires a schema")
        validator = _oracle(definition)
        variants = [definition]
        if isinstance(definition, dict):
            variants.append(dict(reversed(tuple(definition.items()))))
        for variant_index, variant in enumerate(variants):
            for instance_index, instance in enumerate(instances):
                result.append(
                    Case(
                        f"draft/{schema_index}/{variant_index}/{instance_index}",
                        "schema",
                        json.dumps(instance),
                        Response(valid=validator.is_valid(instance)),
                        json.dumps(variant),
                    )
                )
    return result
