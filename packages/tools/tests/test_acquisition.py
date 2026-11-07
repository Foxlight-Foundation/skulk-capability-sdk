"""Anonymous binary acquisition remains hash pinned across HTTPS redirects."""

import hashlib
from pathlib import Path

import httpx
import pytest
from packaging.markers import default_environment
from skulk_capability_tools.wheelhouse import (
    LockedFile,
    LockedPackage,
    Lockfile,
    build_wheelhouse,
    fetch_locked,
)

from skulk_capability_sdk.contracts import Denied


def wheel() -> LockedFile:
    """Name one synthetic immutable wheel without requiring a package server."""
    return LockedFile(
        url="https://publisher.invalid/fixture-1.0.0-py3-none-any.whl",
        hash="sha256:" + hashlib.sha256(b"synthetic wheel").hexdigest(),
        size=15,
    )


def test_https_redirects_send_no_client_credentials_or_cookies() -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={
                    "Location": "https://assets.invalid/pinned",
                    "Set-Cookie": "secret=value",
                },
            )
        return httpx.Response(200, content=b"synthetic wheel")

    with httpx.Client(
        transport=httpx.MockTransport(transport),
        auth=("fixture", "secret"),
        headers={"Authorization": "fixture", "Cookie": "fixture=value"},
    ) as client:
        assert fetch_locked(client)(wheel(), 16) == b"synthetic wheel"
    assert len(requests) == 2
    assert all(
        "authorization" not in request.headers and "cookie" not in request.headers
        for request in requests
    )


@pytest.mark.parametrize(
    "location",
    [
        "http://assets.invalid/wheel",
        "https://user:secret@assets.invalid/wheel",
        "file:///wheel",
    ],
)
def test_redirects_refuse_unsafe_destinations_before_fetch(location: str) -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": location})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(Denied):
            fetch_locked(client)(wheel(), 16)
    assert len(requests) == 1


def test_redirect_and_body_budgets_are_bounded() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"Location": "/loop"})
        )
    ) as client:
        with pytest.raises(Denied, match="redirect limit"):
            fetch_locked(client)(wheel(), 16)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 17)
        )
    ) as client:
        with pytest.raises(Denied, match="exceeds"):
            fetch_locked(client)(wheel(), 16)


@pytest.mark.parametrize("tamper", [False, True])
def test_direct_wheel_sources_use_locked_bytes_without_a_source_build(
    tmp_path: Path, tamper: bool
) -> None:
    file = wheel()
    lock = Lockfile(
        package=(
            LockedPackage(
                name="fixture",
                version="1.0.0",
                source={"url": str(file.url)},
                wheels=(file,),
            ),
        ),
        version=1,
    )

    def build(package: LockedPackage) -> tuple[str, bytes]:
        raise AssertionError(
            "A locked wheel must never invoke a compiler or source checkout"
        )

    output = tmp_path / "wheels"
    environment = {key: str(value) for key, value in default_environment().items()}
    if tamper:
        with pytest.raises(Denied, match="differs"):
            build_wheelhouse(
                lock,
                output,
                root="fixture",
                family="macos-arm64",
                environment=environment,
                fetch=lambda file, limit: b"altered bytes",
                build=build,
            )
        assert not output.exists()
    else:
        report = build_wheelhouse(
            lock,
            output,
            root="fixture",
            family="macos-arm64",
            environment=environment,
            fetch=lambda file, limit: b"synthetic wheel",
            build=build,
        )
        assert len(report.wheels) == 1 and report.wheels[0].origin == "locked"
        assert (output / file.filename).read_bytes() == b"synthetic wheel"
