/**
 * Generic in-memory TTL cache with LRU-style max-size cap.
 * Lazy expiration on get() — no background timers.
 * When the cache is full, the oldest inserted entry is evicted (FIFO).
 */
export class CacheStore<T> {
  private items = new Map<string, { data: T; expiresAt: number }>();

  constructor(
    private defaultTtlMs: number = 60_000,
    private maxSize: number = 500,
  ) {}

  get(key: string): T | null {
    const entry = this.items.get(key);
    if (!entry) return null;
    if (Date.now() > entry.expiresAt) {
      this.items.delete(key);
      return null;
    }
    return entry.data;
  }

  set(key: string, data: T, ttlMs?: number): void {
    // Evict the oldest entry when at capacity (Map preserves insertion order)
    if (!this.items.has(key) && this.items.size >= this.maxSize) {
      const oldestKey = this.items.keys().next().value;
      if (oldestKey !== undefined) {
        this.items.delete(oldestKey);
      }
    }
    this.items.set(key, {
      data,
      expiresAt: Date.now() + (ttlMs ?? this.defaultTtlMs),
    });
  }

  has(key: string): boolean {
    return this.get(key) !== null;
  }

  delete(key: string): void {
    this.items.delete(key);
  }

  clear(): void {
    this.items.clear();
  }

  get size(): number {
    return this.items.size;
  }
}
