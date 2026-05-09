import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiBase;

  get<T>(path: string, params?: Record<string, string>): Observable<T> {
    const httpParams = params
      ? new HttpParams({ fromObject: params })
      : undefined;
    return this.http.get<T>(`${this.baseUrl}${path}`, { params: httpParams, withCredentials: true });
  }

  post<T>(path: string, body: unknown = {}): Observable<T> {
    return this.http.post<T>(`${this.baseUrl}${path}`, body, { withCredentials: true });
  }

  put<T>(path: string, body: unknown = {}): Observable<T> {
    return this.http.put<T>(`${this.baseUrl}${path}`, body, { withCredentials: true });
  }

  patch<T>(path: string, body: unknown = {}): Observable<T> {
    return this.http.patch<T>(`${this.baseUrl}${path}`, body, { withCredentials: true });
  }

  delete<T>(path: string): Observable<T> {
    return this.http.delete<T>(`${this.baseUrl}${path}`, { withCredentials: true });
  }

  /**
   * Create an SSE (Server-Sent Events) connection for streaming responses.
   * Returns an Observable that emits each SSE message data string.
   *
   * @param idleTimeoutMs Abort and error if no data is received for this long (default 60s).
   *                      Covers both initial connection hang and mid-stream silence.
   */
  sse(path: string, body: unknown, idleTimeoutMs = 60_000): Observable<string> {
    return new Observable<string>((subscriber) => {
      const abortController = new AbortController();
      let idleTimer: ReturnType<typeof setTimeout> | null = null;

      const resetIdleTimer = () => {
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(() => {
          abortController.abort();
          subscriber.error(new Error('Connection timed out — no response received. Please try again.'));
        }, idleTimeoutMs);
      };

      const clearIdleTimer = () => {
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      };

      resetIdleTimer(); // start immediately — covers initial connect hang

      fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        // credentials: 'include' sends the oprai-auth-token HttpOnly cookie automatically.
        credentials: 'include',
        body: JSON.stringify(body),
        signal: abortController.signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            clearIdleTimer();
            const status = response.status;
            let message: string;
            if (status === 401 || status === 403) {
              message = 'Authentication error. Please reconnect your wallet.';
            } else if (status === 429) {
              message = 'Too many requests. Please wait a moment and try again.';
            } else if (status === 503 || status === 502) {
              message = 'Chat service is temporarily unavailable. Please try again shortly.';
            } else {
              message = `Request failed (${status}). Please try again.`;
            }
            subscriber.error(new Error(message));
            return;
          }

          const reader = response.body?.getReader();
          if (!reader) {
            clearIdleTimer();
            subscriber.error(new Error('No readable stream in response'));
            return;
          }

          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            resetIdleTimer(); // reset on every chunk — detects mid-stream hangs

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() ?? '';

            for (const line of lines) {
              const trimmed = line.trim();
              if (trimmed.startsWith('data:')) {
                const data = trimmed.slice(5).trim();
                if (data === '[DONE]') {
                  clearIdleTimer();
                  subscriber.complete();
                  return;
                }
                subscriber.next(data);
              }
            }
          }

          clearIdleTimer();
          subscriber.complete();
        })
        .catch((err) => {
          clearIdleTimer();
          if (err.name !== 'AbortError') {
            subscriber.error(err);
          }
        });

      return () => {
        clearIdleTimer();
        abortController.abort();
      };
    });
  }
}
