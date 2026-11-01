"use client";

import { useEffect, useState } from "react";
import type { FieldDef } from "@/lib/api";

export function toInputValue(f: FieldDef, v: unknown): string {
  if (v === null || v === undefined) return "";
  if (f.type === "date") return String(v).slice(0, 10);
  return String(v);
}

/** One journal field. Commits on blur / Enter (text, numbers) or immediately (selects, chips). */
export default function FieldInput({ field, value, onCommit, invalid }: {
  field: FieldDef;
  value: unknown;
  onCommit: (v: unknown) => void;
  invalid?: boolean;
}) {
  const [draft, setDraft] = useState(toInputValue(field, value));
  useEffect(() => {
    setDraft(toInputValue(field, value));
  }, [field, value]);

  const base = `w-full rounded-lg border px-2.5 py-1.5 text-sm ${invalid ? "border-crit" : "border-line"} focus:outline-2 focus:outline-accent`;
  const commit = (raw: string) => {
    if (raw === toInputValue(field, value)) return;
    onCommit(raw === "" ? null : raw);
  };

  if (field.type === "select") {
    return (
      <select className={base} value={draft} onChange={(e) => commit(e.target.value)} aria-label={field.label}>
        <option value="">—</option>
        {field.options.map((o) => (
          <option key={o}>{o}</option>
        ))}
        {draft && !field.options.includes(draft) && <option>{draft}</option>}
      </select>
    );
  }

  if (field.type === "multiselect") {
    const sel = Array.isArray(value) ? (value as string[]) : [];
    const toggle = (o: string) => {
      const next = sel.includes(o) ? sel.filter((x) => x !== o) : [...sel, o];
      onCommit(next.length ? next : null);
    };
    return (
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={field.label}>
        {[...field.options, ...sel.filter((s) => !field.options.includes(s))].map((o) => (
          <button
            key={o}
            type="button"
            aria-pressed={sel.includes(o)}
            onClick={() => toggle(o)}
            className={`rounded-full border px-2.5 py-1 text-xs ${
              sel.includes(o) ? "border-accent bg-accent text-white" : "border-line text-ink-2 hover:bg-surface-2"
            }`}
          >
            {o}
          </button>
        ))}
      </div>
    );
  }

  if (field.type === "rating") {
    const n = Number(value) || 0;
    return (
      <div className="flex gap-1" role="radiogroup" aria-label={field.label}>
        {[1, 2, 3, 4, 5].map((i) => (
          <button
            key={i}
            type="button"
            role="radio"
            aria-checked={n === i}
            onClick={() => onCommit(n === i ? null : i)}
            className={`h-8 w-8 rounded-lg border text-sm ${i <= n ? "border-accent bg-accent text-white" : "border-line text-ink-2 hover:bg-surface-2"}`}
          >
            {i}
          </button>
        ))}
      </div>
    );
  }

  if (field.type === "longtext") {
    return (
      <textarea
        className={`${base} min-h-24`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => commit(draft)}
        aria-label={field.label}
      />
    );
  }

  return (
    <input
      className={`${base} ${field.type === "number" ? "tnum" : ""}`}
      type={field.type === "date" ? "date" : "text"}
      inputMode={field.type === "number" ? "decimal" : undefined}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => commit(draft)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      aria-label={field.label}
    />
  );
}
