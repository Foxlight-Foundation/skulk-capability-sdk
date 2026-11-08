"""Installed native schema entrypoint preserves JSON text across the Python ABI."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.portable_profile_cases import fixture

LEGACY = '"$schema":"https://json-schema.org/draft/2019-09/schema"'


def test_portable_profile_through_installed_python_abi() -> None:
    """The installed wheel retains exact new-profile decisions and identities."""
    reference = fixture()
    assert native.portable_profile_digest() == reference.profile_digest
    for case in reference.codec:
        if case.error:
            with pytest.raises(ValueError, match=f"^{case.error}:"):
                native.portable_canonical_json(case.source)
        else:
            assert case.canonical is not None
            assert (
                native.portable_canonical_json(case.source) == case.canonical.encode()
            )
    for case in reference.contracts:
        if case.error:
            with pytest.raises(ValueError, match=f"^{case.error}:"):
                native.portable_contract_json(case.name, case.source)
        else:
            assert case.canonical is not None
            assert (
                native.portable_contract_json(case.name, case.source)
                == case.canonical.encode()
            )
            assert (
                native.portable_contract_digest(case.name, case.source) == case.digest
            )


def test_portable_compiled_schema_reuse_and_safe_errors() -> None:
    """Immutable native compilation has no cross-call result or error leakage."""
    schema = native.PortableSchema('{"type":"integer","minimum":2}')
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert (
            list(workers.map(schema.matches, ["2", "1", "true", "2.0"] * 20))
            == [True, False, False, True] * 20
        )
    with pytest.raises(ValueError, match="^portable_number:"):
        schema.matches("9007199254740992")
    assert schema.matches("3")
    for definition in [
        '{"private-unknown-key":"credential-like-synthetic-text"}',
        '{"pattern":"(?=secret)"}',
    ]:
        with pytest.raises(ValueError) as failure:
            native.PortableSchema(definition)
        assert "credential-like-synthetic-text" not in str(failure.value)
        assert "secret" not in str(failure.value)


def test_portable_python_rejects_unescaped_language_surrogates() -> None:
    """Malformed Python strings get the same safe error as malformed Node UTF-16."""
    for source in ['"\ud800"', '{"\udfff":1}']:
        with pytest.raises(ValueError, match="^portable_unicode:"):
            native.portable_canonical_json(source)
        with pytest.raises(ValueError, match="^portable_unicode:"):
            native.PortableSchema("true").matches(source)


def test_signed_portable_file_preserves_contract_identity(tmp_path: Path) -> None:
    """Actual signature/file acquisition uses identical candidate validation.

    This is an isolated new-contract file, not a normal managed-plugin install
    or a replacement for the production publisher/owner acceptance gate.
    """
    source = fixture().contracts[1].source
    canonical = native.portable_contract_json("ContractEnvelope", source)
    key = Ed25519PrivateKey.generate()
    signature = key.sign(canonical)
    path = tmp_path / "signed-contracts.json"
    path.write_text(
        json.dumps(
            {
                "contracts": json.loads(canonical),
                "signature": base64.b64encode(signature).decode(),
            }
        )
    )
    acquired = json.loads(path.read_text())
    received = native.portable_contract_json(
        "ContractEnvelope", json.dumps(acquired["contracts"])
    )
    key.public_key().verify(base64.b64decode(acquired["signature"]), received)
    assert received == canonical
    assert native.portable_contract_digest(
        "ContractEnvelope", received.decode()
    ) == native.portable_contract_digest("ContractEnvelope", source)
    acquired["contracts"]["descriptors"][0]["title"] = "Different synthetic title"
    tampered = native.portable_contract_json(
        "ContractEnvelope", json.dumps(acquired["contracts"])
    )
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, tampered)


@pytest.mark.parametrize(
    ("schema", "instance", "expected"),
    [
        ('{"minimum":9007199254740993}', "9007199254740993", True),
        ('{"minimum":9007199254740993}', "9007199254740992", False),
        ('{"multipleOf":0.1}', "0.3", False),
        ('{"pattern":"^a$"}', '"a\\n"', True),
        ('{"type":"string"}', '"\\ud800"', True),
        ('{"const":"\\ud800"}', '"\\udb80\\udc00"', False),
        ('{"not":{"$ref":"https://example.invalid/schema"}}', "1", False),
        (
            "{" + LEGACY + ',"allOf":[{"additionalProperties":{"type":"integer"}}],'
            '"unevaluatedProperties":false}',
            '{"x":1}',
            False,
        ),
        (
            "{" + LEGACY + ',"unevaluatedProperties":{"type":"integer"}}',
            '{"type":"s"}',
            True,
        ),
    ],
)
def test_schema_text_acceptance(schema: str, instance: str, expected: bool) -> None:
    """Schema, number and surrogate identity are unchanged by ABI conversion."""
    assert native.schema_matches(schema, instance) is expected


def test_schema_definition_refusal_never_echoes_its_input() -> None:
    """Malformed definitions fail with a fixed safe error."""
    with pytest.raises(ValueError, match="^invalid payload schema$"):
        native.schema_matches("secret malformed schema", "1")


@pytest.mark.parametrize(
    ("tail", "instance", "expected"),
    [
        (
            '"$defs":{"v":{"additionalProperties":{"type":"integer"}}},'
            '"$ref":"#/$defs/v","unevaluatedProperties":false',
            '{"x":1}',
            False,
        ),
        (
            '"$defs":{"v":{"unevaluatedProperties":{"type":"integer"}}},'
            '"$ref":"#/$defs/v","unevaluatedProperties":false',
            '{"type":"s"}',
            True,
        ),
    ],
)
def test_static_reference_coverage(tail: str, instance: str, expected: bool) -> None:
    """Referenced coverage survives the installed Python ABI without a helper."""
    assert native.schema_matches("{" + LEGACY + "," + tail + "}", instance) is expected


def test_recursive_coverage_and_negated_cycle() -> None:
    """Recursive values keep exemptions while nonproductive cycles refuse."""
    schema = (
        "{" + LEGACY + ',"$recursiveAnchor":true,'
        '"properties":{"child":{"$recursiveRef":"#"}},'
        '"unevaluatedProperties":{"type":"integer"}}'
    )
    assert native.schema_matches(schema, '{"child":{"type":"s"}}')
    assert not native.schema_matches(schema, '{"child":{"x":"s"}}')
    cycle = "{" + LEGACY + ',"not":{"$ref":"#"},"unevaluatedProperties":false}'
    assert not native.schema_matches(cycle, "{}")


def test_installed_root_uses_owner_dialect_without_changing_generic_api() -> None:
    """Root draft selection is an execution contract, independent of author bytes."""
    schema = (
        '{"$schema":"http://json-schema.org/draft-07/schema#",'
        '"definitions":{"a":{"type":"object"}},'
        '"$ref":"#/definitions/a","type":"integer"}'
    )
    assert native.schema_matches(schema, "{}")
    assert not native.schema_matches_installed(schema, "{}")
    with pytest.raises(ValueError, match="^invalid payload schema$"):
        native.schema_matches_installed('{"$schema":1}', "{}")


def test_installed_schema_budget_does_not_expand_wire_payload_budget() -> None:
    """Large metadata remains separate from the normal call payload envelope."""
    schema = '{"description":"' + "x" * 70_000 + '"}'
    with pytest.raises(ValueError, match="^invalid payload schema$"):
        native.schema_matches(schema, "{}")
    assert native.schema_matches_installed(schema, '{"x":"' + "a" * 63_000 + '"}')
    assert not native.schema_matches_installed(schema, '{"x":"' + "a" * 66_000 + '"}')
