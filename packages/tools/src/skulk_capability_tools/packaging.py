"""Describe a capability well enough to package it, without naming it here.

The publisher used to enumerate capabilities in a closed literal and archive the
whole SDK package, so adding a capability meant editing core tooling and every
bundle carried every other capability. A capability now describes itself in a
``capability.toml`` (or a ``[tool.skulk-capability]`` table in its
``pyproject.toml``) and the publisher reads that, which is what lets a
capability live in its own repository and publish itself.
"""

import ast
import importlib
import importlib.util
import io
import subprocess
import sys
import tomllib
import zipfile
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import Self, cast, final

from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, Field, model_validator

from skulk_capability_sdk.contracts import Denied, Manifest

from .release_runtime import dependency_inventory, dependency_roots

SPEC_TABLE = ("tool", "skulk-capability")
"""Where the spec lives inside a ``pyproject.toml``."""

HOST_ENTRYPOINTS = ("__owner__", "__manage__")
"""Names Skulk itself runs against an installed artifact. They are no longer
packed: the owner and management launchers come from the SDK wheel installed
into the generation runtime, declared under ``skulk.capability_runtime``, so
a bundle carries only the capability instead of the SDK's whole host layer. The
names stay reserved so a capability cannot claim them."""


@final
class SourcePackage(BaseModel):
    """One additional regular Python package confined to the spec's project."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    package: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z_0-9]*$",
        max_length=128,
        description="Top-level import name of an additional source package.",
    )
    package_root: str = Field(
        min_length=1,
        max_length=4096,
        description="Spec-relative package directory containing __init__.py.",
    )

    @model_validator(mode="after")
    def unreserved_namespace(self) -> Self:
        """Prevent shared source from replacing interpreter or SDK namespaces."""
        if (
            self.package in sys.stdlib_module_names
            or self.package
            in {
                "skulk_capability_nodes",
                "skulk_capability_sdk",
                "skulk_capability_tools",
                "skulk",
            }
            or self.package.startswith("__")
        ):
            raise ValueError("source package uses a reserved namespace")
        return self


@final
class CapabilitySpec(BaseModel):
    """Everything the publisher needs to turn one capability into an artifact."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    package: str = Field(description="Importable package holding the capability.")
    package_root: str = Field(
        description=(
            "Spec-relative directory the package must resolve inside. Module "
            "lookup uses the publisher's interpreter, so without this a spec "
            "pointing at a separate checkout would silently archive and sign a "
            "different installed copy of the same package."
        )
    )
    entry: str = Field(description="Module whose main() the artifact runs.")
    validator: str | None = Field(
        default=None,
        description=(
            "Optional 'module:function' the publisher calls with the manifest. "
            "Manifest rules belong to the capability that has to honor them."
        ),
    )
    dependencies: tuple[str, ...] = Field(
        default=(),
        max_length=32,
        description="Named PEP 508 runtime requirements; no extras, markers or URLs.",
    )
    source_packages: tuple[SourcePackage, ...] = Field(
        default=(),
        max_length=16,
        description="Additional project-local source namespaces to follow and pack.",
    )
    extra_modules: tuple[str, ...] = Field(
        default=(),
        description=(
            "Modules this capability loads by computed name. The closure reads "
            "the import graph, so a module reached only through import_module "
            "with a built string is invisible to it and would be missing from "
            "the artifact."
        ),
    )
    entrypoints: dict[str, str] = Field(
        default_factory=dict,
        description="Extra '__name__' to 'module:function' shims to pack.",
    )
    data_files: dict[str, str] = Field(
        default_factory=dict,
        description="Archive path to spec-relative file to pack verbatim.",
    )
    template_path: str | None = Field(
        default=None,
        description=(
            "Archive path a caller-supplied qualified template is bound into. "
            "A capability that declares none cannot be handed one."
        ),
    )
    template_model: str | None = Field(
        default=None,
        description=(
            "'module:Model' a supplied template must validate against before it "
            "is packed. Signing unvalidated bytes into an artifact would make "
            "the signature attest to something nobody checked."
        ),
    )

    @model_validator(mode="after")
    def check_names(self) -> Self:
        """Refuse a spec that cannot address its own code."""
        dependency_roots(self.dependencies)
        names = [self.package, *(one.package for one in self.source_packages)]
        if any(
            not name.isidentifier()
            or name.startswith("__")
            or name in sys.stdlib_module_names
            or name
            in {
                "skulk",
                "skulk_capability_nodes",
                "skulk_capability_sdk",
                "skulk_capability_tools",
            }
            for name in names
        ):
            raise ValueError("source package uses a reserved namespace")
        if len(set(names)) != len(names):
            raise ValueError("duplicate source package")
        if (self.template_model is None) != (self.template_path is None):
            raise ValueError("a template needs both a packed path and a model")
        if not self.entry.startswith(f"{self.package}.") and self.entry != self.package:
            raise ValueError("entry must live inside the declared package")
        for name in (*self.entrypoints, *HOST_ENTRYPOINTS):
            # Installed entrypoints are launched with runpy.run_module, so the
            # name is a module name. A dot in it reads as a package boundary and
            # the entrypoint cannot be launched at all.
            if (
                not name.startswith("__")
                or not name.endswith("__")
                or not name.isidentifier()
            ):
                raise ValueError(f"entrypoint {name} must be a dunder identifier")
        # Skulk runs __owner__ and __manage__ itself, and __main__ is the entry
        # this spec declares. A capability that could redefine them would run
        # its own code in the owner and management processes.
        packed = tuple(names)
        for name, target in self.entrypoints.items():
            module = target.partition(":")[0]
            if not any(module == one or module.startswith(f"{one}.") for one in packed):
                # The probe runs with the interpreter's site packages visible,
                # so an entrypoint aimed at a publisher-installed module would
                # verify here and be missing from the isolated installation.
                raise ValueError(
                    f"entrypoint {name} targets {module}, which the artifact "
                    "does not carry"
                )
        for name in self.extra_modules:
            if not any(name == one or name.startswith(f"{one}.") for one in packed):
                # It resolves on the publisher, so the required-module check
                # passes, and then the closure skips it for being outside the
                # packed namespaces: declared, absent, and unnoticed.
                raise ValueError(
                    f"declared module {name} is outside the packed packages"
                )
        reserved = {"__main__", *HOST_ENTRYPOINTS} & set(self.entrypoints)
        if reserved:
            raise ValueError(
                f"{', '.join(sorted(reserved))} belong to the host and cannot be "
                "declared by a capability"
            )
        if self.template_path is not None and self.template_path in self.data_files:
            raise ValueError(
                f"{self.template_path} is the template path; a data file there "
                "would be packed without validation whenever none is supplied"
            )
        for path in (*self.data_files, self.template_path or "x"):
            if path.startswith("/") or ".." in Path(path).parts:
                raise ValueError(f"packed path {path} must stay inside the archive")
        return self


