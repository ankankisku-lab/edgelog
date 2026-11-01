"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, ReactNode, Suspense, useEffect, useRef, useState } from "react";
import { Button, Card } from "@/components/ui";
import { api, streamChat, Trade } from "@/lib/api";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

const EXAMPLES = [
  "Why did my last five MNQ trades result in losses?",
  "Which setup has the highest win rate?",
  "Am I exiting profitable trades too early?",
  "How does my EMA crossover strategy perform on M1 entries?",
  "Which timeframe produces the best average R-multiple?",
  "Am I consistently violating my stop-loss rules?",
  "Gold vs MNQ: where am I more consistent?",
];

/** Minimal markdown: headings, bullets, numbered lists, **bold** and `code`. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : part.startsWith("`") && part.endsWith("`") ? (
      <code key={i} className="rounded bg-surface-2 px-1">
        {part.slice(1, -1)}
      </code>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

function Markdown({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    out.push(
      <Tag key={out.length} className={`${list.ordered ? "list-decimal" : "list-disc"} space-y-0.5 pl-5`}>
        {list.items.map((it, i) => (
          <li key={i}>{inline(it)}</li>
        ))}
      </Tag>,
    );
    list = null;
  };
  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    const heading = line.match(/^#{1,4}\s+(.*)/);
    if (bullet || numbered) {
      const ordered = !!numbered;
      if (list && list.ordered !== ordered) flush();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
      continue;
    }
    flush();
    if (heading) out.push(<p key={out.length} className="mt-2 font-semibold">{inline(heading[1])}</p>);
    else if (line.trim()) out.push(<p key={out.length}>{inline(line)}</p>);
  }
  flush();
  return <div className="space-y-2 text-sm leading-relaxed">{out}</div>;
}

function AssistantInner() {
  const sp = useSearchParams();
  const tradeId = sp.get("trade") ? Number(sp.get("trade")) : undefined;
  const [trade, setTrade] = useState<Trade | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (tradeId) api.trade(tradeId).then(setTrade, () => setTrade(null));
  }, [tradeId]);

  useEffect(() => {
    // braces: newer browsers return a Promise from scrollIntoView, and an effect may only return a cleanup
    end.current?.scrollIntoView({ block: "end" });
  }, [msgs]);

  const send = async (text: string, period?: "week" | "month") => {
    if (!text.trim() || streaming) return;
    const history: Msg[] = [...msgs, { role: "user", content: text.trim() }];
    setMsgs([...history, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);
    abort.current = new AbortController();
    try {
      await streamChat(
        { messages: history, trade_id: tradeId, period },
        (chunk) =>
          setMsgs((m) => {
            const copy = [...m];
            copy[copy.length - 1] = { role: "assistant", content: copy[copy.length - 1].content + chunk };
            return copy;
          }),
        abort.current.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setMsgs((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { role: "assistant", content: `[${e instanceof Error ? e.message : String(e)}]` };
          return copy;
        });
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">AI trading assistant</h1>
        <p className="text-sm text-muted">
          Answers come from your journal records and statistics. Chart readings are labelled as interpretation, not fact.
        </p>
      </div>

      {tradeId && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>
              Reviewing{" "}
              <Link href={`/journal/${tradeId}`} className="font-medium text-accent hover:underline">
                trade #{tradeId}
                {trade?.asset ? ` · ${trade.asset}` : ""}
                {trade?.direction ? ` · ${trade.direction}` : ""}
              </Link>
              {trade ? ` with its ${trade.images.length} screenshot${trade.images.length === 1 ? "" : "s"}` : ""}
            </span>
            <div className="flex gap-2">
              <Button
                variant="primary"
                disabled={streaming}
                onClick={() =>
                  send(
                    "Review this trade: compare the execution with the recorded strategy, point out entry and exit mistakes, and evaluate the risk management.",
                  )
                }
              >
                Review this trade
              </Button>
              <Link href="/assistant" className="inline-flex items-center rounded-lg px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2">
                Clear
              </Link>
            </div>
          </div>
        </Card>
      )}

      {msgs.length === 0 && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Button disabled={streaming} onClick={() => send("Summarise my trading this week compared with last week.", "week")}>
              Weekly summary
            </Button>
            <Button disabled={streaming} onClick={() => send("Summarise my trading this month compared with last month.", "month")}>
              Monthly summary
            </Button>
            <Button
              disabled={streaming}
              onClick={() => send("Which strategies perform consistently, which recurring patterns hurt me, and what should I improve first?")}
            >
              What should I improve?
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((q) => (
              <button
                key={q}
                onClick={() => send(q)}
                className="rounded-full border border-line px-3 py-1 text-left text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-3">
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div
              className={`max-w-[90%] rounded-xl px-4 py-3 ${
                m.role === "user" ? "bg-accent text-white" : "border border-line bg-surface text-ink"
              }`}
            >
              {m.role === "user" ? (
                <p className="text-sm whitespace-pre-wrap">{m.content}</p>
              ) : m.content ? (
                <Markdown text={m.content} />
              ) : (
                <span className="text-sm text-muted">Thinking…</span>
              )}
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="sticky bottom-0 flex gap-2 bg-page py-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your trading…"
          className="min-w-0 flex-1 rounded-lg border border-line px-3 py-2 text-sm"
          aria-label="Message"
        />
        {streaming ? (
          <Button type="button" onClick={() => abort.current?.abort()}>
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" disabled={!input.trim()}>
            Send
          </Button>
        )}
      </form>
    </div>
  );
}

export default function AssistantPage() {
  return (
    <Suspense>
      <AssistantInner />
    </Suspense>
  );
}
