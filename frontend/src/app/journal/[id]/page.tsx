"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Dropzone from "@/components/Dropzone";
import FieldInput from "@/components/FieldInput";
import { Button, Card, ErrorNote, Pill, ProcessingBadge, Spinner } from "@/components/ui";
import { api, FieldDef, FieldMeta, HistoryRow, imageUrl, Suggestion, Trade, TradeImage } from "@/lib/api";
import { date, isAiSource, sourceLabel } from "@/lib/format";

const REASON: Record<string, string> = {
  low_confidence: "Low confidence — not filled in",
  conflict: "Differs from the current value",
  conflicts_verified: "Differs from a value you verified",
};

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return date(v, true);
  return String(v);
}

function MetaLine({ meta, onVerify }: { meta?: FieldMeta; onVerify: () => void }) {
  if (!meta) return null;
  const ai = isAiSource(meta.source);
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
      {meta.source === "manual" ? (
        <Pill tone="manual">Manual</Pill>
      ) : meta.source === "computed" ? (
        <Pill>Computed</Pill>
      ) : (
        <Pill>
          AI · {sourceLabel(meta.source)} {Math.round(meta.confidence * 100)}%
        </Pill>
      )}
      {meta.status === "verified" && ai && <Pill tone="good">✓ Verified</Pill>}
      {meta.status === "needs_review" && (
        <>
          <Pill tone="warn">⚠ Check this</Pill>
          <button onClick={onVerify} className="text-accent hover:underline">
            Looks right
          </button>
        </>
      )}
      {meta.evidence && (
        <details className="w-full text-muted">
          <summary className="cursor-pointer select-none hover:text-ink-2">Where this came from</summary>
          <p className="mt-1 break-words">{meta.evidence}</p>
        </details>
      )}
    </div>
  );
}

