import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // This app lives inside the voxera-ai repo; pin the workspace root so Next
  // doesn't pick up an unrelated lockfile from a parent directory.
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
