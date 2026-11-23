/** Issued, immutable compiler-free addon identities. */
export const cohort = {
  "linux-arm64": {
    "dependency": "@foxlight/skulk-kernel-linux-arm64",
    "files": {
      "dist/kernel.node": "d35018c6106978892e3b22cbb5e85947b64fa7ae999f706a9970c2069ebf574d",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "linux-x64": {
    "dependency": "@foxlight/skulk-kernel-linux-x64",
    "files": {
      "dist/kernel.node": "4ed4ce35d5ebebb10dc733dd83873640492430843144d300fa2b6af2900b8e4e",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  },
  "macos-arm64": {
    "dependency": "@foxlight/skulk-kernel-macos-arm64",
    "files": {
      "dist/kernel.node": "840a0a9dd2df579a5ac259905ac603ab045705f8d605df5887cc26ed253b013c",
      "dist/codec.js": "9b1930fadfcf7d5ff4deecff26e10427e3d777aad862ea0610aec1b48c179523",
      "dist/loader.cjs": "21ff4154482172c498b8fec3eaff43218d996b1a34cff1f6e79c39eb0171a5dc"
    }
  }
} as const;
