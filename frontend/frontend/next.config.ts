import type { NextConfig } from 'next';

const FRONTEND_BUILD_ID =
    process.env.CODEAI_FRONTEND_BUILD_ID
    || process.env.NEXT_BUILD_ID
    || `build-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}`;

const noStoreHeaders = [
    {
        key: 'Cache-Control',
        value: 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0',
    },
    {
        key: 'Pragma',
        value: 'no-cache',
    },
    {
        key: 'Expires',
        value: '0',
    },
    {
        key: 'Surrogate-Control',
        value: 'no-store',
    },
    {
        key: 'x-frontend-build-id',
        value: FRONTEND_BUILD_ID,
    },
    {
        key: 'x-frontend-build-marker',
        value: 'codeai-frontend',
    },
];

const nextConfig: NextConfig = {
    generateBuildId: async () => FRONTEND_BUILD_ID,
    env: {
        NEXT_PUBLIC_FRONTEND_BUILD_ID: FRONTEND_BUILD_ID,
    },
    async headers() {
        return [
            {
                source: '/admin/:path*',
                headers: noStoreHeaders,
            },
            {
                source: '/marketplace/:path*',
                headers: noStoreHeaders,
            },
            {
                source: '/privacy',
                headers: noStoreHeaders,
            },
            {
                source: '/terms',
                headers: noStoreHeaders,
            },
        ];
    },
};

export default nextConfig;
