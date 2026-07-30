import { useEffect, useState } from "react";
import { ClipboardPaste, ScanSearch, CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import { Card, Badge, Button } from "@/components/ui/primitives";
import { api } from "@/lib/api";

/*
 * Browser-assisted seat check.
 *
 * This is the only path that reaches every chain. The server can read AMC's seat
 * map, but Regal's is behind a CAPTCHA and Cinemark's robots.txt disallows theirs —
 * neither of which applies to a page the user opened themselves. A bookmarklet
 * reads that page and hands the grid back here.
 *
 * The handoff arrives as a URL fragment because the chain's page cannot POST to
 * localhost: Chrome's Private Network Access blocks https -> 127.0.0.1 (measured
 * on AMC, where the request hung until aborted). A fragment never reaches the
 * server on its own, so the grid is posted from here instead, and pasting the
 * clipboard copy is offered as a fallback.
 */

const FRAGMENT_KEY = "seatcheck";

function readFragment() {
  const hash = window.location.hash || "";
  const m = hash.match(new RegExp(`[#&]${FRAGMENT_KEY}=([^&]+)`));
  if (!m) return null;
  try {
    return JSON.parse(decodeURIComponent(m[1]));
  } catch {
    return null;
  }
}

function SeatRow({ row, minRow }) {
  const meets = row.physical_row >= minRow;
  return (
    <div className="flex items-center gap-2">
      <span
        className={
          "w-16 shrink-0 text-right text-[11px] tabular-nums " +
          (meets ? "text-foreground" : "text-muted-foreground/50")
        }
      >
        row {row.physical_row}
      </span>
      <div className="flex gap-0.5">
        {row.seats_available.map((a, j) => (
          <span
            key={j}
            className={"h-3 w-3 rounded-[3px] " + (a ? "bg-emerald-400/80" : "bg-muted-foreground/25")}
            title={a ? "available" : "unavailable"}
          />
        ))}
      </div>
    </div>
  );
}

export default function BrowserSeatCheck({ form, showtimes = [], onApply }) {
  const [payload, setPayload] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [pasted, setPasted] = useState("");
  const [applyKey, setApplyKey] = useState("");
  const [applied, setApplied] = useState(false);

  // Pick up a grid handed over by the bookmarklet.
  useEffect(() => {
    const p = readFragment();
    if (p) {
      setPayload(p);
      // Clear the fragment so a refresh doesn't re-submit a stale grid.
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, []);

  useEffect(() => {
    if (payload) {
      setApplied(false);
      setApplyKey("");
      submit(payload);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  const submit = async (p) => {
    setBusy(true);
    setErr(null);
    try {
      setResult(
        await api.verifySeatsFromGrid({
          rows: p.rows,
          chain: p.chain || "unknown",
          theater_id: p.theater_id || "single",
          seats_together: form.seats_together,
          min_row: form.min_row,
          strategy: p.strategy,
          source_url: p.source_url,
        }),
      );
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitPasted = () => {
    try {
      const p = JSON.parse(pasted);
      if (!p.rows?.length) throw new Error("No rows in that payload");
      setPayload(p);
    } catch (e) {
      setErr("Could not read that clipboard payload: " + e.message);
    }
  };

  // Only offer showtimes the grid could plausibly belong to. The chain is inferred
  // server-side from the seat page's own URL, so it is more trustworthy than
  // anything the page could claim.
  const inferredChain = result?.stats?.chain;
  const inferredTheater = result?.stats?.theater_id;
  const candidates = showtimes.filter((st) => {
    if (inferredTheater && inferredTheater !== "single") return st.theater_id === inferredTheater;
    if (inferredChain && inferredChain !== "unknown") return st.chain === inferredChain;
    return true;
  });

  const apply = () => {
    const st = candidates.find((s) => s.key === applyKey);
    if (!st || !result?.seat_check) return;
    onApply?.(st.key, result.seat_check);
    setApplied(true);
  };

  const setupUrl =
    (import.meta.env.VITE_API_BASE || "") +
    "/api/seat-bookmarklet/setup?app_url=" +
    encodeURIComponent(window.location.origin + window.location.pathname);

  const seat = result?.seat_check;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <ScanSearch className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">Check seats from your browser</h2>
        <Badge tone="blue">works for every chain</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        The server can only read AMC's seat map — Regal's is behind a CAPTCHA and
        Cinemark's robots.txt disallows theirs. Opening the seat page yourself gets
        around neither restriction by trickery; it simply isn't automated access.
        {" "}
        <a className="underline" href={setupUrl} target="_blank" rel="noreferrer">
          Install the bookmarklet
        </a>
        , open a showtime's seat-selection step, and click it.
      </p>

      {busy && <p className="mt-3 text-xs text-muted-foreground">Reading grid…</p>}
      {err && <p className="mt-3 text-xs text-red-300">{err}</p>}

      {seat && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {seat.status === "match" ? (
              <Badge tone="green">
                <CheckCircle2 className="h-3.5 w-3.5" /> {seat.best_block_size} together
              </Badge>
            ) : seat.status === "no_match" ? (
              <Badge tone="red">
                <XCircle className="h-3.5 w-3.5" /> No {seat.seats_together_requested}-block
              </Badge>
            ) : (
              <Badge tone="yellow">
                <HelpCircle className="h-3.5 w-3.5" /> Check manually
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">
              {result.stats?.available_found} of {result.stats?.seats_found} seats free ·{" "}
              {result.stats?.rows_found} rows
              {result.stats?.strategy ? ` · read via ${result.stats.strategy}` : ""}
            </span>
          </div>
          {seat.best_block_row?.physical_row && (
            <p className="text-xs text-muted-foreground">
              Best block at physical row {seat.best_block_row.physical_row} (needed{" "}
              {seat.min_row_requested}+).
            </p>
          )}
          <div className="overflow-x-auto rounded-md border border-border/60 bg-background/40 p-2">
            <div className="mb-1 text-center text-[10px] uppercase tracking-widest text-muted-foreground">
              screen
            </div>
            <div className="space-y-0.5">
              {result.grid.map((row, i) => (
                <SeatRow key={i} row={row} minRow={form.min_row} />
              ))}
            </div>
          </div>
          {result.stats?.caution ? (
            <p className="text-xs text-amber-300/90">{result.stats.caution}</p>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              Compare this against the map on screen — it was read from the rendered
              page, not from a documented API.
            </p>
          )}

          {candidates.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
              <label className="text-xs text-muted-foreground" htmlFor="apply-to">
                Apply to
              </label>
              <select
                id="apply-to"
                className="max-w-[22rem] rounded-md border border-border/60 bg-background/60 px-2 py-1 text-xs"
                value={applyKey}
                onChange={(e) => {
                  setApplyKey(e.target.value);
                  setApplied(false);
                }}
              >
                <option value="">Choose a showtime…</option>
                {candidates.map((st) => (
                  <option key={st.key} value={st.key}>
                    {st.start_datetime.slice(5, 10)} {st.start_time_label} · {st.format} ·{" "}
                    {st.theater_name}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="outline" onClick={apply} disabled={!applyKey || applied}>
                {applied ? "Applied" : "Apply"}
              </Button>
              {applied && (
                <span className="text-xs text-emerald-300">
                  That showtime's badge now reflects this grid.
                </span>
              )}
            </div>
          )}
          {result && candidates.length === 0 && showtimes.length > 0 && (
            <p className="text-[11px] text-amber-300/80">
              None of the current results are at{" "}
              {inferredTheater && inferredTheater !== "single"
                ? inferredTheater
                : inferredChain || "this chain"}
              , so there is nothing to attach this to. Search that theater first.
            </p>
          )}
        </div>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Paste the grid instead
        </summary>
        <div className="mt-2 flex flex-col gap-2">
          <textarea
            className="h-20 w-full rounded-md border border-border/60 bg-background/40 p-2 font-mono text-[11px]"
            placeholder="Paste the clipboard payload the bookmarklet copied"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
          />
          <Button size="sm" variant="outline" onClick={submitPasted} disabled={!pasted.trim()}>
            <ClipboardPaste className="h-3.5 w-3.5" /> Use pasted grid
          </Button>
        </div>
      </details>
    </Card>
  );
}
