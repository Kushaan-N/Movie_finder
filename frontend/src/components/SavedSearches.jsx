import { Play, Trash2, Bookmark } from "lucide-react";
import { Card, Button, Badge } from "@/components/ui/primitives";

export default function SavedSearches({ items, onRun, onDelete, onLoad, activeId }) {
  if (!items?.length) {
    return (
      <Card className="p-4 text-sm text-muted-foreground">
        <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
          <Bookmark className="h-4 w-4 text-primary" /> Saved searches
        </div>
        Save a search configuration to re-run it in one tap.
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <Bookmark className="h-4 w-4 text-primary" /> Saved searches
      </div>
      <div className="space-y-2">
        {items.map((s) => (
          <div
            key={s.id}
            className={
              "flex items-center justify-between gap-2 rounded-lg border p-2.5 " +
              (activeId === s.id ? "border-primary/50 bg-primary/10" : "border-border/60")
            }
          >
            <button
              onClick={() => onLoad(s)}
              className="min-w-0 flex-1 text-left"
              title="Load into the form (does not search)"
              aria-label={`Load saved search "${s.name}" into the form`}
            >
              <div className="truncate text-sm font-medium">{s.name}</div>
              <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className="truncate">
                  {s.config.movie_title || "any movie"} ·{" "}
                  {(s.config.formats?.length ? s.config.formats : [s.config.format || "Any"]).join(", ")} ·{" "}
                  {s.config.seats_together} together · row {s.config.min_row}+
                </span>
              </div>
            </button>
            <div className="flex shrink-0 items-center gap-1">
              {/* aria-label, not just title: an icon-only button has no text for a
                  screen reader to announce, and `title` is not a reliable
                  substitute. */}
              <Button
                size="icon"
                variant="ghost"
                onClick={() => onRun(s.id)}
                title={`Re-run "${s.name}" and highlight what's new`}
                aria-label={`Re-run saved search "${s.name}"`}
              >
                <Play className="h-4 w-4" />
              </Button>
              {/* Confirm first: this is destructive, irreversible, and sits one
                  icon away from Run. */}
              <Button
                size="icon"
                variant="ghost"
                onClick={() => {
                  if (window.confirm(`Delete the saved search "${s.name}"? This can't be undone.`)) {
                    onDelete(s.id);
                  }
                }}
                title={`Delete "${s.name}"`}
                aria-label={`Delete saved search "${s.name}"`}
              >
                <Trash2 className="h-4 w-4 text-red-400" />
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
