"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Pill, Spinner } from "@/components/ui";
import { Account, api, SystemStatus } from "@/lib/api";

function FolderPicker({ onPick }: { onPick: (id: string, name: string) => void }) {
  const [trail, setTrail] = useState<{ id: string; name: string }[]>([{ id: "root", name: "My Drive" }]);
  const [folders, setFolders] = useState<{ id: string; name: string }[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const here = trail[trail.length - 1];

  useEffect(() => {
    setFolders(null);
    api.driveFolders(here.id).then(setFolders, setError);
  }, [here.id]);

  return (
    <div className="rounded-lg border border-line">
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-3 py-2 text-sm">
        {trail.map((t, i) => (
          <span key={t.id} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted">/</span>}
            <button className="hover:underline" onClick={() => setTrail(trail.slice(0, i + 1))}>
              {t.name}
            </button>
          </span>
        ))}
        {here.id !== "root" && (
          <Button variant="primary" className="ml-auto" onClick={() => onPick(here.id, here.name)}>
            Use “{here.name}”
          </Button>
        )}
      </div>
      <ErrorNote error={error} />
      <ul className="max-h-64 overflow-y-auto text-sm">
        {!folders && (
          <li className="px-3 py-2 text-muted">
            <Spinner small /> Loading folders…
          </li>
        )}
        {folders?.length === 0 && <li className="px-3 py-2 text-muted">No subfolders here.</li>}
        {folders?.map((f) => (
          <li key={f.id}>
            <button className="w-full px-3 py-1.5 text-left hover:bg-surface-2" onClick={() => setTrail([...trail, f])}>
              📁 {f.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AccountCard() {
  const [acc, setAcc] = useState<Account | null>(null);
  const [balance, setBalance] = useState("");
  const [risk, setRisk] = useState("");
  const [applyAll, setApplyAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.account().then((a) => {
      setAcc(a);
      setBalance(a.starting_balance?.toString() ?? "");
      setRisk(a.risk_per_trade?.toString() ?? "");
    }, setError);
  }, []);

  const riskChanged = acc && risk !== (acc.risk_per_trade?.toString() ?? "");

  const save = async () => {
    setSaving(true);
    setError(null);
    setMsg(null);
    try {
      const r = await api.updateAccount({
        starting_balance: balance.trim() ? Number(balance) : null,
        risk_per_trade: risk.trim() ? Number(risk) : null,
        apply_to_existing: applyAll,
      });
      setAcc(r);
      setApplyAll(false);
      setMsg(`Saved. P&L updated on ${r.trades_updated} trade${r.trades_updated === 1 ? "" : "s"}.`);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const input = "w-40 rounded-lg border border-line px-2.5 py-1.5 text-sm tnum";
  return (
    <Card title="Account" subtitle="Balance and P&L are measured from these. A trade's P&L = its R × the risk on that trade.">
      {!acc && !error && <Spinner small />}
      {acc && (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-6">
            <label className="space-y-1">
              <span className="block text-xs font-medium text-ink-2">Starting balance ($)</span>
              <input className={input} inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="e.g. 10000" />
            </label>
            <label className="space-y-1">
              <span className="block text-xs font-medium text-ink-2">Risk per trade ($)</span>
              <input className={input} inputMode="decimal" value={risk} onChange={(e) => setRisk(e.target.value)} placeholder="e.g. 120" />
            </label>
          </div>
          {riskChanged && (
            <label className="flex items-start gap-2 text-ink-2">
              <input type="checkbox" className="mt-0.5" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} />
              <span>
                Also use this risk for trades already in the journal.
                <span className="block text-xs text-muted">
                  Leave unticked if your risk was different back then: past trades keep the risk they were taken with. A risk you typed on a
                  trade is never changed.
                </span>
              </span>
            </label>
          )}
          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
            {msg && <span className="text-good">{msg}</span>}
          </div>
          <ErrorNote error={error} />
        </div>
      )}
    </Card>
  );
}

function SettingsInner() {
  const sp = useSearchParams();
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [picking, setPicking] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.system().then(setSys, setError), []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!sys?.drive.syncing) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [sys?.drive.syncing, load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const d = sys?.drive;
  const flash = sp.get("drive");

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-semibold">Settings</h1>
      {flash === "connected" && <div className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-good">Google Drive connected. Now choose your screenshots folder.</div>}
      {flash === "error" && <ErrorNote error={`Google sign-in failed: ${sp.get("message") ?? "unknown error"}`} />}
      <ErrorNote error={error} />
      <AccountCard />
      {!sys && !error && <Spinner small />}

      {sys && d && (
        <>
          <Card title="Google Drive" subtitle="Screenshots in the chosen folder and its subfolders are imported automatically. Each subfolder is one trade.">
            {!d.oauth_configured ? (
              <div className="space-y-2 text-sm text-ink-2">
                <p>Drive isn&apos;t set up yet. In <code>backend/.env</code> set:</p>
                <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs">{`GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
APP_SECRET_KEY=...   # see backend/.env.example`}</pre>
                <p>
                  Create an OAuth client (type “Web application”) in Google Cloud Console with redirect URI{" "}
                  <code>http://localhost:8000/api/drive/auth/callback</code>, enable the Google Drive API, then restart the backend.
                </p>
              </div>
            ) : !d.connected ? (
              <Button variant="primary" disabled={busy} onClick={() => run(async () => (window.location.href = (await api.driveAuthStart()).url))}>
                Connect Google Drive
              </Button>
            ) : (
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span>
                    Connected{d.account_email ? ` as ${d.account_email}` : ""} · read-only access
                  </span>
                  <Button variant="ghost" disabled={busy} onClick={() => confirm("Disconnect Google Drive?") && run(api.driveDisconnect)}>
                    Disconnect
                  </Button>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-ink-2">Folder:</span>
                  <span className="font-medium">{d.folder_name ?? "not chosen"}</span>
                  <Button onClick={() => setPicking((p) => !p)}>{picking ? "Cancel" : d.folder_id ? "Change" : "Choose folder"}</Button>
                </div>
                {picking && (
                  <FolderPicker
                    onPick={(id, name) =>
                      run(async () => {
                        await api.driveSetFolder(id, name);
                        setPicking(false);
                      })
                    }
                  />
                )}
                {d.folder_id && (
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="primary"
                      disabled={busy || d.syncing}
                      onClick={() =>
                        run(async () => {
                          const r = await api.driveSync();
                          setSyncMsg(
                            r.status === "ok"
                              ? `Imported ${r.new_images} new screenshot(s) into ${r.new_trades} new trade(s).`
                              : r.status === "already_running"
                                ? "A sync is already running."
                                : (r.message ?? r.status),
                          );
                        })
                      }
                    >
                      {d.syncing ? "Syncing…" : "Sync now"}
                    </Button>
                    <span className="text-xs text-muted">
                      Auto-sync every {d.sync_interval_minutes} min · {d.files_seen} files tracked
                      {d.last_sync_at && ` · last ${new Date(d.last_sync_at).toLocaleString()} (${d.last_sync_status})`}
                    </span>
                  </div>
                )}
                {syncMsg && <p className="text-ink-2">{syncMsg}</p>}
                {d.last_sync_status === "error" && d.last_sync_message && <ErrorNote error={d.last_sync_message} />}
              </div>
            )}
          </Card>

          <Card title="AI extraction & assistant" subtitle="Via OpenRouter. Models are tried in order; the next one is used when one is rate-limited.">
            <ul className="space-y-1.5 text-sm">
              <li className="flex flex-wrap items-center gap-2">
                API key {sys.ai_configured ? <Pill tone="good">✓ set</Pill> : <Pill tone="warn">⚠ not set</Pill>}
                {!sys.ai_configured && (
                  <span className="text-ink-2">
                    Add <code>OPENROUTER_API_KEY=…</code> to <code>backend/.env</code> and restart the backend. Without it, filename, OCR and chart
                    vision still run.
                  </span>
                )}
              </li>
              <li>Vision models: <code>{sys.vision_models.join(", ")}</code></li>
              <li>Assistant models: <code>{sys.assistant_models.join(", ")}</code></li>
              <li className="flex items-center gap-2">
                OCR {sys.ocr_available ? <Pill tone="good">✓ ready</Pill> : <Pill tone="crit">✕ {sys.ocr_error}</Pill>}
              </li>
              <li>
                Confidence: ≥ {Math.round(sys.thresholds.high * 100)}% filled automatically, ≥ {Math.round(sys.thresholds.medium * 100)}% filled and flagged
                for review, lower ones become suggestions.
              </li>
            </ul>
          </Card>

          <Card title="Instruments" subtitle="Detected from chart symbols, legends and price level. Point value is used to compute P&L from size.">
            <ul className="divide-y divide-line text-sm">
              {sys.instruments.map((i) => (
                <li key={i.asset} className="flex justify-between py-1.5">
                  <span>
                    <span className="font-medium">{i.asset}</span> <span className="text-muted">· {i.name}</span>
                  </span>
                  <span className="tnum text-ink-2">{i.point_value ? `$${i.point_value} / point` : "—"}</span>
                </li>
              ))}
            </ul>
          </Card>

          <p className="text-xs text-muted">Database: {sys.database} · extraction queue: {sys.queue}</p>
        </>
      )}
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense>
      <SettingsInner />
    </Suspense>
  );
}
