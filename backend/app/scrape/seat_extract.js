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
  // Rows narrower than this are not auditorium rows -- see score().
  var MIN_ROW_WIDTH = options.minRowWidth || 4;
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
    return out;
  }

  // One seat is usually several nested seat-sized boxes (a wrapper, its <svg>, and
  // the <path> inside). Measured on AMC: 822 candidates for 186 real seats, which
  // inflated every row ~4x and doubled the available count.
  //
  // Deduping runs per strategy, on the seats a strategy has already classified,
  // NOT on raw candidates. Doing it up front picked whichever nested box was
  // largest -- the unpainted wrapper -- and destroyed the availability signal the
  // paint strategy needed.
  //
  // At a given position the survivor must be the most authoritative reading, not
  // an arbitrary one: an explicit SVG fill (rank 3) beats a computed background
  // (2) beats a bare border (1). Keeping an arbitrary element inverted AMC's map,
  // because a seat wrapper's background happened to sit near the legend's
  // "available" swatch while the real seat was a transparent gradient.
  function dedupeSeats(seats, tol) {
    tol = tol || 6;
    var kept = [];
    for (var i = 0; i < seats.length; i++) {
      var s = seats[i], dup = false;
      for (var j = 0; j < kept.length; j++) {
        if (Math.abs(kept[j].x - s.x) < tol && Math.abs(kept[j].y - s.y) < tol) {
          var better = (kept[j].gap && !s.gap) ||
                       ((s.rank || 0) > (kept[j].rank || 0));
          if (better) kept[j] = s;
          dup = true; break;
        }
      }
      if (!dup) kept.push(s);
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
      if (hasAny(d, IGNORE_TOKENS)) { seats.push({ x: c.x, y: c.y, h: c.h, gap: true }); continue; }
      var taken = hasAny(d, TAKEN_TOKENS);
      var avail = hasAny(d, AVAIL_TOKENS);
      if (!taken && !avail) continue;
      // "unavailable" contains "available", so taken wins ties.
      seats.push({ x: c.x, y: c.y, h: c.h, available: taken ? false : true });
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
      if (hasAny(d, IGNORE_TOKENS)) { seats.push({ x: c.x, y: c.y, h: c.h, gap: true }); continue; }
      var disabled = el.disabled === true || norm(el.getAttribute('aria-disabled')) === 'true';
      seats.push({ x: c.x, y: c.y, h: c.h, available: !disabled });
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

  // Resolve an element's paint.
  //
  // Returns null when the element declares no paint at all — that means "not a
  // seat" and it must be skipped. Returns {rgb: null} when paint IS declared but
  // resolves to transparent, which is how AMC draws a TAKEN seat. Conflating those
  // two loses either the taken seats or picks up every unpainted wrapper.
  function paintOf(el) {
    var svg = el.tagName === 'svg' ? el
            : (el.firstElementChild && el.firstElementChild.tagName === 'svg' ? el.firstElementChild : null);
    var path = (svg && svg.querySelector) ? svg.querySelector('path,rect,circle,polygon') : null;
    var target = path || el;
    var fill = target.getAttribute && target.getAttribute('fill');
    if (fill) {
      var f = norm(fill);
      // An explicit "none" is decoration (icons, the screen arc), not a seat.
      if (f === 'none' || f === 'currentcolor') return null;
      // Match the gradient id on the RAW value: element ids are case-sensitive and
      // React generates them with capitals (":R2339l9fjsqv7qbseja:"). Matching the
      // lowercased string made every AMC seat unresolvable, which silently pushed
      // classification onto wrapper backgrounds and inverted the whole map.
      var m = String(fill).match(/url\(#(.+?)\)/);
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
        return { rgb: best, rank: 3 }; // rgb null == all stops transparent == taken
      }
      return { rgb: parseColor(fill), rank: 3 };
    }
    var cs = window.getComputedStyle(el);
    var bg = parseColor(cs.backgroundColor);
    if (bg) return { rgb: bg, rank: 2 };
    // A visible border with no fill is still a drawn seat (outline = taken).
    var bw = parseFloat(cs.borderTopWidth || '0');
    if (bw > 0 && parseColor(cs.borderTopColor)) return { rgb: null, rank: 1 };
    return null;
  }

  var lastPaintSource = null;

  function dist(a, b) {
    if (!a || !b) return Infinity;
    var dr = a.r - b.r, dg = a.g - b.g, db = a.b - b.b;
    return Math.sqrt(dr * dr + dg * dg + db * db);
  }

  // Read the map's own legend instead of guessing what a colour means.
  //
  // Seat maps label their states in plain text ("Available", "Occupied",
  // "Selected") next to a swatch drawn in the same colour as the seats. Verified
  // present on AMC. Using it turns colour classification from a heuristic into
  // something the page itself asserts, and it generalizes to any chain that ships
  // a legend — which is effectively all of them, since human viewers need one too.
  function legendPalette() {
    var STATES = { available: 'avail', open: 'avail', 'seats available': 'avail',
                   occupied: 'taken', sold: 'taken', unavailable: 'taken',
                   taken: 'taken', 'sold out': 'taken' };
    // Single pass: collect the state labels and every possible swatch at once.
    // Scanning the whole document again per label would be quadratic, and paintOf
    // forces style resolution, so that cost is real on a large page.
    var labels = [], swatches = [];
    var all = document.getElementsByTagName('*');
    var n = Math.min(all.length, MAX_SCAN);
    for (var i = 0; i < n; i++) {
      var el = all[i];
      if (el.childElementCount > 4) continue;
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;

      if (el.childElementCount <= 2) {
        var t = norm(el.textContent);
        if (t && t.length <= 20) {
          var key = t.replace(/[:*]+$/, '');
          if (STATES[key]) {
            labels.push({ kind: STATES[key], x: r.x, y: r.y + r.height / 2 });
            continue;
          }
        }
      }
      if (r.width >= 6 && r.width <= 48 && r.height >= 6 && r.height <= 48) {
        swatches.push({ el: el, right: r.x + r.width, y: r.y + r.height / 2 });
      }
    }
    if (!labels.length || !swatches.length) return null;

    // The swatch is the nearest painted box on the label's own line. Both layouts
    // occur: a sibling immediately before the text (AMC) and one nested inside the
    // label element, which puts it to the RIGHT of the label's left edge. So the
    // window spans both directions and the closest wins — that also keeps a legend
    // entry from stealing its neighbour's swatch, since its own is always nearer.
    var out = { avail: [], taken: [] };
    for (var L = 0; L < labels.length; L++) {
      var lab = labels[L], best = null, bestD = Infinity;
      for (var j = 0; j < swatches.length; j++) {
        var sw = swatches[j];
        if (Math.abs(sw.y - lab.y) > 14) continue;
        var dx = lab.x - sw.right;
        if (dx < -60 || dx > 90) continue;
        var d = Math.abs(dx);
        if (d >= bestD) continue;
        var p = paintOf(sw.el);
        if (!p || !p.rgb) continue;
        bestD = d; best = p.rgb;
      }
      if (best) out[lab.kind].push(best);
    }
    if (!out.avail.length) return null;
    return out;
  }

  // Collect paint readings WITHOUT deciding what they mean yet.
  function readPaint(cands) {
    var read = [];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      var p = paintOf(c.el);
      if (!p) continue; // no paint declared -> not a seat
      read.push({ x: c.x, y: c.y, h: c.h, rgb: p.rgb, rank: p.rank });
    }
    return read;
  }

  // Decide which colour means "free", using ONLY the cells that survived row
  // filtering. Deciding earlier let page chrome pollute the distribution: two
  // saturated blue chrome boxes were enough to push the choice from
  // painted-vs-unpainted to saturation and invert an entire map.
  //
  // Returns false when the readings cannot be split, so the caller can decline.
  function decidePaint(rows) {
    var cells = [];
    for (var r = 0; r < rows.length; r++)
      for (var j = 0; j < rows[r].length; j++)
        if (!rows[r][j].gap) cells.push(rows[r][j]);
    if (!cells.length) return false;

    var isAvail, source;
    var legend = palette && palette.length ? null : legendPalette();

    if (palette && palette.length) {
      // Explicit hint for a known chain wins.
      var want = [];
      for (var w = 0; w < palette.length; w++) want.push(parseColor(palette[w]));
      source = 'palette';
      isAvail = function (rgb) {
        for (var k = 0; k < want.length; k++) if (dist(rgb, want[k]) < 40) return true;
        return false;
      };
    } else if (legend) {
      source = 'legend';
      isAvail = function (rgb) {
        if (!rgb) return false;   // unpainted/transparent is never "available"
        var da = Infinity, dt = Infinity, k;
        for (k = 0; k < legend.avail.length; k++) da = Math.min(da, dist(rgb, legend.avail[k]));
        for (k = 0; k < legend.taken.length; k++) dt = Math.min(dt, dist(rgb, legend.taken[k]));
        return da < dt && da < 60;
      };
    } else {
      var painted = 0, unpainted = 0, i2;
      for (i2 = 0; i2 < cells.length; i2++) (cells[i2].rgb ? painted++ : unpainted++);
      var sats = cells.map(function (s) { return saturation(s.rgb); })
                      .filter(function (v) { return v >= 0; }).sort(function (a, b) { return a - b; });
      var spread = sats.length ? sats[sats.length - 1] - sats[0] : 0;

      if (painted >= 3 && unpainted >= 3 && spread < 0.15) {
        // Two states where one is simply "not filled" — how AMC draws a taken seat
        // (a gradient whose stops are all transparent). Saturation cannot split
        // this, because the taken seats have no colour to measure at all.
        source = 'painted-vs-unpainted';
        isAvail = function (rgb) { return !!rgb; };
      } else if (sats.length && spread >= 0.15) {
        // Free seats are drawn saturated, taken ones grey.
        var mid = (sats[0] + sats[sats.length - 1]) / 2;
        source = 'saturation';
        isAvail = function (rgb) { return saturation(rgb) > mid; };
      } else {
        return false; // one visual state only — refuse rather than guess
      }
    }
    for (var c2 = 0; c2 < cells.length; c2++) cells[c2].available = !!isAvail(cells[c2].rgb);
    lastPaintSource = source;
    return true;
  }

  // --- shared: cluster into rows ------------------------------------------ //
  // Group seats into rows by VERTICAL OVERLAP rather than a fixed centre distance.
  // Seats in one row often differ in height (a seat carrying an inline label is
  // taller), which shifts its centre and split single rows in two under a fixed
  // tolerance. Overlap is the standard way to detect a line of boxes and tolerates
  // that naturally.
  function toRows(seats) {
    seats = dedupeSeats(seats.slice()).sort(function (a, b) { return a.y - b.y; });
    var rows = [], cur = [], bandTop = 0, bandBot = 0;
    for (var i = 0; i < seats.length; i++) {
      var s = seats[i], h = s.h || ROW_TOL, top = s.y - h / 2, bot = s.y + h / 2;
      if (!cur.length) {
        cur = [s]; bandTop = top; bandBot = bot; continue;
      }
      var overlap = Math.min(bot, bandBot) - Math.max(top, bandTop);
      if (overlap > 0.4 * Math.min(h, bandBot - bandTop)) {
        cur.push(s);
        bandTop = Math.min(bandTop, top); bandBot = Math.max(bandBot, bot);
      } else {
        rows.push(cur); cur = [s]; bandTop = top; bandBot = bot;
      }
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
        cells.push({ available: !!row[j].available, gap: !!row[j].gap, rgb: row[j].rgb });
      }
      out.push(cells);
    }
    return dropNonSeatRows(out);
  }

  // Discard clusters that aren't auditorium rows.
  //
  // Page chrome sits at seat size too — nav icons, the collapse button, legend
  // swatches — and on AMC it formed two clusters (5 and 1 elements) ABOVE the map.
  // Left in, they shift every physical row number by two and silently corrupt
  // min_row, which is defined as distance from the screen.
  //
  // A real auditorium has broadly uniform rows, so anything far narrower than the
  // typical row is not one. Comparing against the median (not the max) keeps
  // genuinely short front rows.
  function dropNonSeatRows(rows) {
    if (rows.length < 3) return rows;
    var widths = rows.map(function (r) {
      return r.filter(function (c) { return !c.gap; }).length;
    });
    var sorted = widths.slice().sort(function (a, b) { return a - b; });
    var median = sorted[Math.floor(sorted.length / 2)];
    var floor = Math.max(3, Math.round(median * 0.4));
    var kept = [];
    for (var i = 0; i < rows.length; i++) if (widths[i] >= floor) kept.push(rows[i]);
    return kept.length >= MIN_ROWS ? kept : rows;
  }

  function score(rows) {
    var seats = 0, widths = [];
    for (var i = 0; i < rows.length; i++) {
      var w = 0;
      for (var j = 0; j < rows[i].length; j++) if (!rows[i][j].gap) { seats++; w++; }
      widths.push(w);
    }
    widths.sort(function (a, b) { return a - b; });
    return {
      seats: seats,
      rows: rows.length,
      // An auditorium has rows several seats wide. Without this, a strategy that
      // picked up 224 scattered elements scored 224 "rows" of one seat each and
      // still produced a verdict -- measured on AMC Eastridge, where `interactive`
      // beat the correct `paint` reading with that shape.
      medianWidth: widths.length ? widths[Math.floor(widths.length / 2)] : 0,
    };
  }

  // --- run strategies in order of trustworthiness ------------------------- //
  var cands = candidates();
  var attempts = [
    { name: 'attr', fn: byAttr },
    { name: 'interactive', fn: byInteractive },
    // Paint is two-phase: read the colours, cluster and filter rows, and only
    // then decide which colour means "free" — using the surviving seats alone.
    { name: 'paint', fn: readPaint, decide: decidePaint }
  ];
  var tried = {};
  var viable = [];
  for (var a = 0; a < attempts.length; a++) {
    var rows = toRows(attempts[a].fn(cands));
    if (attempts[a].decide && rows.length && !attempts[a].decide(rows)) {
      tried[attempts[a].name] = { seats: 0, rows: 0, undecidable: true };
      continue;
    }
    var s = score(rows), avail = 0;
    s.plausible = s.medianWidth >= MIN_ROW_WIDTH;
    for (var i = 0; i < rows.length; i++)
      for (var j = 0; j < rows[i].length; j++) if (rows[i][j].available) avail++;
    s.available = avail;
    // Did this strategy actually distinguish two states, or did it paint every
    // seat the same? A uniform answer usually means the signal isn't there — most
    // dangerously "everything is free", e.g. a map where selection is JS-driven so
    // no seat carries `disabled`. Real all-free and sold-out auditoriums exist, so
    // this is a preference, not a veto.
    s.discriminates = avail > 0 && avail < s.seats;
    tried[attempts[a].name] = s;
    if (s.seats >= MIN_SEATS && s.rows >= MIN_ROWS && s.plausible) {
      viable.push({ name: attempts[a].name, rows: rows, s: s, colour: lastPaintSource });
    }
  }

  // Prefer, in order: a strategy that distinguished two states, then any that met
  // the size thresholds. Within each group the earlier (more trustworthy) wins.
  var chosen = null;
  for (var v = 0; v < viable.length; v++) if (viable[v].s.discriminates) { chosen = viable[v]; break; }
  if (!chosen && viable.length) chosen = viable[0];

  if (chosen) {
    return {
      ok: true,
      strategy: chosen.name,
      rows: chosen.rows,
      stats: {
        seats_found: chosen.s.seats, rows_found: chosen.s.rows,
        available_found: chosen.s.available, uniform: !chosen.s.discriminates,
        candidates: cands.length, tried: tried, colour_source: chosen.colour
      },
      url: location.href,
      title: document.title
    };
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
