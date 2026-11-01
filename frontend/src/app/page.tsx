"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DistributionChart, EquityChart, PeriodBars, Toggle, Unit, WinLossBar, WinRateChart, BreakdownBars } from "@/components/charts";
import Filters, { EMPTY_FILTERS, FilterState, toParams } from "@/components/Filters";
import { Card, Empty, ErrorNote, Spinner, Stat } from "@/components/ui";
import { Analytics, api } from "@/lib/api";
import { money, num, pct, rMult, signClass, usd } from "@/lib/format";

type Grain = "day" | "week" | "month";

export default function Dashboard() {
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [unit, setUnit] = useState<Unit>("money");
  const [grain, setGrain] = useState<Grain>("day");

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.analytics(toParams(filters)).then(
        (d) => alive && (setData(d), setError(null)),
        (e) => alive && setError(e),
      );
    load();
    const t = setInterval(load, 20000); // dashboards refresh as new screenshots are extracted
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [filters]);

  const o = data?.overview;
  const q = data?.data_quality;

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Performance overview</h1>
          <p className="text-sm text-muted">Built from closed trades in your journal. Open trades and trades without results are left out.</p>
        </div>
        <Filters value={filters} onChange={setFilters} />
      </div>

      <ErrorNote error={error} />
      {!data && !error && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner small /> Loading analytics…
        </div>
      )}

      {q && (q.missing_required > 0 || q.needs_review > 0 || q.pending_suggestions > 0 || q.processing > 0) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-line bg-warn-wash px-4 py-2.5 text-sm text-warn">
          <span className="font-medium">Journal needs attention:</span>
          {q.missing_required > 0 && <Link className="underline" href="/journal?status=missing">{q.missing_required} with missing fields</Link>}
          {q.needs_review > 0 && <Link className="underline" href="/journal?status=review">{q.needs_review} to review</Link>}
          {q.pending_suggestions > 0 && <Link className="underline" href="/journal?status=suggestions">{q.pending_suggestions} AI suggestions</Link>}
          {q.processing > 0 && <span>{q.processing} still processing</span>}
          {q.without_r > 0 && <span>{q.without_r} closed without R-multiple</span>}
        </div>
      )}

      {o && o.trades === 0 && (
        <Empty title="No trades yet">
          <Link href="/upload" className="text-accent underline">Add screenshots</Link> or connect Google Drive in{" "}
          <Link href="/settings" className="text-accent underline">Settings</Link>.
        </Empty>
      )}

      {o && o.trades > 0 && data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <div className="col-span-2 rounded-xl border border-line bg-surface px-4 py-3 md:row-span-2 xl:col-span-2">
              {o.balance !== undefined ? (
                <>
                  <div className="text-xs text-muted">Account balance</div>
                  <div className="mt-1 text-4xl font-semibold break-all text-ink sm:text-5xl">{usd(o.balance)}</div>
                  <div className="mt-2 text-sm">
                    <span className={signClass(o.net_pnl)}>
                      {money(o.net_pnl)} ({o.return_pct !== undefined && o.return_pct > 0 ? "+" : ""}
                      {pct(o.return_pct, 2)})
                    </span>
                    <span className="text-ink-2"> · {rMult(o.total_r)}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted">
                    from {usd(o.starting_balance)} · {o.closed} closed{o.open ? ` · ${o.open} open` : ""}
                    {o.risk_per_trade ? ` · $${num(o.risk_per_trade)} risk/trade` : ""}
                  </div>
                </>
              ) : (
                <>
                  <div className="text-xs text-muted">Net P&L</div>
                  <div className={`mt-1 text-4xl font-semibold break-all sm:text-5xl ${signClass(o.net_pnl)}`}>{money(o.net_pnl)}</div>
                  <div className="mt-2 text-sm text-ink-2">
                    {rMult(o.total_r)} total · {o.closed} closed{o.open ? ` · ${o.open} open` : ""}
                  </div>
                  <Link href="/settings" className="mt-1 inline-block text-xs text-accent hover:underline">
                    Set a starting balance to track your account →
                  </Link>
                </>
              )}
            </div>
            <Stat label="Total trades" value={o.trades} hint={`${o.wins} wins · ${o.losses} losses${o.breakeven ? ` · ${o.breakeven} BE` : ""}`} />
            <Stat label="Win rate" value={pct(o.win_rate)} hint="wins ÷ (wins + losses)" />
            <Stat label="Profit factor" value={o.profit_factor ?? "—"} hint={o.profit_factor_basis === "r" ? "from R (no P&L recorded)" : "gross profit ÷ gross loss"} />
            <Stat label="Max drawdown" value={money(o.max_drawdown)} tone={signClass(o.max_drawdown)} hint={`${rMult(o.max_drawdown_r)}${o.max_drawdown_pct !== undefined ? ` · ${o.max_drawdown_pct}% of peak` : ""}`} />
            <Stat label="Avg R-multiple" value={rMult(o.avg_r)} tone={signClass(o.avg_r)} />
            {o.avg_rr ? <Stat label="Avg planned R:R" value={`1 : ${num(o.avg_rr)}`} hint="TP (R) or entry/SL/TP" /> : (
              <Stat label="Best / worst trade" value={<span className="text-base">{rMult(o.best_r)} / {rMult(o.worst_r)}</span>} />
            )}
            <Stat label="Avg win / loss" value={<span className="text-base">{money(o.avg_win)} / {money(o.avg_loss)}</span>} />
            <Stat label="Streaks" value={<span className="text-base">{o.best_win_streak}W best · {o.worst_loss_streak}L worst</span>} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card
              className="lg:col-span-2"
              title={o.starting_balance && unit === "money" ? "Account balance" : "Equity curve"}
              subtitle={o.starting_balance && unit === "money" ? "Balance after each trade" : "Cumulative result, trade by trade"}
              action={<Toggle value={unit} onChange={setUnit} options={[{ value: "money", label: "$" }, { value: "r", label: "R" }]} />}
            >
              {data.equity.length ? <EquityChart data={data.equity} unit={unit} start={o.starting_balance} /> : <p className="text-sm text-muted">No closed trades.</p>}
            </Card>
            <div className="space-y-4">
              <Card title="Winning vs losing trades">
                <WinLossBar wins={o.wins} losses={o.losses} breakeven={o.breakeven} />
              </Card>
              <Card title="Win rate over time" subtitle="Rolling, last 20 decided trades (from the 5th)">
                {data.rolling_win_rate.length ? <WinRateChart data={data.rolling_win_rate} /> : <p className="text-sm text-muted">Needs at least 5 wins/losses.</p>}
              </Card>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card
              title={`${grain === "day" ? "Daily" : grain === "week" ? "Weekly" : "Monthly"} P&L`}
              subtitle={unit === "money" ? "Net P&L per period" : "Total R per period"}
              action={
                <Toggle
                  value={grain}
                  onChange={setGrain}
                  options={[{ value: "day", label: "Day" }, { value: "week", label: "Week" }, { value: "month", label: "Month" }]}
                />
              }
            >
              <PeriodBars data={data.periods[grain]} unit={unit} />
            </Card>
            <Card title="Profit distribution" subtitle="Closed trades by R-multiple">
              <DistributionChart data={data.distribution} />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Performance by asset" subtitle="Net P&L">
              <BreakdownBars rows={data.breakdowns.asset ?? []} metric="net_pnl" />
            </Card>
            <Card title="Performance by entry timeframe" subtitle="Average R-multiple">
              <BreakdownBars rows={data.breakdowns.entry_timeframe ?? []} metric="avg_r" />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
