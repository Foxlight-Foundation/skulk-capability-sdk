"""Initialize explicit, model-free native Python or TypeScript author projects."""

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from skulk_capability_sdk.contracts import Denied

from .inspection import read_public
from .native_author import Language, declaration

_PYTHON = '''"""Model-free native capability; handlers never select caller authority."""
import argparse
import asyncio
from collections.abc import AsyncIterator
from pydantic import JsonValue
from skulk_capability_sdk.application import (
    CapabilityApp, InputFrame, Invocation, OutputFrame,
)

app = CapabilityApp()

async def echo(call: Invocation) -> dict[str, JsonValue]:
    """Return text from this startup-bound local request."""
    return {"text": call.payload["text"]}

async def stream(
    call: Invocation, inputs: AsyncIterator[InputFrame]
) -> AsyncIterator[OutputFrame]:
    """Produce bounded text/media and one terminal through the native channel."""
    if call.capability_id.endswith('.stream'):
        yield OutputFrame('chunk', {"text": call.payload['text']})
        yield OutputFrame('completed', {"text": call.payload['text']})
        return
    texts: list[str] = []
    async for frame in inputs:
        if frame.projection['kind'] != 'chunk':
            continue
        value = frame.projection['payload']
        if not isinstance(value, dict) or not isinstance(value.get('text'), str):
            raise ValueError('Expected text')
        text = value['text']
        if not isinstance(text, str):
            raise ValueError('Expected text')
        texts.append(text)
        if call.capability_id.endswith('.duplex'):
            metadata = frame.projection.get('media')
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError('Expected media metadata')
            yield OutputFrame('chunk', value, media=frame.media,
                media_metadata=metadata)
    yield OutputFrame('completed', {"text": ''.join(texts)})

app.unary('BUNDLE.echo', echo, version='1.0.0')
for suffix in ('stream', 'collect', 'duplex'):
    app.stream('BUNDLE.' + suffix, stream, version='1.0.0')

def main() -> None:
    """Consume only the inherited managed startup descriptor."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-fd', type=int, required=True)
    asyncio.run(app.serve(parser.parse_args().config_fd))

if __name__ == '__main__':
    main()
'''

_TYPESCRIPT = """import {
  CapabilityApp, OutputFrame, type ApplicationFactory, type JsonObject, type JsonValue,
} from '@foxlight/skulk-capability-sdk';

/** Narrow portable objects, including the readonly array/object distinction. */
function object(value: JsonValue | undefined): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Model-free native example; runtime metadata supplies no Fabric authority. */
const createApplication: ApplicationFactory = runtime => {
  const app = new CapabilityApp({credentialHelper: runtime.credentialHelper});
  app.unary('BUNDLE.echo', async call => ({text: call.payload.text ?? ''}),
    {version:'1.0.0'});
  for (const suffix of ['stream', 'collect', 'duplex']) {
    app.stream('BUNDLE.' + suffix, async function* (call, inputs) {
      if (suffix === 'stream') {
        yield new OutputFrame('chunk', {text:call.payload.text ?? ''});
        yield new OutputFrame('completed', {text:call.payload.text ?? ''});
        return;
      }
      const texts: string[] = [];
      for await (const frame of inputs) {
        if (frame.projection.kind !== 'chunk') continue;
        const value=frame.payload;
        if (!object(value) || typeof value.text !== 'string')
          throw Error('Expected text');
        texts.push(value.text);
        const metadata=frame.projection.media;
        if (metadata != null && !object(metadata))
          throw Error('Expected media metadata');
        if (suffix === 'duplex') yield new OutputFrame('chunk', value,
          {media:frame.media, ...(metadata == null ? {} : {mediaMetadata:metadata})});
      }
      yield new OutputFrame('completed', {text:texts.join('')});
    }, {version:'1.0.0'});
  }
  return app;
};
export default createApplication;
"""

_STAGE = """import {readFile, writeFile, rm} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

// Building is explicit. The author session never runs npm or compiler hooks.
const root = new URL('./', import.meta.url);
const project = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));
const target = new URL('stage/package.json', root);
const npm = process.env.npm_execpath;
if (npm === undefined) throw Error('Run through npm run build');
await writeFile(target, JSON.stringify({private:true, type:'module',
  dependencies:project.dependencies}, null, 2) + '\\n');
const result = spawnSync(process.execPath, [npm, 'install', '--ignore-scripts',
  '--omit=dev', '--no-package-lock', '--prefix', fileURLToPath(new URL('stage', root))],
  {stdio:'inherit'});
// Local acquisition paths and installer bookkeeping are not runtime assets.
// Sanitize even when dependency acquisition fails, before reporting the error.
await writeFile(target, JSON.stringify({private:true, type:'module'}) + '\\n');
await rm(new URL('stage/package-lock.json', root), {force:true});
if (result.error !== undefined) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);
"""


