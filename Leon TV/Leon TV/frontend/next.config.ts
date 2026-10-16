import type { NextConfig } from 'next';

const backendApiUrl = (process.env.BACKEND_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

const nextConfig: NextConfig = {
  agentRules: false,
  async rewrites() {
    return [
      { source: '/code/:path*', destination: `${backendApiUrl}/code/:path*` },
      { source: '/api/:path*', destination: `${backendApiUrl}/api/:path*` },
    ];
  },
};

export default nextConfig;
