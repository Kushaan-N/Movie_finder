import { useState } from "react";
import {
  MapPin,
  Ticket,
  CheckCircle2,
  HelpCircle,
  XCircle,
  ArmchairIcon,
  ScanSearch,
  Loader2,
} from "lucide-react";
import { Card, Badge, Button } from "@/components/ui/primitives";
import { api } from "@/lib/api";

function formatBadgeTone(fmt) {
  const f = (fmt || "").toLowerCase();
  if (f.includes("imax")) return "blue";
  if (f.includes("dolby") || f.includes("xd") || f.includes("screenx")) return "new";
  return "default";
}

function SeatBadge({ seat }) {
  const s = seat.status;
  if (s === "match") {
    return (
      <Badge tone="green">
        <CheckCircle2 className="h-3.5 w-3.5" /> {seat.best_block_size} together
      </Badge>
    );
  }
  if (s === "check_manually") {
    return (
      <Badge tone="yellow">
        <HelpCircle className="h-3.5 w-3.5" /> Check manually
      </Badge>
    );
  }
  return (
    <Badge tone="red">
      <XCircle className="h-3.5 w-3.5" /> No {seat.seats_together_requested}-block
    </Badge>
  );
}

// Compact seat grid preview (rows top-to-bottom = screen to back).
function SeatGrid({ grid, minRow }) {
  if (!grid?.length) return null;
  return (
    <div className="mt-2 overflow-x-auto rounded-md border border-border/60 bg-background/40 p-2">
      <div className="mb-1 text-center text-[10px] uppercase tracking-widest text-muted-foreground">
        screen
      </div>
      <div className="space-y-0.5">
        {grid.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <span
              className={
                "w-24 shrink-0 text-right text-[11px] tabular-nums " +
                (row.physical_row >= minRow ? "text-foreground" : "text-muted-foreground/50")
              }
            >
              {row.raw_label ? `${row.raw_label} → ` : ""}row {row.physical_row}
            </span>
            <div className="flex gap-0.5">
              {row.seats_available.map((a, j) => (
                <span
                  key={j}
                  className={
                    "h-3 w-3 rounded-[3px] " +
                    (a ? "bg-emerald-400/80" : "bg-muted-foreground/25")
                  }
                  title={a ? "available" : "unavailable"}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ShowtimeCard({ st, canVerify }) {
  const [verified, setVerified] = useState(null); // { seat_check, grid, reason, available }
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const seat = verified?.seat_check || st.seat_check;
  // The seat page is resolved server-side from theater + start time, so a
  // booking_url is no longer required to offer verification (providers only give
  // google.com links anyway).
  const showVerifyBtn =
    canVerify && !verified && st.seat_check.status === "check_manually";

  const runVerify = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.verifySeats({
        chain: st.chain,
        theater_id: st.theater_id,
        start_datetime: st.start_datetime,
        seats_together: st.seat_check.seats_together_requested,
        min_row: st.seat_check.min_row_requested,
      });
      setVerified(res);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={
        "flex flex-col gap-2 rounded-lg border p-3 transition-colors " +
        (st.is_new ? "border-primary/50 bg-primary/10" : "border-border/60 bg-background/30")
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold tabular-nums">{st.start_time_label}</span>
        <Badge tone={formatBadgeTone(st.format)}>{st.format}</Badge>
        {st.is_new && <Badge tone="new">NEW</Badge>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SeatBadge seat={seat} />
        {seat.best_block_row?.display && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <ArmchairIcon className="h-3.5 w-3.5" />
            {seat.best_block_row.display}
          </span>
        )}
      </div>

      {verified?.grid?.length ? (
        <SeatGrid grid={verified.grid} minRow={st.seat_check.min_row_requested} />
      ) : null}
      {verified && !verified.grid?.length && verified.reason && (
        <p className="text-xs text-amber-300/80">{verified.reason}</p>
      )}
      {err && <p className="text-xs text-red-300">{err}</p>}

      <div className="mt-1 flex flex-wrap gap-2">
        <a href={st.booking_url || "#"} target="_blank" rel="noreferrer">
          <Button size="sm" variant="subtle">
            <Ticket className="h-3.5 w-3.5" /> Book
          </Button>
        </a>
        {showVerifyBtn && (
          <Button size="sm" variant="outline" onClick={runVerify} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ScanSearch className="h-3.5 w-3.5" />}
            {busy ? "Checking…" : "Check seats"}
          </Button>
        )}
      </div>
    </div>
  );
}

function groupByTheaterThenDate(showtimes) {
  const byTheater = new Map();
  for (const st of showtimes) {
    if (!byTheater.has(st.theater_id)) {
      byTheater.set(st.theater_id, { theater: st, dates: new Map() });
    }
    const day = st.start_datetime.slice(0, 10);
    const group = byTheater.get(st.theater_id);
    if (!group.dates.has(day)) group.dates.set(day, []);
    group.dates.get(day).push(st);
  }
  return byTheater;
}

function prettyDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export default function Results({ result, config }) {
  if (!result) return null;
  const { meta, showtimes } = result;

  // On-demand verification is offered only when the server can do it AND the
  // results are real (never for synthetic demo booking URLs).
  const verifyFor = (chain) =>
    !!config?.seat_verification &&
    meta.provider_used !== "demo" &&
    (config?.verify_chains || []).includes(chain);

  if (!showtimes.length) {
    return (
      <Card className="p-8 text-center text-muted-foreground">
        <p className="text-base font-medium">No showtimes matched your criteria.</p>
        {meta.notes?.map((n, i) => (
          <p key={i} className="mt-2 text-sm">
            {n}
          </p>
        ))}
      </Card>
    );
  }

  const grouped = groupByTheaterThenDate(showtimes);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Badge tone={meta.provider_used === "demo" ? "yellow" : "blue"}>
          source: {meta.provider_used}
        </Badge>
        <span>
          {meta.showtimes_returned} showtimes · {grouped.size} theaters
        </span>
      </div>

      {meta.notes?.length ? (
        <Card className="border-amber-500/30 bg-amber-500/5 p-3 text-sm text-amber-200/90">
          {meta.notes.map((n, i) => (
            <p key={i}>{n}</p>
          ))}
        </Card>
      ) : null}

      {[...grouped.values()].map(({ theater, dates }) => (
        <Card key={theater.theater_id} className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 bg-background/40 px-5 py-4">
            <div>
              <h3 className="text-base font-semibold">{theater.theater_name}</h3>
              <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                <MapPin className="h-3.5 w-3.5" />
                {theater.address || theater.chain}
                {theater.distance_miles != null && (
                  <span className="ml-1">· {theater.distance_miles} mi</span>
                )}
              </p>
            </div>
            <Badge tone="default" className="uppercase">
              {theater.chain}
            </Badge>
          </div>

          <div className="divide-y divide-border/50">
            {[...dates.entries()].map(([day, times]) => (
              <div key={day} className="px-5 py-4">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {prettyDate(day)}
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  {times.map((st) => (
                    <ShowtimeCard key={st.key} st={st} canVerify={verifyFor(st.chain)} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