def initialize_native(
    destination: Path, name: str, language: Language, sdk_package: Path | None = None
) -> None:
    """Reserve a new four-mode project without running hooks or replacing files.

    An optional audited SDK wheel/tarball is referenced for candidate acquisition;
    initialization never downloads or installs it. Native declarations use the
    actual installed public compiled reader.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name):
        raise Denied("Project name must be a lowercase Python package name")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise Denied("Project destination already exists")
    sdk = importlib.metadata.version("skulk-capability-sdk")
    bundle = name.replace("_", ".")
    dependency = "skulk-node-app-" + hashlib.sha256(bundle.encode()).hexdigest()[:24]
    manifest = declaration(
        bundle, ("skulk-capability-sdk",) if language == "python" else (dependency,)
    )
    if sdk_package is not None:
        sdk_package = sdk_package.resolve(strict=True)
        read_public(sdk_package, 64 * 1024 * 1024)
        if sdk_package.suffix != (".whl" if language == "python" else ".tgz"):
            raise Denied("Select a Python SDK wheel or TypeScript SDK tarball")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".skulk-native-init-", dir=destination.parent))
    try:
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        license_text = importlib.metadata.distribution(
            "skulk-capability-tools"
        ).read_text("licenses/LICENSE.md")
        if license_text is None:
            raise Denied("Installed author tool license is unavailable")
        (stage / "LICENSE").write_text(license_text)
        (stage / ".gitignore").write_text(
            ".venv/\nnode_modules/\nstage/node_modules/\n__pycache__/\ndist/\n"
        )
        if language == "python":
            package = stage / name
            package.mkdir()
            (package / "__init__.py").write_text('"""Native capability example."""\n')
            (package / "app.py").write_text(_PYTHON.replace("BUNDLE", bundle))
            (stage / "capability.toml").write_text(
                f'package = "{name}"\npackage_root = "{name}"\nentry = "{name}.app"\n'
                f'dependencies = ["skulk-capability-sdk=={sdk}"]\n'
            )
            configuration = (
                f'[project]\nname = "{name.replace("_", "-")}"\nversion = "0.1.0"\n'
                'requires-python = ">=3.13"\n'
                f'dependencies = ["skulk-capability-sdk=={sdk}"]\n'
            )
            if sdk_package:
                configuration += (
                    "\n[tool.uv.sources]\nskulk-capability-sdk = { url = "
                    + json.dumps(sdk_package.as_uri())
                    + " }\n"
                )
            (stage / "pyproject.toml").write_text(configuration)
        else:
            (stage / "src").mkdir()
            (stage / "stage").mkdir()
            (stage / "src/app.ts").write_text(_TYPESCRIPT.replace("BUNDLE", bundle))
            # npm's file specifier preserves literal spaces; it does not decode
            # a percent-escaped file URL consistently across npm versions.
            spec = (
                "file:" + str(sdk_package) if sdk_package else sdk.replace("rc", "-rc.")
            )
            dependencies = {"@foxlight/skulk-capability-sdk": spec}
            (stage / "package.json").write_text(
                json.dumps(
                    {
                        "name": name.replace("_", "-"),
                        "version": "0.1.0",
                        "private": True,
                        "type": "module",
                        "dependencies": dependencies,
                        "devDependencies": {"typescript": "5.8.3"},
                        "scripts": {
                            "compile": "tsc",
                            "build": "npm run compile && node stage.mjs",
                        },
                    },
                    indent=2,
                )
                + "\n"
            )
            (stage / "stage/package.json").write_text(
                json.dumps(
                    {"private": True, "type": "module"},
                    indent=2,
                )
                + "\n"
            )
            (stage / "stage.mjs").write_text(_STAGE)
            (stage / "tsconfig.json").write_text(
                json.dumps(
                    {
                        "compilerOptions": {
                            "strict": True,
                            "target": "ES2022",
                            "lib": ["ES2024"],
                            "module": "NodeNext",
                            "moduleResolution": "NodeNext",
                            "outDir": "stage",
                            "rootDir": "src",
                            "skipLibCheck": False,
                        },
                        "include": ["src/**/*.ts"],
                    },
                    indent=2,
                )
                + "\n"
            )
            (stage / "node-capability.toml").write_text(
                'format = 1\nassets = "stage"\nentry = "app.js"\nlicense = "LICENSE"\n'
            )
        (stage / "README.md").write_text(
            f"# {name}\n\nA model-free native {language} capability "
            "with four I/O modes.\n\n"
            "Use an isolated author environment with the matching SDK/tools, "
            "published native kernel and (for TypeScript) the selected Node "
            "runtime wheel. Candidate packages are not stable SDK1.\n\n"
            + (
                "Run `npm install --ignore-scripts` and `npm run build` first. "
                "Watch compiled output with `npm run compile -- --watch`.\n\n"
                if language == "typescript"
                else ""
            )
            + "Run `skulk-cap run --native`. In another terminal:\n\n"
            f"```sh\nskulk-cap invoke --native --capability {bundle}.echo@1.0.0 "
            '--input \'{"text":"Hello"}\'\n'
            "skulk-cap debug --native\n```\n\n"
            "`skulk-cap watch --native` restarts only between calls. "
            "Local development has no Fabric identity or grants. "
            "Use normal signed installation for real host authority.\n"
        )
        destination.mkdir()
        for path in stage.iterdir():
            os.rename(path, destination / path.name)
    finally:
        shutil.rmtree(stage)
