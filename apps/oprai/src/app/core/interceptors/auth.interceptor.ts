import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from '../services/auth.service';
import { environment } from '../../../environments/environment';

/**
 * Injects Authorization Bearer token for gateway requests.
 * Admin API requests rely on the HttpOnly `oprai-admin-token` cookie set by the
 * admin service on login — withCredentials ensures the browser sends it
 * automatically. The token is NOT read from or stored in localStorage (XSS risk).
 *
 * If no user token is available, the request is passed through so the backend
 * can return 401 and the errorInterceptor can trigger re-authentication.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const authService = inject(AuthService);

  const isGatewayRequest = req.url.startsWith(environment.apiBase);
  const isAdminRequest = req.url.startsWith(environment.adminApiBase);

  if (isAdminRequest) {
    return next(req.clone({
      withCredentials: true,
      setHeaders: { 'X-Requested-With': 'XMLHttpRequest' },
    }));
  }

  if (isGatewayRequest) {
    // Pre-auth endpoints must never carry a Bearer token. If the user has a
    // revoked/expired JWT in memory, attaching it to /auth/nonce or /auth/verify
    // makes the gateway reject the very request that would re-establish auth,
    // creating a 401-loop self-DOS. The cookie is also harmless here because
    // the auth service ignores it for these endpoints.
    const path = req.url.slice(environment.apiBase.length).split('?')[0];
    const isPreAuth = path === '/auth/nonce' || path === '/auth/verify';

    const token = authService.getToken();
    const headers: Record<string, string> = { 'X-Requested-With': 'XMLHttpRequest' };
    if (token && !isPreAuth) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return next(req.clone({
      withCredentials: !isPreAuth,
      setHeaders: headers,
    }));
  }

  return next(req);
};