function ImageCard({ img, onChange, onDelete, timeframes }: {
  img: TradeImage;
  onChange: (body: { role?: string; timeframe?: string }) => void;
  onDelete: () => void;
  timeframes: string[];
}) {
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-surface">
      <a href={imageUrl(img)} target="_blank" rel="noreferrer" title="Open full size">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={imageUrl(img)} alt={img.file_name} className="max-h-80 w-full bg-surface-2 object-contain" loading="lazy" />
      </a>
      <figcaption className="space-y-2 p-3 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-medium text-ink" title={img.file_name}>
            {img.file_name}
          </span>
          {img.web_link && (
            <a href={img.web_link} target="_blank" rel="noreferrer" className="shrink-0 text-accent hover:underline">
              Drive
            </a>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-muted">
            Role
            <select
              value={img.role ?? ""}
              onChange={(e) => onChange({ role: e.target.value })}
              className="rounded-md border border-line px-1.5 py-0.5 text-ink"
            >
              <option value="" disabled>
                ?
              </option>
              <option value="entry">Entry chart</option>
              <option value="setup">Setup chart</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-muted">
            TF
            <select
              value={img.timeframe ?? ""}
              onChange={(e) => onChange({ timeframe: e.target.value })}
              className="rounded-md border border-line px-1.5 py-0.5 text-ink"
            >
              <option value="" disabled>
                ?
              </option>
              {timeframes.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          {img.role_source && <span className="text-muted">({img.role_source === "manual" ? "set by you" : `from ${img.role_source}`})</span>}
          <button onClick={onDelete} className="ml-auto text-loss hover:underline">
            Remove
          </button>
        </div>
      </figcaption>
    </figure>
  );
}

export default function TradePage() {
  const { id } = useParams<{ id: string }>();
  const tid = Number(id);
  const router = useRouter();
  const [trade, setTrade] = useState<Trade | null>(null);
  const [fields, setFields] = useState<{ categories: string[]; fields: FieldDef[] } | null>(null);
  const [timeframes, setTimeframes] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(0);

  const load = useCallback(() => api.trade(tid).then(setTrade, setError), [tid]);

  useEffect(() => {
    load();
    api.fields().then(setFields, setError);
    api.options().then((o) => setTimeframes(o.timeframes), () => {});
  }, [load]);

  const processing = trade && ["queued", "processing"].includes(trade.processing_status);
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [processing, load]);

  const save = async (key: string, value: unknown) => {
    pending.current++;
    setSaving("saving");
    try {
      const t = await api.updateTrade(tid, { [key]: value });
      setTrade(t);
      setFieldErr((m) => {
        const rest = { ...m };
        delete rest[key];
        return rest;
      });
      if (--pending.current === 0) setSaving("saved");
    } catch (e) {
      pending.current--;
      setFieldErr((m) => ({ ...m, [key]: e instanceof Error ? e.message : String(e) }));
      setSaving("error");
    }
  };

  const act = async (fn: () => Promise<Trade | unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "field_meta" in r) setTrade(r as Trade);
      else await load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const byCategory = useMemo(() => {
    if (!fields) return [];
    return fields.categories.map((c) => ({ category: c, items: fields.fields.filter((f) => f.category === c) }));
  }, [fields]);

  const suggestionsByField = useMemo(() => {
    const m: Record<string, Suggestion[]> = {};
    for (const s of trade?.suggestions ?? []) (m[s.field] ??= []).push(s);
    return m;
  }, [trade]);

  if (error && !trade) return <ErrorNote error={error} />;
  if (!trade || !fields)
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Spinner small /> Loading trade…
      </div>
    );

  const labelOf = (k: string) => fields.fields.find((f) => f.key === k)?.label ?? k;
  const ex = trade.extraction;
  const annotations = ex?.annotations ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/journal" className="text-sm text-muted hover:text-ink">
            ← Journal
          </Link>
          <h1 className="mt-1 text-xl font-semibold">
            Trade #{trade.id}
            {trade.asset ? ` · ${trade.asset}` : ""}
            {trade.direction ? ` · ${trade.direction}` : ""}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
            <span>{date(trade.trade_date, true)}</span>
            {trade.folder_name && <span>· {trade.folder_path || trade.folder_name}</span>}
            {trade.images.length > 0 && <ProcessingBadge status={trade.processing_status} />}
            <span aria-live="polite" className="text-xs">
              {saving === "saving" ? "Saving…" : saving === "saved" ? "All changes saved" : saving === "error" ? "Some changes were not saved" : ""}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {trade.needs_review.length > 0 && (
            <Button variant="primary" disabled={busy} onClick={() => act(() => api.verify(tid).then((r) => r.trade))}>
              Confirm {trade.needs_review.length} AI value{trade.needs_review.length > 1 ? "s" : ""}
            </Button>
          )}
          {trade.images.length > 0 && (
            <Button
              disabled={busy || !!processing}
              title="Runs extraction again. Values you entered or verified are kept; differing readings become suggestions."
              onClick={() => act(() => api.reprocess(tid))}
            >
              Reprocess with AI
            </Button>
          )}
          <Link href={`/assistant?trade=${tid}`} className="inline-flex items-center rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2">
            AI review
          </Link>
          <Button
            variant="danger"
            disabled={busy}
            onClick={async () => {
              if (!confirm(`Delete trade #${tid}? Its screenshots stay in Google Drive and won't be re-imported.`)) return;
              await api.deleteTrade(tid).then(() => router.push("/journal"), setError);
            }}
          >
            Delete
          </Button>
        </div>
      </div>

      <ErrorNote error={error} />

      {trade.missing_required.length > 0 && (
        <div className="rounded-xl border border-line bg-warn-wash px-4 py-2.5 text-sm text-warn">
          Missing: {trade.missing_required.map(labelOf).join(", ")}. Fill these in below.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        {/* left: screenshots + extraction notes */}
        <div className="space-y-4">
          {trade.images.map((img) => (
            <ImageCard
              key={img.id}
              img={img}
              timeframes={timeframes}
              onChange={(b) => act(() => api.patchImage(img.id, b))}
              onDelete={() => confirm(`Remove ${img.file_name} from this trade?`) && act(() => api.deleteImage(img.id))}
            />
          ))}
          <Dropzone compact disabled={busy} onFiles={(files) => act(() => api.upload(files, { tradeId: tid }))} />

          {(trade.processing_error || ex) && (
            <Card title="Extraction details">
              <div className="space-y-2 text-sm">
                {trade.processing_error && <p className="whitespace-pre-line text-warn">{trade.processing_error}</p>}
                {ex?.by_source && (
                  <p className="text-ink-2">
                    Readings per stage:{" "}
                    {Object.entries(ex.by_source)
                      .map(([k, v]) => `${sourceLabel(k)} ${v}`)
                      .join(" · ")}
                    {ex.vision_model ? ` · model ${ex.vision_model}` : ""}
                  </p>
                )}
                {!!ex?.warnings?.length && (
                  <ul className="list-disc pl-5 text-warn">
                    {ex.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                )}
                {ex?.notes && <p className="text-ink-2">AI notes: {ex.notes}</p>}
              </div>
            </Card>
          )}

          {annotations.length > 0 && (
            <Card title="Chart annotations" subtitle="Read by AI vision. Analysis drawings are kept apart from actual trade levels.">
              <ul className="divide-y divide-line text-sm">
                {annotations.map((a, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 py-1.5">
                    <span>
                      <span className="text-muted">{a.type}</span> · {a.label}
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {a.price && <span className="tnum">{a.price}</span>}
                      <Pill tone={a.is_trade_level ? "neutral" : "manual"}>{a.is_trade_level ? "trade level" : "analysis"}</Pill>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        {/* right: suggestions + fields */}
        <div className="space-y-4">
          {!!trade.suggestions?.length && (
            <Card title={`AI suggestions (${trade.suggestions.length})`} subtitle="Not applied yet. Approve to use the value, reject to dismiss it.">
              <ul className="divide-y divide-line">
                {trade.suggestions.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                    <div className="min-w-0">
                      <div>
                        <span className="font-medium">{labelOf(s.field)}</span>: <span className="tnum">{show(s.value)}</span>
                        <span className="text-muted"> (now {show(trade[s.field])})</span>
                      </div>
                      <div className="text-xs text-muted">
                        {REASON[s.reason] ?? s.reason} · {sourceLabel(s.source)} {Math.round(s.confidence * 100)}%
                      </div>
                      {s.evidence && <div className="truncate text-xs text-muted" title={s.evidence}>{s.evidence}</div>}
                    </div>
                    <div className="flex gap-1.5">
                      <Button disabled={busy} onClick={() => act(() => api.approve(s.id))}>
                        Approve
                      </Button>
                      <Button variant="ghost" disabled={busy} onClick={() => act(() => api.reject(s.id))}>
                        Reject
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {byCategory.map(({ category, items }) => (
            <Card key={category} title={category}>
              <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                {items.map((f) => {
                  const missing = trade.missing_required.includes(f.key);
                  const wide = ["multiselect", "longtext"].includes(f.type);
                  return (
                    <div key={f.key} className={`${wide ? "sm:col-span-2" : ""} ${missing ? "-m-1.5 rounded-lg bg-warn-wash p-1.5" : ""}`}>
                      <label className="mb-1 flex items-center gap-1 text-xs font-medium text-ink-2">
                        {f.label}
                        {f.required && <span className="text-warn" title="Required">*</span>}
                        {suggestionsByField[f.key] && <Pill tone="manual">suggestion</Pill>}
                        {f.help && (
                          <span className="font-normal text-muted" title={f.help}>
                            ⓘ
                          </span>
                        )}
                      </label>
                      <FieldInput field={f} value={trade[f.key]} invalid={!!fieldErr[f.key]} onCommit={(v) => save(f.key, v)} />
                      {fieldErr[f.key] && <p className="mt-1 text-xs text-loss">{fieldErr[f.key]}</p>}
                      <MetaLine meta={trade.field_meta[f.key]} onVerify={() => act(() => api.verify(tid, [f.key]).then((r) => r.trade))} />
                    </div>
                  );
                })}
              </div>
            </Card>
          ))}

          <Card
            title="Change history"
            subtitle="Every extraction, edit, approval and verification"
            action={
              <Button variant="ghost" onClick={() => api.history(tid).then(setHistory, setError)}>
                {history ? "Refresh" : "Show"}
              </Button>
            }
          >
            {history && (
              <ul className="max-h-80 space-y-1 overflow-y-auto text-xs">
                {history.map((h) => (
                  <li key={h.id} className="flex gap-2">
                    <span className="tnum shrink-0 text-muted">{new Date(h.at).toLocaleString()}</span>
                    <span>
                      <span className="font-medium">{h.action}</span>
                      {h.field && ` ${labelOf(h.field)}`}
                      {h.field && `: ${show(h.old)} → ${show(h.new)}`} <span className="text-muted">({h.source})</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
