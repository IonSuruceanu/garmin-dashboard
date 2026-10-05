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

  // ---------- actions (serve.py only) ----------
  var server = { server: false, telegram: false };
  fetch('/api/status', { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
    if (!s || !s.server) return;
    server = s;
    $('actions').hidden = false;
    document.querySelectorAll('[data-needs-telegram]').forEach(function (b) {
      if (!s.telegram) { b.disabled = true; b.title = 'Set up Telegram first: python telegram_summary.py --setup'; }
    });
    if (data) renderToday();
  }).catch(function () {});

  var toastTimer;
  function toast(msg, isError) {
    var t = $('toast');
    t.textContent = msg; t.className = 'toast' + (isError ? ' error' : ''); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, isError ? 8000 : 4000);
  }

  function callApi(name, body, btn) {
    var label = btn && btn.textContent;
    if (btn) { btn.disabled = true; btn.textContent = name === 'refresh' ? 'Refreshing… (the first time takes a few minutes)' : 'Saving…'; }
    return fetch('/api/' + name, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) {
        return r.text().then(function (text) {
          var j;
          try { j = JSON.parse(text); } catch (e) {
            // An HTML page instead of our JSON: usually an older `python -m http.server` on the same port.
            throw new Error(r.status === 501 || r.status === 404
              ? 'This button needs serve.py, but another (older) server answered. Close any Terminal window running "python -m http.server", then start the dashboard again.'
              : 'The dashboard server sent an unexpected reply (HTTP ' + r.status + '). Check its Terminal window.');
          }
          if (!r.ok) throw new Error(j.error || 'Something went wrong');
          return j;
        });
      })
      .then(function (j) {
        toast(j.message || 'Done');
        if (name === 'refresh') return load('data/garmin.json').then(function (d) { data = d; render(); });
      })
      .catch(function (e) { toast(e.message, true); })
      .then(function () { if (btn) { btn.disabled = false; btn.textContent = label; } });
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-api]');
    if (!b) return;
    e.stopPropagation();
    callApi(b.dataset.api, b.dataset.key ? { key: b.dataset.key } : null, b);
  }, true);

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
    renderHistory();
    renderActivities();
    renderNotesExtra();
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
        (server.telegram ? '<button class="act-btn primary" data-api="workout" data-key="' + pick.key + '">Send to Telegram</button>' : '') +
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
      { label: 'Training status', value: r.trainingStatus || '—', note: 'from Garmin' },
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

    renderVo2(r.vo2);
    var hs = r.hr || {};
    $('hr-settings').innerHTML = hs.maxHr ? 'Heart-rate settings: max <strong>' + hs.maxHr + '</strong> (' + esc(hs.maxHrSource) + ')' +
      (hs.lthr ? ' · lactate threshold <strong>' + hs.lthr + '</strong>' : '') +
      (hs.easyCap ? ' · easy ≤ <strong>' + hs.easyCap + '</strong> (' + esc(hs.easySource) + ')' : '') +
      (hs.zones ? ' · zones from ' + hs.zones.join(' / ') + ' bpm' : '') : '';
    var dy = r.dynamics;
    $('dyn-tiles').hidden = !(dy && (dy.now.strideCm || dy.now.gctMs || dy.now.vertOscCm));
    if (dy) {
      var dyn = [['Stride length', 'strideCm', 'cm', 0], ['Ground contact', 'gctMs', 'ms', 0], ['Vertical oscillation', 'vertOscCm', 'cm', 1],
        ['Vertical ratio', 'vertRatio', '%', 1], ['Weekly load', null, '', 0]];
      var lastFull = r.weekly.length > 1 ? r.weekly[r.weekly.length - 2] : null;
      $('dyn-tiles').innerHTML = dyn.map(function (d) {
        if (!d[1]) return '<div class="tile"><span class="tile-label">' + d[0] + '</span><span class="tile-value">' + num(lastFull && lastFull.load) + '</span><span class="tile-note">last full week' +
          (r.garminLoad && r.garminLoad.acute != null ? ' · Garmin acute ' + r.garminLoad.acute : ' · estimated from HR zones') + '</span></div>';
        var v = dy.now[d[1]], b = dy.before && dy.before[d[1]];
        return '<div class="tile"><span class="tile-label">' + d[0] + '</span><span class="tile-value">' + num(v, d[3]) + (v != null ? '<small>' + d[2] + '</small>' : '') +
          '</span><span class="tile-note">' + (b != null && v != null ? 'was ' + num(b, d[3]) + ' the 4 weeks before' : '4-week average') + '</span></div>';
      }).join('');
    }

    var box = $('run-charts');
    box.innerHTML = '';
    var weeks = r.weekly.map(function (w) { return { date: w.week, km: w.km, runs: w.runs, partial: w.partial }; });
    addChart(box, weeks, { key: 'km', title: 'Weekly distance', kind: 'bar', partialLast: true,
      fmt: function (v, row) { return num(v, 1) + ' km · ' + row.runs + ' run' + (row.runs === 1 ? '' : 's'); },
      when: function (row) { return 'Week of ' + day(row.date) + (row.partial ? ' (so far)' : ''); },
      axis: function (v) { return num(v); },
      summary: 'last 12 weeks' });
    if (r.vo2 && r.vo2.history && r.vo2.history.length >= 3) {
      addChart(box, r.vo2.history, { key: 'value', title: 'VO2 max', kind: 'line', minSpan: 2, round: 1,
        fmt: function (v) { return 'VO2 max ' + num(v, 1); },
        axis: function (v) { return num(v); },
        summary: r.vo2.source === 'Garmin' ? 'from Garmin, per run' : 'weekly estimate from pace and heart rate' });
    }
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

  var VO2_LEVELS = ['Poor', 'Fair', 'Good', 'Excellent', 'Superior'];
  function renderVo2(v) {
    $('vo2').hidden = !v;
    if (!v) return;
    var who = v.profile && v.profile.age ? ' for a ' + v.profile.age + '-year-old ' + (v.profile.sex === 'female' ? 'woman' : 'man') : '';
    var idx = VO2_LEVELS.indexOf(v.level);
    var change = v.change == null ? '' : Math.abs(v.change) < 0.5 ? 'Stable since ' + day(v.since) + '.' :
      '<span class="' + (v.change > 0 ? 'up' : 'down') + '">' + (v.change > 0 ? '▲ +' : '▼ ') + num(v.change, 1) + '</span> since ' + day(v.since) + '.';
    $('vo2').innerHTML =
      '<div class="vo2-value"><small>VO2 max</small>' + num(v.value, 1) + '</div>' +
      '<div>' + (v.level ?
        '<div class="vo2-level">' + v.level + '<span>' + esc(who) + '</span></div>' +
        '<div class="vo2-scale" role="img" aria-label="Fitness level ' + v.level + '">' + VO2_LEVELS.map(function (l, i) { return '<div class="' + (i === idx ? 'on' : '') + '"></div>'; }).join('') + '</div>' +
        '<div class="vo2-scale-labels">' + VO2_LEVELS.map(function (l, i) { return '<span class="' + (i === idx ? 'on' : '') + '">' + l + '</span>'; }).join('') + '</div>'
        : '<div class="vo2-level">ml/kg/min<span> · add your age and sex in Garmin Connect to see your fitness level</span></div>') +
      '<p>' + change + (v.next ? ' ' + esc(v.next.level) + ' starts at ' + num(v.next.at, 1) + '.' : '') +
      ' <span class="muted">Source: ' + esc(v.source) + '.</span></p></div>';
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
      if ((n - 1 - i) % every === 0) el('text', { x: x(i), y: H - 4, 'text-anchor': 'middle' }, axis).textContent = c.xLabel ? c.xLabel(r) : day(r.date);
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

  // ---------- training history ----------
  function monthName(m) { return new Date(m + '-15T12:00:00').toLocaleDateString(undefined, { month: 'short', year: 'numeric' }); }

  function renderHistory() {
    var h = data.running && data.running.history;
    var months = (data.running && data.running.monthly) || [];
    $('history-panel').hidden = !(h && h.weekly && h.weekly.length > 12);
    if ($('history-panel').hidden) return;
    $('history-sub').textContent = 'Since ' + day(h.from, { day: 'numeric', month: 'short', year: 'numeric' }) + ' · a block ends after 2+ weeks under ' + num(h.thresholdKm, 0) + ' km';
    var box = $('history-charts'); box.innerHTML = '';
    addChart(box, h.weekly.map(function (w) { return { date: w.week, km: w.km, runs: w.runs, partial: w.partial }; }), {
      key: 'km', title: 'Weekly running distance', kind: 'bar', partialLast: true,
      fmt: function (v, row) { return num(v, 1) + ' km · ' + row.runs + ' run' + (row.runs === 1 ? '' : 's'); },
      when: function (row) { return 'Week of ' + day(row.date) + (row.partial ? ' (so far)' : ''); },
      axis: function (v) { return num(v); }, summary: h.weekly.length + ' weeks' });
    $('blocks-table').className = 'data-table';
    $('blocks-table').innerHTML = '<thead><tr><th>Block</th><th class="num">Weeks</th><th class="num">Distance</th><th class="num">Avg / week</th><th class="num">Peak week</th><th class="num">Late jump</th></tr></thead><tbody>' +
      (h.blocks || []).map(function (b, i) {
        return '<tr class="' + (b.current ? 'cur' : '') + '"><td>' + day(b.start) + ' – ' + (b.current ? 'now' : day(b.end)) + (b.current ? ' <span class="muted">(current)</span>' : '') + '</td>' +
          '<td class="num">' + b.weeks + '</td><td class="num">' + num(b.km) + ' km</td><td class="num">' + num(b.avgKm, 1) + ' km</td><td class="num">' + num(b.peakKm, 1) + ' km</td>' +
          '<td class="num">' + (b.lateJumpPct != null ? (b.lateJumpPct > 0 ? '+' : '') + b.lateJumpPct + '%' : '—') + '</td></tr>';
      }).join('') + '</tbody>';
    var fc = data.running.fitnessCompare;
    $('months-table').className = 'data-table';
    $('months-table').innerHTML = '<thead><tr><th>Month</th><th class="num">Runs</th><th class="num">Distance</th><th class="num">Best 5K-equiv.</th><th class="num">Heat-adj.</th><th class="num">m / beat</th><th class="num">Heat-adj.</th><th class="num">Avg temp</th></tr></thead><tbody>' +
      months.map(function (m) {
        var peak = fc && fc.peakMonth === m.month;
        return '<tr class="' + (peak ? 'cur' : '') + '"><td>' + monthName(m.month) + (peak ? ' <span class="muted">(best)</span>' : '') + '</td><td class="num">' + m.runs + '</td><td class="num">' + num(m.km) + ' km</td>' +
          '<td class="num">' + (m.best5k ? raceTime(m.best5k) : '—') + '</td><td class="num">' + (m.best5kAdj ? raceTime(m.best5kAdj) : '—') + '</td>' +
          '<td class="num">' + num(m.efficiency, 2) + '</td><td class="num">' + num(m.efficiencyAdj, 2) + '</td><td class="num">' + (m.avgTempC != null ? num(m.avgTempC, 0) + '°C' : '—') + '</td></tr>';
      }).join('') + (fc ? '<tr class="cur"><td>Last 6 weeks</td><td class="num">' + fc.nowRuns + '</td><td></td><td colspan="2" class="num">' + raceTime(fc.now5k) + (fc.heatAdjusted ? ' adj.' : '') +
        ' (' + (fc.changePct > 0 ? '+' : '') + fc.changePct + '% vs ' + monthName(fc.peakMonth) + ')</td><td colspan="3"></td></tr>' : '') + '</tbody>';
  }

  // ---------- run detail ----------
  var RUN_TYPES = ['solo', 'run club', 'with friends', 'race', 'treadmill'];
  var openId = null;

  function weatherText(w) {
    if (!w) return '';
    return num(w.tempC, 0) + '°C' + (w.dewPointC != null ? ', dew point ' + num(w.dewPointC, 0) + '°C' : '') +
      (w.humidity != null ? ', humidity ' + num(w.humidity, 0) + '%' : '') + (w.windKmh != null ? ', wind ' + num(w.windKmh, 0) + ' km/h' : '');
  }

  function openDetail(id) {
    var a = (data.activities || []).filter(function (x) { return String(x.id) === String(id); })[0];
    if (!a) return;
    openId = id;
    $('drawer').hidden = false;
    var p = a.distanceKm && a.durationMin ? a.durationMin / a.distanceKm : null;
    var isRun = (a.type || '').indexOf('running') >= 0;
    var head = '<span class="eyebrow">' + day((a.start || '').slice(0, 10), { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) + ' · ' + esc((a.start || '').slice(11, 16)) + '</span>' +
      '<h2 id="drawer-title">' + esc(a.name || a.type) + '</h2>' +
      (a.weather ? '<p class="weather-line">🌡 ' + weatherText(a.weather) + (a.heatPct >= 0.5 ? ' · heat costs about ' + num(a.heatPct, 1) + '% pace' : ' · no heat penalty') + '</p>' : '') +
      '<div class="tiles">' + [
        ['Distance', a.distanceKm ? num(a.distanceKm, 2) : '—', 'km'], ['Time', hm(a.durationMin), ''],
        ['Pace', isRun && p ? pace(p) : '—', '/km'], ['Heat-adjusted', a.heatAdjPace ? pace(a.heatAdjPace) : '—', '/km'],
        ['Avg HR', num(a.avgHr), 'bpm'], ['HR drift', a.decoupling != null ? num(a.decoupling, 1) : '—', '%']
      ].map(function (t) { return '<div class="tile"><span class="tile-label">' + t[0] + '</span><span class="tile-value">' + t[1] + (t[2] && t[1] !== '—' ? '<small>' + t[2] + '</small>' : '') + '</span></div>'; }).join('') + '</div>';
    $('drawer-body').innerHTML = head + '<div id="detail-charts"></div><div id="detail-laps"></div><h3 class="sub-head">Your notes</h3><div id="detail-note"></div>';
    renderNoteForm(a);
    if (!a.detailPath) {
      $('detail-laps').innerHTML = '<p class="muted">' + (isRun && (a.distanceKm || 0) >= 4 ? 'Laps and heart-rate details will appear after the next refresh.' : 'Laps and details are kept for runs over 4 km.') + '</p>';
      return;
    }
    load('data/' + a.detailPath).then(function (d) {
      if (openId !== id) return;
      var s = d.series, box = $('detail-charts');
      if (s && s.t && s.t.length) {
        var rows = s.t.map(function (t, i) { return { t: t, hr: s.hr[i], pace: s.pace[i] }; });
        var minLabel = function (r) { return Math.round(r.t) + ' min'; };
        addChart(box, rows, { key: 'hr', title: 'Heart rate', kind: 'line', minSpan: 10, round: 5, xLabel: minLabel,
          when: function (r) { return 'at ' + minLabel(r); }, fmt: function (v) { return num(v) + ' bpm'; }, axis: function (v) { return num(v); },
          summary: d.decoupling != null ? 'drift ' + num(d.decoupling, 1) + '%' : '' });
        addChart(box, rows, { key: 'pace', title: 'Pace', kind: 'line', minSpan: 0.5, round: 0.25, xLabel: minLabel,
          when: function (r) { return 'at ' + minLabel(r); }, fmt: function (v) { return pace(v) + ' /km'; }, axis: function (v) { return pace(v); },
          summary: 'min/km · lower is faster' });
      }
      var laps = d.laps || [];
      if (laps.length) {
        $('detail-laps').innerHTML = '<h3 class="sub-head">Laps</h3><div class="table-wrap"><table class="data-table"><thead><tr><th>Lap</th><th class="num">Distance</th><th class="num">Pace</th><th class="num">Avg HR</th><th class="num">Cadence</th><th class="num">Stride</th><th class="num">Elev. +</th></tr></thead><tbody>' +
          laps.map(function (l) {
            return '<tr><td>' + l.lap + '</td><td class="num">' + num(l.distanceKm, 2) + ' km</td><td class="num">' + pace(l.pace) + '</td><td class="num">' + num(l.avgHr) + '</td>' +
              '<td class="num">' + num(l.cadence) + '</td><td class="num">' + (l.strideCm ? num(l.strideCm) + ' cm' : '—') + '</td><td class="num">' + (l.elevGainM != null ? num(l.elevGainM) + ' m' : '—') + '</td></tr>';
          }).join('') + '</tbody></table></div>';
      }
    }).catch(function () { $('detail-laps').innerHTML = '<p class="muted">Couldn\'t load the details for this run.</p>'; });
  }

  function renderNoteForm(a) {
    var n = a.note || {};
    var shoes = ((data.running && data.running.shoes) || []).map(function (s) { return s.name; });
    if (!server.server) {
      $('detail-note').innerHTML = (n.runType || n.shoes || n.effort || n.comment ? '<p>' + [n.runType, n.shoes, n.effort ? 'effort ' + n.effort + '/10' : '', n.comment].filter(Boolean).map(esc).join(' · ') + '</p>' : '') +
        '<p class="muted">Start the dashboard with <code>python serve.py</code> (or start.command) to add notes here.</p>';
      return;
    }
    $('detail-note').innerHTML = '<form class="note-form" id="note-form">' +
      '<label>Run type<select name="runType"><option value="">—</option>' + RUN_TYPES.map(function (t) { return '<option' + (n.runType === t ? ' selected' : '') + '>' + t + '</option>'; }).join('') + '</select></label>' +
      '<label>Shoes<input name="shoes" list="shoe-list" value="' + esc(n.shoes || '') + '" placeholder="e.g. Novablast 4"><datalist id="shoe-list">' + shoes.map(function (s) { return '<option value="' + esc(s) + '">'; }).join('') + '</datalist></label>' +
      '<label>How hard (1–10)<select name="effort"><option value="">—</option>' + [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(function (v) { return '<option' + (n.effort === v ? ' selected' : '') + '>' + v + '</option>'; }).join('') + '</select></label>' +
      '<label class="full">Comment<input name="comment" value="' + esc(n.comment || '') + '" placeholder="Anything worth remembering"></label>' +
      '<div class="full"><button class="act-btn primary" type="submit">Save note</button></div></form>';
    $('note-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var f = e.target, btn = f.querySelector('button');
      var body = { id: a.id, runType: f.runType.value, shoes: f.shoes.value.trim(), effort: f.effort.value ? +f.effort.value : '', comment: f.comment.value.trim() };
      callApi('note', body, btn).then(function () { return load('data/garmin.json'); }).then(function (d) { data = d; render(); openDetail(a.id); });
    });
  }

  function closeDetail() { $('drawer').hidden = true; openId = null; }
  $('drawer-close').addEventListener('click', closeDetail);
  $('drawer').addEventListener('click', function (e) { if (e.target === $('drawer')) closeDetail(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !$('drawer').hidden) closeDetail(); });
  $('activities').addEventListener('click', function (e) {
    var row = e.target.closest('tr[data-id]');
    if (row) openDetail(row.dataset.id);
  });
  $('activities').addEventListener('keydown', function (e) {
    var row = e.target.closest('tr[data-id]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openDetail(row.dataset.id); }
  });

  // ---------- notes: weight and shoes ----------
  function renderNotesExtra() {
    var r = data.running || {};
    var w = r.weight, shoes = r.shoes || [];
    $('notes-extra').innerHTML =
      '<div><span class="tile-label">Weight</span><div class="tile-value">' + (w ? num(w.latest, 1) + '<small>kg</small>' : '—') + '</div>' +
      '<span class="tile-note">' + (w ? day(w.date) + (w.change28d != null ? ' · ' + (w.change28d > 0 ? '+' : '') + num(w.change28d, 1) + ' kg in 4 weeks' : '') : 'from Garmin or your notes') + '</span>' +
      (server.server ? '<form id="weight-form"><input name="kg" type="number" step="0.1" min="30" max="250" placeholder="kg" aria-label="Weight in kg" required><button class="act-btn" type="submit">Log today</button></form>' : '') + '</div>' +
      '<div><span class="tile-label">Shoes</span>' + (shoes.length ? '<ul>' + shoes.map(function (s) {
        return '<li><strong>' + esc(s.name) + '</strong> · ' + num(s.km) + ' km · ' + s.runs + ' runs</li>';
      }).join('') + '</ul>' : '<p class="muted">Add shoes to a run\'s notes to track their mileage.</p>') + '</div>';
    var wf = $('weight-form');
    if (wf) wf.addEventListener('submit', function (e) {
      e.preventDefault();
      callApi('weight', { kg: +wf.kg.value }, wf.querySelector('button')).then(function () { return load('data/garmin.json'); }).then(function (d) { data = d; render(); });
    });
  }

  var TYPE_LABEL = { running: 'Run', trail_running: 'Trail run', treadmill_running: 'Treadmill', cycling: 'Ride', road_biking: 'Ride', indoor_cycling: 'Indoor ride', virtual_ride: 'Virtual ride', lap_swimming: 'Pool swim', open_water_swimming: 'Open-water swim', strength_training: 'Strength', walking: 'Walk', hiking: 'Hike', yoga: 'Yoga' };

  function renderActivities() {
    var acts = data.activities || [];
    var from = rangeDays && data.daily && data.daily.length ? visibleDays()[0].date : '';
    var list = acts.filter(function (a) { return !from || (a.start || '') >= from; });
    $('act-count').textContent = list.length + ' in range';
    var body = $('activities').querySelector('tbody');
    body.innerHTML = list.length ? list.slice(0, 40).map(function (a) {
      var t = TYPE_LABEL[a.type] || (a.type || '').replace(/_/g, ' ');
      var isRun = (a.type || '').indexOf('running') >= 0;
      var p = isRun && a.distanceKm && a.durationMin ? a.durationMin / a.distanceKm : null;
      return '<tr data-id="' + esc(a.id) + '" tabindex="0" title="Show details"><td>' + day((a.start || '').slice(0, 10), { weekday: 'short', day: 'numeric', month: 'short' }) + '</td>' +
        '<td>' + esc(a.name || t) + (a.name && t.toLowerCase() !== a.name.toLowerCase() ? '<span class="act-type">' + esc(t) + '</span>' : '') + '</td>' +
        '<td class="num">' + (a.distanceKm ? num(a.distanceKm, 2) + ' km' : '—') + '</td>' +
        '<td class="num">' + hm(a.durationMin) + '</td>' +
        '<td class="num">' + (p ? pace(p) : '—') + '</td>' +
        '<td class="num">' + (a.avgHr ? num(a.avgHr) + ' bpm' : '—') + '</td>' +
        '<td class="num">' + (a.weather ? num(a.weather.tempC, 0) + '°C' : '—') + '</td>' +
        '<td class="num">' + (a.decoupling != null ? num(a.decoupling, 1) + '%' : '—') + '</td></tr>';
    }).join('') : '<tr><td colspan="8" class="muted">No activities in this range.</td></tr>';
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
