"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ReactNode, Suspense, useEffect, useMemo, useState } from "react";
import { Button, Empty, ErrorNote, Pill, ProcessingBadge, Select, Spinner } from "@/components/ui";
import { api, Trade } from "@/lib/api";
import { date, money, num, rMult, signClass } from "@/lib/format";

const STATUS_FILTERS = [
  { value: "review", label: "Needs review" },
  { value: "missing", label: "Missing fields" },
  { value: "suggestions", label: "Has AI suggestions" },
  { value: "processing", label: "Processing" },
  { value: "error", label: "Extraction problems" },
];

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const EMPTY = "__empty";

/** The API sends wall-clock times without an offset; read them as-is. */
function when(t: Trade): Date | null {
  if (!t.trade_date) return null;
  const s = String(t.trade_date);
  const d = new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s) ? `${s}Z` : s);
  return Number.isNaN(d.getTime()) ? null : d;
}

function weekday(t: Trade): string | null {
  const d = when(t);
  return d ? DAYS[(d.getUTCDay() + 6) % 7] : null;
}

type Filter = "date" | "select" | "range";

interface Col {
  key: string; // field key (for missing / review highlights)
  id: string; // column id, used in the URL
  label: string;
  num?: boolean;
  filter?: Filter;
  value: (t: Trade) => string | number | null; // what sorting and filtering use
  render?: (t: Trade) => ReactNode;
}

const str = (k: string) => (t: Trade) => (t[k] == null || t[k] === "" ? null : String(t[k]));
const n = (k: string) => (t: Trade) => (typeof t[k] === "number" ? (t[k] as number) : null);

const COLS: Col[] = [
  { key: "trade_date", id: "date", label: "Date", filter: "date", value: (t) => when(t)?.getTime() ?? null, render: (t) => date(t.trade_date) },
  { key: "trade_date", id: "day", label: "Day", filter: "select", value: weekday },
  { key: "session", id: "session", label: "Session", filter: "select", value: str("session") },
  { key: "asset", id: "asset", label: "Asset", filter: "select", value: str("asset") },
  { key: "direction", id: "side", label: "Side", filter: "select", value: str("direction") },
  { key: "entry_timeframe", id: "etf", label: "Entry TF", filter: "select", value: str("entry_timeframe") },
  { key: "setup_timeframe", id: "stf", label: "Setup TF", filter: "select", value: str("setup_timeframe") },
  { key: "entry_price", id: "entry", label: "Entry", num: true, value: n("entry_price") },
  { key: "stop_loss", id: "sl", label: "SL", num: true, value: n("stop_loss") },
  { key: "take_profit", id: "tp", label: "TP", num: true, value: n("take_profit") },
  { key: "exit_price", id: "exit", label: "Exit", num: true, value: n("exit_price") },
  {
    key: "r_multiple", id: "r", label: "R", num: true, filter: "range", value: n("r_multiple"),
    render: (t) => <span className={signClass(t.r_multiple as number)}>{rMult(t.r_multiple as number)}</span>,
  },
  {
    key: "pnl", id: "pnl", label: "P&L", num: true, filter: "range", value: n("pnl"),
    render: (t) => <span className={signClass(t.pnl as number)}>{money(t.pnl as number)}</span>,
  },
  { key: "outcome", id: "outcome", label: "Outcome", filter: "select", value: str("outcome") },
  { key: "strategy_name", id: "strategy", label: "Strategy", filter: "select", value: str("strategy_name") },
];

const ORDER: Record<string, string[]> = { day: DAYS, session: ["Asia", "London", "New York"], side: ["Long", "Short"], outcome: ["Win", "Loss", "Breakeven", "Open"] };

function distinct(items: Trade[], col: Col): string[] {
  // trading days are always offered, even before any trade falls on them
  const vals = new Set<string>(col.id === "day" ? DAYS.slice(0, 5) : []);
  for (const t of items) {
    const v = col.value(t);
    if (v !== null) vals.add(String(v));
  }
  const order = ORDER[col.id];
  return [...vals].sort((a, b) => (order ? order.indexOf(a) - order.indexOf(b) : a.localeCompare(b, undefined, { numeric: true })));
}

