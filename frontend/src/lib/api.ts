export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type FieldType = "text" | "number" | "date" | "select" | "multiselect" | "rating" | "longtext";

export interface FieldDef {
  key: string;
  label: string;
  category: string;
  type: FieldType;
  options: string[];
  extractable: boolean;
  computed: boolean;
  required: boolean;
  min: number | null;
  max: number | null;
  help: string;
}

export interface FieldRegistry {
  categories: string[];
  fields: FieldDef[];
}

export type FieldStatus = "auto" | "needs_review" | "verified";

export interface FieldMeta {
  source: string; // manual | computed | ocr | cv | vision | filename | folder | image | a+b
  confidence: number;
  status: FieldStatus;
  evidence: string;
  updated_at: string;
}

export interface TradeImage {
  id: number;
  file_name: string;
  role: "entry" | "setup" | "other" | null;
  timeframe: string | null;
  role_source: string | null;
  web_link: string | null;
  url: string;
  drive: boolean;
}

export interface Suggestion {
  id: number;
  field: string;
  value: unknown;
  source: string;
  confidence: number;
  evidence: string;
  reason: "low_confidence" | "conflict" | "conflicts_verified" | string;
  status: string;
  created_at: string;
  trade_id?: number;
}

export interface Annotation {
  image: number;
  type: string;
  label: string;
  price: string | null;
  is_trade_level: boolean;
}

export interface Trade {
  id: number;
  [field: string]: unknown;
  field_meta: Record<string, FieldMeta>;
  source: string;
  folder_name: string | null;
  folder_path: string | null;
  processing_status: string;
  processing_error: string | null;
  processed_at: string | null;
  retry_after: string | null;
  created_at: string;
  images: TradeImage[];
  missing_required: string[];
  needs_review: string[];
  pending_suggestions: number;
  suggestions?: Suggestion[];
  extraction?: {
    warnings?: string[];
    stats?: Record<string, number>;
    by_source?: Record<string, number>;
    vision_model?: string | null;
    annotations?: Annotation[];
    notes?: string | null;
  } | null;
}

