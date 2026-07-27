import { useEffect, useState } from "react";
import { Clapperboard, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";
import { defaultSearch } from "@/lib/defaults";
import SearchForm from "@/components/SearchForm";
import Results from "@/components/Results";
import SavedSearches from "@/components/SavedSearches";

export default function App() {
  const [form, setForm] = useState(defaultSearch());
  const [formats, setFormats] = useState(["Any"]);
  const [result, setResult] = useState(null);
  const [saved, setSaved] = useState([]);
  const [activeSavedId, setActiveSavedId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const flash = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  useEffect(() => {
    api.getConfig().then((c) => setFormats(c.formats)).catch(() => {});
    refreshSaved();
  }, []);

  const refreshSaved = () => api.listSaved().then(setSaved).catch(() => {});

  const doSearch = async () => {
    if (!form.movie_title.trim()) {
      setError("Enter a movie title to search.");
      return;
    }
    setError(null);
    setLoading(true);
    setActiveSavedId(null);
    try {
      setResult(await api.search(form));
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
      setResult(res);
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
    setForm({ ...defaultSearch(), ...s.config });
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

        <Results result={result} />
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
