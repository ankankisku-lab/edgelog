"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BreakdownBars, Metric, METRICS, Toggle } from "@/components/charts";
import Filters, { EMPTY_FILTERS, FilterState, toParams } from "@/components/Filters";
import { Card, Empty, ErrorNote, Select, Spinner } from "@/components/ui";
import { Analytics, api, Compare, GroupStats } from "@/lib/api";
import { money, pct, rMult, signClass } from "@/lib/format";

function SetupCard({ label, s, metric }: { label: string; s: GroupStats | null; metric: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      {s ? (
        <>
          <div className="mt-1 truncate text-lg font-semibold">{s.key}</div>
          <div className="mt-0.5 text-sm text-ink-2">
            <span className={signClass(metric === "net_pnl" ? s.net_pnl : s.total_r)}>
              {metric === "net_pnl" ? money(s.net_pnl) : rMult(s.total_r)}
            </span>{" "}
            · {pct(s.win_rate)} win · {s.closed} trades
          </div>
        </>
      ) : (
        <div className="mt-1 text-sm text-muted">Not enough trades yet</div>
      )}
    </div>
  );
}

function StatsTable({ rows }: { rows: GroupStats[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="border-b border-line text-left text-xs text-muted">
          <tr>
            <th className="py-1.5 pr-2 font-medium">Group</th>
            {["Trades", "Win rate", "Net P&L", "Total R", "Avg R", "Profit factor", "Avg win", "Avg loss"].map((h) => (
              <th key={h} className="px-2 py-1.5 text-right font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tnum">
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-line last:border-0">
              <td className="py-1.5 pr-2">{r.key}</td>
              <td className="px-2 text-right">
                {r.closed}
                {r.trades > r.closed ? <span className="text-muted"> +{r.trades - r.closed} open</span> : ""}
              </td>
              <td className="px-2 text-right">{pct(r.win_rate)}</td>
              <td className={`px-2 text-right ${signClass(r.net_pnl)}`}>{money(r.net_pnl)}</td>
              <td className={`px-2 text-right ${signClass(r.total_r)}`}>{rMult(r.total_r)}</td>
              <td className={`px-2 text-right ${signClass(r.avg_r)}`}>{rMult(r.avg_r)}</td>
              <td className="px-2 text-right">{r.profit_factor ?? "—"}</td>
              <td className="px-2 text-right">{money(r.avg_win)}</td>
              <td className="px-2 text-right">{money(r.avg_loss)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompareCell({ c }: { c: Compare }) {
  if (!c.closed) return <span className="text-muted">—</span>;
  return (
    <span className="tnum">
      {c.closed} · {pct(c.win_rate)} · <span className={signClass(c.avg_r)}>{rMult(c.avg_r)}</span>
    </span>
  );
}

export default function StrategyPage() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [dim, setDim] = useState("strategy_name");
  const [metric, setMetric] = useState<Metric>("avg_r");

  useEffect(() => {
    api.analytics(toParams(filters)).then((d) => (setData(d), setError(null)), setError);
  }, [filters]);

  const rows = data?.breakdowns[dim] ?? [];
  const b = data?.behavior;

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Strategy & behaviour</h1>
          <p className="text-sm text-muted">Compare what works, based only on what your journal records.</p>
        </div>
        <Filters value={filters} onChange={setFilters} />
      </div>
      <ErrorNote error={error} />
      {!data && !error && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner small /> Loading…
        </div>
      )}
      {data && data.overview.trades === 0 && <Empty title="No trades in this selection" />}

      {data && data.overview.trades > 0 && b && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <SetupCard label={`Most profitable setup (min ${data.setups.min_trades} trades)`} s={data.setups.most_profitable} metric={data.setups.metric} />
            <SetupCard label="Least profitable setup" s={data.setups.least_profitable} metric={data.setups.metric} />
          </div>

          <Card
            title="Compare performance"
            subtitle="Pick what to group by and which result to compare"
            action={
              <div className="flex flex-wrap gap-2">
                <Select
                  aria-label="Group by"
                  value={dim}
                  onChange={setDim}
                  options={Object.entries(data.dimension_labels).map(([value, label]) => ({ value, label }))}
                />
                <Toggle value={metric} onChange={setMetric} options={METRICS} />
              </div>
            }
          >
            <BreakdownBars rows={rows} metric={metric} />
            <details className="mt-3">
              <summary className="cursor-pointer text-sm text-accent">Table view</summary>
              <div className="mt-2">
                <StatsTable rows={rows} />
              </div>
            </details>
          </Card>

          <Card
            title="Behavioural patterns"
            subtitle="“Tagged” counts trades you marked with that mistake; “detected” uses the rule shown, from recorded times, prices and results. Results are closed trades · win rate · avg R."
          >
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="border-b border-line text-left text-xs text-muted">
                  <tr>
                    <th className="py-1.5 pr-2 font-medium">Pattern</th>
                    <th className="px-2 text-right font-medium">Tagged</th>
                    <th className="px-2 text-right font-medium">Detected</th>
                    <th className="px-2 font-medium">With it</th>
                    <th className="px-2 font-medium">Without it</th>
                    <th className="px-2 font-medium">Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {b.items.map((it) => (
                    <tr key={it.key} className="border-b border-line align-top last:border-0">
                      <td className="py-2 pr-2">
                        <div className="font-medium">{it.label}</div>
                        {it.detected_rule && <div className="text-xs text-muted">{it.detected_rule}</div>}
                      </td>
                      <td className="tnum px-2 py-2 text-right">{it.tagged}</td>
                      <td className="tnum px-2 py-2 text-right">{it.detected ?? <span className="text-muted">n/a</span>}</td>
                      <td className="px-2 py-2">
                        <CompareCell c={it.with} />
                      </td>
                      <td className="px-2 py-2">
                        <CompareCell c={it.without} />
                      </td>
                      <td className="px-2 py-2 text-xs">
                        {it.trade_ids.slice(0, 8).map((id) => (
                          <Link key={id} href={`/journal/${id}`} className="mr-1.5 text-accent hover:underline">
                            #{id}
                          </Link>
                        ))}
                        {it.trade_ids.length > 8 && <span className="text-muted">+{it.trade_ids.length - 8}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Mistake tags" subtitle="From the Mistakes field">
              {b.mistake_tags.length ? (
                <table className="w-full text-sm">
                  <thead className="border-b border-line text-left text-xs text-muted">
                    <tr>
                      <th className="py-1.5 font-medium">Tag</th>
                      <th className="text-right font-medium">Trades</th>
                      <th className="text-right font-medium">Win rate</th>
                      <th className="text-right font-medium">Avg R</th>
                      <th className="text-right font-medium">Net P&L</th>
                    </tr>
                  </thead>
                  <tbody className="tnum">
                    {b.mistake_tags.map((m) => (
                      <tr key={m.tag} className="border-b border-line last:border-0">
                        <td className="py-1.5">{m.tag}</td>
                        <td className="text-right">{m.count}</td>
                        <td className="text-right">{pct(m.win_rate)}</td>
                        <td className={`text-right ${signClass(m.avg_r)}`}>{rMult(m.avg_r)}</td>
                        <td className={`text-right ${signClass(m.net_pnl)}`}>{money(m.net_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-muted">No mistakes tagged yet. Tag them in each trade&apos;s Psychology section.</p>
              )}
              <p className="mt-3 text-sm text-ink-2">
                Trades with no mistakes: {b.clean_trades.closed} closed · {pct(b.clean_trades.win_rate)} win ·{" "}
                <span className={signClass(b.clean_trades.avg_r)}>{rMult(b.clean_trades.avg_r)}</span> avg ·{" "}
                <span className={signClass(b.clean_trades.net_pnl)}>{money(b.clean_trades.net_pnl)}</span>
              </p>
            </Card>
            <Card title="Emotion & discipline" subtitle={`${METRICS.find((m) => m.value === metric)?.label} by recorded state`}>
              <div className="space-y-4">
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-2">Emotion</div>
                  <BreakdownBars rows={b.by_emotion} metric={metric} />
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-2">Discipline (1–5)</div>
                  <BreakdownBars rows={b.by_discipline} metric={metric} />
                </div>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
