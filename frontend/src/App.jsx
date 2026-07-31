import { useEffect, useRef, useState } from "react";
import { Clapperboard, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { defaultSearch } from "@/lib/defaults";
import { loadLastSearch, saveLastSearch } from "@/lib/lastSearch";
import SearchForm from "@/components/SearchForm";
import Results from "@/components/Results";
import SavedSearches from "@/components/SavedSearches";
import BrowserSeatCheck from "@/components/BrowserSeatCheck";

export default function App() {
  const [form, setForm] = useState(defaultSearch());
  const [formats, setFormats] = useState(["Any"]);
  const [config, setConfig] = useState(null);
  const [result, setResult] = useState(null);
  const [occupancyBusy, setOccupancyBusy] = useState(false);
  // Why occupancy came back empty, when it did. Surfaced rather than dropped:
  // a page full of "Seats unknown" with no explanation reads as broken.
  const [occupancyNotes, setOccupancyNotes] = useState([]);
  // Guards against a slow occupancy reply landing on a newer search's results.
  const searchToken = useRef(0);
  const [saved, setSaved] = useState([]);
  const [activeSavedId, setActiveSavedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const resultsRef = useRef(null);

  const flash = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setFormats(c.formats);
        setConfig(c);
      })
      .catch(() => {});
    refreshSaved();
    // Restore the last search so a grid handed over by the bookmarklet -- which
    // arrives in a new tab -- still has showtimes to be attached to.
    const last = loadLastSearch();
    if (last) {
      setForm(last.form);
      setResult(last.result);
    }
  }, []);

  const refreshSaved = () => api.listSaved().then(setSaved).catch(() => {});

  // Apply a seat check read in the user's own browser to the showtime it belongs
  // to, so the badge on that card reflects it instead of the verdict sitting off
  // to one side.
  const applySeatCheck = (key, seatCheck) => {
    setResult((prev) =>
      prev
        ? {
            ...prev,
            showtimes: prev.showtimes.map((st) =>
              st.key === key ? { ...st, seat_check: seatCheck } : st,
            ),
          }
        : prev,
    );
  };

  // Fill in how full each showing is, after results are already on screen.
  //
  // This is a second request rather than part of the search because it costs a
  // real page load per theatre and date. Search stays as fast as it was, and
  // "seats unknown" -- which used to be the answer for nearly every card --
  // resolves a few seconds later for the chains whose listings publish it.
  //
  // Best-effort throughout: a chain that rate-limits or hides behind an
  // interstitial simply leaves those cards unknown, exactly as before.
  const fillOccupancy = async (res, token) => {
    const all = res?.showtimes || [];
    if (!all.length) return;

    // One request per date, earliest first.
    //
    // A listing load costs a couple of seconds and the whole set can take the
    // better part of a minute, so asking for everything at once means the badges
    // all sit at "unknown" until the last one lands. Per-date requests fill the
    // top of the page — the dates the user is most likely to want — within a few
    // seconds, and each date's cards update as its answer arrives.
    const byDate = new Map();
    for (const st of all) {
      const day = st.start_datetime.slice(0, 10);
      if (!byDate.has(day)) byDate.set(day, []);
      byDate.get(day).push({
        key: st.key, chain: st.chain, theater_id: st.theater_id,
        movie_title: st.movie_title, start_datetime: st.start_datetime,
      });
    }

    setOccupancyBusy(true);
    setOccupancyNotes([]);
    const notes = new Set();
    try {
      for (const day of [...byDate.keys()].sort()) {
        if (searchToken.current !== token) return;  // a newer search superseded us
        let occupancy, dayNotes;
        try {
          ({ occupancy, notes: dayNotes } = await api.availability(byDate.get(day)));
        } catch {
          continue;  // one bad date shouldn't stop the rest
        }
        for (const n of dayNotes || []) notes.add(n);
        if (searchToken.current === token) setOccupancyNotes([...notes]);
        if (!occupancy || !Object.keys(occupancy).length) continue;
        if (searchToken.current !== token) return;
        setResult((prev) =>
          prev && prev.showtimes
            ? {
                ...prev,
                showtimes: prev.showtimes.map((st) =>
                  occupancy[st.key]
                    ? { ...st, seat_check: { ...st.seat_check, occupancy: occupancy[st.key] } }
                    : st,
                ),
              }
            : prev,
        );
      }
    } finally {
      if (searchToken.current === token) setOccupancyBusy(false);
    }
  };

  const doSearch = async () => {
    if (!form.movie_title.trim()) {
      setError("Enter a movie title to search.");
      return;
    }
    setError(null);
    setLoading(true);
    setActiveSavedId(null);
    try {
      const res = await api.search(form);
      const token = ++searchToken.current;
      setResult(res);
      saveLastSearch(form, res);
      fillOccupancy(res, token);
      requestAnimationFrame(() =>
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const doSave = async (name) => {
    try {
      await api.createSaved(name, form);
      await refreshSaved();
      flash(`Saved “${name}”`);
    } catch (e) {
      setError(e.message);
    }
  };

  const runSaved = async (id) => {
    setError(null);
    setLoading(true);
    setActiveSavedId(id);
    try {
      const res = await api.runSaved(id);
      const token = ++searchToken.current;
      setResult(res);
      saveLastSearch(form, res);
      fillOccupancy(res, token);
      requestAnimationFrame(() =>
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
      await refreshSaved();
      flash(res.new_count > 0 ? `${res.new_count} new showtime(s) since last run` : "Re-ran — no new showtimes");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const deleteSaved = async (id) => {
    await api.deleteSaved(id);
    if (activeSavedId === id) setActiveSavedId(null);
    refreshSaved();
  };

  const loadSaved = (s) => {
    setForm({
      ...defaultSearch(),
      ...s.config,
      formats: s.config.formats?.length ? s.config.formats : [s.config.format || "Any"],
    });
    flash(`Loaded “${s.name}” into the form`);
  };

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:py-10">
      <header className="mb-6 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/15 ring-1 ring-primary/30">
          <Clapperboard className="h-6 w-6 text-primary" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">showtime-finder</h1>
          <p className="text-sm text-muted-foreground">
            Find showtimes that fit your seats, row, and schedule.
          </p>
        </div>
      </header>

      <div className="space-y-6">
        <SearchForm
          value={form}
          onChange={setForm}
          onSearch={doSearch}
          onSave={doSave}
          formats={formats}
          loading={loading}
        />

        <SavedSearches
          items={saved}
          onRun={runSaved}
          onDelete={deleteSaved}
          onLoad={loadSaved}
          activeId={activeSavedId}
        />

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
            <AlertCircle className="h-4 w-4" /> {error}
          </div>
        )}

        <div ref={resultsRef} className="space-y-6">
          {occupancyBusy && (
            <p className="text-xs text-muted-foreground">
              Checking how full each showing is, nearest dates first…
            </p>
          )}
          {!occupancyBusy && occupancyNotes.length > 0 && (
            <div className="rounded-lg border border-border/60 bg-background/40 p-3 text-xs text-muted-foreground">
              {occupancyNotes.map((n, i) => (
                <p key={i}>{n}</p>
              ))}
            </div>
          )}
          <Results result={result} config={config} form={form} />
        </div>

        <BrowserSeatCheck
          form={form}
          showtimes={result?.showtimes || []}
          onApply={applySeatCheck}
        />
      </div>

      {toast && (
        <div className="fixed inset-x-0 bottom-5 z-50 mx-auto w-fit rounded-full border border-border bg-card px-4 py-2 text-sm shadow-lg">
          {toast}
        </div>
      )}

      <footer className="mt-10 text-center text-xs text-muted-foreground">
        v1 · SQLite + FastAPI · single local user (auth-ready schema)
      </footer>
    </div>
  );
}
