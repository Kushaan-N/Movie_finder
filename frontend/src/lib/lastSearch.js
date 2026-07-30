/*
 * Persist the most recent search so it survives a page load.
 *
 * This exists because of how the browser-assisted seat check works: the
 * bookmarklet hands its grid over by opening the app in a NEW tab, so the app
 * mounts fresh with no results. Without this, the panel had nothing to attach a
 * verdict to and the "Apply to this showtime" step could never appear — measured
 * end-to-end against AMC's live seat page.
 *
 * localStorage rather than sessionStorage precisely because sessionStorage is
 * per-tab and the handoff arrives in a different tab. Entries expire so a grid is
 * never attached to a search from days ago.
 */

const KEY = "showtime-finder:last-search";
const TTL_MS = 2 * 60 * 60 * 1000; // 2 hours

export function saveLastSearch(form, result) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ at: Date.now(), form, result }));
  } catch {
    // Quota or private mode — persistence is a convenience, never required.
  }
}

export function loadLastSearch() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (!saved?.at || Date.now() - saved.at > TTL_MS) {
      localStorage.removeItem(KEY);
      return null;
    }
    if (!saved.result?.showtimes) return null;
    return saved;
  } catch {
    return null;
  }
}

export function clearLastSearch() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to do */
  }
}