def load_spec(path: Path) -> tuple[CapabilitySpec, Path]:
    """Read a capability spec and return it with the directory it resolves against."""
    document: dict[str, object] = tomllib.loads(path.read_text())
    if path.name == "pyproject.toml":
        for key in SPEC_TABLE:
            section = document.get(key)
            if not isinstance(section, dict):
                raise Denied(f"{path} has no [{'.'.join(SPEC_TABLE)}] table")
            document = cast(dict[str, object], section)
    # TOML arrays are lists and the contract is strict on purpose, so every
    # sequence is normalized here rather than loosening the model. Doing it by
    # value type rather than by field name keeps a future sequence field from
    # reintroducing the same failure.
    document = {
        key: tuple(cast(list[object], value)) if isinstance(value, list) else value
        for key, value in document.items()
    }
    return CapabilitySpec.model_validate(document), path.parent


def within(name: str, packages: tuple[str, ...]) -> bool:
    """Whether a module belongs to one of these packages, on a dotted boundary.

    A plain prefix test would treat ``skulk_capability_nodes.owner`` as part of a
    capability package named ``skulk_capability``.
    """
    return any(
        name == package or name.startswith(f"{package}.") for package in packages
    )


def module_file(name: str) -> Path | None:
    """Resolve an importable module to its source file without importing it."""
    try:
        specification = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if specification is None or specification.origin in (None, "namespace"):
        return None
    return Path(str(specification.origin))


