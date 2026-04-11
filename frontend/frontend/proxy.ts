import { NextRequest, NextResponse } from 'next/server';

const BLOCKED_POST_PATHS = new Set(['/api/server-actions', '/submit']);
const NO_STORE_VALUE = 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0';
const FRONTEND_BUILD_ID = process.env.NEXT_PUBLIC_FRONTEND_BUILD_ID || process.env.CODEAI_FRONTEND_BUILD_ID || 'unknown-build';

function isAdminHost(request: NextRequest): boolean {
  const host = request.headers.get('host') ?? '';
  return host.startsWith('admin.') || host.endsWith(':3005');
}

function applyNoStoreHeaders(response: NextResponse): NextResponse {
  response.headers.set('Cache-Control', NO_STORE_VALUE);
  response.headers.set('Pragma', 'no-cache');
  response.headers.set('Expires', '0');
  response.headers.set('Surrogate-Control', 'no-store');
  response.headers.set('x-frontend-shell-cache-policy', 'no-store');
   response.headers.set('x-frontend-build-id', FRONTEND_BUILD_ID);
   response.headers.set('x-frontend-build-marker', 'codeai-frontend');
  return response;
}

function shouldApplyNoStore(pathname: string): boolean {
  return pathname.startsWith('/admin')
    || pathname.startsWith('/marketplace')
    || pathname === '/privacy'
    || pathname === '/terms';
}

export function proxy(request: NextRequest) {
  const hasNextActionHeader = request.headers.has('next-action');
  const pathname = request.nextUrl.pathname;

  // 현재 앱은 서버 액션을 사용하지 않으므로, 오래된 배포/외부 스캔 요청은 진입 전에 차단한다.
  if (request.method === 'POST' && (hasNextActionHeader || BLOCKED_POST_PATHS.has(pathname))) {
    return NextResponse.json(
      {
        detail: '지원하지 않는 서버 액션 또는 비정상 폼 제출 요청입니다. 페이지를 새로고침한 뒤 다시 시도해주세요.',
        code: 'STALE_OR_INVALID_SERVER_ACTION',
      },
      {
        status: 410,
        headers: {
          'Cache-Control': 'no-store',
          'x-codeai-blocked-reason': hasNextActionHeader ? 'invalid-next-action' : 'blocked-post-path',
        },
      },
    );
  }

  if (!isAdminHost(request)) {
    const response = NextResponse.next();
    return shouldApplyNoStore(pathname) ? applyNoStoreHeaders(response) : response;
  }

  if (
    pathname.startsWith('/admin/_next') ||
    pathname.startsWith('/_next') ||
    pathname.startsWith('/admin') ||
    pathname.startsWith('/marketplace') ||
    pathname.startsWith('/marketplace/orchestrator') ||
    pathname.startsWith('/api')
  ) {
    const response = NextResponse.next();
    return shouldApplyNoStore(pathname) ? applyNoStoreHeaders(response) : response;
  }

  const url = request.nextUrl.clone();
  url.pathname = '/admin';
  url.search = '';
  return applyNoStoreHeaders(NextResponse.redirect(url));
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)'],
};