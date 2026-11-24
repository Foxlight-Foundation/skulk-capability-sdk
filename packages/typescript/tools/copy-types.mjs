import {copyFile} from 'node:fs/promises';
await copyFile(new URL('../src/native-api.d.cts', import.meta.url), new URL('../dist/native-api.d.cts', import.meta.url));