def imported_names(
    source: str, module: str, packages: tuple[str, ...], is_package: bool
) -> set[str]:
    """Read every module one module refers to, by import or by name.

    Import statements are not the whole story: ``worker_process`` launches five
    helpers by running ``skulk_capability_nodes.<worker>`` in a fresh
    interpreter, and their names live in a ``Literal`` annotation. A walk that
    followed only imports would leave those out of the artifact and the child
    would fail at runtime, so any string that names a module inside the packed
    packages counts too. Over-inclusion costs bytes; under-inclusion ships a
    broken bundle.
    """
    # Inside a package's __init__, `from . import x` means this package's own
    # x. Inside an ordinary module it means the parent's. Treating both the same
    # drops a level and silently leaves modules out of the artifact.
    context = module.split(".") if is_package else module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                depth = len(context) - (node.level - 1)
                parts = context[:depth] if depth > 0 else context[:1]
                base = ".".join(parts)
                root = f"{base}.{node.module}" if node.module else base
            elif node.module:
                root = node.module
            else:
                continue
            found.add(root)
            found.update(f"{root}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if within(node.value, packages):
                found.add(node.value)
            elif node.value.isidentifier():
                # Unqualified strings describe this module's own namespace.
                # Guessing against every namespace could copy private host
                # modules into an external bundle merely because it says "store".
                found.update(
                    f"{name}.{node.value}"
                    for name in packages
                    if within(module, (name,))
                )
    return found


def dynamic_targets(source: str, module: str, is_package: bool) -> set[str]:
    """Read module names passed literally to import_module or __import__.

    The constant scan that feeds the closure cannot distinguish a module name
    from any other identifier-shaped string, so it guesses generously and packs
    what resolves. That is safe for packing and useless for refusing: a literal
    third-party name would be read as one of the capability's own modules and
    never questioned. These call sites are unambiguous, so they are the ones
    that can be held to the dependency inventory.

    Local names bound to the importers are followed, because
    ``from importlib import import_module as load`` is an ordinary spelling.
    This stays best effort by nature: a name assembled at runtime cannot be read
    statically, which is what ``extra_modules`` is for.
    """
    # Only names actually bound to importlib count. A capability may define its
    # own import_module helper, or call one on an object of its own, and
    # treating those as dynamic imports would refuse a valid build.
    importers = {"__import__"}
    modules_named_importlib: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            importers.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, ast.Import):
            modules_named_importlib.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "importlib"
            )
    context = module.split(".") if is_package else module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {word.arg: word.value for word in node.keywords}
        # import_module(name=..., package=...) is an ordinary spelling.
        positional = [*node.args]
        if not positional and "name" in keywords:
            positional.append(keywords["name"])
            if "package" in keywords:
                positional.append(keywords["package"])
        elif len(positional) == 1 and "package" in keywords:
            positional.append(keywords["package"])
        if not positional:
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            owner = target.value
            dynamic = target.attr == "import_module" and (
                isinstance(owner, ast.Name) and owner.id in modules_named_importlib
            )
        elif isinstance(target, ast.Name):
            dynamic = target.id in importers
        else:
            dynamic = False
        first = positional[0]
        if not dynamic or not isinstance(first, ast.Constant):
            continue
        if not isinstance(first.value, str):
            continue
        value: str = first.value
        if value.startswith("."):
            # A relative name resolves against the call's own package argument,
            # which is the scanned module's package only when the caller passes
            # __package__. Anything else names a different package, and when it
            # is not a literal we cannot know which, so the call is left alone
            # rather than resolved against the wrong one.
            anchor = positional[1] if len(positional) > 1 else None
            if anchor is None:
                # CPython raises TypeError for a relative name with no package,
                # so this call cannot run. The probe imports the entrypoint
                # without executing it, which is why it reaches here at all.
                raise Denied(
                    f"{module} calls import_module({value!r}) with no package; "
                    "a relative name needs one and this call cannot run"
                )
            if isinstance(anchor, ast.Name) and anchor.id == "__package__":
                base = context
            elif isinstance(anchor, ast.Constant) and isinstance(anchor.value, str):
                base = anchor.value.split(".")
            else:
                continue
            level = len(value) - len(value.lstrip("."))
            depth = len(base) - (level - 1)
            parts = base[:depth] if depth > 0 else base[:1]
            value = ".".join((*parts, value.lstrip(".")))
        found.add(value)
    return found


