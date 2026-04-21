/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    optimizePackageImports: ["recharts", "lucide-react"],
  },
};

module.exports = nextConfig;
