"""Compiled native author records for an explicitly local development session.

These helpers create no Fabric identity, issuer or installation permission.
The existing published native readers own all declaration and wire validation.
"""

import importlib
import json
from typing import Literal, Protocol, cast

from pydantic import JsonValue, TypeAdapter

from .native_publishing import NativeProfiles

type Language = Literal["python", "typescript"]
type Mode = Literal["unary", "server_streaming", "client_streaming", "bidirectional"]
OBJECT = TypeAdapter(dict[str, JsonValue])
OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
TEXT = TypeAdapter(str)


class Record(Protocol):
    """Owned canonical record supplied by the actual native binding."""

    @property
    def digest(self) -> str: ...
    def canonical(self) -> bytes: ...
    def admit_hello(self, source: str, call_id: str | None = None) -> object: ...


class Factory(Protocol):
    """Native record constructor, without a Python validation substitute."""

    def from_json(self, name: str, source: str) -> Record: ...


class Schema(Protocol):
    """Compiled portable schema selected by its actual author declaration."""

    def matches(self, source: str) -> bool: ...


class SchemaFactory(Protocol):
    """Construct a compiled schema from bounded public JSON."""

    def __call__(self, source: str) -> Schema: ...


class Observer(Protocol):
    """Native framing observation grants no caller authority."""

    def accept_input(self, packet: bytes) -> tuple[str, bytes]: ...
    def accept_output(self, packet: bytes) -> tuple[str, bytes]: ...
    @property
    def finished(self) -> bool: ...


class ObserverFactory(Protocol):
    """Bind one native media observer to a local call and exact mode."""

    def __call__(self, call_id: str, mode: str) -> Observer: ...


class Kernel(Protocol):
    """Public native ABI subset used by independent author tooling."""

    ManagedV2Contract: Factory
    PortableSchema: SchemaFactory
    TransportV2Observer: ObserverFactory

    def managed_v2_profile_digest(self) -> str: ...
    def portable_v2_profile_digest(self) -> str: ...
    def composition_v3_profile_digest(self) -> str: ...
    def calls_v2_profile_digest(self) -> str: ...
    def portable_v2_contract_digest(self, name: str, source: str) -> str: ...
    def encode_call_v2_frame(self, source: str, payload: bytes) -> bytes: ...
    def decode_call_v2_frame(self, packet: bytes) -> tuple[bytes, bytes]: ...
    def call_v2_payload_size(self, source: str) -> int: ...
    def encode_transport_v2_packet(self, source: str, media: bytes) -> bytes: ...
    def transport_v2_media_size(self, header: bytes) -> int: ...


def kernel() -> Kernel:
    """Load only the explicitly installed public native publisher cohort."""
    NativeProfiles.installed()
    return cast(
        Kernel, cast(object, importlib.import_module("_skulk_capability_kernel"))
    )


def document(value: object) -> bytes:
    """Encode bounded caller-owned JSON without nonfinite number aliases."""
    return json.dumps(value, allow_nan=False, ensure_ascii=True).encode()


def declaration(name: str, dependencies: tuple[str, ...]) -> dict[str, JsonValue]:
    """Create a model-free four-mode example through the compiled managed reader."""
    native = kernel()
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 4096}},
        "required": ["text"],
        "additionalProperties": False,
    }
    descriptors: list[JsonValue] = []
    for suffix, mode in (
        ("echo", "unary"),
        ("stream", "server_streaming"),
        ("collect", "client_streaming"),
        ("duplex", "bidirectional"),
    ):
        descriptors.append(
            {
                "contract_profile": "skulk.portable/2",
                "profile_digest": native.portable_v2_profile_digest(),
                "id": name + "." + suffix,
                "version": "1.0.0",
                "title": suffix.title(),
                "description": "Local model-free text/media example.",
                "behavior": "read",
                "processing_domain": "local",
                "io_mode": mode,
                "input_schema": schema,
                "output_schema": schema,
                "input_chunk_schema": schema
                if mode in {"client_streaming", "bidirectional"}
                else None,
                "output_chunk_schema": schema
                if mode in {"server_streaming", "bidirectional"}
                else None,
                "annotations": {},
            }
        )
    value: dict[str, JsonValue] = {
        "protocol": 6,
        "contract_profile": "skulk.managed/2",
        "profile_digest": native.managed_v2_profile_digest(),
        "bundle_id": name,
        "bundle_version": "0.1.0",
        "title": name,
        "executable": "bundle.pyz",
        "executable_sha256": "0" * 64,
        "arguments": [],
        "contracts": {
            "protocol": 6,
            "contract_profile": "skulk.portable/2",
            "profile_digest": native.portable_v2_profile_digest(),
            "descriptors": descriptors,
            "configuration_schema": {"type": "object"},
        },
        "bounds": {
            "startup_seconds": 30,
            "health_seconds": 2,
            "call_seconds": 30,
            "inference_seconds": None,
            "stream_seconds": 300,
            "shutdown_seconds": 5,
            "restart_budget": 3,
            "backoff_seconds": 0.1,
            "log_bytes": 0,
            "max_children": 4,
            "concurrency": 1,
        },
        "effect": "read",
        "credential_inputs": [],
        "runtime_dependencies": list(dependencies),
        "skulk_requires": ">=2.0.3,<3",
        "steward": [],
        "surfaces": [],
        "operations": None,
        "preflight_profile": None,
    }
    record = native.ManagedV2Contract.from_json("Manifest", document(value).decode())
    return OBJECT.validate_json(record.canonical())
