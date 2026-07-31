import { useState } from "react";
import { Search, MapPin, Calendar, Clock, Users, Film, Sparkles } from "lucide-react";
import { Card, Button, Input, Label, Stepper, Switch } from "@/components/ui/primitives";

const MOVIE_SUGGESTIONS = [
  "Dune: Part Two",
  "Oppenheimer",
  "Wicked",
  "Gladiator II",
  "Avatar: Fire and Ash",
];

export default function SearchForm({ value, onChange, onSearch, onSave, formats, loading }) {
  const [saveName, setSaveName] = useState("");
  const [showSave, setShowSave] = useState(false);

  const set = (patch) => onChange({ ...value, ...patch });
  const setRule = (patch) => onChange({ ...value, time_rule: { ...value.time_rule, ...patch } });
  const selectedFormats = value.formats?.length ? value.formats : [value.format || "Any"];
  const toggleFormat = (format) => {
    if (format === "Any") {
      set({ formats: ["Any"], format: "Any" });
      return;
    }
    const withoutAny = selectedFormats.filter((item) => item !== "Any");
    const next = withoutAny.includes(format)
      ? withoutAny.filter((item) => item !== format)
      : [...withoutAny, format];
    const formatsNext = next.length ? next : ["Any"];
    set({ formats: formatsNext, format: formatsNext.length === 1 ? formatsNext[0] : "Any" });
  };

  return (
    <Card className="p-5 sm:p-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSearch();
        }}
        className="space-y-5"
      >
        {/* Movie */}
        <div>
          <Label hint="required">
            <Film className="h-4 w-4 text-primary" /> Movie title
          </Label>
          <Input
            aria-label="Movie title"
            list="movie-suggestions"
            placeholder="e.g. Dune: Part Two"
            value={value.movie_title}
            onChange={(e) => set({ movie_title: e.target.value })}
            required
          />
          <datalist id="movie-suggestions">
            {MOVIE_SUGGESTIONS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>

        {/* Formats */}
        <fieldset className="rounded-lg border border-border/60 bg-background/40 p-4">
          <legend className="px-1 text-sm font-semibold">
            <span className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" /> Formats
            </span>
          </legend>
          <p className="mb-3 text-xs text-muted-foreground">
            Select one or more. Results can match any checked format.
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {(formats || ["Any", "IMAX", "Dolby", "70mm IMAX", "Standard"]).map((format) => {
              const checked = selectedFormats.includes(format);
              return (
                <label
                  key={format}
                  className={
                    "flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors " +
                    (checked
                      ? "border-primary/50 bg-primary/10 text-foreground"
                      : "border-border/60 bg-background/30 text-muted-foreground hover:bg-muted/50")
                  }
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleFormat(format)}
                    className="h-4 w-4 rounded border-input accent-primary"
                  />
                  {format}
                </label>
              );
            })}
          </div>
        </fieldset>

        {/* Location + radius */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label>
              <MapPin className="h-4 w-4 text-primary" /> Location
            </Label>
            <Input
              placeholder="Address or ZIP (e.g. 94103)"
              aria-label="Location — address or ZIP to search around"
            value={value.location}
              onChange={(e) => set({ location: e.target.value })}
              required
            />
          </div>
          <div>
            <Label hint={`${value.radius_miles} mi`}>
              Radius
            </Label>
            <div className="flex h-10 items-center">
              <input
                type="range"
                aria-label={`Search radius in miles (currently ${value.radius_miles})`}
                min={1}
                max={100}
                value={value.radius_miles}
                onChange={(e) => set({ radius_miles: Number(e.target.value) })}
                className="w-full"
              />
            </div>
          </div>
        </div>

        {/* Date range */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label>
              <Calendar className="h-4 w-4 text-primary" /> From
            </Label>
            <Input
              type="date"
              aria-label="Earliest date"
              value={value.date_from}
              onChange={(e) => set({ date_from: e.target.value })}
            />
          </div>
          <div>
            <Label>
              <Calendar className="h-4 w-4 text-primary" /> To
            </Label>
            <Input
              type="date"
              aria-label="Latest date"
              value={value.date_to}
              onChange={(e) => set({ date_to: e.target.value })}
            />
          </div>
        </div>

        {/* Time window rules */}
        <div className="rounded-lg border border-border/60 bg-background/40 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Clock className="h-4 w-4 text-primary" /> Time window
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label hint="weekdays start at/after">Weekday cutoff</Label>
              <Input
                type="time"
                aria-label="Weekday cutoff — weekdays start at or after this time"
                value={value.time_rule.weekday_cutoff}
                onChange={(e) => setRule({ weekday_cutoff: e.target.value })}
              />
            </div>
            <div className="flex items-end justify-between gap-3 pb-2">
              <Label className="mb-0">Weekends unrestricted</Label>
              <Switch
                aria-label="Weekends unrestricted — ignore the cutoff on Saturday and Sunday"
                checked={value.time_rule.weekends_unrestricted}
                onChange={(v) => setRule({ weekends_unrestricted: v })}
              />
            </div>
          </div>
        </div>

        {/* Seat requirements — prominent, editable */}
        <div className="rounded-lg border border-primary/25 bg-primary/5 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Users className="h-4 w-4 text-primary" /> Seat requirements
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <Label>Seats together</Label>
              <Stepper
                label="seats together"
                value={value.seats_together}
                onChange={(v) => set({ seats_together: v })}
                min={1}
                max={20}
              />
            </div>
            <div>
              <Label>Minimum row</Label>
              <Stepper
                label="minimum row"
                value={value.min_row}
                onChange={(v) => set({ min_row: v })}
                min={1}
                max={40}
              />
              <p className="mt-1.5 text-xs text-muted-foreground">
                Counted from the screen, not the printed row letter — 5 means the fifth
                row back or further. A chain that skips letters (AMC has no row I) is
                translated, so "row 5" is the same seat wherever you search.
              </p>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button type="submit" size="lg" className="flex-1" disabled={loading}>
            <Search className="h-4 w-4" />
            {loading ? "Searching…" : "Search showtimes"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="lg"
            onClick={() => setShowSave((s) => !s)}
          >
            Save search
          </Button>
        </div>

        {showSave && (
          <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-background/40 p-3 sm:flex-row">
            <Input
              aria-label="Name for this saved search"
              placeholder='Name it, e.g. "SF/SJ IMAX 4-together row 5+"'
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
            />
            <Button
              type="button"
              onClick={() => {
                if (saveName.trim()) {
                  onSave(saveName.trim());
                  setSaveName("");
                  setShowSave(false);
                }
              }}
            >
              Save
            </Button>
          </div>
        )}
      </form>
    </Card>
  );
}
