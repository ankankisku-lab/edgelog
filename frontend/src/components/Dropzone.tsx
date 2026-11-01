"use client";

import { useRef, useState } from "react";

const ACCEPT = "image/png,image/jpeg,image/webp,image/bmp,image/gif";

export default function Dropzone({ onFiles, compact, disabled }: {
  onFiles: (files: File[]) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
    if (files.length) onFiles(files);
  };
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) take(e.dataTransfer.files);
      }}
      onPaste={(e) => !disabled && take(e.clipboardData.files)}
      className={`rounded-xl border-2 border-dashed text-center transition ${over ? "border-accent bg-surface-2" : "border-line"} ${
        compact ? "px-3 py-3" : "px-6 py-10"
      }`}
    >
      <input ref={ref} type="file" accept={ACCEPT} multiple hidden onChange={(e) => (take(e.target.files), (e.target.value = ""))} />
      <p className={`text-ink-2 ${compact ? "text-xs" : "text-sm"}`}>
        Drop chart screenshots here, paste them, or{" "}
        <button type="button" disabled={disabled} className="font-medium text-accent hover:underline" onClick={() => ref.current?.click()}>
          browse files
        </button>
      </p>
      {!compact && <p className="mt-1 text-xs text-muted">PNG, JPG or WebP · up to 25 MB each</p>}
    </div>
  );
}
