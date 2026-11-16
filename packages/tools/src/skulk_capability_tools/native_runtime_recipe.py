"""Explicit native binary acquisition; no installation or execution authority."""

from typing import Literal, Self

from packaging.utils import canonicalize_name, parse_wheel_filename
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .native_publishing import NativeProfiles, native_cohort
from .runtime_release import canonical_platform
from .wheelhouse import LockedFile


class NativeRuntimeRecipe(BaseModel):
    """Versioned compiler-free inputs, pinned to one exact published native cohort.

    Recipes are reviewed acquisition inputs, independently of publisher trust,
    signed application releases and host authorization. Legacy recipes stay v1.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    format: Literal[2] = Field(description="Explicit native recipe version.")
    owner_version: str = Field(description="Exact separately licensed owner version.")
    sdk_version: str = Field(description="Exact public author SDK version.")
    platform: Literal["macos-arm64", "linux-glibc-x86_64", "linux-glibc-aarch64"] = (
        Field(description="Selected native binary family.")
    )
    source_owner_commit: str = Field(
        pattern=r"^[a-f0-9]{40}$", description="Immutable owner build source."
    )
    source_runtime_digest: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="Provenance of the independently qualified runtime closure.",
    )
    kernel: NativeProfiles = Field(
        description="Exact public native source and ABI profiles."
    )
    wheels: tuple[LockedFile, ...] = Field(
        min_length=3,
        max_length=64,
        description="Complete reviewed binary inventory with anonymous HTTPS URLs.",
    )

    @field_validator("format", mode="before")
    @classmethod
    def integer_format(cls, value: object) -> object:
        """Require an integer version rather than a numeric alias."""
        if type(value) is not int:
            raise ValueError("native recipe format must be an exact integer")
        return value

    @model_validator(mode="after")
    def exact_inputs(self) -> Self:
        """Bind all three versioned roots and the exact public platform asset."""
        distributions: dict[str, str] = {}
        for wheel in self.wheels:
            name, version, _, _ = parse_wheel_filename(wheel.filename)
            normalized = str(canonicalize_name(name))
            if normalized in distributions:
                raise ValueError("native recipe repeats a distribution")
            distributions[normalized] = str(version)
            if wheel.size is None or not 0 < wheel.size <= 64 * 1024 * 1024:
                raise ValueError("native recipe requires exact bounded wheel sizes")
        if sum(wheel.size or 0 for wheel in self.wheels) > 256 * 1024 * 1024:
            raise ValueError("native recipe exceeds its acquisition budget")
        if any(
            distributions.get(name) != version
            for name, version in (
                ("skulk-capability-nodes", self.owner_version),
                ("skulk-capability-sdk", self.sdk_version),
                ("skulk-capability-kernel", self.kernel.version),
            )
        ):
            raise ValueError("native recipe roots differ from their exact versions")
        receipt = native_cohort(self.kernel.version)
        platforms = receipt["platforms"]
        if not isinstance(platforms, list):
            raise ValueError("native cohort platform inventory refused")
        selected = next(
            (
                item
                for item in platforms
                if isinstance(item, dict)
                and canonical_platform(str(item["platform"]))
                == canonical_platform(self.platform)
            ),
            None,
        )
        if not isinstance(selected, dict) or not isinstance(selected["wheel"], dict):
            raise ValueError("native recipe platform has no qualified public asset")
        asset = selected["wheel"]
        wheel = next(
            item
            for item in self.wheels
            if parse_wheel_filename(item.filename)[0] == "skulk-capability-kernel"
        )
        if (
            wheel.filename != asset["name"]
            or wheel.sha256 != asset["sha256"]
            or wheel.size != asset["size"]
        ):
            raise ValueError("native recipe binary differs from the public cohort")
        return self
