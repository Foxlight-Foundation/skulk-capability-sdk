# Acquire the SDK1 native author runtime

Owner **0.18.1**, SDK **1.0.1** and kernel **0.7.0** have matching, hash-pinned
runtime recipes. Use author tools **0.2.0** from the
[public package quickstart](native-public-quickstart.md). The
[SDK1 cohort](sdk1-cohort.md) separates these published inputs from final
application and Skulk-release qualification. The
[earlier preview recipes](native-preview-runtime-acquisition.md) remain available
for their original package versions.

The recipes download complete runtime wheelhouses without a private checkout,
Foxlight account, provider account, model, Rust compiler or fleet lease. The
downloaded owner has its own license, separate from the MIT SDK and author tools.
Downloading these files does not install the owner into your author environment
or grant publisher, host or caller permissions.

## Choose the binary family

Use Python 3.13 with one of these supported runtime families:

| Family / recipe file | Host | Recipe SHA256 |
| --- | --- | --- |
| `macos-arm64.json` | Apple Silicon, macOS 11+ | `bbd95adb1f93a23afed0aa709960f8debd6745ea98648c5816833d6558afcfda` |
| `linux-glibc-x86_64.json` | Linux x86_64, glibc 2.34+ | `3b210a0cffed4f2784171214f718b6dfbd0fd371efe4e0ec9309aaa091f08314` |
| `linux-glibc-aarch64.json` | Linux ARM64, glibc 2.34+ | `649746117747109200418400646b9f3ae51d2ee88f41c4c3ae86133891ec468c` |

The reviewed source copies and checksum inventory are under
[`runtime-recipes/native-owner-0.18.1`](../runtime-recipes/native-owner-0.18.1/).
The [SDK1 runtime recipe release](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-runtime-v0.18.1)
supplies those same three JSON files and `SHA256SUMS`. The recipe format is 2;
each file binds the exact platform, owner source, runtime identity, native ABI
cohort, wheel URLs, byte lengths and hashes.

## Download and verify a recipe

In the directory prepared by the public quickstart, choose the filename and
digest from the table. This example selects macOS ARM64:

```sh
RECIPE=macos-arm64.json
RECIPE_SHA256=bbd95adb1f93a23afed0aa709960f8debd6745ea98648c5816833d6558afcfda
BASE=https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/native-runtime-v0.18.1
curl --fail --location --proto '=https' --max-time 60 \
  "$BASE/$RECIPE" --output "$RECIPE"
.author-env/bin/python -I - "$RECIPE" "$RECIPE_SHA256" <<'PYTHON'
import hashlib
from pathlib import Path
import sys

if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit("Recipe differs from the reviewed digest")
print("Recipe verified")
PYTHON
```

## Acquire the wheelhouse

Set `PLATFORM` to the chosen family without `.json`. `AUTHOR` remains the absolute
path to your public author environment's `skulk-cap` command.

```sh
PLATFORM=macos-arm64
"$AUTHOR" runtime --native --recipe "$RECIPE" --platform "$PLATFORM" \
  --output ./runtime-wheels
```

The CLI checks every anonymous HTTPS download against its reviewed size and
SHA256 before writing the wheelhouse. It refuses a nonempty output directory,
an incompatible native cohort, a platform mismatch or changed bytes. It does not
run a source build or choose a replacement version. The JSON result identifies
the platform, owner version and wheel count.

The Python reference runtime has 29 wheels on macOS and 31 on each Linux family.
TypeScript applications also need the separately published Node 22.23.5 wheel
and their own compiled application asset wheel; see
[Node packaging](node-packaging.md#select-node-and-sign). Additional application
dependencies must be selected and qualified explicitly.

Continue with [native publishing](native-publishing.md) to build and sign your
application. Recipes are acquisition inputs, not a complete application feed,
publisher trust document or installation approval.

## Host and qualification scope

These inputs come from the signed model-free `foxlight.sdk-reference` release,
sequence 45512577737, served by the Foxlight Store. The qualified host input is
[Core 0996766a8](https://github.com/Foxlight-Foundation/Skulk/tree/0996766a8da6cb5262207c911f25f5b6c95030ca),
on the SDK1 initiative. A released Skulk version must independently support the
selected native cohort before it can install the runtime. Authoring without a
compiler does not remove Core's own build and service prerequisites.

All three signed reference packages passed normal Core/owner installation,
revision-fenced enable, enrolled unary HTTP and three streaming modes with
binary media, manager/owner restart with existing access, and uninstall.
Independent production Store readback and anonymous acquisition of all 91
wheel references bind these recipes to their actual served bytes. These receipts do not qualify application migrations, provider-account behavior
or the final coordinated Skulk release. Public acquisition, independent author
journeys and installed applications retain their own acceptance receipts.
