// Thin API client. In dev, Vite proxies /api to the backend. In a split-origin
// production deploy, set VITE_API_BASE to the backend URL.
const BASE = import.meta.env.VITE_API_BASE || "";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  getConfig: () => req("/api/config"),
  search: (payload) => req("/api/search", { method: "POST", body: JSON.stringify(payload) }),
  verifySeats: (payload) =>
    req("/api/verify-seats", { method: "POST", body: JSON.stringify(payload) }),
  // Seat grid read by the bookmarklet in the user's own browser -- the only route
  // that reaches every chain (Regal is CAPTCHA-gated, Cinemark robots-disallowed).
  verifySeatsFromGrid: (payload) =>
    req("/api/verify-seats/from-grid", { method: "POST", body: JSON.stringify(payload) }),
  // How full each showing is, from the chains' own listing pages. Deliberately a
  // second call: it costs a page load per theatre and date, so search stays fast
  // and the badges fill in behind it.
  availability: (showtimes) =>
    req("/api/availability", { method: "POST", body: JSON.stringify({ showtimes }) }),
  bookmarklet: (appUrl) =>
    req(`/api/seat-bookmarklet?app_url=${encodeURIComponent(appUrl)}`),
  listSaved: () => req("/api/saved-searches"),
  createSaved: (name, config) =>
    req("/api/saved-searches", { method: "POST", body: JSON.stringify({ name, config }) }),
  runSaved: (id) => req(`/api/saved-searches/${id}/run`, { method: "POST" }),
  deleteSaved: (id) => req(`/api/saved-searches/${id}`, { method: "DELETE" }),
};
