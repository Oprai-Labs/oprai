import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { Observable, catchError, map, of } from 'rxjs';
import { AdminApiService } from '../../features/admin/services/admin-api.service';

/**
 * Guard that validates the admin session against the server.
 *
 * The admin session is maintained via an HttpOnly `oprai-admin-token` cookie
 * (set by the backend on login). The cookie is never accessible from JS, so
 * this guard calls GET /admin/auth/verify to ask the backend whether the
 * current cookie is valid. A 200 means the session is live; any other status
 * redirects to the login page.
 *
 * withCredentials is injected automatically by the auth interceptor for all
 * adminApiBase requests, so the cookie is attached on this verify call.
 */
export const adminGuard: CanActivateFn = (): Observable<boolean | UrlTree> => {
  const adminApi = inject(AdminApiService);
  const router = inject(Router);

  return adminApi.verify().pipe(
    map(() => true),
    catchError(() => of(router.createUrlTree(['/admin/login'])))
  );
};