/** min / max inputs that commit on blur or Enter, so typing doesn't refilter on every key. */
function RangeInputs({ min, max, onChange, type = "number" }: {
  min: string;
  max: string;
  onChange: (min: string, max: string) => void;
  type?: "number" | "date";
}) {
  const [lo, setLo] = useState(min);
  const [hi, setHi] = useState(max);
  useEffect(() => {
    setLo(min);
    setHi(max);
  }, [min, max]);
  const commit = () => (lo !== min || hi !== max) && onChange(lo, hi);
  const cls = `rounded border border-line px-1 py-0.5 text-xs font-normal ${type === "date" ? "w-[7.5rem]" : "w-14"}`;
  const keys = (e: React.KeyboardEvent<HTMLInputElement>) => e.key === "Enter" && (e.target as HTMLInputElement).blur();
  return (
    <div className={`flex gap-1 ${type === "date" ? "flex-col" : ""}`}>
      <input type={type} value={lo} onChange={(e) => setLo(e.target.value)} onBlur={commit} onKeyDown={keys}
        placeholder={type === "number" ? "min" : undefined} aria-label="from" className={cls} />
      <input type={type} value={hi} onChange={(e) => setHi(e.target.value)} onBlur={commit} onKeyDown={keys}
        placeholder={type === "number" ? "max" : undefined} aria-label="to" className={cls} />
    </div>
  );
}

