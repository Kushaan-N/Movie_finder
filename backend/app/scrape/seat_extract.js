/*
 * Chain-agnostic seat-map extractor, designed to run in a page the USER opened.
 *
 * Why generic: the three chains are wildly different, and two of them cannot be
 * inspected ahead of time — Regal's seat page is behind a CAPTCHA and Cinemark's
 * is robots-disallowed to automation — so this cannot rely on per-chain selectors
 * that were verified in advance. It tries several independent strategies and
 * reports which one fired, plus the grid it read, so the result can be eyeballed
 * against the map on screen instead of trusted blindly.
 *
 * Rows always come from y-geometry rather than row containers: clustering seat
 * boxes by vertical position is the one thing every seat map has in common, and
 * it yields physical order (screen-first) directly — which is exactly what
 * min_row means. Aisles are detected from anomalous x-spacing so a contiguous
 * run cannot span one.
 *
 * Strategies, most trustworthy first:
 *   attr        explicit availability attribute/class (Cinemark: available="True")
 *   interactive seat is a live control; taken seats are disabled/aria-disabled
 *   paint       availability encoded only visually (AMC: gradient stop-colors).
 *               Which colour means "free" is decided by saturation — free seats
 *               are drawn saturated, taken ones grey/outline/transparent — or by
 *               an explicit palette passed in for a known chain.
 *
 * Returns: {ok, strategy, rows:[[{available,gap}]], stats:{...}, reason}
 * Never guesses: if no strategy produces a plausible auditorium it returns
 * ok:false with a reason, and the caller keeps "check manually".
 *
 * This file is a bare function EXPRESSION (no trailing semicolon) so every
 * consumer can invoke it directly -- page.evaluate("(<src>)({})") from Playwright,
 * or inlined into a bookmarklet. Keep it that way.
 */
