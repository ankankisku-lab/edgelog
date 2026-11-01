"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Select } from "./ui";

export interface FilterState {
  range: string;
  asset: string;
  strategy: string;
  direction: string;
  entry_timeframe: string;
}

export const EMPTY_FILTERS: FilterState = { range: "all", asset: "", strategy: "", direction: "", entry_timeframe: "" };

const RANGES = [
  { value: "all", label: "All time" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "mtd", label: "Month to date" },
  { value: "ytd", label: "Year to date" },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Turns the UI filter state into /api/analytics and /api/trades query params. */
export function toParams(f: FilterState): Record<string, string | undefined> {
  const now = new Date();
  let from: string | undefined;
  if (f.range === "mtd") from = iso(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)));
  else if (f.range === "ytd") from = iso(new Date(Date.UTC(now.getUTCFullYear(), 0, 1)));
  else if (f.range !== "all") from = iso(new Date(now.getTime() - Number(f.range) * 86400000));
  return {
    date_from: from,
    asset: f.asset || undefined,
    strategy: f.strategy || undefined,
    direction: f.direction || undefined,
    entry_timeframe: f.entry_timeframe || undefined,
  };
}

export default function Filters({ value, onChange }: { value: FilterState; onChange: (f: FilterState) => void }) {
  const [opts, setOpts] = useState<{ assets: string[]; strategies: string[]; timeframes: string[] } | null>(null);
  useEffect(() => {
    api.options().then(setOpts, () => {});
  }, []);
  const set = (k: keyof FilterState) => (v: string) => onChange({ ...value, [k]: v });
  const dirty = JSON.stringify(value) !== JSON.stringify(EMPTY_FILTERS);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select aria-label="Date range" value={value.range} onChange={set("range")} options={RANGES} />
      <Select aria-label="Asset" value={value.asset} onChange={set("asset")} options={opts?.assets ?? []} placeholder="All assets" />
      <Select aria-label="Strategy" value={value.strategy} onChange={set("strategy")} options={opts?.strategies ?? []} placeholder="All strategies" />
      <Select aria-label="Direction" value={value.direction} onChange={set("direction")} options={["Long", "Short"]} placeholder="Long & short" />
      <Select aria-label="Entry timeframe" value={value.entry_timeframe} onChange={set("entry_timeframe")} options={opts?.timeframes ?? []} placeholder="Any entry TF" />
      {dirty && (
        <button className="text-sm text-accent hover:underline" onClick={() => onChange(EMPTY_FILTERS)}>
          Reset
        </button>
      )}
    </div>
  );
}
