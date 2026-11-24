# Acquire the native author runtime

For new SDK1 applications, consult the [matching SDK1 cohort](sdk1-cohort.md).
The acquisition receipt below is the separately issued SDK16/owner8 preview;
its immutable package identities are preserved.

Native owner **0.18.0rc8**, SDK **0.5.16rc1** and kernel **0.7.0rc7** now have
explicit, hash-pinned runtime recipes. Use author tools **0.2.0rc4** from the
[public package quickstart](native-public-quickstart.md). These are SDK1
candidates; they are not stable SDK1 or a stable Skulk host release.

The recipes download complete runtime wheelhouses without a private checkout,
Foxlight account, provider account, model, Rust compiler or fleet lease. The
downloaded owner has its own license, separate from the MIT SDK and author tools.
Downloading these files does not install the owner into your author environment
or grant publisher, host or caller permissions.

## Choose the binary family

Use Python 3.13 with one of these supported runtime families:

| Family / recipe file | Host | Recipe SHA256 |
| --- | --- | --- |
| `macos-arm64.json` | Apple Silicon, macOS 11+ | `6158e11954f681c5a4baaba09919df8740c58b380e15261952fc957964ce967f` |
| `linux-glibc-x86_64.json` | Linux x86_64, glibc 2.34+ | `b7bee0be25fd471f5e06174fdf0502793784707b06fb818d28f8118053b7e832` |
| `linux-glibc-aarch64.json` | Linux ARM64, glibc 2.34+ | `bfdc12d59409447e82fe8cf0021d231247532427592b9132ee2ba4f81ee09909` |

The reviewed source copies and checksum inventory are under
[`runtime-recipes/native-owner-0.18.0rc8`](../runtime-recipes/native-owner-0.18.0rc8/).
The [native runtime preview release](https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/tag/native-runtime-v0.18.0-rc.8)
supplies those same three JSON files and `SHA256SUMS`. The recipe format is 2;
each file binds the exact platform, owner source, runtime identity, native ABI
cohort, wheel URLs, byte lengths and hashes.

## Download and verify a recipe

In the directory prepared by the public quickstart, choose the filename and
digest from the table. This example selects macOS ARM64:

```sh
RECIPE=macos-arm64.json
RECIPE_SHA256=6158e11954f681c5a4baaba09919df8740c58b380e15261952fc957964ce967f
BASE=https://github.com/Foxlight-Foundation/skulk-capability-sdk/releases/download/native-runtime-v0.18.0-rc.8
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
sequence 45233353911, served by the Foxlight Store. The preview host input is
[Core 40379228c](https://github.com/Foxlight-Foundation/Skulk/tree/40379228cabe7726641cc7d5d1ee6ab581c4429f),
on the SDK1 initiative. A released Skulk version must independently support the
selected native cohort before it can install the runtime. Authoring without a
compiler does not remove Core's own build and service prerequisites.

All three signed reference packages passed normal Core/owner installation,
revision-fenced enable, enrolled unary HTTP and three streaming modes with
binary media, manager/owner restart with existing access, and uninstall.
Independent production Store readback and anonymous acquisition of all 91
wheel references bind these recipes to their actual served bytes. This does not
establish a complete independent Linux author journey, application migration,
provider-account behavior or the final coordinated SDK1 release. Complete
Python/TypeScript author/feed/Store journeys retain their own acceptance gates.