(function (options) {
  options = options || {};
  var MIN_SEATS = options.minSeats || 20;
  var MIN_ROWS = options.minRows || 3;
  var SEAT_MIN = options.seatMinPx || 8;
  var SEAT_MAX = options.seatMaxPx || 80;
  var ROW_TOL = options.rowTolerancePx || 12;
  var AISLE = options.aisleGapFactor || 1.8;
  var palette = options.availableColors || null; // optional known-chain hint
  // Hard caps so a huge page cannot hang the renderer.
  var MAX_SCAN = options.maxScan || 60000;
  var MAX_CANDIDATES = options.maxCandidates || 4000;

  var AVAIL_TOKENS = ['available', 'open', 'unsold', 'sellable', 'free', 'true'];
  var TAKEN_TOKENS = ['unavailable', 'taken', 'sold', 'occupied', 'held', 'broken', 'false'];
  var IGNORE_TOKENS = ['space', 'aisle', 'spacer', 'blank', 'placeholder', 'wheelchair', 'companion'];

  function norm(s) { return String(s == null ? '' : s).trim().toLowerCase(); }
  function hasAny(hay, toks) {
    var h = norm(hay);
    for (var i = 0; i < toks.length; i++) if (h.indexOf(toks[i]) !== -1) return true;
    return false;
  }

  // --- candidate collection ------------------------------------------------ //
  // Seat-sized, laid-out elements. Deliberately broad; strategies filter further.
  //
  // Performance matters here: real seat pages are megabytes with tens of
  // thousands of nodes, so this stays a single linear pass. Anything per-candidate
  // that walks a subtree (querySelectorAll) would make it quadratic and hang the
  // renderer — measured on AMC's page, which froze the tab.
  function candidates() {
    var all = document.getElementsByTagName('*');
    var n = Math.min(all.length, MAX_SCAN);
    var out = [];
    for (var i = 0; i < n; i++) {
      var el = all[i];
      // Seats are leaves or near-leaves; skip containers cheaply and without
      // touching their subtrees.
      if (el.childElementCount > 4) continue;
      var r = el.getBoundingClientRect();
      if (r.width < SEAT_MIN || r.width > SEAT_MAX) continue;
      if (r.height < SEAT_MIN || r.height > SEAT_MAX) continue;
      if (r.width === 0 || r.height === 0) continue;
      out.push({ el: el, x: r.x + r.width / 2, y: r.y + r.height / 2,
                 w: r.width, h: r.height, area: r.width * r.height });
      if (out.length >= MAX_CANDIDATES) break;
    }
    return dedupe(out);
  }

  // One seat is usually several nested seat-sized boxes (a wrapper, its <svg>,
  // and the <path> inside). Measured on AMC: 822 candidates for 186 real seats,
  // which inflated every row ~4x and doubled the available count. Keeping one
  // element per screen position fixes that generically, without knowing the
  // chain's markup: walk largest-first and skip anything centred on a box we
  // already kept.
  function dedupe(cands) {
    cands.sort(function (a, b) { return b.area - a.area; });
    var kept = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i], tol = Math.max(4, Math.min(c.w, c.h) * 0.6), dup = false;
      for (var j = 0; j < kept.length; j++) {
        if (Math.abs(kept[j].x - c.x) < tol && Math.abs(kept[j].y - c.y) < tol) { dup = true; break; }
      }
      if (!dup) kept.push(c);
    }
    return kept;
  }

  function descriptor(el) {
    // Everything an availability decision might reasonably be encoded in.
    var bits = [el.className && el.className.toString ? el.className.toString() : '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('title') || ''];
    var attrs = el.attributes;
    for (var i = 0; i < attrs.length; i++) {
      var n = attrs[i].name;
      if (/avail|status|state|seat|sold|taken/i.test(n)) bits.push(n + '=' + attrs[i].value);
    }
    return bits.join(' ');
  }

  // --- strategy: explicit attribute / class -------------------------------- //
  function byAttr(cands) {
    var seats = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i], d = descriptor(c.el);
      if (!d.trim()) continue;
      if (hasAny(d, IGNORE_TOKENS)) { seats.push({ x: c.x, y: c.y, gap: true }); continue; }
      var taken = hasAny(d, TAKEN_TOKENS);
      var avail = hasAny(d, AVAIL_TOKENS);
      if (!taken && !avail) continue;
      // "unavailable" contains "available", so taken wins ties.
      seats.push({ x: c.x, y: c.y, available: taken ? false : true });
    }
    return seats;
  }

  // --- strategy: interactive control --------------------------------------- //
  function byInteractive(cands) {
    var seats = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i], el = c.el;
      var isCtl = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' ||
                  el.tagName === 'INPUT' || el.hasAttribute('aria-disabled');
      if (!isCtl) continue;
      var d = descriptor(el);
      if (hasAny(d, IGNORE_TOKENS)) { seats.push({ x: c.x, y: c.y, gap: true }); continue; }
      var disabled = el.disabled === true || norm(el.getAttribute('aria-disabled')) === 'true';
      seats.push({ x: c.x, y: c.y, available: !disabled });
    }
    return seats;
  }

  // --- strategy: paint (colour only) -------------------------------------- //
  function parseColor(c) {
    c = norm(c);
    if (!c || c === 'none' || c === 'transparent') return null;
    var m = c.match(/rgba?\(([^)]+)\)/);
    if (m) {
      var p = m[1].split(',').map(function (v) { return parseFloat(v); });
      if (p.length > 3 && p[3] === 0) return null; // fully transparent
      return { r: p[0], g: p[1], b: p[2] };
    }
    m = c.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/);
    if (m) {
      var h = m[1];
      if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
      return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
    }
    return null;
  }

  function saturation(rgb) {
    if (!rgb) return -1;
    var mx = Math.max(rgb.r, rgb.g, rgb.b), mn = Math.min(rgb.r, rgb.g, rgb.b);
    if (mx === 0) return 0;
    return (mx - mn) / mx;
  }

  // Resolve an element's paint: an svg seat's gradient stops, else its own fill
  // or background colour.
  function paintOf(el) {
    var svg = el.tagName === 'svg' ? el
            : (el.firstElementChild && el.firstElementChild.tagName === 'svg' ? el.firstElementChild : null);
    var path = (svg && svg.querySelector) ? svg.querySelector('path,rect,circle,polygon') : null;
    var target = path || el;
    var fill = target.getAttribute && target.getAttribute('fill');
    if (fill) {
      var m = String(fill).match(/url\(#(.+)\)/);
      if (m) {
        var grad = null;
        try { grad = (svg || document).querySelector('#' + CSS.escape(m[1])); } catch (e) {}
        grad = grad || document.getElementById(m[1]);
        if (!grad) return null;
        var stops = grad.querySelectorAll('stop'), best = null;
        for (var i = 0; i < stops.length; i++) {
          var rgb = parseColor(stops[i].getAttribute('stop-color'));
          if (rgb && saturation(rgb) > saturation(best)) best = rgb;
        }
        return best; // null when every stop is transparent
      }
      return parseColor(fill);
    }
    var cs = window.getComputedStyle(el);
    return parseColor(cs.backgroundColor) || parseColor(cs.borderColor);
  }

  function byPaint(cands) {
    var read = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      read.push({ x: c.x, y: c.y, rgb: paintOf(c.el) });
    }
    if (!read.length) return [];

    var isAvail;
    if (palette && palette.length) {
      var want = palette.map(norm);
      isAvail = function (rgb, el) {
        if (!rgb) return false;
        var hex = '#' + [rgb.r, rgb.g, rgb.b].map(function (v) {
          var s = Math.round(v).toString(16); return s.length === 1 ? '0' + s : s;
        }).join('');
        return want.indexOf(hex) !== -1;
      };
    } else {
      // Unknown chain: free seats are drawn saturated, taken ones grey/outline.
      // Split on the midpoint between the two saturation clusters.
      var sats = read.map(function (s) { return saturation(s.rgb); })
                     .filter(function (v) { return v >= 0; }).sort(function (a, b) { return a - b; });
      if (!sats.length) return [];
      var lo = sats[0], hi = sats[sats.length - 1];
      if (hi - lo < 0.15) return []; // one visual state only — can't tell them apart
      var mid = (lo + hi) / 2;
      isAvail = function (rgb) { return saturation(rgb) > mid; };
    }
    return read.map(function (s) { return { x: s.x, y: s.y, available: !!isAvail(s.rgb) }; });
  }

  // --- shared: cluster into rows ------------------------------------------ //
  function toRows(seats) {
    seats = seats.slice().sort(function (a, b) { return a.y - b.y; });
    var rows = [], cur = [];
    for (var i = 0; i < seats.length; i++) {
      if (cur.length && Math.abs(seats[i].y - cur[cur.length - 1].y) > ROW_TOL) {
        rows.push(cur); cur = [];
      }
      cur.push(seats[i]);
    }
    if (cur.length) rows.push(cur);

    var out = [];
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r].sort(function (a, b) { return a.x - b.x; });
      var real = row.filter(function (s) { return !s.gap; });
      if (!real.length) continue; // page chrome, not a seat row
      var deltas = [];
      for (var k = 1; k < row.length; k++) deltas.push(row[k].x - row[k - 1].x);
      deltas.sort(function (a, b) { return a - b; });
      var pitch = deltas.length ? deltas[Math.floor(deltas.length / 2)] : 0;
      var cells = [];
      for (var j = 0; j < row.length; j++) {
        if (j > 0 && pitch > 0 && (row[j].x - row[j - 1].x) > pitch * AISLE) {
          cells.push({ available: false, gap: true });
        }
        cells.push({ available: !!row[j].available, gap: !!row[j].gap });
      }
      out.push(cells);
    }
    return out;
  }

  function score(rows) {
    var seats = 0;
    for (var i = 0; i < rows.length; i++)
      for (var j = 0; j < rows[i].length; j++) if (!rows[i][j].gap) seats++;
    return { seats: seats, rows: rows.length };
  }

  // --- run strategies in order of trustworthiness ------------------------- //
  var cands = candidates();
  var attempts = [
    { name: 'attr', fn: byAttr },
    { name: 'interactive', fn: byInteractive },
    { name: 'paint', fn: byPaint }
  ];
  var tried = {};
  for (var a = 0; a < attempts.length; a++) {
    var rows = toRows(attempts[a].fn(cands));
    var s = score(rows);
    tried[attempts[a].name] = s;
    if (s.seats >= MIN_SEATS && s.rows >= MIN_ROWS) {
      var avail = 0;
      for (var i = 0; i < rows.length; i++)
        for (var j = 0; j < rows[i].length; j++) if (rows[i][j].available) avail++;
      return {
        ok: true,
        strategy: attempts[a].name,
        rows: rows,
        stats: {
          seats_found: s.seats, rows_found: s.rows, available_found: avail,
          candidates: cands.length, tried: tried
        },
        url: location.href,
        title: document.title
      };
    }
  }
  return {
    ok: false,
    strategy: null,
    rows: [],
    stats: { candidates: cands.length, tried: tried },
    reason: 'No seat map recognized on this page. Open the seat-selection step ' +
            'for a specific showtime, wait for the seats to draw, then retry.',
    url: location.href,
    title: document.title
  };
})