def definite_imports(source: str, module: str, is_package: bool) -> set[str]:
    """Names this module certainly imports, as opposed to ones merely guessed.

    ``imported_names`` deliberately over-guesses: it treats an identifier-shaped
    string as a possible module and reads ``from x import y`` as a possible
    submodule, because packing too much is harmless. Those guesses must stay
    permissive, and they must never reach a refusal: a constant that merely
    looks like a module name would otherwise reject a valid capability. An
    import statement's own target is different. If it names a module inside the
    capability and that module does not exist, the artifact is incomplete and
    signing it produces a failure at launch, and if it names a package the
    inventory does not carry, the capability cannot run once installed. This is
    the set that decisions are made on; ``imported_names`` only decides what to
    pack.
    """
    context = module.split(".") if is_package else module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                depth = len(context) - (node.level - 1)
                parts = context[:depth] if depth > 0 else context[:1]
                found.add(".".join((*parts, node.module) if node.module else parts))
            elif node.module:
                found.add(node.module)
    return found


def closure(
    roots: tuple[str, ...], packages: tuple[str, ...], required: tuple[str, ...] = ()
) -> dict[str, Path]:
    """Collect the transitive module closure of the given entry modules."""
    collected: dict[str, Path] = {}
    for name in required:
        if module_file(name) is None:
            raise Denied(
                f"declared module {name} does not resolve; a module the spec "
                "names cannot be quietly left out of the artifact"
            )
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in collected or not within(name, packages):
            continue
        path = module_file(name)
        if path is None:
            continue
        collected[name] = path
        parts = name.split(".")
        pending.extend(".".join(parts[:depth]) for depth in range(1, len(parts)))
        source = path.read_text()
        is_package = path.name == "__init__.py"
        dynamic = dynamic_targets(source, name, is_package)
        for definite in (*definite_imports(source, name, is_package), *dynamic):
            if within(definite, packages) and module_file(definite) is None:
                raise Denied(
                    f"{name} imports {definite}, which does not resolve; the "
                    "artifact would be signed without it and fail at launch"
                )
        pending.extend(imported_names(source, name, packages, is_package))
        # A name given literally to import_module is an import like any other,
        # so the module it names has to be packed, not merely checked.
        pending.extend(dynamic)
    return collected


def entrypoints(spec: CapabilitySpec) -> dict[str, str]:
    """Every '__name__' the artifact exposes, mapped to 'module:function'.

    The reserved names are checked again here rather than trusted from
    validation: ``frozen=True`` stops the field being rebound but not the
    mapping inside it being edited, and these entries decide which code runs in
    the owner and management processes.
    """
    declared = dict(spec.entrypoints)
    reserved = {"__main__", *HOST_ENTRYPOINTS} & set(declared)
    if reserved:
        raise Denied(
            f"{', '.join(sorted(reserved))} belong to the host and cannot be "
            "declared by a capability"
        )
    return {"__main__": f"{spec.entry}:main", **declared}


