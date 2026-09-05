import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.CASEFILE_WEB_DIST_DIR ?? ".next",
  allowedDevOrigins: ["127.0.0.1"],
  reactStrictMode: true,
  async redirects() {
    return [
      {
        source: "/demo/intake",
        destination: "/",
        permanent: false,
      },
      {
        source: "/demo",
        destination: "/workbench",
        permanent: false,
      },
      {
        source: "/demo/:path*",
        destination: "/workbench",
        permanent: false,
      },
      {
        source: "/brief",
        destination: "/",
        permanent: false,
      },
      {
        source: "/reasoning",
        destination: "/workbench",
        permanent: false,
      },
      {
        source: "/quality",
        destination: "/workbench",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
