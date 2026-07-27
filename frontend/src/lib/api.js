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
  listSaved: () => req("/api/saved-searches"),
  createSaved: (name, config) =>
    req("/api/saved-searches", { method: "POST", body: JSON.stringify({ name, config }) }),
  runSaved: (id) => req(`/api/saved-searches/${id}/run`, { method: "POST" }),
  deleteSaved: (id) => req(`/api/saved-searches/${id}`, { method: "DELETE" }),
};
