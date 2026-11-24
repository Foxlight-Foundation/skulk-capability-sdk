/** Issued, immutable compiler-free addon identities. */
export const cohort = {
  "linux-arm64": {
    "dependency": "@foxlight/skulk-kernel-linux-arm64",
    "files": {
      "dist/kernel.node": "c7da198db372684b6d2f5d7bf78f6c4981033cc4c76e7ac162bc87c55f008aff",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "linux-x64": {
    "dependency": "@foxlight/skulk-kernel-linux-x64",
    "files": {
      "dist/kernel.node": "4d72d4942ab5aaebe77dfa79a7f626d2cf0ee41b9d75d3651c903afd56af3311",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "macos-arm64": {
    "dependency": "@foxlight/skulk-kernel-macos-arm64",
    "files": {
      "dist/kernel.node": "9bd8b65725b65323f1099803fcb4be7f1c6ab3f6a421c0f2e2741da7e23c9840",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  }
} as const;