def check_imports(spec: CapabilitySpec, modules: dict[str, Path]) -> None:
    """Refuse a capability that imports something the installation will not have.

    The verification subprocess runs with the publisher's ordinary path, so a
    third-party package installed here satisfies the import and the release is
    signed. The isolated installation has only the artifact and the signed
    dependency inventory, so the capability fails the moment it is imported
    there. Declared requirements expand the verified inventory, never the
    publisher's ambient import path.
    """
    # A distribution's name is not its import name: keyring's closure carries
    # jaraco-context, which is imported as jaraco.context. Ask the metadata
    # which top-level names each installed distribution provides.
    # The fixed roots are what every runtime carries. The SDK's own modules
    # reach a capability through the packed closure, not as a distribution,
    # and another launcher-declaring distribution in the publisher's
    # environment must not widen what a capability may import.
    carried = {
        canonicalize_name(name)
        for name in dependency_inventory(
            launcher_roots=False, additional=spec.dependencies
        )
    }
    provided = {
        top
        for top, distributions in packages_distributions().items()
        if any(canonicalize_name(one) in carried for one in distributions)
    }
    available = {
        *sys.stdlib_module_names,
        *provided,
        spec.package,
        *(one.package for one in spec.source_packages),
    }
    sources = (spec.package, *(one.package for one in spec.source_packages))
    if set(sources) & provided:
        raise Denied("source package shadows a signed runtime dependency")
    unsatisfied: set[str] = set()
    for name, path in modules.items():
        if not within(name, sources):
            continue
        source = path.read_text()
        is_package = path.name == "__init__.py"
        for imported in (
            *definite_imports(source, name, is_package),
            *dynamic_targets(source, name, is_package),
        ):
            root = imported.split(".")[0]
            if root in {"skulk", "skulk_capability_nodes"}:
                raise Denied("Author code must use public capability SDK interfaces")
            if root not in available and not within(imported, sources):
                unsatisfied.add(root)
    if unsatisfied:
        raise Denied(
            f"{spec.package} imports {', '.join(sorted(unsatisfied))}, which the "
            "signed dependency inventory does not carry; declare its runtime "
            "requirement or project-local source package"
        )


def check_origins(spec: CapabilitySpec, source: Path, modules: dict[str, Path]) -> None:
    """Refuse to pack a capability resolved from outside its declared source.

    ``find_spec`` answers from the publisher's interpreter. If the capability
    beside this spec is not installed there, packaging would fail confusingly;
    worse, if a different copy of the same package is installed, the publisher
    would archive and sign that one instead. Comparing resolved origins against
    the declared root turns both into one clear refusal. SDK modules are exempt:
    they are the publisher's own, by construction.
    """
    project = source.resolve(strict=True)
    roots = [(spec.package, (source / spec.package_root).resolve(strict=True))]
    for additional in spec.source_packages:
        root = (project / additional.package_root).resolve(strict=True)
        if not root.is_relative_to(project) or not (root / "__init__.py").is_file():
            raise Denied("additional source package must be a regular project package")
        roots.append((additional.package, root))
    for package, root in roots:
        outside = sorted(
            str(path)
            for name, path in modules.items()
            if within(name, (package,)) and not path.resolve().is_relative_to(root)
        )
        if outside:
            raise Denied(
                f"{package} resolved outside {root}: " + ", ".join(outside[:3])
            )


