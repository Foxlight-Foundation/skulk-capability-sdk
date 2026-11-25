# Start from public SDK1 packages

Use Python/TypeScript **1.0.1**, author tools **0.2.0**, native kernel **0.7.0**
and selected Node **22.23.5**. These packages are published as immutable GitHub
release downloads; the instructions do not assume a PyPI or npm registry release.
The [SDK1 cohort](sdk1-cohort.md) records the matching owner/runtime and host
requirements. The [earlier preview quickstart](native-preview-quickstart.md)
retains its original package identities.

You can create, run and debug a Python or TypeScript capability without a Skulk
checkout, private repository access, Foxlight account, model or fleet. Package
publication does not make a compatible Skulk host available on your machine.

Use macOS ARM64 or glibc2.34+ Linux x86_64/ARM64, `uv`, and Python3.13.
TypeScript development also needs npm; the starter obtains its TypeScript
compiler through npm. No Rust or C compiler is needed for this local journey.

## Download and install

Start in a new directory:

```sh
mkdir sdk1-author
cd sdk1-author
umask 077
uv venv --managed-python --python 3.13 .author-env
```

This script selects exact public wheels for the current host, writes a
requirements file with SHA256-bound URLs, and downloads the audited TypeScript
archive. It refuses an unsupported platform or an existing output file.

```sh
.author-env/bin/python -I - <<'PYTHON'
import hashlib
import platform
from pathlib import Path
from urllib.request import urlopen

base = "https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/"
choices = {
    ("Darwin", "arm64"): (
        "macosx_11_0_arm64",
        "ac66250223f65870bf59543a430930fa2f366cef7669ca3b0603b4d8e172c778",
        "2137ce717c312a109eec259fe469dbd4d0d34d3b5d44205b94078bfd716a668a",
    ),
    ("Linux", "x86_64"): (
        "manylinux_2_34_x86_64",
        "27cd7bada32a6511448648fcd58543abf393bd33975c4a35f117a6e547268d65",
        "1870c7d939a74934975bfbfc947820c25484a56b4d74ef25ba962549f84d9705",
    ),
    ("Linux", "aarch64"): (
        "manylinux_2_34_aarch64",
        "1c86be3a6e318dcf4ad0ef83ff98446a72bacdbaface2b7ad40e24be30accdc0",
        "db24b11bd9faae8e25d52940c0e9d36c9dfb63cfab5c29379c415ba68d832e1f",
    ),
}
selected = choices.get((platform.system(), platform.machine()))
if selected is None:
    raise SystemExit("This release has no binary for this platform")
wheel_platform, kernel_hash, node_hash = selected
packages = [
    ("v1.0.1", "skulk_capability_sdk-1.0.1-py3-none-any.whl",
     "df30b89d8fa8588d94b12af03894dcfe3845acd2397b371194481801d9e46c0a"),
    ("v1.0.1", "skulk_capability_tools-0.2.0-py3-none-any.whl",
     "76cc28efec91727085aaafcd85ba38d5504f51b4283f2b9b502b2da11b36db9f"),
    ("native-v0.7.0", f"skulk_capability_kernel-0.7.0-cp313-abi3-{wheel_platform}.whl", kernel_hash),
    ("v0.5.13-rc.1", f"skulk_node_runtime-22.23.5-py3-none-{wheel_platform}.whl", node_hash),
]
requirements = "".join(base + tag + "/" + name + "#sha256=" + digest + "\n"
                       for tag, name, digest in packages)
archive = Path("foxlight-skulk-capability-sdk-1.0.1.tgz")
with urlopen(base + "v1.0.1/" + archive.name, timeout=60) as response:
    content = response.read(51403)
if len(content) != 51402 or hashlib.sha256(content).hexdigest() != "dde740907bed604b092140db889d0c0d4e90f04202c30b6644f20484712eb7e4":
    raise SystemExit("TypeScript archive differs from the selected release")
with Path("author-requirements.txt").open("x") as output:
    output.write(requirements)
with archive.open("xb") as output:
    output.write(content)
wheel = Path("skulk_capability_sdk-1.0.1-py3-none-any.whl")
with urlopen(base + "v1.0.1/" + wheel.name, timeout=60) as response:
    content = response.read(88464)
if len(content) != 88463 or hashlib.sha256(content).hexdigest() != "df30b89d8fa8588d94b12af03894dcfe3845acd2397b371194481801d9e46c0a":
    raise SystemExit("Python SDK wheel differs from the selected release")
with wheel.open("xb") as output:
    output.write(content)
PYTHON

uv pip install --no-config --python .author-env/bin/python \
  --only-binary :all: -r author-requirements.txt
AUTHOR="$PWD/.author-env/bin/skulk-cap"
SDK_TARBALL="$PWD/foxlight-skulk-capability-sdk-1.0.1.tgz"
SDK_WHEEL="$PWD/skulk_capability_sdk-1.0.1-py3-none-any.whl"
NODE_BIN="$(.author-env/bin/python -I -c 'import sys; from pathlib import Path; from skulk_capability_sdk.node_runtime import select_node; print(select_node(Path(sys.prefix)).executable.parent)')"
export PATH="$NODE_BIN:$PATH"
```

