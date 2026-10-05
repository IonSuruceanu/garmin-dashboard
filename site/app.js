(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var data = null, rangeDays = 30;
  var $ = function (id) { return document.getElementById(id); };
  var tooltip = $('tooltip');

  // ---------- formatting ----------
  function num(v, d) { return v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d || 0 }); }
  function hm(min) { if (min == null) return '—'; return Math.floor(min / 60) + 'h ' + String(Math.round(min % 60)).padStart(2, '0') + 'm'; }
  function day(iso, opts) { return new Date(iso + 'T12:00:00').toLocaleDateString(undefined, opts || { day: 'numeric', month: 'short' }); }
  function ago(iso) {
    var m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 2) return 'just now'; if (m < 60) return m + ' min ago';
    var h = Math.round(m / 60); if (h < 36) return h + ' h ago';
    return Math.round(h / 24) + ' days ago';
  }
  function avg(rows, key) {
    var v = rows.map(function (r) { return r[key]; }).filter(function (x) { return x != null; });
    return v.length ? v.reduce(function (a, b) { return a + b; }, 0) / v.length : null;
  }
  function latest(rows, key) {
    for (var i = rows.length - 1; i >= 0; i--) if (rows[i][key] != null) return rows[i];
    return null;
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  // ---------- loading ----------
  function load(url) { return fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw r.status; return r.json(); }); }

  load('data/garmin.json').catch(function () {
    return load('data/sample.json').then(function (d) { d.source = 'sample'; return d; });
  }).then(function (d) {
    data = d; render();
  }).catch(function () {
    $('sync-line').textContent = 'No data found.';
    showBanner('No data yet. Run <code>python fetch_garmin.py</code> (or <code>--sample</code> for demo data), then serve this folder with <code>python -m http.server -d site</code>.');
  });

  function showBanner(html) { var b = $('banner'); b.innerHTML = html; b.hidden = false; }

  // ---------- render ----------
  function visibleDays() {
    var rows = data.daily || [];
    return rangeDays ? rows.slice(-rangeDays) : rows;
  }

  function render() {
    var rows = visibleDays();
    $('athlete').textContent = data.athlete || 'Garmin dashboard';
    $('sync-line').textContent = (data.source === 'sample' ? 'Demo data · generated ' : 'Synced from Garmin Connect ') + ago(data.fetchedAt) +
      (rows.length ? ' · ' + day(rows[0].date) + ' – ' + day(rows[rows.length - 1].date) : '');
    if (data.source === 'garmin' && !data.today) showBanner('Your data was fetched with an older version, so <strong>Today, insights and the running analysis</strong> are missing. Run <code>python fetch_garmin.py</code> again, then reload.');
    if (data.source === 'sample') showBanner('You are looking at <strong>demo data</strong>. Run <code>python fetch_garmin.py</code> to load your own Garmin data.');
    renderWearNote(rows);
    renderToday();
    renderTiles(rows);
    renderInsights();
    renderRunning();
    renderCharts(rows);
    renderActivities();
    renderSources();
  }

  function renderTiles(rows) {
    var rhr = latest(rows, 'restingHr'), hrv = latest(rows, 'hrv'), bb = latest(rows, 'bodyBatteryHigh');
    var tiles = [
      { label: 'Avg steps', value: num(avg(rows, 'steps')), note: 'per day' },
      { label: 'Avg sleep', value: hm(avg(rows, 'sleepMin')), note: 'score ' + num(avg(rows, 'sleepScore')) },
      { label: 'Resting HR', value: num(rhr && rhr.restingHr), unit: 'bpm', note: rhr && !rhr.sleepMin ? 'daytime estimate: watch not worn overnight' : 'avg ' + num(avg(rows, 'restingHr')) },
      { label: 'HRV', value: num(hrv && hrv.hrv), unit: 'ms', note: hrv && hrv.hrvStatus ? hrv.hrvStatus.toLowerCase().replace(/_/g, ' ') : 'last night' },
      { label: 'Avg stress', value: num(avg(rows, 'stressAvg')), note: '0–100 scale' },
      { label: 'Body Battery', value: num(bb && bb.bodyBatteryHigh), note: 'latest daily high' }
    ];
    $('tiles').innerHTML = tiles.map(function (t) {
      return '<div class="tile"><span class="tile-label">' + t.label + '</span><span class="tile-value">' + t.value +
        (t.unit && t.value !== '—' ? '<small>' + t.unit + '</small>' : '') + '</span><span class="tile-note">' + esc(t.note) + '</span></div>';
    }).join('');
  }

  // Insights are computed by insights.py when the data is fetched, so they
  // describe the latest days regardless of the range selected above.
  var INSIGHT_TAG = {
    watch: ['Watch', '<path d="M12 8v5M12 16.5v.5"/><path d="M10.3 3.9L2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'],
    good: ['Good', '<path d="M5 12l5 5 9-10"/>'],
    info: ['Note', '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>']
  };
  function renderInsights() {
    var list = data.insights || [];
    $('insights-panel').hidden = !list.length;
    $('insights').innerHTML = list.map(function (i) {
      var tag = INSIGHT_TAG[i.level] || INSIGHT_TAG.info;
      return '<article class="insight ' + esc(i.level) + '"><span class="insight-tag"><svg viewBox="0 0 24 24" aria-hidden="true">' + tag[1] + '</svg>' + tag[0] + '</span>' +
        '<h3>' + esc(i.title) + '</h3><p>' + esc(i.detail) + '</p></article>';
    }).join('');
  }

  // Sleep, HRV and a true resting heart rate all need the watch worn overnight.
  function renderWearNote(rows) {
    var nights = rows.filter(function (r) { return r.sleepMin; }).length;
    var show = rows.length >= 3 && nights < rows.length * 0.3;
    $('wear-note').hidden = !show;
    if (show) $('wear-note').innerHTML = '<strong>Your watch recorded sleep on only ' + nights + ' of ' + rows.length + ' nights.</strong> ' +
      'Wear it overnight to get sleep, HRV and an accurate resting heart rate (without night-time data Garmin estimates resting HR from daytime, so it reads high). ' +
      'Until then, readiness is based mostly on your training.';
  }

  // ---------- today ----------
  var OPT_TAG = { recommended: 'Recommended', good: 'Good option', no: 'Not today' };
  function choiceKey(d) { return 'garmin-choice-' + d; }
  function getChoice(d) { try { return localStorage.getItem(choiceKey(d)); } catch (e) { return null; } }
  function setChoice(d, k) { try { k ? localStorage.setItem(choiceKey(d), k) : localStorage.removeItem(choiceKey(d)); } catch (e) {} }

  function renderToday() {
    var t = data.today;
    $('today-panel').hidden = !t;
    if (!t) return;
    $('today-title').textContent = 'Today · ' + day(t.date, { weekday: 'long', day: 'numeric', month: 'short' });
    $('today-source').textContent = 'Readiness from ' + t.source;
    var effect = { '+': ['plus', '+'], '-': ['minus', '−'], '=': ['same', '·'] };
    $('readiness').className = 'readiness ' + t.level;
    $('readiness').innerHTML =
      '<span class="tile-label">Readiness</span>' +
      '<div class="ready-score">' + t.score + '<small> / 100</small></div>' +
      '<div class="ready-meter" role="img" aria-label="Readiness ' + t.score + ' out of 100"><span style="width:' + t.score + '%"></span></div>' +
      '<span class="ready-label">' + esc(t.label) + '</span>' +
      '<ul class="factors">' + (t.factors || []).map(function (f) {
        var e = effect[f.effect] || effect['='];
        return '<li class="' + e[0] + '"><b aria-hidden="true">' + e[1] + '</b>' + esc(f.text) + '</li>';
      }).join('') + '</ul>';

    var chosen = getChoice(t.date);
    $('options').innerHTML = t.options.map(function (o) {
      return '<button class="option ' + o.status + (o.key === chosen ? ' chosen' : '') + '" data-key="' + o.key + '" aria-pressed="' + (o.key === chosen) + '">' +
        '<span class="opt-tag ' + (o.key === chosen ? 'chosen' : o.status) + '">' + (o.key === chosen ? 'Your choice' : OPT_TAG[o.status]) + '</span>' +
        '<h4>' + esc(o.title) + '</h4><span class="dur">' + esc(o.duration) + '</span>' +
        '<p>' + esc(o.summary) + '</p>' + (o.why ? '<p class="why">' + esc(o.why) + '</p>' : '') + '</button>';
    }).join('');

    var pick = t.options.filter(function (o) { return o.key === chosen; })[0];
    $('choice').hidden = !pick;
    if (pick) {
      $('choice').innerHTML = '<span class="tile-label">Your plan today</span><h3>' + esc(pick.title) + ' · ' + esc(pick.duration) + '</h3>' +
        '<ol>' + pick.steps.map(function (st) { return '<li>' + esc(st) + '</li>'; }).join('') + '</ol>' +
        '<div class="choice-actions">' + (pick.status === 'no' ? '<span class="muted">Heads up: ' + esc(pick.why) + '</span>' : '') +
        '<button class="link-btn" data-clear>Change my choice</button></div>';
    }
  }

  document.addEventListener('click', function (e) {
    if (!data || !data.today) return;
    var opt = e.target.closest('.option');
    if (opt) { setChoice(data.today.date, opt.dataset.key); renderToday(); $('choice').scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
    if (e.target.closest('[data-clear]')) { setChoice(data.today.date, null); renderToday(); }
  });

  // ---------- running ----------
  function pace(p) { if (p == null) return '—'; var t = Math.round(p * 60); return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0'); }
  function raceTime(min) {
    var t = Math.round(min * 60), h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), sec = t % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0');
  }

  function renderRunning() {
    var r = data.running;
    var has = r && r.weekly && r.weekly.some(function (w) { return w.runs; });
    $('running-panel').hidden = !has;
    if (!has) return;
    var p = r.paces || {};
    $('running-status').textContent = r.trainingStatus ? 'Garmin training status: ' + r.trainingStatus : '';
    var tiles = [
      { label: 'This week', value: num(r.thisWeekKm, 1), unit: 'km', note: r.runsThisWeek + ' run' + (r.runsThisWeek === 1 ? '' : 's') + ' so far' },
      { label: '4-week average', value: num(r.avgWeekKm, 1), unit: 'km', note: 'per week' },
      { label: 'Longest (2 wks)', value: num(r.longestRecentKm, 1), unit: 'km', note: r.daysSinceLong < 99 ? 'last long run ' + r.daysSinceLong + ' d ago' : '' },
      { label: 'Easy pace', value: p.easy ? pace(p.easy[0]) + '–' + pace(p.easy[1]) : '—', unit: '/km', note: p.easyHrMax ? 'HR under ' + p.easyHrMax : '' },
      { label: 'VO2 max', value: r.vo2max ? num(r.vo2max, 1) : '—', note: 'from Garmin' },
      { label: 'Cadence', value: num(r.cadence), unit: 'spm', note: '4-week average' }
    ];
    $('run-tiles').innerHTML = tiles.map(function (t) {
      return '<div class="tile"><span class="tile-label">' + t.label + '</span><span class="tile-value">' + t.value +
        (t.unit && t.value !== '—' ? '<small>' + t.unit + '</small>' : '') + '</span><span class="tile-note">' + esc(t.note) + '</span></div>';
    }).join('');

    var pr = p.predictions || {};
    var names = [['5k', '5K'], ['10k', '10K'], ['half', 'Half'], ['marathon', 'Marathon']];
    $('predictions').innerHTML = pr['10k'] ? '<span class="tile-label">Race predictions</span>' + names.filter(function (n) { return pr[n[0]]; }).map(function (n) {
      return '<span>' + n[1] + ' <strong>' + raceTime(pr[n[0]]) + '</strong></span>';
    }).join('') + '<span class="muted">' + esc(p.source || '') + '</span>' : '';

    var box = $('run-charts');
    box.innerHTML = '';
    var weeks = r.weekly.map(function (w) { return { date: w.week, km: w.km, runs: w.runs, partial: w.partial }; });
    addChart(box, weeks, { key: 'km', title: 'Weekly distance', kind: 'bar', partialLast: true,
      fmt: function (v, row) { return num(v, 1) + ' km · ' + row.runs + ' run' + (row.runs === 1 ? '' : 's'); },
      when: function (row) { return 'Week of ' + day(row.date) + (row.partial ? ' (so far)' : ''); },
      axis: function (v) { return num(v); },
      summary: 'last 12 weeks' });
    var eff = (r.efficiency || []).slice(-30);
    if (eff.length >= 4) {
      addChart(box, eff, { key: 'value', title: 'Running efficiency', kind: 'line', minSpan: 0.06, round: 0.02,
        fmt: function (v) { return num(v, 2) + ' m per heartbeat'; },
        axis: function (v) { return num(v, 2); },
        summary: 'metres per heartbeat · higher = fitter' });
    }

    var it = r.intensity;
    $('intensity').innerHTML = it ? '<h3 class="sub-head">Effort balance, last 4 weeks <span class="muted">(' + hm(it.minutes) + ' of running · aim for ~80% easy)</span></h3>' +
      '<div class="int-bar" role="img" aria-label="Easy ' + it.easy + '%, moderate ' + it.moderate + '%, hard ' + it.hard + '%">' +
      [['easy', 1], ['moderate', 2], ['hard', 3]].map(function (z) { return it[z[0]] ? '<span style="width:' + it[z[0]] + '%;background:var(--int-' + z[1] + ')"></span>' : ''; }).join('') + '</div>' +
      '<div class="int-legend">' + [['Easy', 'easy', 1], ['Moderate', 'moderate', 2], ['Hard', 'hard', 3]].map(function (z) {
        return '<span><i style="background:var(--int-' + z[2] + ')"></i>' + z[0] + ' <strong>' + it[z[1]] + '%</strong></span>';
      }).join('') + '</div>' : '';
  }

  function addChart(wrap, rows, c) {
    var fig = document.createElement('figure');
    fig.className = 'chart'; fig.style.margin = 0;
    fig.innerHTML = '<div class="chart-head"><h3>' + c.title + '</h3><span class="muted">' + esc(c.summary) + '</span></div>';
    wrap.appendChild(fig);
    drawChart(fig, rows, c);
  }

  var CHARTS = [
    { key: 'steps', title: 'Steps', kind: 'bar', fmt: function (v) { return num(v); }, summary: function (r) { return 'avg ' + num(avg(r, 'steps')); } },
    { key: 'sleepMin', title: 'Sleep', kind: 'bar', fmt: hm, axis: function (v) { return Math.round(v / 60) + 'h'; }, summary: function (r) { return 'avg ' + hm(avg(r, 'sleepMin')); } },
    { key: 'restingHr', title: 'Resting heart rate', kind: 'line', nightOnly: true, emptyText: 'Needs the watch worn overnight', fmt: function (v) { return num(v) + ' bpm'; }, summary: function (r) { var v = avg(r.filter(function (x) { return x.sleepMin; }), 'restingHr'); return v == null ? '' : 'avg ' + num(v) + ' bpm'; } },
    { key: 'hrv', title: 'Overnight HRV', kind: 'line', fmt: function (v) { return num(v) + ' ms'; }, summary: function (r) { return 'avg ' + num(avg(r, 'hrv')) + ' ms'; } }
  ];

  function renderCharts(rows) {
    var wrap = $('charts');
    wrap.innerHTML = '';
    CHARTS.forEach(function (c) {
      var box = document.createElement('figure');
      box.className = 'chart'; box.style.margin = 0;
      box.innerHTML = '<div class="chart-head"><h3>' + c.title + '</h3><span class="muted">' + c.summary(rows) + '</span></div>';
      wrap.appendChild(box);
      // Without overnight wear Garmin's resting HR is a daytime estimate, so leave it out.
      drawChart(box, c.nightOnly ? rows.map(function (r) { return Object.assign({}, r, { restingHr: r.sleepMin ? r.restingHr : null }); }) : rows, c);
    });
  }

  function niceMax(v) {
    if (!v) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    var steps = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
    for (var i = 0; i < steps.length; i++) if (n <= steps[i]) return steps[i] * p;
    return 10 * p;
  }

  function drawChart(box, rows, c) {
    var W = Math.max(box.clientWidth - 36, 200), H = 180;
    var pad = { l: 40, r: 8, t: 8, b: 22 };
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': c.title + ' per day' }, box);
    var vals = rows.map(function (r) { return r[c.key]; });
    var present = vals.filter(function (v) { return v != null; });
    if (!present.length) { el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', 'class': 'empty-note' }, svg).textContent = c.emptyText || 'No data in this range'; return; }

    // Bars start at zero; lines zoom to their range so small changes stay visible.
    var lo = 0, hi;
    if (c.kind === 'bar') hi = niceMax(Math.max.apply(null, present));
    else {
      var mn = Math.min.apply(null, present), mx = Math.max.apply(null, present);
      var span = Math.max(mx - mn, c.minSpan || 4), stepV = c.round || 2;
      lo = Math.floor((mn - span * 0.25) / stepV) * stepV; hi = Math.ceil((mx + span * 0.25) / stepV) * stepV;
    }
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b, n = rows.length, step = iw / n;
    var x = function (i) { return pad.l + step * (i + 0.5); };
    var y = function (v) { return pad.t + ih - (v - lo) / (hi - lo) * ih; };

    var axis = el('g', { 'class': 'axis' }, svg);
    [0, 0.5, 1].forEach(function (f) {
      var v = lo + (hi - lo) * f, yy = y(v);
      el('line', { x1: pad.l, x2: W - pad.r, y1: yy, y2: yy, 'class': 'gridline' }, axis);
      el('text', { x: pad.l - 6, y: yy + 4, 'text-anchor': 'end' }, axis).textContent = c.axis ? c.axis(v) : num(v);
    });
    var every = Math.ceil(n / Math.max(2, Math.floor(iw / 64)));
    rows.forEach(function (r, i) {
      if ((n - 1 - i) % every === 0) el('text', { x: x(i), y: H - 4, 'text-anchor': 'middle' }, axis).textContent = day(r.date);
    });

    var marks = [];
    if (c.kind === 'bar') {
      var bw = Math.max(2, Math.min(step - 2, 22));
      rows.forEach(function (r, i) {
        var v = r[c.key]; if (v == null) { marks.push(null); return; }
        var top = y(v), h = Math.max(1, y(lo) - top), rad = Math.min(4, bw / 2, h);
        // Rounded top corners only; the bottom stays square on the baseline.
        var x0 = x(i) - bw / 2, x1 = x0 + bw, base = y(lo);
        marks.push(el('path', { 'class': 'bar' + (c.partialLast && i === n - 1 ? ' partial' : ''), d: 'M' + x0 + ',' + base + 'V' + (top + rad) + 'Q' + x0 + ',' + top + ' ' + (x0 + rad) + ',' + top + 'H' + (x1 - rad) + 'Q' + x1 + ',' + top + ' ' + x1 + ',' + (top + rad) + 'V' + base + 'Z' }, svg));
      });
    } else {
      var d = '', pen = false;
      rows.forEach(function (r, i) {
        var v = r[c.key];
        if (v == null) { pen = false; return; }
        d += (pen ? 'L' : 'M') + x(i) + ',' + y(v); pen = true;
      });
      el('path', { 'class': 'line', d: d }, svg);
    }

    var cross = el('line', { 'class': 'cross', y1: pad.t, y2: pad.t + ih, visibility: 'hidden' }, svg);
    var dot = c.kind === 'line' ? el('circle', { 'class': 'dot', r: 5, visibility: 'hidden' }, svg) : null;

    // Hit targets are full-height columns, wider than the marks themselves.
    rows.forEach(function (r, i) {
      var hit = el('rect', { 'class': 'hit', x: pad.l + step * i, y: pad.t, width: step, height: ih }, svg);
      hit.addEventListener('mousemove', function (e) { show(e, i); });
      hit.addEventListener('mouseleave', hide);
    });

    function show(e, i) {
      var r = rows[i], v = r[c.key];
      if (c.kind === 'line') {
        cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
        if (v != null) { dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(v)); dot.setAttribute('visibility', 'visible'); }
        else dot.setAttribute('visibility', 'hidden');
      } else {
        marks.forEach(function (m, j) { if (m) m.classList.toggle('dim', j !== i); });
      }
      tooltip.innerHTML = '<strong>' + (v == null ? 'No data' : c.fmt(v, r)) + '</strong>' + (c.when ? c.when(r, i) : day(r.date, { weekday: 'short', day: 'numeric', month: 'short' }));
      tooltip.hidden = false;
      var tx = e.clientX + 14, ty = e.clientY - 12, tw = tooltip.offsetWidth;
      if (tx + tw > window.innerWidth - 8) tx = e.clientX - tw - 14;
      tooltip.style.left = tx + 'px'; tooltip.style.top = ty + 'px';
    }
    function hide() {
      tooltip.hidden = true;
      cross.setAttribute('visibility', 'hidden');
      if (dot) dot.setAttribute('visibility', 'hidden');
      marks.forEach(function (m) { if (m) m.classList.remove('dim'); });
    }
  }

  var TYPE_LABEL = { running: 'Run', trail_running: 'Trail run', treadmill_running: 'Treadmill', cycling: 'Ride', road_biking: 'Ride', indoor_cycling: 'Indoor ride', virtual_ride: 'Virtual ride', lap_swimming: 'Pool swim', open_water_swimming: 'Open-water swim', strength_training: 'Strength', walking: 'Walk', hiking: 'Hike', yoga: 'Yoga' };

  function renderActivities() {
    var acts = data.activities || [];
    var from = rangeDays && data.daily && data.daily.length ? visibleDays()[0].date : '';
    var list = acts.filter(function (a) { return !from || (a.start || '') >= from; });
    $('act-count').textContent = list.length + ' in range';
    var body = $('activities').querySelector('tbody');
    body.innerHTML = list.length ? list.slice(0, 25).map(function (a) {
      var t = TYPE_LABEL[a.type] || (a.type || '').replace(/_/g, ' ');
      return '<tr><td>' + day((a.start || '').slice(0, 10), { weekday: 'short', day: 'numeric', month: 'short' }) + '</td>' +
        '<td>' + esc(a.name || t) + (a.name && t.toLowerCase() !== a.name.toLowerCase() ? '<span class="act-type">' + esc(t) + '</span>' : '') + '</td>' +
        '<td class="num">' + (a.distanceKm ? num(a.distanceKm, 2) + ' km' : '—') + '</td>' +
        '<td class="num">' + hm(a.durationMin) + '</td>' +
        '<td class="num">' + (a.avgHr ? num(a.avgHr) + ' bpm' : '—') + '</td>' +
        '<td class="num">' + num(a.calories) + '</td></tr>';
    }).join('') : '<tr><td colspan="6" class="muted">No activities in this range.</td></tr>';
  }

  function renderSources() {
    var live = data.source === 'garmin';
    var sources = [
      { name: 'Garmin Connect', tile: '#1c1c1c', on: live, note: live ? 'Last sync ' + ago(data.fetchedAt) : 'Run fetch_garmin.py to connect' },
      { name: 'Strava', tile: '#e8590c', note: 'Not set up' },
      { name: 'Apple Health', tile: '#d6336c', note: 'Not set up' }
    ];
    $('sources').innerHTML = sources.map(function (s) {
      return '<div class="source' + (s.on ? ' on' : '') + '"><span class="source-tile" style="--tile:' + s.tile + '" aria-hidden="true">' +
        s.name.split(' ').map(function (w) { return w[0]; }).join('').slice(0, 2) + '</span><div><strong>' + s.name + '</strong><span class="muted">' + esc(s.note) +
        '</span></div><span class="badge ' + (s.on ? 'ok">Connected' : 'off">Off') + '</span></div>';
    }).join('');
  }

  // ---------- controls ----------
  document.querySelector('.range').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-days]'); if (!b || !data) return;
    rangeDays = +b.dataset.days;
    document.querySelectorAll('.range button').forEach(function (x) { x.setAttribute('aria-pressed', x === b); });
    render();
  });

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (data) { renderCharts(visibleDays()); renderRunning(); } }, 150);
  });
})();
