"use client";

import { SearchBar } from "./search-bar";
import { Select } from "@/components/ui/select";
import { DateRangePicker } from "@/components/ui/date-range-picker";
import { Button } from "@/components/ui/button";
import { X } from "lucide-react";
import type { ReactNode } from "react";

interface FilterOption {
  key: string;
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}

interface FilterBarProps {
  searchPlaceholder?: string;
  onSearch: (query: string) => void;
  filters?: FilterOption[];
  dateRange?: {
    from: string;
    to: string;
    onChange: (from: string, to: string) => void;
  };
  onClear?: () => void;
  actions?: ReactNode;
}

export function FilterBar({
  searchPlaceholder,
  onSearch,
  filters,
  dateRange,
  onClear,
  actions,
}: FilterBarProps) {
  const hasActiveFilters = (filters?.some((f) => f.value) || dateRange?.from || dateRange?.to);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="w-72">
        <SearchBar placeholder={searchPlaceholder} onSearch={onSearch} />
      </div>
      {filters?.map((filter) => (
        <Select
          key={filter.key}
          placeholder={filter.label}
          options={filter.options}
          value={filter.value}
          onChange={(e) => filter.onChange(e.target.value)}
        />
      ))}
      {dateRange && (
        <DateRangePicker
          from={dateRange.from}
          to={dateRange.to}
          onChange={dateRange.onChange}
        />
      )}
      {hasActiveFilters && onClear && (
        <Button variant="ghost" size="sm" onClick={onClear} className="gap-1">
          <X className="h-3 w-3" /> Clear
        </Button>
      )}
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  );
}
