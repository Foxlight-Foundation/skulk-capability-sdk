"""Existing Core administration preserves exact intent across transport uncertainty."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue
from skulk_capability_tools.host import HostClient, installation_path, origin

from skulk_capability_sdk.contracts import Denied


def test_deadline_covers_the_whole_host_request() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    client = HostClient(
        "https://host.example",
        transport=httpx.MockTransport(transport),
        request_seconds=0.01,
    )
    with pytest.raises(Denied, match="uncertain"):
        client.request("GET", installation_path("managed.fixture"))


def test_uncertain_mutation_is_journaled_and_subsequent_call_only_reads_status(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    path = installation_path("managed.fixture") + "/operations"
    status = path + "/" + "a" * 32

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert (
                json.loads((tmp_path / "private" / "operation.json").read_text())[
                    "phase"
                ]
                == "submitted"
            )
            raise httpx.ReadTimeout("Synthetic lost acknowledgement")
        return httpx.Response(
            200,
            json={
                "state": "complete",
                "request": {
                    "operation_id": "a" * 32,
                    "action": "activate",
                },
            },
        )

    client = HostClient(
        "http://127.0.0.1:12345", transport=httpx.MockTransport(transport)
    )
    journal = tmp_path / "private" / "operation.json"
    body: dict[str, JsonValue] = {"operation_id": "a" * 32, "action": "activate"}
    try:
        with pytest.raises(Denied, match="uncertain"):
            client.mutate(path=path, status_path=status, body=body, journal=journal)
        assert journal.stat().st_mode & 0o777 == 0o600
        assert client.mutate(
            path=path, status_path=status, body=body, journal=journal
        ) == {
            "state": "complete",
            "request": {
                "operation_id": "a" * 32,
                "action": "activate",
            },
        }
        assert [request.method for request in requests] == ["POST", "GET"]
        assert requests[0].headers["Origin"] == "http://127.0.0.1:12345"
        with pytest.raises(Denied, match="different exact intent"):
            client.mutate(
                path=path,
                status_path=status,
                body={"action": "uninstall"},
                journal=journal,
            )
        assert len(requests) == 2
    finally:
        client.close()


def test_bearer_redirect_is_not_followed_and_error_does_not_print_token() -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/"})

    client = HostClient(
        "https://host.example",
        token="synthetic-secret",
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(Denied, match="HTTP 302") as raised:
            client.request("GET", installation_path("managed.fixture"))
        assert "synthetic-secret" not in str(raised.value)
        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer synthetic-secret"
        assert "Origin" not in requests[0].headers
    finally:
        client.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com/#token",
    ],
)
def test_host_origin_refuses_credentials_and_unsafe_destinations(url: str) -> None:
    with pytest.raises(Denied):
        origin(url)


def test_operation_status_cannot_be_redirected_to_another_host(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"state": "accepted", "request": {"operation_id": "a" * 32}}
        )

    journal = tmp_path / "private" / "operation.json"
    path = installation_path("managed.fixture") + "/install"
    first = HostClient("https://one.example", transport=httpx.MockTransport(transport))
    second = HostClient("https://two.example", transport=httpx.MockTransport(transport))
    try:
        first.mutate(
            path=path,
            status_path=path,
            body={"operation_id": "a" * 32},
            journal=journal,
        )
        with pytest.raises(Denied, match="another host"):
            second.status(journal)
        assert len(requests) == 1
    finally:
        first.close()
        second.close()


def test_mismatched_acknowledgement_retains_uncertain_intent(tmp_path: Path) -> None:
    requests: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(
            200,
            json={"state": "complete", "request": {"operation_id": "b" * 32}},
        )

    path = installation_path("managed.fixture") + "/operations"
    journal = tmp_path / "private" / "operation.json"
    client = HostClient(
        "https://host.example", transport=httpx.MockTransport(transport)
    )
    with pytest.raises(Denied, match="different operation"):
        client.mutate(
            path=path,
            status_path=path + "/" + "a" * 32,
            body={"operation_id": "a" * 32},
            journal=journal,
        )
    assert json.loads(journal.read_text())["phase"] == "submitted"
    with pytest.raises(Denied, match="different operation"):
        client.status(journal)
    assert requests == ["POST", "GET"]
