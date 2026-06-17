/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: true,
  },
  // 后端 API 走 docker compose 内部网络：浏览器侧走 NEXT_PUBLIC_API_BASE_URL
  async rewrites() {
    const backend = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
    if (process.env.NODE_ENV !== "production") {
      return [
        {
          source: "/api/:path*",
          destination: `${backend}/api/:path*`,
        },
      ];
    }
    return [];
  },
};

export default nextConfig;
