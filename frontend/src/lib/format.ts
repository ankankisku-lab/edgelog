export function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "−" : v > 0 ? "+" : "";
  // whole amounts stay short ("$384"); any cents show both digits ("$7,303.20", not "$7,303.2")
  const cents = Math.round(Math.abs(v) * 100) % 100 !== 0;
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: 2 })}`;
}

/** An amount with no +/− sign, e.g. a balance. */
export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function compactMoney(v: number): string {
  const a = Math.abs(v);
  const s = a >= 1_000_000 ? `${+(a / 1_000_000).toFixed(1)}M` : a >= 10_000 ? `${+(a / 1000).toFixed(1)}K` : `${+a.toFixed(0)}`;
  return `${v < 0 ? "−" : ""}$${s}`;
}

export function rMult(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}R`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
}

export function date(v: unknown, withTime = false): string {
  if (!v) return "—";
  // the API sends wall-clock times without an offset (folder dates, chart times): show them as-is
  const s = String(v);
  const d = new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s) ? `${s}Z` : s);
  if (Number.isNaN(d.getTime())) return String(v);
  const hasTime = withTime && (d.getUTCHours() !== 0 || d.getUTCMinutes() !== 0);
  return d.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(hasTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    timeZone: "UTC",
  });
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-ink-2";
  return v > 0 ? "text-gain" : "text-loss";
}

export const SOURCE_LABEL: Record<string, string> = {
  manual: "Manual",
  computed: "Computed",
  filename: "Filename",
  folder: "Folder name",
  image: "Image name",
  ocr: "OCR",
  cv: "Chart vision",
  vision: "AI vision",
};

export function sourceLabel(src: string | undefined): string {
  if (!src) return "";
  return src
    .split("+")
    .map((s) => SOURCE_LABEL[s] ?? s)
    .join(" + ");
}

export function isAiSource(src: string | undefined): boolean {
  return !!src && src !== "manual" && src !== "computed";
}