function JournalInner() {
  const router = useRouter();
  const sp = useSearchParams();
  const [items, setItems] = useState<Trade[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [creating, setCreating] = useState(false);

  // search + status run on the server; column filters and sorting run here
  const serverParams = useMemo(() => ({ q: sp.get("q") ?? undefined, status: sp.get("status") ?? undefined, limit: "5000" }), [sp]);
  const sort = sp.get("sort") ?? "-date";

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    router.replace(`/journal?${next.toString()}`, { scroll: false });
  };

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = () =>
      api.trades(serverParams).then(
        (r) => {
          if (!alive) return;
          setItems(r.items);
          setError(null);
          // keep refreshing while screenshots are still being extracted
          const busy = r.items.some((t) => ["queued", "processing"].includes(t.processing_status));
          timer = setTimeout(load, busy ? 2500 : 15000);
        },
        (e) => alive && setError(e),
      );
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [serverParams]);

  useEffect(() => {
    const t = setTimeout(() => q !== (sp.get("q") ?? "") && update({ q }), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const options = useMemo(() => {
    const o: Record<string, string[]> = {};
    for (const c of COLS) if (c.filter === "select") o[c.id] = distinct(items ?? [], c);
    return o;
  }, [items]);

  const activeFilters = COLS.filter((c) => c.filter && (sp.get(c.id) || sp.get(`${c.id}_min`) || sp.get(`${c.id}_max`)));

  const rows = useMemo(() => {
    if (!items) return [];
    let out = items.filter((t) =>
      COLS.every((c) => {
        if (!c.filter) return true;
        const v = c.value(t);
        if (c.filter === "select") {
          const want = sp.get(c.id);
          if (!want) return true;
          return want === EMPTY ? v === null : v !== null && String(v) === want;
        }
        const lo = sp.get(`${c.id}_min`);
        const hi = sp.get(`${c.id}_max`);
        if (!lo && !hi) return true;
        if (v === null) return false;
        const conv = (s: string) => (c.filter === "date" ? Date.parse(`${s}T00:00:00Z`) : Number(s));
        if (lo && (v as number) < conv(lo)) return false;
        if (hi && (v as number) > conv(hi) + (c.filter === "date" ? 86399999 : 0)) return false;
        return true;
      }),
    );
    const id = sort.replace(/^-/, "");
    const col = COLS.find((c) => c.id === id);
    if (col) {
      const dir = sort.startsWith("-") ? -1 : 1;
      const rank = (v: string | number | null) => (col.id === "day" && v !== null ? DAYS.indexOf(String(v)) : v);
      out = [...out].sort((a, b) => {
        const x = rank(col.value(a));
        const y = rank(col.value(b));
        if (x === null || y === null) return x === y ? b.id - a.id : x === null ? 1 : -1; // blanks last
        const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
        return cmp * dir || b.id - a.id;
      });
    }
    return out;
  }, [items, sp, sort]);

  const newManual = async () => {
    setCreating(true);
    try {
      const t = await api.createTrade({ trade_date: new Date().toISOString().slice(0, 10) });
      router.push(`/journal/${t.id}`);
    } catch (e) {
      setError(e);
      setCreating(false);
    }
  };

  const clearColumnFilters = () => {
    const changes: Record<string, string> = {};
    for (const c of COLS) Object.assign(changes, { [c.id]: "", [`${c.id}_min`]: "", [`${c.id}_max`]: "" });
    update(changes);
  };

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Trading journal</h1>
          <p className="text-sm text-muted">
            {items ? (rows.length === items.length ? `${items.length} trades` : `${rows.length} of ${items.length} trades`) : "…"}.
            Highlighted cells are missing; an amber dot means AI filled it and it needs a look.
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={newManual} disabled={creating}>
            New manual trade
          </Button>
          <Link href="/upload" className="inline-flex items-center rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
            Add screenshots
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search asset, strategy, TP reason, folder, file…"
          className="w-full rounded-lg border border-line px-3 py-1.5 text-sm sm:w-80"
        />
        <Select aria-label="Status" value={sp.get("status") ?? ""} onChange={(v) => update({ status: v })} options={STATUS_FILTERS} placeholder="Any status" />
        {activeFilters.length > 0 && (
          <>
            <span className="text-xs text-muted">Filtered by {activeFilters.map((c) => c.label).join(", ")}</span>
            <button className="text-sm text-accent hover:underline" onClick={clearColumnFilters}>
              Clear column filters
            </button>
          </>
        )}
      </div>

      <ErrorNote error={error} />
      {!items && !error && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner small /> Loading…
        </div>
      )}
      {items && items.length === 0 && (
        <Empty title={sp.get("q") || sp.get("status") ? "No trades match this search" : "Your journal is empty"}>
          Upload screenshots, sync Google Drive, or add a manual trade.
        </Empty>
      )}

      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[1300px] text-sm">
            <thead className="border-b border-line text-left text-xs text-muted">
              <tr>
                <th className="px-3 pt-2 font-medium">#</th>
                <th className="px-2 pt-2 font-medium">Shots</th>
                {COLS.map((c) => (
                  <th key={c.id} className={`px-2 pt-2 font-medium whitespace-nowrap ${c.num ? "text-right" : ""}`}>
                    <button onClick={() => update({ sort: sort === `-${c.id}` ? c.id : `-${c.id}` })} className="hover:text-ink">
                      {c.label}
                      {sort === c.id ? " ↑" : sort === `-${c.id}` ? " ↓" : ""}
                    </button>
                  </th>
                ))}
                <th className="px-3 pt-2 font-medium">Status</th>
              </tr>
              <tr className="align-top">
                <th className="px-3 pt-1 pb-2" />
                <th className="px-2 pt-1 pb-2" />
                {COLS.map((c) => (
                  <th key={c.id} className={`px-2 pt-1 pb-2 ${c.num ? "text-right" : ""}`}>
                    {c.filter === "select" && (
                      <select
                        aria-label={`Filter ${c.label}`}
                        value={sp.get(c.id) ?? ""}
                        onChange={(e) => update({ [c.id]: e.target.value })}
                        className={`max-w-28 rounded border px-1 py-0.5 text-xs font-normal ${sp.get(c.id) ? "border-accent text-ink" : "border-line text-muted"}`}
                      >
                        <option value="">All</option>
                        {options[c.id].map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                        <option value={EMPTY}>(empty)</option>
                      </select>
                    )}
                    {(c.filter === "range" || c.filter === "date") && (
                      <div className={c.num ? "flex justify-end" : ""}>
                        <RangeInputs
                          type={c.filter === "date" ? "date" : "number"}
                          min={sp.get(`${c.id}_min`) ?? ""}
                          max={sp.get(`${c.id}_max`) ?? ""}
                          onChange={(lo, hi) => update({ [`${c.id}_min`]: lo, [`${c.id}_max`]: hi })}
                        />
                      </div>
                    )}
                  </th>
                ))}
                <th className="px-3 pt-1 pb-2" />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={COLS.length + 3} className="px-3 py-8 text-center text-sm text-muted">
                    No trades match these column filters.{" "}
                    <button className="text-accent hover:underline" onClick={clearColumnFilters}>
                      Clear them
                    </button>
                  </td>
                </tr>
              )}
              {rows.map((t) => (
                <tr
                  key={t.id}
                  onClick={() => router.push(`/journal/${t.id}`)}
                  className="cursor-pointer border-b border-line last:border-0 hover:bg-surface-2"
                >
                  <td className="tnum px-3 py-2 text-muted">{t.id}</td>
                  <td className="px-2 py-2 text-muted">{t.images.length || "—"}</td>
                  {COLS.map((c) => {
                    const missing = c.id !== "day" && t.missing_required.includes(c.key);
                    const review = c.id !== "day" && t.needs_review.includes(c.key);
                    const v = c.value(t);
                    return (
                      <td
                        key={c.id}
                        className={`px-2 py-2 whitespace-nowrap ${c.num ? "tnum text-right" : ""} ${missing ? "bg-warn-wash text-warn" : ""}`}
                        title={missing ? "Missing — click to fill in" : review ? "AI value, needs review" : undefined}
                      >
                        <span className="inline-flex items-center gap-1">
                          {review && <span aria-label="needs review" className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--warning)" }} />}
                          {missing ? "missing" : c.render ? c.render(t) : c.num ? num(v) : v ?? "—"}
                        </span>
                      </td>
                    );
                  })}
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {t.source === "manual" && !t.images.length ? <ProcessingBadge status="manual" /> : <ProcessingBadge status={t.processing_status} />}
                      {t.pending_suggestions > 0 && <Pill tone="manual">{t.pending_suggestions} suggestion{t.pending_suggestions > 1 ? "s" : ""}</Pill>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function JournalPage() {
  return (
    <Suspense>
      <JournalInner />
    </Suspense>
  );
}
