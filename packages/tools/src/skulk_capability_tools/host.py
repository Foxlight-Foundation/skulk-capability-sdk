"""Explicit host administration with durable intent and no mutation retries."""

import asyncio
import ipaddress
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from skulk_capability_sdk.contracts import Denied
from skulk_capability_sdk.storage import (
    HostLock,
    atomic_private,
    read_private,
    secure_directory,
)

from .inspection import read_public
from .releases import PublisherTrust

INSTALLATIONS = "/v1/plugins/managed/installations"


def origin(url: str) -> tuple[str, bool]:
    """Accept HTTPS or literal loopback HTTP without URL credentials or paths."""
    parts = urlsplit(url)
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        raise Denied("Host URL must be an origin without credentials or a path")
    try:
        loopback = ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise Denied("Host administration requires HTTPS or literal loopback HTTP")
    return url.rstrip("/"), loopback


def installation_path(identifier: str) -> str:
    """Confine administrative paths to one exact managed installation."""
    if not re.fullmatch(r"managed\.[a-zA-Z0-9_.-]{1,120}", identifier):
        raise Denied("Choose an exact managed installation ID")
    return f"{INSTALLATIONS}/{identifier}"


class Intent(BaseModel):
    """Private, replay-resistant record saved before one HTTP mutation."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: Literal[1] = 1
    host: str
    path: str
    status_path: str
    body: dict[str, JsonValue]
    phase: Literal["prepared", "submitted", "observed"]


class SourceDefinition(BaseModel):
    """Explicit public feed and publisher trust; credentials are never journaled."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    expected_revision: int = Field(ge=0)
    base_url: str = Field(max_length=2048)
    metadata_filename: str = Field(
        default="release.json", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.json$"
    )
    trust: PublisherTrust