export interface HistoryRow {
  id: number;
  action: string;
  field: string | null;
  old: unknown;
  new: unknown;
  source: string;
  at: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, init);
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API}. Is it running?`);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  fields: () => request<FieldRegistry>("/api/fields"),
  options: () =>
    request<{ assets: string[]; strategies: string[]; setups: string[]; timeframes: string[]; mistakes: string[] }>(
      "/api/trades/options",
    ),
  trades: (params: Record<string, string | undefined>) =>
    request<{ total: number; items: Trade[] }>(`/api/trades${qs(params)}`),
  trade: (id: number) => request<Trade>(`/api/trades/${id}`),
  createTrade: (values: Record<string, unknown>) => request<Trade>("/api/trades", json("POST", { values })),
  updateTrade: (id: number, values: Record<string, unknown>) =>
    request<Trade>(`/api/trades/${id}`, json("PATCH", { values })),
  verify: (id: number, fields?: string[]) =>
    request<{ verified: number; trade: Trade }>(`/api/trades/${id}/verify`, json("POST", { fields: fields ?? null })),
  reprocess: (id: number) => request<{ queued: boolean }>(`/api/trades/${id}/reprocess`, json("POST")),
  deleteTrade: (id: number) => request<{ deleted: number }>(`/api/trades/${id}`, json("DELETE")),
  history: (id: number) => request<HistoryRow[]>(`/api/trades/${id}/history`),
  upload: (files: File[], opts: { mode?: "single" | "separate"; tradeId?: number; folderName?: string }) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f, f.name));
    fd.append("mode", opts.mode ?? "single");
    if (opts.tradeId !== undefined) fd.append("trade_id", String(opts.tradeId));
    if (opts.folderName) fd.append("folder_name", opts.folderName);
    return request<{ trade_ids: number[] }>("/api/trades/upload", { method: "POST", body: fd });
  },
  patchImage: (id: number, body: { role?: string; timeframe?: string }) =>
    request<Trade>(`/api/images/${id}`, json("PATCH", body)),
  deleteImage: (id: number) => request<Trade>(`/api/images/${id}`, json("DELETE")),
  approve: (sid: number) => request<Trade>(`/api/suggestions/${sid}/approve`, json("POST")),
  reject: (sid: number) => request<Trade>(`/api/suggestions/${sid}/reject`, json("POST")),
  analytics: (params: Record<string, string | undefined>) => request<Analytics>(`/api/analytics${qs(params)}`),
  account: () => request<Account>("/api/account"),
  updateAccount: (body: { starting_balance: number | null; risk_per_trade: number | null; apply_to_existing: boolean }) =>
    request<Account & { trades_updated: number }>("/api/account", json("PUT", body)),
  system: () => request<SystemStatus>("/api/system/status"),
  driveStatus: () => request<DriveStatus>("/api/drive/status"),
  driveAuthStart: () => request<{ url: string }>("/api/drive/auth/start"),
  driveDisconnect: () => request<DriveStatus>("/api/drive/disconnect", json("POST")),
  driveFolders: (parent = "root") => request<{ id: string; name: string }[]>(`/api/drive/folders${qs({ parent })}`),
  driveSetFolder: (id: string, name: string) => request<DriveStatus>("/api/drive/folder", json("POST", { id, name })),
  driveSync: () =>
    request<{ status: string; new_trades?: number; new_images?: number; queued?: number; message?: string }>(
      "/api/drive/sync",
      json("POST"),
    ),
};

export function imageUrl(img: TradeImage) {
  return `${API}${img.url}`;
}

/** Streams the assistant's reply, calling onChunk with each piece of text as it arrives. */
export async function streamChat(
  body: { messages: { role: string; content: string }[]; trade_id?: number; period?: string },
  onChunk: (text: string) => void,
  signal?: AbortSignal,
) {
  const res = await fetch(`${API}/api/assistant/chat`, { ...json("POST", body), signal });
  if (!res.ok || !res.body) throw new ApiError(res.status, `assistant request failed (${res.status})`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(dec.decode(value, { stream: true }));
  }
}

// ---------- analytics ----------

export interface GroupStats {
  key?: string;
  trades: number;
  closed: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number | null;
  net_pnl: number | null;
  total_r: number | null;
  avg_r: number | null;
  profit_factor: number | null;
  profit_factor_basis: string;
  avg_win: number | null;
  avg_loss: number | null;
}

export interface Overview extends GroupStats {
  max_drawdown: number | null;
  max_drawdown_r: number | null;
  open: number;
  avg_rr: number | null;
  best_win_streak: number;
  worst_loss_streak: number;
  largest_win: number | null;
  largest_loss: number | null;
  best_r: number | null;
  worst_r: number | null;
  risk_per_trade: number | null;
  // present once a starting balance is set
  starting_balance?: number;
  balance?: number;
  return_pct?: number;
  max_drawdown_pct?: number;
}

export interface PeriodRow {
  period: string;
  closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  net_pnl: number | null;
  total_r: number | null;
}

export interface Compare {
  closed: number;
  win_rate: number | null;
  avg_r: number | null;
  net_pnl: number | null;
  total_r: number | null;
}

export interface BehaviorItem {
  key: string;
  label: string;
  tagged: number;
  detected: number | null;
  detected_rule?: string;
  trade_ids: number[];
  days_flagged?: string[];
  with: Compare;
  without: Compare;
}

export interface Analytics {
  overview: Overview;
  equity: {
    trade_id: number;
    date: string;
    asset: string | null;
    pnl: number | null;
    r: number | null;
    equity: number;
    equity_r: number;
    drawdown: number;
    drawdown_r: number;
    balance?: number;
  }[];
  periods: { day: PeriodRow[]; week: PeriodRow[]; month: PeriodRow[] };
  rolling_win_rate: { trade_id: number; date: string; win_rate: number; window: number }[];
  distribution: { bin: string; lo: number | null; hi: number | null; count: number }[];
  breakdowns: Record<string, GroupStats[]>;
  dimension_labels: Record<string, string>;
  setups: { metric: string; min_trades: number; most_profitable: GroupStats | null; least_profitable: GroupStats | null };
  behavior: {
    items: BehaviorItem[];
    mistake_tags: { tag: string; count: number; win_rate: number | null; avg_r: number | null; net_pnl: number | null; total_r: number | null }[];
    clean_trades: GroupStats;
    by_emotion: GroupStats[];
    by_discipline: GroupStats[];
  };
  data_quality: {
    missing_required: number;
    needs_review: number;
    pending_suggestions: number;
    without_r: number;
    without_pnl: number;
    open: number;
    processing: number;
  };
}

export interface Account {
  starting_balance: number | null;
  risk_per_trade: number | null;
  currency: string;
}

export interface DriveStatus {
  oauth_configured: boolean;
  connected: boolean;
  account_email: string | null;
  folder_id: string | null;
  folder_name: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_message: string | null;
  files_seen: number;
  sync_interval_minutes: number;
  syncing: boolean;
}

export interface SystemStatus {
  ai_configured: boolean;
  vision_models: string[];
  assistant_models: string[];
  ocr_available: boolean;
  ocr_error: string | null;
  database: string;
  queue: number;
  drive: DriveStatus;
  instruments: { asset: string; name: string; point_value: number | null }[];
  thresholds: { high: number; medium: number };
}
