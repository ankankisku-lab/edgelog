"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import Dropzone from "@/components/Dropzone";
import { Button, Card, ErrorNote, ProcessingBadge } from "@/components/ui";
import { api, Trade } from "@/lib/api";
import { num } from "@/lib/format";

interface Group {
  name: string; // trade name, parsed like a Drive folder name
  files: File[];
}

const isImage = (f: File) => f.type.startsWith("image/");

function Thumb({ file, onRemove }: { file: File; onRemove: () => void }) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return (
    <div className="relative w-36 shrink-0 overflow-hidden rounded-lg border border-line">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={file.name} className="h-24 w-full bg-surface-2 object-cover" />
      <div className="truncate px-2 py-1 text-xs" title={file.name}>
        {file.name}
      </div>
      <button
        onClick={onRemove}
        aria-label={`Remove ${file.name}`}
        className="absolute top-1 right-1 rounded-full bg-surface px-1.5 text-xs text-ink-2 shadow hover:text-loss"
      >
        ×
      </button>
    </div>
  );
}

export default function UploadPage() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [separate, setSeparate] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [created, setCreated] = useState<Trade[]>([]);
  const folderInput = useRef<HTMLInputElement>(null);

  // poll the trades we just created until extraction finishes
  useEffect(() => {
    if (!created.some((t) => ["queued", "processing"].includes(t.processing_status))) return;
    const t = setTimeout(async () => {
      const fresh = await Promise.all(created.map((c) => api.trade(c.id).catch(() => c)));
      setCreated(fresh);
    }, 2000);
    return () => clearTimeout(t);
  }, [created]);

  const addLoose = (files: File[]) =>
    setGroups((g) => {
      const loose = g.find((x) => x.name === "" ) ?? { name: "", files: [] };
      const rest = g.filter((x) => x !== loose);
      return [{ ...loose, files: [...loose.files, ...files] }, ...rest];
    });

  const addFolder = (list: FileList | null) => {
    const byDir = new Map<string, File[]>();
    for (const f of Array.from(list ?? [])) {
      if (!isImage(f)) continue;
      const parts = (f.webkitRelativePath || f.name).split("/");
      const dir = parts.length > 1 ? parts[parts.length - 2] : "";
      byDir.set(dir, [...(byDir.get(dir) ?? []), f]);
    }
    setGroups((g) => [...g, ...Array.from(byDir, ([name, files]) => ({ name, files }))]);
  };

  const total = groups.reduce((n, g) => n + g.files.length, 0);

  const upload = async () => {
    setUploading(true);
    setError(null);
    try {
      const ids: number[] = [];
      for (const g of groups) {
        if (!g.files.length) continue;
        const loose = g.name === "";
        const r = await api.upload(g.files, {
          mode: loose && separate ? "separate" : "single",
          folderName: g.name || undefined,
        });
        ids.push(...r.trade_ids);
      }
      setCreated(await Promise.all(ids.map((id) => api.trade(id))));
      setGroups([]);
    } catch (e) {
      setError(e);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Add screenshots</h1>
        <p className="text-sm text-muted">
          Upload the charts for a trade, e.g. the M1 entry chart plus the M5 setup chart(s). Each trade&apos;s screenshots are read together,
          so a TP reason or TP R written on any of them is picked up.
        </p>
      </div>

      <Card>
        <Dropzone onFiles={addLoose} disabled={uploading} />
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
          <input
            ref={folderInput}
            type="file"
            hidden
            multiple
            // @ts-expect-error non-standard but supported by every major browser
            webkitdirectory=""
            onChange={(e) => (addFolder(e.target.files), (e.target.value = ""))}
          />
          <Button onClick={() => folderInput.current?.click()} disabled={uploading}>
            Pick a folder…
          </Button>
          <span className="text-xs text-muted">Each subfolder becomes one trade, named after the folder (like the Drive sync).</span>
        </div>
      </Card>

      {groups.map((g, gi) => (
        <Card
          key={gi}
          title={
            <input
              value={g.name}
              onChange={(e) => setGroups((all) => all.map((x, i) => (i === gi ? { ...x, name: e.target.value } : x)))}
              placeholder="Trade name (optional), e.g. MNQ long 03-11-2026"
              className="w-72 max-w-full rounded-lg border border-line px-2 py-1 text-sm font-normal"
              aria-label="Trade name"
            />
          }
          subtitle="Asset, direction and date in the name are used like a Drive folder name."
          action={
            <Button variant="ghost" onClick={() => setGroups((all) => all.filter((_, i) => i !== gi))}>
              Remove
            </Button>
          }
        >
          <div className="flex gap-2 overflow-x-auto pb-1">
            {g.files.map((f, fi) => (
              <Thumb
                key={`${f.name}-${fi}`}
                file={f}
                onRemove={() =>
                  setGroups((all) => all.map((x, i) => (i === gi ? { ...x, files: x.files.filter((_, j) => j !== fi) } : x)).filter((x) => x.files.length))
                }
              />
            ))}
          </div>
          {g.name === "" && g.files.length > 1 && (
            <label className="mt-3 flex items-center gap-2 text-sm text-ink-2">
              <input type="checkbox" checked={separate} onChange={(e) => setSeparate(e.target.checked)} />
              These are separate trades (one trade per screenshot)
            </label>
          )}
        </Card>
      ))}

      {total > 0 && (
        <div className="flex items-center justify-end gap-3">
          <span className="text-sm text-muted">
            {total} screenshot{total > 1 ? "s" : ""}
          </span>
          <Button variant="primary" onClick={upload} disabled={uploading}>
            {uploading ? "Uploading…" : "Add to journal"}
          </Button>
        </div>
      )}

      <ErrorNote error={error} />

      {created.length > 0 && (
        <Card title="Added to the journal" subtitle="Extraction runs in the background. Open a trade to review what was read.">
          <ul className="divide-y divide-line text-sm">
            {created.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <Link href={`/journal/${t.id}`} className="font-medium text-accent hover:underline">
                  Trade #{t.id}
                  {t.asset ? ` · ${t.asset}` : ""}
                  {t.direction ? ` · ${t.direction}` : ""}
                </Link>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {t.entry_price != null && <span className="tnum">entry {num(t.entry_price)}</span>}
                  {t.missing_required.length > 0 && <span className="text-warn">{t.missing_required.length} missing</span>}
                  {t.needs_review.length > 0 && <span className="text-warn">{t.needs_review.length} to review</span>}
                  <ProcessingBadge status={t.processing_status} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
