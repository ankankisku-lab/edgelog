"use client";

import { ReactNode, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Analytics, GroupStats, PeriodRow } from "@/lib/api";
import { compactMoney, date, money, pct, rMult, usd } from "@/lib/format";

const C = {
  gain: "var(--gain)",
  loss: "var(--loss)",
  line: "var(--series-1)",
  grid: "var(--grid)",
  axis: "var(--axis)",
  muted: "var(--muted)",
  neutral: "var(--axis)",
};

const tick = { fill: "var(--muted)", fontSize: 11 };

export type Unit = "money" | "r";

const fmt = (u: Unit) => (v: number) => (u === "money" ? compactMoney(v) : `${+v.toFixed(1)}R`);
const fmtFull = (u: Unit) => (v: number | null | undefined) => (u === "money" ? money(v) : rMult(v));

function TipBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink shadow-md">{children}</div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted">{k}</span>
      <span className="tnum font-medium">{v}</span>
    </div>
  );
}

/** Bar with a 4px rounded data-end and a square baseline, for positive and negative values alike. */
function SignedBar(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  fill?: string;
  value?: number | number[];
  horizontal?: boolean;
}) {
  const { fill, horizontal } = props;
  let { x = 0, y = 0, width = 0, height = 0 } = props;
  if (width < 0) [x, width] = [x + width, -width];
  if (height < 0) [y, height] = [y + height, -height];
  const raw = Array.isArray(props.value) ? props.value[1] : props.value;
  const neg = (raw ?? 0) < 0;
  const r = Math.min(4, horizontal ? width : height, horizontal ? height / 2 : width / 2);
  if (width <= 0 || height <= 0) return null;
  let d: string;
  if (!horizontal) {
    d = neg
      ? `M${x},${y} h${width} v${height - r} q0,${r} -${r},${r} h-${width - 2 * r} q-${r},0 -${r},-${r} Z`
      : `M${x},${y + height} v-${height - r} q0,-${r} ${r},-${r} h${width - 2 * r} q${r},0 ${r},${r} v${height - r} Z`;
  } else {
    d = neg
      ? `M${x + width},${y} v${height} h-${width - r} q-${r},0 -${r},-${r} v-${height - 2 * r} q0,-${r} ${r},-${r} Z`
      : `M${x},${y} h${width - r} q${r},0 ${r},${r} v${height - 2 * r} q0,${r} -${r},${r} h-${width - r} Z`;
  }
  return <path d={d} fill={fill} />;
}