The hash fragments bind the selected four wheels; their ordinary dependencies
resolve from the public package index. This quickstart is not a frozen production
runtime lock. The older release tag for the Node wheel selects the same issued
Node22.23.5 bytes; it does not downgrade the SDK or native kernel.

## Create and try a capability

Choose a language. Python uses the installed SDK:

```sh
"$AUTHOR" init --native --language python --name hello_capability \
  --sdk-package "$SDK_WHEEL" ./hello
```

TypeScript, as the alternative to the Python command:

```sh
"$AUTHOR" init --native --language typescript --name hello_capability \
  --sdk-package "$SDK_TARBALL" ./hello
cd hello
npm install --ignore-scripts
npm run build
cd ..
```

Start a local session:

```sh
"$AUTHOR" run --native --project ./hello
```

In a second terminal in `sdk1-author`, set `AUTHOR` to this directory's absolute
`.author-env/bin/skulk-cap` path and run:

```sh
"$AUTHOR" debug --native --project ./hello
"$AUTHOR" invoke --native --project ./hello \
  --capability hello.capability.echo@1.0.0 --input '{"text":"Hello"}'
"$AUTHOR" invoke --native --project ./hello \
  --capability hello.capability.collect@1.0.0 \
  --input-chunk '{"text":"one"}' --input-chunk '{"text":"two"}'
```

The unary result contains `Hello`; the collect stream completes with `onetwo`.
Stop the session with Ctrl-C. Use `watch --native --project ./hello` instead of
`run` while editing: it replaces only its own child and preserves session state.
See the [native author guide](native-authoring.md) for all four modes, media,
configuration, credentials, cancellation and stream framing.

## From local development to distribution

Local sessions supply synthetic development authority. They do not enroll a
publisher, create a client grant, install an OS service or permit provider access.
For signed builds, acquire the exact owner/runtime wheelhouse using the
[public native runtime recipes](native-runtime-acquisition.md), then follow the
[publishing guide](native-publishing.md) with a compatible Core input. The runtime
download is separate from this local author environment and requires no private
repository access. It does not by itself establish a complete independent
install/feed/Store journey.

The public-only local journey has been exercised on macOS ARM64 from anonymously
downloaded SDK/tools/native/Node packages, with no source checkout or private
runtime installed. Both generated languages passed all four modes, exact binary
media, live debug, watch refresh, retained state and owned child cleanup. Existing
three-platform package/CLI tests remain separate evidence; this walkthrough does
not qualify a Linux independent-author walkthrough or the final coordinated Skulk release.
