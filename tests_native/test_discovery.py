"""Native metadata admission and real HTTP discovery resource ownership."""

import asyncio
import copy
import ipaddress
import json
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import _skulk_capability_kernel as native  # pyright: ignore[reportMissingModuleSource]
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import JsonValue, TypeAdapter

from skulk_capability_sdk.discovery import (
    MAXIMUM_DIRECTORY_BYTES,
    CapabilityDirectory,
    DiscoveryError,
    discover_capabilities,
)
from tests_native.test_application import Mode, startup_value

_OBJECT = TypeAdapter(dict[str, JsonValue])
FIXTURE = _OBJECT.validate_json(
    Path(__file__).with_name("discovery-fixture.json").read_bytes()
)
_CASES = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
    Path(__file__).with_name("discovery-cases.json").read_bytes()
)


@pytest.mark.parametrize("case", _CASES, ids=[str(case["name"]) for case in _CASES])
def test_shared_directory_decisions(case: dict[str, JsonValue]) -> None:
    """Both languages admit the same bytes, including hostile identifiers."""
    source = case["source"]
    assert isinstance(source, str)
    if case["code"] is None:
        CapabilityDirectory.from_json(source.encode())
    else:
        with pytest.raises(DiscoveryError) as failure:
            CapabilityDirectory.from_json(source.encode())
        assert failure.value.code == case["code"]


def document(offers: list[dict[str, JsonValue]]) -> bytes:
    """Encode synthetic observations without touching provider or caller state."""
    return json.dumps(offers, allow_nan=False).encode()


def altered(path: tuple[str, ...], value: JsonValue) -> bytes:
    """Change one field while retaining all other original target bindings."""
    offer = copy.deepcopy(FIXTURE)
    parent = offer
    for key in path[:-1]:
        child = parent[key]
        assert isinstance(child, dict)
        parent = child
    parent[path[-1]] = value
    return document([offer])


def test_observations_are_owned_exact_and_uncapped() -> None:
    """Parsing does not grant, infer a cap or retain caller-mutable descriptor data."""
    source = document([FIXTURE])
    directory = CapabilityDirectory.from_json(source)
    offer = directory.select("managed.example", "example", "1.0.0")
    assert offer.inference_seconds is None
    assert offer.io_mode == "unary"
    assert offer.runtime_digest == "a" * 64
    target = FIXTURE["target"]
    assert isinstance(target, dict)
    assert offer.endpoint.capability_id == target["capability_id"]
    assert offer.endpoint.version == target["version"]
    assert offer.endpoint.descriptor_revision == target["descriptor_revision"]
    descriptor = offer.endpoint.descriptor()
    descriptor["behavior"] = "execute"
    assert offer.endpoint.descriptor()["behavior"] == "inference"
    assert "a" * 64 not in repr(directory)
    assert CapabilityDirectory.from_json(b"[]").offers == ()
    with pytest.raises(ValueError, match="exactly one"):
        directory.select("managed.other", "example", "1.0.0")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("runtime_digest",), "x" * 64),
        (("incarnation",), "b" * 64),
        (("plugin_id",), "invalid/provider"),
        (("target", "node_id"), "bad node"),
        (("target", "capability_id"), "other"),
        (("target", "version"), "9.0.0"),
        (("target", "descriptor_revision"), "0" * 64),
        (("target", "behavior"), "read"),
        (("target", "processing_domain"), "local"),
        (("target", "extra"), True),
        (("extra",), True),
        (("stream_seconds",), True),
        (("cleanup_seconds",), 0),
        (("startup_seconds",), -1),
        (("inference_seconds",), False),
        (("descriptor", "behavior"), "execute"),
        (("descriptor", "unknown_field"), "private-response-marker"),
    ],
)
def test_changed_or_future_metadata_refuses_atomically(
    path: tuple[str, ...], value: JsonValue
) -> None:
    """Discovery never relabels a signed descriptor or returns a valid subset."""
    raw = altered(path, value)
    with pytest.raises(DiscoveryError) as failure:
        CapabilityDirectory.from_json(raw)
    assert failure.value.code == "invalid_directory"
    assert "private-response-marker" not in str(failure.value)


def test_duplicate_destinations_and_json_keys_refuse() -> None:
    """A second matching route and ambiguous JSON cannot silently win selection."""
    with pytest.raises(DiscoveryError):
        CapabilityDirectory.from_json(document([FIXTURE, FIXTURE]))
    raw = document([FIXTURE]).replace(
        b'"plugin_id":', b'"plugin_id":"managed.other","plugin_id":', 1
    )
    with pytest.raises(DiscoveryError):
        CapabilityDirectory.from_json(raw)