export function Toggle<T extends string>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div role="group" className="inline-flex rounded-lg border border-line p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          className={`rounded-md px-2 py-1 ${value === o.value ? "bg-surface-2 font-medium text-ink" : "text-muted hover:text-ink"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function EquityChart({ data, unit, start }: { data: Analytics["equity"]; unit: Unit; start?: number }) {
  // with a starting balance, the $ view plots the account balance; otherwise cumulative P&L from zero
  const asBalance = unit === "money" && !!start;
  const rows = data.map((d, i) => ({
    ...d,
    n: i + 1,
    y: unit === "money" ? (asBalance ? d.balance ?? start! + d.equity : d.equity) : d.equity_r,
    dd: unit === "money" ? d.drawdown : d.drawdown_r,
  }));
  const f = asBalance ? compactMoney : fmt(unit);
  const ff = fmtFull(unit);
  const base = asBalance ? start! : 0;
  // a balance near 10,000 moving by hundreds needs a zoomed axis, padded so the line never touches the edge
  const span = Math.max(...rows.map((r) => Math.abs(r.y - base)), 1);
  const domain: [number, number] | undefined = asBalance
    ? [Math.min(base, ...rows.map((r) => r.y)) - span * 0.1, Math.max(base, ...rows.map((r) => r.y)) + span * 0.1]
    : undefined;
  return (
    <div>
      <div className="h-56">
        <ResponsiveContainer>
          <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={C.grid} />
            <XAxis dataKey="n" tick={tick} tickLine={false} axisLine={{ stroke: C.axis }} minTickGap={24} />
            <YAxis tickFormatter={f} tick={tick} tickLine={false} axisLine={false} width={60} domain={domain ?? ["auto", "auto"]} />
            <ReferenceLine y={base} stroke={C.axis} />
            <Tooltip
              cursor={{ stroke: C.muted, strokeWidth: 1 }}
              content={({ active, payload }) => {
                const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
                if (!active || !p) return null;
                return (
                  <TipBox>
                    <div className="mb-1 font-medium">
                      Trade #{p.trade_id} · {p.asset ?? "—"} · {date(p.date)}
                    </div>
                    <Row k="This trade" v={ff(unit === "money" ? p.pnl : p.r)} />
                    <Row k={asBalance ? "Balance" : "Equity"} v={asBalance ? usd(p.y) : ff(p.y)} />
                    <Row k="Drawdown" v={ff(p.dd)} />
                  </TipBox>
                );
              }}
            />
            <Area
              type="linear"
              dataKey="y"
              stroke={C.line}
              strokeWidth={2}
              fill={C.line}
              fillOpacity={0.1}
              baseValue={base}
              dot={false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 text-xs text-muted">Drawdown from peak</div>
      <div className="h-20">
        <ResponsiveContainer>
          <AreaChart data={rows} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
            <XAxis dataKey="n" hide />
            <YAxis tickFormatter={f} tick={tick} tickLine={false} axisLine={false} width={56} />
            <ReferenceLine y={0} stroke={C.axis} />
            <Tooltip
              cursor={{ stroke: C.muted, strokeWidth: 1 }}
              content={({ active, payload }) => {
                const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
                if (!active || !p) return null;
                return (
                  <TipBox>
                    <Row k={`After trade #${p.trade_id}`} v={ff(p.dd)} />
                  </TipBox>
                );
              }}
            />
            <Area type="stepAfter" dataKey="dd" stroke={C.loss} strokeWidth={2} fill={C.loss} fillOpacity={0.1} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function PeriodBars({ data, unit }: { data: PeriodRow[]; unit: Unit }) {
  const key = unit === "money" ? "net_pnl" : "total_r";
  const rows = data.map((d) => ({ ...d, v: (d[key] as number | null) ?? 0 }));
  const f = fmt(unit);
  return (
    <div className="h-56">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="period" tick={tick} tickLine={false} axisLine={{ stroke: C.axis }} minTickGap={16} />
          <YAxis tickFormatter={f} tick={tick} tickLine={false} axisLine={false} width={56} />
          <ReferenceLine y={0} stroke={C.axis} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
              if (!active || !p) return null;
              return (
                <TipBox>
                  <div className="mb-1 font-medium">{p.period}</div>
                  <Row k="Net P&L" v={money(p.net_pnl)} />
                  <Row k="Total R" v={rMult(p.total_r)} />
                  <Row k="Trades" v={`${p.closed} (${p.wins}W / ${p.losses}L)`} />
                  <Row k="Win rate" v={pct(p.win_rate)} />
                </TipBox>
              );
            }}
          />
          <Bar dataKey="v" maxBarSize={24} shape={<SignedBar />} isAnimationActive={false}>
            {rows.map((r) => (
              <Cell key={r.period} fill={r.v >= 0 ? C.gain : C.loss} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WinRateChart({ data }: { data: Analytics["rolling_win_rate"] }) {
  const rows = data.map((d, i) => ({ ...d, n: i + 1 }));
  return (
    <div className="h-48">
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="n" tick={tick} tickLine={false} axisLine={{ stroke: C.axis }} minTickGap={24} />
          <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}%`} tick={tick} tickLine={false} axisLine={false} width={40} />
          <ReferenceLine y={50} stroke={C.axis} />
          <Tooltip
            cursor={{ stroke: C.muted, strokeWidth: 1 }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
              if (!active || !p) return null;
              return (
                <TipBox>
                  <div className="mb-1 font-medium">Up to trade #{p.trade_id} · {date(p.date)}</div>
                  <Row k={`Win rate (last ${p.window})`} v={pct(p.win_rate)} />
                </TipBox>
              );
            }}
          />
          <Line type="linear" dataKey="win_rate" stroke={C.line} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Tall segmented bar: wins / breakeven / losses as one whole. */
export function WinLossBar({ wins, losses, breakeven }: { wins: number; losses: number; breakeven: number }) {
  const total = wins + losses + breakeven;
  const [hover, setHover] = useState<string | null>(null);
  if (!total) return <p className="text-sm text-muted">No closed trades yet.</p>;
  const parts = [
    { key: "Wins", n: wins, fill: C.gain, ink: "#ffffff" },
    { key: "Breakeven", n: breakeven, fill: C.neutral, ink: "var(--ink)" },
    { key: "Losses", n: losses, fill: C.loss, ink: "#ffffff" },
  ].filter((p) => p.n > 0);
  return (
    <div>
      <div className="flex h-7 gap-0.5" role="img" aria-label={parts.map((p) => `${p.key} ${p.n}`).join(", ")}>
        {parts.map((p) => {
          const share = (p.n / total) * 100;
          return (
            <div
              key={p.key}
              tabIndex={0}
              onMouseEnter={() => setHover(p.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(p.key)}
              onBlur={() => setHover(null)}
              className="flex items-center rounded px-2 text-xs font-medium outline-none"
              style={{ width: `${share}%`, background: p.fill, color: p.ink }}
              title={`${p.key}: ${p.n} (${share.toFixed(0)}%)`}
            >
              {share >= 12 && `${share.toFixed(0)}%`}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
        {parts.map((p) => (
          <span key={p.key} className={`inline-flex items-center gap-1.5 ${hover && hover !== p.key ? "opacity-50" : ""}`}>
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: p.fill }} />
            {p.key} <span className="tnum text-ink">{p.n}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function DistributionChart({ data }: { data: Analytics["distribution"] }) {
  const rows = data.map((d) => ({ ...d, sign: d.hi !== null && d.hi <= 0 ? -1 : d.lo !== null && d.lo >= 0 ? 1 : 0 }));
  return (
    <div className="h-48">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis
            dataKey="bin"
            tick={tick}
            tickLine={false}
            axisLine={{ stroke: C.axis }}
            interval="preserveStartEnd"
            // "-2 to -1.5R" -> "-2R": the bin's lower edge; the tooltip shows the full range
            tickFormatter={(_, i) => {
              const r = rows[i];
              return r?.lo === null || r?.lo === undefined ? `<${r?.hi}R` : `${r.lo}R`;
            }}
            minTickGap={4}
          />
          <YAxis allowDecimals={false} tick={tick} tickLine={false} axisLine={false} width={32} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
              if (!active || !p) return null;
              return (
                <TipBox>
                  <Row k={`R-multiple ${p.bin}`} v={`${p.count} trade${p.count === 1 ? "" : "s"}`} />
                </TipBox>
              );
            }}
          />
          <Bar dataKey="count" maxBarSize={24} shape={<SignedBar />} isAnimationActive={false}>
            {rows.map((r) => (
              <Cell key={r.bin} fill={r.sign > 0 ? C.gain : r.sign < 0 ? C.loss : C.neutral} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export type Metric = "net_pnl" | "total_r" | "avg_r" | "win_rate";

export const METRICS: { value: Metric; label: string }[] = [
  { value: "net_pnl", label: "Net P&L" },
  { value: "total_r", label: "Total R" },
  { value: "avg_r", label: "Avg R" },
  { value: "win_rate", label: "Win rate" },
];

export function metricFmt(m: Metric, v: number | null | undefined) {
  if (m === "net_pnl") return money(v);
  if (m === "win_rate") return pct(v);
  return rMult(v);
}

/** Horizontal bars comparing one metric across the groups of a dimension. */
export function BreakdownBars({ rows, metric }: { rows: GroupStats[]; metric: Metric }) {
  const data = rows
    .filter((r) => r[metric] !== null)
    .map((r) => ({ ...r, v: r[metric] as number }))
    .sort((a, b) => b.v - a.v);
  if (!data.length) return <p className="text-sm text-muted">No closed trades with this information yet.</p>;
  const isRate = metric === "win_rate";
  return (
    <div style={{ height: Math.max(80, data.length * 34 + 30) }}>
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 80, bottom: 0, left: 0 }} barCategoryGap="25%">
          <CartesianGrid horizontal={false} stroke={C.grid} />
          <XAxis
            type="number"
            // bars grow from zero, so zero must always be on the axis
            domain={isRate ? [0, 100] : [(min: number) => Math.min(0, min), (max: number) => Math.max(0, max)]}
            allowDataOverflow={false}
            tickFormatter={(v) => (metric === "net_pnl" ? compactMoney(v) : isRate ? `${v}%` : `${v}R`)}
            tick={tick}
            tickLine={false}
            axisLine={false}
          />
          <YAxis type="category" dataKey="key" width={120} tick={{ ...tick, fill: "var(--ink-2)" }} tickLine={false} axisLine={{ stroke: C.axis }} />
          {!isRate && <ReferenceLine x={0} stroke={C.axis} />}
          {isRate && <ReferenceLine x={50} stroke={C.axis} />}
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (typeof data)[number] | undefined;
              if (!active || !p) return null;
              return (
                <TipBox>
                  <div className="mb-1 font-medium">{p.key}</div>
                  <Row k="Trades" v={`${p.closed} closed (${p.wins}W / ${p.losses}L)`} />
                  <Row k="Win rate" v={pct(p.win_rate)} />
                  <Row k="Net P&L" v={money(p.net_pnl)} />
                  <Row k="Total R" v={rMult(p.total_r)} />
                  <Row k="Avg R" v={rMult(p.avg_r)} />
                  <Row k="Profit factor" v={p.profit_factor ?? "—"} />
                </TipBox>
              );
            }}
          />
          <Bar
            dataKey="v"
            maxBarSize={20}
            shape={<SignedBar horizontal />}
            isAnimationActive={false}
            label={{ position: "right", fill: "var(--ink-2)", fontSize: 11, formatter: (v: number) => metricFmt(metric, v) }}
          >
            {data.map((r) => (
              <Cell key={r.key} fill={isRate ? C.line : r.v >= 0 ? C.gain : C.loss} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
