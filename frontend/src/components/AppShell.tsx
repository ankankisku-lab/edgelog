"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, SystemStatus } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/journal", label: "Journal" },
  { href: "/upload", label: "Add screenshots" },
  { href: "/strategy", label: "Strategy & behaviour" },
  { href: "/assistant", label: "AI assistant" },
  { href: "/settings", label: "Settings" },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [sys, setSys] = useState<SystemStatus | null>(null);
  const [down, setDown] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.system().then(
        (s) => alive && (setSys(s), setDown(false)),
        () => alive && setDown(true),
      );
    load();
    const t = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [path]);

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-4 py-3 md:py-5">
          <Link href="/" className="text-base font-semibold text-ink">
            Trade Journal
          </Link>
          <button
            className="rounded-lg px-2 py-1 text-sm text-ink-2 hover:bg-surface-2 md:hidden"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            Menu
          </button>
        </div>
        <nav className={`${open ? "block" : "hidden"} px-2 pb-3 md:block`}>
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={`block rounded-lg px-3 py-2 text-sm ${
                active(n.href) ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"
              }`}
            >
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="hidden px-4 pt-4 text-xs text-muted md:block">
          {down ? (
            <span className="text-loss">Backend offline</span>
          ) : sys ? (
            <ul className="space-y-1">
              <li>OCR {sys.ocr_available ? "ready" : "unavailable"}</li>
              <li>AI vision {sys.ai_configured ? "on" : "off (no API key)"}</li>
              <li>Drive {sys.drive.connected ? (sys.drive.folder_name ? `· ${sys.drive.folder_name}` : "connected") : "not connected"}</li>
              {sys.queue > 0 && <li className="text-accent">{sys.queue} in extraction queue</li>}
            </ul>
          ) : null}
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8">
        {down && (
          <div role="alert" className="mb-4 rounded-lg border border-line bg-crit-wash px-3 py-2 text-sm text-loss">
            Cannot reach the backend. Start it with <code>uvicorn app.main:app --reload</code> in <code>backend/</code>.
          </div>
        )}
        {children}
      </main>
    </div>
  );
}