@pytest.mark.parametrize(
    "mode", ["unary", "server_streaming", "client_streaming", "bidirectional"]
)
def test_supported_modes_retain_their_exact_descriptor(mode: Mode) -> None:
    """Every existing Core mode preserves its schema and explicit owner ceiling."""
    value = startup_value("unused-control", "unused-stream", mode)
    manifest = value["manifest"]
    assert isinstance(manifest, dict)
    contracts = manifest["contracts"]
    assert isinstance(contracts, dict)
    descriptors = contracts["descriptors"]
    assert isinstance(descriptors, list) and isinstance(descriptors[0], dict)
    descriptor = descriptors[0]
    offer = copy.deepcopy(FIXTURE)
    target = offer["target"]
    assert isinstance(target, dict)
    offer["descriptor"] = descriptor
    offer["inference_seconds"] = 9.25
    target["descriptor_revision"] = native.portable_v2_contract_digest(
        "Descriptor", json.dumps(descriptor)
    )
    observed = CapabilityDirectory.from_json(document([offer])).offers[0]
    assert observed.io_mode == mode and observed.inference_seconds == 9.25


def test_large_directories_and_metadata_are_not_payloads() -> None:
    """Directory and descriptor metadata retain their larger native record bounds."""
    offers: list[dict[str, JsonValue]] = []
    for index in range(120):
        offer = copy.deepcopy(FIXTURE)
        target = offer["target"]
        assert isinstance(target, dict)
        target["node_id"] = f"node-{index}"
        offers.append(offer)
    raw = document(offers)
    assert len(raw) > 65536
    directory = CapabilityDirectory.from_json(raw)
    assert len(directory.offers) == 120
    with pytest.raises(ValueError, match="exactly one"):
        directory.select("managed.example", "example", "1.0.0")
    assert (
        directory.select(
            "managed.example", "example", "1.0.0", node_id="node-2"
        ).endpoint.node_id
        == "node-2"
    )
    offer = copy.deepcopy(FIXTURE)
    descriptor = offer["descriptor"]
    target = offer["target"]
    assert isinstance(descriptor, dict) and isinstance(target, dict)
    for field in ("input_schema", "output_schema"):
        descriptor[field] = {"type": "object", "description": "x" * 35000}
    target["descriptor_revision"] = native.portable_v2_contract_digest(
        "Descriptor", json.dumps(descriptor)
    )
    assert len(CapabilityDirectory.from_json(document([offer])).offers) == 1
    with pytest.raises(DiscoveryError) as failure:
        CapabilityDirectory.from_json(b" " * (MAXIMUM_DIRECTORY_BYTES + 1))
    assert failure.value.code == "limit"


@asynccontextmanager
async def serving(
    body: bytes,
    *,
    status: int = 200,
    stalled: bool = False,
    received: asyncio.Event | None = None,
    extra_headers: dict[str, str] | None = None,
    tls: ssl.SSLContext | None = None,
) -> AsyncGenerator[tuple[str, list[bytes], asyncio.Event]]:
    """Own an actual HTTP fixture and every accepted socket without logging headers."""
    requests: list[bytes] = []
    disconnected = asyncio.Event()
    handlers: set[asyncio.Task[None]] = set()

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            if received is not None:
                received.set()
            additional = "".join(
                f"{key}: {value}\r\n" for key, value in (extra_headers or {}).items()
            )
            headers = (
                f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body) if not stalled else 1000000}\r\n"
                f"Connection: close\r\n{additional}\r\n"
            ).encode()
            writer.write(headers)
            if not stalled:
                writer.write(body)
            await writer.drain()
            await reader.read()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            disconnected.set()

    def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(handle(reader, writer))
        handlers.add(task)
        task.add_done_callback(handlers.discard)

    server = await asyncio.start_server(accepted, "127.0.0.1", 0, ssl=tls)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{port}", requests, disconnected
    finally:
        server.close()
        await server.wait_closed()
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


