/** Issued, immutable compiler-free addon identities. */
export const cohort = {
  "linux-arm64": {
    "dependency": "@foxlight/skulk-kernel-linux-arm64",
    "files": {
      "dist/kernel.node": "20820711e5467e9da0f4303bb1979e56f7be0554ad10789c386be573b01258d0",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "linux-x64": {
    "dependency": "@foxlight/skulk-kernel-linux-x64",
    "files": {
      "dist/kernel.node": "b9662d6867c130a4fe6147f28bd32b43595a4260a574eef27a0e3c84d696dfb9",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "macos-arm64": {
    "dependency": "@foxlight/skulk-kernel-macos-arm64",
    "files": {
      "dist/kernel.node": "100903e5eb852a72e71deff082d9516066e3b4f6ad232925b424130b8c281569",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  }
} as const;
