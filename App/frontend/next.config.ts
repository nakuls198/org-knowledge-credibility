import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false, // hide the framework's own badge so the page reads as one product
  eslint: { ignoreDuringBuilds: true }, // type safety is enforced by `tsc --noEmit` (npm run typecheck)
};

export default nextConfig;