async def test_real_http_discovery_uses_only_its_enrolled_bearer() -> None:
    """One explicit authenticated read has no retry and no inference request."""
    async with serving(document([FIXTURE])) as (base, requests, disconnected):
        result = await discover_capabilities(base, "synthetic-client-key")
        assert len(result.offers) == 1
        assert len(requests) == 1
        assert requests[0].startswith(b"GET /v1/native-inference HTTP/1.1")
        assert b"authorization: Bearer synthetic-client-key\r\n" in requests[0]
        assert b"accept-encoding: identity\r\n" in requests[0]
        await asyncio.wait_for(disconnected.wait(), 3)


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "gzip, gzip"])
async def test_encoded_metadata_refuses_before_body_read(encoding: str) -> None:
    """Compressed metadata is retired at its headers before any decompression."""
    async with serving(
        b"", stalled=True, extra_headers={"Content-Encoding": encoding}
    ) as (base, requests, disconnected):
        with pytest.raises(DiscoveryError) as failure:
            await discover_capabilities(base, "synthetic-client-key", timeout_seconds=1)
        assert failure.value.code == "invalid_directory"
        assert len(requests) == 1
        await asyncio.wait_for(disconnected.wait(), 3)


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
async def test_http_refusals_are_not_replayed(status: int) -> None:
    """Unknown or denied service outcomes never become hidden fallback requests."""
    async with serving(b"private-response-marker", status=status) as (
        base,
        requests,
        _,
    ):
        with pytest.raises(DiscoveryError) as failure:
            await discover_capabilities(base, "synthetic-client-key")
        assert len(requests) == 1
        assert "private-response-marker" not in str(failure.value)


async def test_metadata_timeout_closes_the_socket() -> None:
    """A stalled response is retired before its metadata deadline escapes."""
    async with serving(b"", stalled=True) as (base, _, disconnected):
        with pytest.raises(DiscoveryError) as failure:
            await discover_capabilities(base, "synthetic-client-key", timeout_seconds=1)
        assert failure.value.code == "timeout"
        await asyncio.wait_for(disconnected.wait(), 3)


async def test_redirect_cannot_forward_the_bearer_to_another_origin() -> None:
    """A real redirect target observes no request or credential-bearing header."""
    async with serving(b"[]") as (sink, forwarded, _):
        async with serving(
            b"[]", status=302, extra_headers={"Location": sink + "/v1/native-inference"}
        ) as (origin, requests, _):
            with pytest.raises(DiscoveryError):
                await discover_capabilities(origin, "synthetic-client-key")
            assert len(requests) == 1
            assert forwarded == []


async def test_environment_proxy_cannot_receive_the_client_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured proxy observes neither the metadata request nor its key."""
    async with serving(b"[]") as (proxy, intercepted, _):
        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(variable, proxy)
        monkeypatch.setenv("NO_PROXY", "")
        async with serving(document([FIXTURE])) as (origin, requests, _):
            result = await discover_capabilities(origin, "synthetic-client-key")
            assert len(result.offers) == len(requests) == 1
            assert intercepted == []


async def test_oversized_http_metadata_closes_before_returning() -> None:
    """The transfer bound applies to actual bytes read, not a claimed JSON size."""
    async with serving(b" " * (MAXIMUM_DIRECTORY_BYTES + 1)) as (
        base,
        requests,
        disconnected,
    ):
        with pytest.raises(DiscoveryError) as failure:
            await discover_capabilities(base, "synthetic-client-key")
        assert failure.value.code == "limit"
        assert len(requests) == 1
        await asyncio.wait_for(disconnected.wait(), 3)


async def test_repeated_cancellation_retires_the_pending_request() -> None:
    """Repeated cancellation drains the owned HTTP read before returning."""
    received = asyncio.Event()
    async with serving(b"", stalled=True, received=received) as (base, _, disconnected):
        operation = asyncio.create_task(
            discover_capabilities(base, "synthetic-client-key")
        )
        await asyncio.wait_for(received.wait(), 3)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await asyncio.wait_for(disconnected.wait(), 3)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "http://localhost",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com/#fragment",
        "http://127.0.0.1:0",
        "http://127.0.0.1:99999",
    ],
)
async def test_unsafe_origins_refuse_before_io(origin: str) -> None:
    """Credentials cannot reach a redirectable, plain remote or ambiguous URL."""
    with pytest.raises(ValueError):
        await discover_capabilities(origin, "synthetic-client-key")


@pytest.mark.parametrize("correct_name", [True, False])
async def test_private_ca_keeps_chain_and_hostname_verification(
    tmp_path: Path, correct_name: bool
) -> None:
    """An explicitly trusted private CA never disables the certificate name check."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Discovery fixture")])
    alternative = (
        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
        if correct_name
        else x509.DNSName("other.invalid")
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([alternative]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    async with serving(document([FIXTURE]), tls=context) as (base, requests, _):
        with pytest.raises(DiscoveryError):
            await discover_capabilities(base, "synthetic-client-key")
        assert requests == []
        if correct_name:
            directory = await discover_capabilities(
                base, "synthetic-client-key", certificate_authority=pem.decode()
            )
            assert len(directory.offers) == len(requests) == 1
        else:
            with pytest.raises(DiscoveryError):
                await discover_capabilities(
                    base, "synthetic-client-key", certificate_authority=pem.decode()
                )
            assert requests == []