def archive(spec: CapabilitySpec, source: Path, template: bytes | None = None) -> bytes:
    """Create stable portable source bytes with no local paths, caches or secrets.

    The artifact holds the capability plus exactly the SDK modules its
    entrypoints reach. That is what keeps one capability's bytes out of
    another's bundle, and it is why the caller verifies the result by importing
    the packed entrypoints from the archive alone.
    """
    packages = (
        spec.package,
        *(one.package for one in spec.source_packages),
    )
    shims = entrypoints(spec)
    roots = (
        *(target.split(":")[0] for target in shims.values()),
        *spec.extra_modules,
    )
    files: dict[str, bytes] = {}
    modules = closure(roots, packages, spec.extra_modules)
    check_origins(spec, source, modules)
    check_imports(spec, modules)
    for name, path in modules.items():
        parts = name.split("/") if "/" in name else name.split(".")
        relative = "/".join(parts)
        leaf = "/__init__.py" if path.name == "__init__.py" else ".py"
        files[f"{relative}{leaf}"] = path.read_bytes()
    for name, target in shims.items():
        module, _, function = target.partition(":")
        shim = f"from {module} import {function}\n{function}()\n"
        files[f"{name}.py"] = shim.encode()
    for packed, relative in spec.data_files.items():
        if packed in files:
            raise Denied(
                f"packed data file {packed} would replace a generated member; "
                "the artifact would run code the spec does not declare"
            )
        files[packed] = read_packed_file(source, relative)
    if template is not None:
        if spec.template_path is None:
            raise ValueError("this capability declares no packed template path")
        if spec.template_path in files:
            raise Denied(
                f"template path {spec.template_path} would replace a generated "
                "member; the artifact would run code the spec does not declare"
            )
        files[spec.template_path] = template
    buffer = io.BytesIO()
    buffer.write(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            target.writestr(info, raw)
    return buffer.getvalue()


def validate_template(spec: CapabilitySpec, raw: bytes) -> bytes:
    """Validate a caller-supplied template against the model its capability names."""
    if spec.template_model is None:
        raise ValueError("this capability declares no template")
    module, _, attribute = spec.template_model.partition(":")
    model = getattr(importlib.import_module(module), attribute)
    parsed: BaseModel = model.model_validate_json(raw)
    return parsed.model_dump_json().encode()


def read_packed_file(source: Path, relative: str) -> bytes:
    """Read one declared data file, refusing anything outside the spec directory.

    A spec is written by whoever authors the capability, and these bytes end up
    inside an artifact this host then signs. Without this check a spec could
    name ``../../publisher.key`` and have the publisher sign its own secret into
    a distributed bundle. Symlinks are resolved before the comparison so a link
    inside the directory cannot point out of it.
    """
    root = source.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise Denied(f"packed data file {relative} must be a file under the spec")
    return path.read_bytes()


def validate_manifest(spec: CapabilitySpec, manifest: Manifest) -> None:
    """Apply the capability's own manifest rules before any bytes are signed.

    A valid signature cannot repair stale descriptor semantics, and the rules
    belong to the capability that has to honor them rather than to a branch in
    the publisher.
    """
    if manifest.runtime_dependencies != dependency_roots(spec.dependencies):
        raise Denied("manifest runtime roots differ from the capability spec")
    if spec.validator is None:
        return
    module, _, function = spec.validator.partition(":")
    checker = getattr(importlib.import_module(module), function)
    checker(manifest)


def verify_self_contained(spec: CapabilitySpec, raw: bytes, staged: Path) -> None:
    """Import the packed entrypoints with only the artifact on the path.

    This is what makes a computed import closure safe to ship: a module the walk
    missed fails here, at build time, rather than on an operator's host.
    """
    staged = staged.resolve()
    staged.write_bytes(raw)
    try:
        for target in entrypoints(spec).values():
            module, _, function = target.partition(":")
            probe = (
                f"from {module} import {function}\n"
                f"assert callable({function}), '{target} is not callable'\n"
            )
            completed = subprocess.run(
                # -P keeps the working directory off sys.path. Without it the
                # publisher's own checkout satisfies an import the artifact is
                # missing, which is exactly the case this check exists to catch,
                # and the neutral working directory means nothing beside the
                # archive can answer either.
                [sys.executable, "-P", "-c", probe],
                env={"PYTHONPATH": str(staged), "PATH": "/usr/bin:/bin"},
                cwd=staged.parent,
                capture_output=True,
                timeout=120,
            )
            if completed.returncode != 0:
                raise Denied(
                    f"packaged artifact cannot resolve {target}: "
                    + completed.stderr.decode()[-2000:]
                )
    finally:
        staged.unlink(missing_ok=True)
