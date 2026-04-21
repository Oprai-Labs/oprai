"use client";

import { useState, useCallback } from "react";

interface FilterState {
  search: string;
  [key: string]: string;
}

export function useFilters(initial: FilterState = { search: "" }) {
  const [filters, setFilters] = useState<FilterState>(initial);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(20);

  const setFilter = useCallback((key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  }, []);

  const setSearch = useCallback((value: string) => {
    setFilter("search", value);
  }, [setFilter]);

  const clearFilters = useCallback(() => {
    setFilters(initial);
    setPage(1);
  }, [initial]);

  const nextPage = useCallback(() => setPage((p) => p + 1), []);
  const prevPage = useCallback(() => setPage((p) => Math.max(1, p - 1)), []);

  const toParams = useCallback((): Record<string, string> => {
    const params: Record<string, string> = { page: String(page), limit: String(limit) };
    Object.entries(filters).forEach(([k, v]) => {
      if (v) params[k] = v;
    });
    return params;
  }, [filters, page, limit]);

  return {
    filters,
    page,
    limit,
    setFilter,
    setSearch,
    setPage,
    setLimit,
    clearFilters,
    nextPage,
    prevPage,
    toParams,
  };
}
