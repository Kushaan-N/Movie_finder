import { MapPin, Ticket, CheckCircle2, HelpCircle, XCircle, ArmchairIcon } from "lucide-react";
import { Card, Badge, Button } from "@/components/ui/primitives";

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

export default function Results({ result }) {
  if (!result) return null;
  const { meta, showtimes } = result;

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
                    <div
                      key={st.key}
                      className={
                        "flex flex-col gap-2 rounded-lg border p-3 transition-colors " +
                        (st.is_new
                          ? "border-primary/50 bg-primary/10"
                          : "border-border/60 bg-background/30")
                      }
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-lg font-semibold tabular-nums">
                            {st.start_time_label}
                          </span>
                          <Badge tone={formatBadgeTone(st.format)}>{st.format}</Badge>
                          {st.is_new && <Badge tone="new">NEW</Badge>}
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        <SeatBadge seat={st.seat_check} />
                        {st.seat_check.best_block_row?.display && (
                          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                            <ArmchairIcon className="h-3.5 w-3.5" />
                            {st.seat_check.best_block_row.display}
                          </span>
                        )}
                      </div>

                      <div className="mt-1">
                        <a href={st.booking_url || "#"} target="_blank" rel="noreferrer">
                          <Button size="sm" variant="subtle" className="w-full sm:w-auto">
                            <Ticket className="h-3.5 w-3.5" /> Book
                          </Button>
                        </a>
                      </div>
                    </div>
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
