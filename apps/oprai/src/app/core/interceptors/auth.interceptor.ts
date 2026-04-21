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
    const token = authService.getToken();
    const headers: Record<string, string> = { 'X-Requested-With': 'XMLHttpRequest' };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return next(req.clone({ withCredentials: true, setHeaders: headers }));
  }

  return next(req);
};