class HostClient:
    """Use existing Core authentication; package installation grants no authority."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        request_seconds: float = 35.0,
    ) -> None:
        self.url, loopback = origin(url)
        headers = {"Accept": "application/json"}
        if token is not None:
            if not token or any(character.isspace() for character in token):
                raise Denied("Malformed host bearer")
            headers["Authorization"] = f"Bearer {token}"
        elif loopback:
            # Direct local administration is an existing host policy, not an
            # SDK-generated bearer. Remote callers must supply their own grant.
            headers.update({"Origin": self.url, "X-Skulk-Dashboard": "pairing-v1"})
        if not 0 < request_seconds <= 35:
            raise Denied("Host request deadline must be within 35 seconds")
        self.headers = headers
        self.transport = transport
        self.request_seconds = request_seconds

    def close(self) -> None:
        """No pooled connections survive an individual bounded request."""

    def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        body: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        """Send once within one whole-request deadline, including response cleanup."""
        if (
            (path != INSTALLATIONS and not path.startswith(INSTALLATIONS + "/"))
            or "?" in path
            or ".." in path
        ):
            raise Denied("Unsupported host administration path")
        return asyncio.run(self._request(method, path, body))

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        body: dict[str, JsonValue] | None,
    ) -> dict[str, JsonValue]:
        try:
            async with (
                asyncio.timeout(self.request_seconds),
                httpx.AsyncClient(
                    base_url=self.url,
                    headers=self.headers,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=self.request_seconds,
                    transport=self.transport,
                ) as client,
            ):
                async with client.stream(method, path, json=body) as response:
                    if (
                        response.headers.get("Content-Encoding", "identity")
                        != "identity"
                    ):
                        raise Denied("Host returned an encoded metadata response")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > 524288:
                            raise Denied("Host response exceeds its metadata bound")
                        raw.extend(chunk)
                    if response.status_code != 200:
                        raise Denied(
                            f"Host refused request (HTTP {response.status_code})"
                        )
                    try:
                        return TypeAdapter(dict[str, JsonValue]).validate_json(
                            bytes(raw), strict=True
                        )
                    except ValueError as error:
                        raise Denied(
                            "Host returned invalid response metadata"
                        ) from error
        except (httpx.HTTPError, TimeoutError) as error:
            # HTTPX errors can carry URLs and credentials. Preserve intent in
            # the private journal; never interpolate their text into output.
            raise Denied(
                "Host response uncertain; read the original operation status"
            ) from error

    def mutate(
        self,
        *,
        path: str,
        status_path: str,
        body: dict[str, JsonValue],
        journal: Path,
    ) -> dict[str, JsonValue]:
        """Journal and send once; reuse only observes the original status."""
        secure_directory(journal.parent)
        lock = HostLock(journal.parent)
        try:
            expected = Intent(
                host=self.url,
                path=path,
                status_path=status_path,
                body=body,
                phase="prepared",
            )
            if journal.exists() or journal.is_symlink():
                previous = Intent.model_validate_json(read_private(journal))
                if previous.model_copy(update={"phase": "prepared"}) != expected:
                    raise Denied("Journal already belongs to a different exact intent")
                return self._observe(previous)
            atomic_private(journal, expected.model_dump_json().encode())
            # A crash here remains conservatively uncertain. Neither prepared
            # nor submitted records automatically authorize another POST.
            atomic_private(
                journal,
                expected.model_copy(update={"phase": "submitted"})
                .model_dump_json()
                .encode(),
            )
            response = self.request("POST", path, body)
            self._validate_observation(expected, response, nested_install=False)
            atomic_private(
                journal,
                expected.model_copy(update={"phase": "observed"})
                .model_dump_json()
                .encode(),
            )
            return response
        finally:
            lock.close()

    def status(self, journal: Path) -> dict[str, JsonValue]:
        """Read an original operation through its same host; never send mutation."""
        intent = Intent.model_validate_json(read_private(journal))
        if intent.host != self.url:
            raise Denied("Journal belongs to another host")
        return self._observe(intent)

    def _observe(self, intent: Intent) -> dict[str, JsonValue]:
        result = self.request("GET", intent.status_path)
        self._validate_observation(
            intent, result, nested_install=intent.status_path.endswith("/install")
        )
        return result

    @staticmethod
    def _validate_observation(
        intent: Intent, result: dict[str, JsonValue], *, nested_install: bool
    ) -> None:
        """Require the response to acknowledge the exact submitted operation."""
        if intent.body.get("operation_id") is None:
            return
        operation = result.get("operation") if nested_install else result
        if not isinstance(operation, dict):
            raise Denied("Host has no observation for the original operation")
        request = operation.get("request")
        if not isinstance(request, dict) or any(
            request.get(key) != value for key, value in intent.body.items()
        ):
            raise Denied("Host observation names a different operation")


def main(command: str, arguments: list[str]) -> None:
    """Expose review, exact install/update, activation, rollback and uninstall."""
    import argparse

    parser = argparse.ArgumentParser(prog=f"skulk-cap {command}")
    parser.add_argument("--host", required=True)
    parser.add_argument("--token-file", type=Path)
    if command == "status":
        parser.add_argument("--journal", type=Path, required=True)
    else:
        parser.add_argument("--installation", required=True)
    if command in {
        "register",
        "source",
        "install",
        "update",
        "activate",
        "rollback",
        "uninstall",
    }:
        parser.add_argument("--journal", type=Path, required=True)
    if command == "source":
        parser.add_argument("--definition", type=Path, required=True)
    if command in {"install", "update", "activate", "rollback", "uninstall"}:
        parser.add_argument("--operation-id", required=True)
    if command in {"install", "update", "activate", "rollback"}:
        parser.add_argument("--runtime-digest", required=True)
    if command in {"install", "update"}:
        parser.add_argument("--source-revision", type=int, required=True)
    if command in {"activate", "rollback", "uninstall"}:
        parser.add_argument("--selection-revision", type=int, required=True)
        parser.add_argument("--accept-permissions", action="store_true")
    options = parser.parse_args(arguments)
    token = (
        read_private(options.token_file, 16384).decode().strip()
        if options.token_file
        else None
    )
    client = HostClient(options.host, token=token)
    try:
        if command == "status":
            result = client.status(options.journal)
        else:
            path = installation_path(options.installation)
            if command == "review":
                result = client.request("GET", path + "/release")
            elif command == "register":
                result = client.mutate(
                    path=INSTALLATIONS,
                    status_path=path,
                    body={"plugin_id": options.installation},
                    journal=options.journal,
                )
            elif command == "source":
                try:
                    source = SourceDefinition.model_validate_json(
                        read_public(options.definition, 32768)
                    )
                except ValueError as error:
                    raise Denied(
                        "Source definition accepts only public feed and trust metadata"
                    ) from error
                parts = urlsplit(source.base_url)
                if (
                    parts.scheme != "https"
                    or not parts.hostname
                    or parts.username is not None
                    or parts.password is not None
                    or parts.query
                    or parts.fragment
                    or not parts.path.endswith("/")
                ):
                    raise Denied(
                        "Feed requires an HTTPS directory without URL credentials"
                    )
                result = client.mutate(
                    path=path + "/source",
                    status_path=path + "/source",
                    body=source.model_dump(mode="json"),
                    journal=options.journal,
                )
            else:
                if not re.fullmatch(r"[a-f0-9]{32}", options.operation_id):
                    raise Denied(
                        "Operation ID must be 32 lowercase hexadecimal characters"
                    )
                body: dict[str, JsonValue] = {"operation_id": options.operation_id}
                if command in {"install", "update"}:
                    body.update(
                        {
                            "runtime_digest": options.runtime_digest,
                            "expected_source_revision": options.source_revision,
                        }
                    )
                    mutation_path = path + "/install"
                    status_path = mutation_path
                else:
                    body.update(
                        {
                            "action": "uninstall"
                            if command == "uninstall"
                            else "activate",
                            "expected_revision": options.selection_revision,
                        }
                    )
                    if command != "uninstall":
                        body.update(
                            {
                                "runtime_digest": options.runtime_digest,
                                "rollback": command == "rollback",
                                "accept_permissions": options.accept_permissions,
                            }
                        )
                    mutation_path = path + "/operations"
                    status_path = mutation_path + "/" + options.operation_id
                result = client.mutate(
                    path=mutation_path,
                    status_path=status_path,
                    body=body,
                    journal=options.journal,
                )
        print(json.dumps(result, sort_keys=True))
    finally:
        client.close()
