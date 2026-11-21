/** Issued, immutable compiler-free addon identities. */
export const cohort = {
  "linux-arm64": {
    "dependency": "@foxlight/skulk-kernel-linux-arm64",
    "files": {
      "dist/kernel.node": "7d1f6ab883578d622a12e0324488f38499f441e215f4e45d8da1ab03d3e80f5b",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "linux-x64": {
    "dependency": "@foxlight/skulk-kernel-linux-x64",
    "files": {
      "dist/kernel.node": "6ee00f6d1ef0e87615b69d46fd09e15b46701d5b7e1399b505d721b8d42b61aa",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "macos-arm64": {
    "dependency": "@foxlight/skulk-kernel-macos-arm64",
    "files": {
      "dist/kernel.node": "49ce36b03a3514eec58a802a5f917a09168f07b3e01493d64b2b3c23497a2870",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  }
} as const;
