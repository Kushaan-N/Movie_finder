import { useState } from "react";
import { Search, MapPin, Calendar, Clock, Users, Film, Sparkles } from "lucide-react";
import { Card, Button, Input, Select, Label, Stepper, Switch } from "@/components/ui/primitives";

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

  return (
    <Card className="p-5 sm:p-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSearch();
        }}
        className="space-y-5"
      >
        {/* Movie + format */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Label hint="required">
              <Film className="h-4 w-4 text-primary" /> Movie title
            </Label>
            <Input
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
          <div>
            <Label>
              <Sparkles className="h-4 w-4 text-primary" /> Format
            </Label>
            <Select value={value.format} onChange={(e) => set({ format: e.target.value })}>
              {(formats || ["Any", "IMAX", "Dolby", "Standard"]).map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {/* Location + radius */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label>
              <MapPin className="h-4 w-4 text-primary" /> Location
            </Label>
            <Input
              placeholder="Address or ZIP (e.g. 94103)"
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
                value={value.time_rule.weekday_cutoff}
                onChange={(e) => setRule({ weekday_cutoff: e.target.value })}
              />
            </div>
            <div className="flex items-end justify-between gap-3 pb-2">
              <Label className="mb-0">Weekends unrestricted</Label>
              <Switch
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
                value={value.seats_together}
                onChange={(v) => set({ seats_together: v })}
                min={1}
                max={20}
              />
            </div>
            <div>
              <Label>Minimum row</Label>
              <Stepper value={value.min_row} onChange={(v) => set({ min_row: v })} min={1} max={40} />
              <p className="mt-1.5 text-xs text-muted-foreground">
                Physical position from the screen — e.g. 5 = row 5 or further back (not the label;
                see row normalization).
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
