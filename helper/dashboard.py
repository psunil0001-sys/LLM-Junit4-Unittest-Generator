"""Offline Kover/JaCoCo HTML dashboard build and render."""

from __future__ import annotations

# --- helper/dashboard/diagnostic_dashboard.py ---
import html
import json
from pathlib import Path


def render_diagnostic_dashboard(model: dict) -> str:
    title = html.escape(str(model.get("title") or "Kover Coverage Dashboard"))
    payload = json.dumps(model, ensure_ascii=True)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" href="vendor/dashboard.css" />
  <script src="vendor/chart.umd.min.js"></script>
</head>
<body>
  <div class="wrap">
    <div class="top-bar">
      <div>
        <div class="nav" style="margin-bottom:6px"><a href="diagnostics_index.html">← Reports hub</a></div>
        <h1>{title}</h1>
      </div>
      <div class="theme-switch" role="group" aria-label="Theme">
        <button type="button" data-theme-set="dark" class="active">Dark</button>
        <button type="button" data-theme-set="aurora">Aurora</button>
        <button type="button" data-theme-set="ember">Ember</button>
        <button type="button" data-theme-set="light">Light</button>
      </div>
    </div>
    <div class="meta" id="meta"></div>

    <section class="dashboard-section" id="section-overview" data-section="overview">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-overview-body">
        <button type="button" class="section-toggle" aria-label="Collapse overview">−</button>
        <h2>Overview</h2>
      </div>
      <div class="section-body" id="section-overview-body">
    <div class="cards" id="summary-cards"></div>
    <div class="charts">
      <div class="panel"><canvas id="coverageChart"></canvas></div>
      <div class="panel"><canvas id="moduleChart"></canvas></div>
    </div>
      </div>
    </section>

    <section class="dashboard-section" id="section-module-coverage" data-section="module-coverage">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-module-coverage-body">
        <button type="button" class="section-toggle" aria-label="Collapse module coverage">−</button>
        <h2>Module coverage</h2>
      </div>
      <div class="section-body" id="section-module-coverage-body">
    <p class="meta" style="margin:0 0 8px">Overall coverage is the Kover/JaCoCo element union; Combined line = per-line union and overlap counts once. Row tint by <strong>Effective %</strong>: &lt;60% red · 60–85% orange · &gt;85% green</p>
    <div class="panel" id="moduleTableWrap"><table id="moduleTable" class="dense-table"><thead>
      <tr>
        <th rowspan="2" class="col-exp"></th>
        <th rowspan="2" data-k="module" title="Module" class="col-mod">Module</th>
        <th rowspan="2" data-k="files" title="Files" class="num-cell col-files">#</th>
        <th colspan="5" class="group-head group-combined g-comb" title="Combined metrics are unions of Kover and JaCoCo elements; overlap counts once">Overall</th>
        <th rowspan="2" data-k="deferred_coverage_pct" title="Deferred coverage %" class="pct-cell col-def">Def%</th>
      </tr>
      <tr>
        <th data-k="combined_class_pct" title="Overall class % (Kover/JaCoCo union)" class="pct-cell sep-left g-comb col-pct">Cl</th>
        <th data-k="combined_method_pct" title="Overall method % (Kover/JaCoCo union)" class="pct-cell g-comb col-pct">Me</th>
        <th data-k="combined_branch_pct" title="Overall branch % (Kover/JaCoCo union)" class="pct-cell g-comb col-pct">Br</th>
        <th data-k="combined_line_pct" title="Overall line % (Kover/JaCoCo union)" class="pct-cell g-comb col-pct">Ln</th>
        <th data-k="combined_instruction_pct" title="Overall instruction % (Kover/JaCoCo union)" class="pct-cell g-comb col-pct">In</th>
      </tr>
    </thead><tbody></tbody><tfoot></tfoot></table></div>
      </div>
    </section>

    <section class="dashboard-section" id="section-deferred" data-section="deferred">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-deferred-body">
        <button type="button" class="section-toggle" aria-label="Collapse deferred coverage">−</button>
        <h2>Deferred coverage by module</h2>
      </div>
      <div class="section-body" id="section-deferred-body">
    <p class="meta" style="margin-top:0">From <code>unreachable_coverage.json</code> — lines parked as not unit-testable after attempt.</p>
    <div id="moduleDeferred"></div>
      </div>
    </section>

    <section class="dashboard-section" id="section-suspected-bugs" data-section="suspected-bugs">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-suspected-bugs-body">
        <button type="button" class="section-toggle" aria-label="Collapse suspected bugs">−</button>
        <h2>Suspected bugs (assertion trap)</h2>
      </div>
      <div class="section-body" id="section-suspected-bugs-body">
    <p class="meta" style="margin-top:0">JUnit contract failures copied to a sidecar before TARGET restore. Production <code>src/main</code> was not changed.</p>
    <div id="suspectedBugs"></div>
      </div>
    </section>

    <section class="dashboard-section" id="section-file-coverage" data-section="file-coverage">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-file-coverage-body">
        <button type="button" class="section-toggle" aria-label="Collapse file coverage">−</button>
        <h2>File coverage (unitTest + androidTest)</h2>
      </div>
      <div class="section-body" id="section-file-coverage-body">
    <div class="filters">
      <input id="projSearch" type="search" placeholder="Search module/package/file..." />
      <select id="projModuleFilter" multiple aria-label="Modules"></select>
      <select id="coverageFilter" aria-label="Coverage filter">
        <option value="all" selected>All files</option>
        <option value="hide_no_exec">Hide interfaces / no-exec</option>
        <option value="no_exec_only">Interfaces / no-exec only</option>
      </select>
    </div>
    <div class="panel"><table id="fileTable" class="dense-table"><thead>
      <tr><th></th><th data-k="module" title="Module">Module</th><th data-k="source_name" title="File (package shown below)">File</th>
      <th data-k="total_line_pct" title="Overall line coverage % (Kover/JaCoCo union)" class="pct-cell">Overall line %</th>
      <th data-k="total_branch_pct" title="Overall branch coverage % (Kover/JaCoCo union)" class="pct-cell">Overall br %</th></tr>
    </thead><tbody></tbody></table></div>
      </div>
    </section>

    <section class="dashboard-section" id="section-run-history" data-section="run-history">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-run-history-body">
        <button type="button" class="section-toggle" aria-label="Collapse run history">−</button>
        <h2>Generation run history</h2>
      </div>
      <div class="section-body" id="section-run-history-body">
    <div class="chip-legend" id="run-status-legend">
      <strong>Run outcome chips</strong> <span class="meta">(per file in generation history)</span>
      <div class="chip-legend-row"><span class="chip completed">Fully covered</span>
        <span class="chip-legend-desc">Last agent run reported no remaining actionable gaps for this source.</span></div>
      <div class="chip-legend-row"><span class="chip inprogress">Gaps remain</span>
        <span class="chip-legend-desc">Run completed but Kover or agent still shows open work on this file.</span></div>
      <div class="chip-legend-row"><span class="chip untouched">Not processed</span>
        <span class="chip-legend-desc">File appears in scope but has not been through a generation run yet.</span></div>
      <div class="chip-legend-row"><span class="chip rejected">Rejected</span>
        <span class="chip-legend-desc">Last run failed validation or was rejected by the pipeline.</span></div>
    </div>
    <div class="filters">
      <input id="search" type="search" placeholder="Search run history..." />
      <select id="moduleFilter" multiple aria-label="Modules"></select>
      <select id="outcomeFilter" multiple aria-label="Outcomes"></select>
    </div>
    <div id="files"></div>
      </div>
    </section>

    <section class="dashboard-section" id="section-reports" data-section="reports">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-reports-body">
        <button type="button" class="section-toggle" aria-label="Collapse coverage reports">−</button>
        <h2>Coverage XML reports (Kover + JaCoCo)</h2>
      </div>
      <div class="section-body" id="section-reports-body">
    <div id="reports" class="meta" style="margin-top:0"></div>
      </div>
    </section>
  </div>
  <script type="application/json" id="report-data">{payload}</script>
  <script>
    const model = JSON.parse(document.getElementById('report-data').textContent);
    const summary = model.summary || {{}};
    document.getElementById('meta').innerHTML = [
      'Generated: ' + (model.generated_at || ''),
      'Root: <code>' + (model.project_root || '') + '</code>',
      'Kover reports: ' + (summary.kover_reports_found || 0) +
      ' · JaCoCo reports: ' + (summary.jacoco_reports_found || 0) +
      ' · Source: ' + (summary.coverage_source || 'kover')
    ].join('<br>');

    const cards = document.getElementById('summary-cards');
    // Prefer UT∪AT union counts when present so Line% / Eff% / pending stay consistent.
    const lineCovered = summary.line_covered_unified != null
      ? summary.line_covered_unified : (summary.line_covered || 0);
    const cardData = [
      ['Lines covered', lineCovered],
      ['Lines deferred (credited)', summary.line_deferred_credited || 0],
      ['Branches deferred (credited)', summary.branch_deferred_credited || 0],
      ['Lines actionable pending', summary.line_actionable_pending || 0],
      ['Line coverage (unified)', (summary.line_coverage_pct || 0) + '%'],
      ['Deferred %', (summary.deferred_coverage_pct || 0) + '%'],
      ['Effective line %', (summary.effective_line_coverage_pct || 0) + '%'],
      ['Branches covered', summary.unified_branch_covered != null ? summary.unified_branch_covered : (summary.branch_covered || 0)],
      ['Branches pending', summary.unified_branch_missed != null ? summary.unified_branch_missed : (summary.branch_missed || 0)],
      ['Files tracked', summary.files_in_kover || 0],
      ['Open gap files', summary.sources_with_open_gaps || 0],
      ['Actionable files', summary.files_actionable || 0],
      ['Deferred-only files', summary.files_deferred_only || 0],
      ['Deferred entries', summary.unreachable_entries_count || 0],
      ['Suspected bugs', summary.suspected_bugs_count || 0],
      ['Sources processed', summary.sources_processed || 0],
      ['Completed', summary.completed || 0],
      ['In progress', summary.in_progress || 0],
      ['Rejected', summary.rejected_status || summary.rejected || 0]
    ];
    function countUp(el, target, suffix) {{
      suffix = suffix || '';
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
        el.textContent = (Number.isInteger(target) ? target.toLocaleString() : target.toFixed(1)) + suffix;
        return;
      }}
      const dur = 750, start = performance.now();
      const isFloat = !Number.isInteger(target);
      function tick(now) {{
        const t = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - t, 4);
        const val = target * eased;
        el.textContent = (isFloat ? val.toFixed(1) : Math.round(val).toLocaleString()) + suffix;
        if (t < 1) requestAnimationFrame(tick);
      }}
      requestAnimationFrame(tick);
    }}
    const barBand = (v) => v < 60 ? 'band-low' : (v <= 85 ? 'band-mid' : 'band-high');
    cards.innerHTML = cardData.map(([label, value], idx) => {{
      const isPct = typeof value === 'string' && value.endsWith('%');
      const num = isPct ? parseFloat(value) || 0 : (value || 0);
      const suffix = isPct ? '%' : '';
      const bar = isPct
        ? '<div class="bar-track"><div class="bar-fill ' + barBand(num) + '" data-w="' + num + '"></div></div>'
        : '';
      return '<div class="card" style="animation-delay:' + Math.min(idx * 40, 480) + 'ms">' +
        '<div class="card-label">' + label + '</div>' +
        '<div class="card-value" data-count="' + num + '" data-suffix="' + suffix + '">0' + suffix + '</div>' +
        bar + '</div>';
    }}).join('');
    requestAnimationFrame(() => {{
      cards.querySelectorAll('.card-value[data-count]').forEach(el => {{
        countUp(el, parseFloat(el.dataset.count), el.dataset.suffix || '');
      }});
      cards.querySelectorAll('.bar-fill').forEach((bar, i) => {{
        setTimeout(() => {{ bar.style.width = Math.min(100, parseFloat(bar.dataset.w) || 0) + '%'; }}, 150 + i * 45);
      }});
    }});

    function esc(s) {{ return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
    function compactRanges(nums) {{
      const numbers = [...new Set((nums || []).map(n => parseInt(n, 10)).filter(n => !Number.isNaN(n)))].sort((a, b) => a - b);
      if (!numbers.length) return 'none';
      const ranges = [];
      let start = numbers[0];
      let prev = numbers[0];
      for (let i = 1; i < numbers.length; i++) {{
        const value = numbers[i];
        if (value === prev + 1) {{
          prev = value;
          continue;
        }}
        ranges.push([start, prev]);
        start = prev = value;
      }}
      ranges.push([start, prev]);
      return ranges.map(([s, e]) => s === e ? String(s) : s + '-' + e).join(', ');
    }}
    function lineNumsHtml(value, deferred) {{
      let text = 'none';
      if (Array.isArray(value)) text = compactRanges(value);
      else if (value !== undefined && value !== null && value !== '' && value !== 'none') text = String(value);
      const cls = 'line-nums' + (deferred ? ' deferred' : '');
      return '<span class="' + cls + '" title="' + esc(text) + '">' + esc(text) + '</span>';
    }}
    function fmtPct(value) {{
      if (value === null || value === undefined) return 'NA';
      return Number(value).toFixed(1) + '%';
    }}
    function pctBandClass(value) {{
      if (value === null || value === undefined) return '';
      if (value < 60) return 'pct-low';
      if (value <= 85) return 'pct-mid';
      return 'pct-high';
    }}
    /** Module table row tint from effective line %: &lt;60 red, 60–85 orange, &gt;85 green. */
    function moduleEffectiveRowClass(effectivePct) {{
      if (effectivePct === null || effectivePct === undefined) return '';
      const v = Number(effectivePct);
      if (Number.isNaN(v) || v < 60) return 'row-eff-low';
      if (v <= 85) return 'row-eff-mid';
      return 'row-eff-high';
    }}
    function isDeferredGap(r) {{
      return gapWorkStatus(r) === 'deferred_only';
    }}
    function selectedValues(select) {{ return new Set([...select.selectedOptions].map(o => o.value)); }}
    function hasPending(r) {{
      return (r.line_missed || 0) > 0 || (r.branch_missed || 0) > 0;
    }}
    function gapWorkStatus(r) {{
      return r.gap_work_status || (hasPending(r) ? 'pending' : 'covered');
    }}
    function gapWorkStatusChip(status) {{
      if (status === 'actionable') {{
        return '<span class="chip actionable" title="Orchestrator would queue this file">actionable</span>';
      }}
      if (status === 'deferred_only') {{
        return '<span class="chip deferred" title="Raw Kover gaps remain but all are deferred in unreachable_coverage.json">deferred only</span>';
      }}
      if (status === 'generated') {{
        return '<span class="chip generated" title="Generated/build output — not under src/main, orchestrator skips">generated</span>';
      }}
      if (status === 'no_exec') {{
        return '<span class="chip noexec" title="Kotlin interface / Hilt @Binds module — no executable lines; cover *Impl instead">no-exec</span>';
      }}
      if (status === 'covered') {{
        return '<span class="chip covered">covered</span>';
      }}
      return '<span class="chip pending">pending</span>';
    }}
    function rowClassForGapStatus(status) {{
      if (status === 'actionable') return 'row-actionable';
      if (status === 'deferred_only') return 'row-deferred';
      if (status === 'generated') return 'row-generated';
      if (status === 'no_exec') return 'row-generated';
      if (status === 'covered') return 'row-covered';
      return 'row-pending';
    }}
    function gapStatusHelp(status) {{
      if (status === 'actionable') {{
        return 'Orchestrator queue: src/main source with actionable Kover gaps after deferrals.';
      }}
      if (status === 'deferred_only') {{
        return 'Kover still reports gaps, but every missed line/branch is recorded as deferred/unreachable.';
      }}
      if (status === 'generated') {{
        return 'Generated Apollo/build file — not under src/main; multifile_orchestrater skips it.';
      }}
      if (status === 'no_exec') {{
        return 'Interface / @Binds-only module: JaCoCo may list the class under androidTest with null %, but there are no executable lines. Do not generate tests — cover *Impl / consumers.';
      }}
      if (status === 'covered') return 'No raw Kover line or branch gaps.';
      return 'Raw Kover gaps present — not yet deferred; needs tests or deferral entry.';
    }}
    function initSectionToggles() {{
      const storageKey = 'koverDashboardSections';
      let saved = {{}};
      try {{
        saved = JSON.parse(sessionStorage.getItem(storageKey) || '{{}}') || {{}};
      }} catch (e) {{ saved = {{}}; }}
      document.querySelectorAll('.dashboard-section').forEach(section => {{
        const id = section.dataset.section || section.id;
        const header = section.querySelector('.section-header');
        const body = section.querySelector('.section-body');
        const btn = section.querySelector('.section-toggle');
        if (!header || !body || !btn) return;
        function setCollapsed(collapsed, instant) {{
          section.classList.toggle('collapsed', collapsed);
          btn.textContent = collapsed ? '+' : '−';
          header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
          btn.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + (id || 'section'));
          if (id) {{
            saved[id] = collapsed;
            try {{ sessionStorage.setItem(storageKey, JSON.stringify(saved)); }} catch (e) {{}}
          }}
          const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          if (instant || reduce) {{
            body.style.maxHeight = collapsed ? '0px' : 'none';
            return;
          }}
          if (collapsed) {{
            body.style.maxHeight = body.scrollHeight + 'px';
            void body.offsetHeight;
            body.style.maxHeight = '0px';
          }} else {{
            body.style.maxHeight = body.scrollHeight + 'px';
            const onEnd = (e) => {{
              if (e.propertyName !== 'max-height') return;
              body.style.maxHeight = 'none';
              body.removeEventListener('transitionend', onEnd);
            }};
            body.addEventListener('transitionend', onEnd);
          }}
        }}
        if (id && saved[id] === true) setCollapsed(true, true);
        function toggle() {{ setCollapsed(!section.classList.contains('collapsed')); }}
        btn.addEventListener('click', (ev) => {{ ev.stopPropagation(); toggle(); }});
        header.addEventListener('click', (ev) => {{
          if (ev.target.closest('a, input, select, button')) return;
          toggle();
        }});
        header.addEventListener('keydown', (ev) => {{
          if (ev.key === 'Enter' || ev.key === ' ') {{ ev.preventDefault(); toggle(); }}
        }});
      }});
    }}
    function initRevealAnimations() {{
      const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const sections = [...document.querySelectorAll('.dashboard-section')];
      if (reduce || !('IntersectionObserver' in window)) return;
      sections.forEach((section, i) => {{
        section.classList.add('reveal');
        section.style.transitionDelay = Math.min(i * 70, 350) + 'ms';
      }});
      const io = new IntersectionObserver((entries) => {{
        entries.forEach(entry => {{
          if (!entry.isIntersecting) return;
          entry.target.classList.add('shown');
          io.unobserve(entry.target);
        }});
      }}, {{ threshold: 0.08, rootMargin: '0px 0px -5% 0px' }});
      sections.forEach(section => io.observe(section));
      setTimeout(() => sections.forEach(section => section.classList.add('shown')), 1500);
    }}
    function deferredHtml(entries) {{
      const rows = entries || [];
      if (!rows.length) return '';
      const items = rows.map(e => {{
        const lineNums = e.lines || [];
        const branchNums = e.branches || [];
        const methods = (e.methods || []).join(', ') || 'none';
        const evidence = e.evidence ? ' · evidence=' + esc(e.evidence) : '';
        const reason = String(e.reason || '').trim() || 'No reason recorded.';
        const probes = [];
        if (lineNums.length) probes.push('lines: ' + lineNumsHtml(lineNums, true));
        if (branchNums.length) probes.push('branches: ' + lineNumsHtml(branchNums, true));
        if (!probes.length) probes.push('probes: none');
        return '<div class="deferred-item">' +
          '<span class="chip deferred">' + esc(e.category || 'deferred') + '</span>' +
          '<div class="meta">' + probes.join(' · ') +
          ' · methods=' + esc(methods) + evidence + '</div>' +
          '<p class="reason">' + esc(reason) + '</p>' +
          '</div>';
      }}).join('');
      return '<div class="deferred-block"><strong>Deferred / unreachable</strong>' + items + '</div>';
    }}

    function coverageSummaryHtml(summary) {{
      if (!summary || typeof summary !== 'object') return '';
      const sections = [];
      const attempted = summary.attempted || [];
      if (attempted.length) {{
        sections.push('<p><strong>Attempted:</strong></p><ul>' + attempted.map(row =>
          '<li>lines=' + esc(JSON.stringify(row.lines || [])) + ' ' +
          esc(row.what || row.note || '') + '</li>'
        ).join('') + '</ul>');
      }}
      const covered = summary.covered || [];
      if (covered.length) {{
        sections.push('<p><strong>Covered (agent):</strong></p><ul>' + covered.map(row =>
          '<li>lines=' + esc(JSON.stringify(row.lines || [])) + ' ' +
          esc(row.note || '') + '</li>'
        ).join('') + '</ul>');
      }}
      const deferred = summary.deferred || [];
      if (deferred.length) {{
        sections.push('<p><strong>Deferred:</strong></p><ul>' + deferred.map(row =>
          '<li>lines=' + esc(JSON.stringify(row.lines || [])) +
          (row.reason ? ' — ' + esc(row.reason) : '') + '</li>'
        ).join('') + '</ul>');
      }}
      if (summary.why_not_improved) {{
        sections.push('<p><strong>Why not improved:</strong> ' + esc(summary.why_not_improved) + '</p>');
      }}
      if (!sections.length) return '';
      return '<div class="deferred-block"><strong>Agent coverage summary</strong>' +
        sections.join('') + '</div>';
    }}

    if (typeof Chart !== 'undefined') {{
      const mods = model.modules_projection || [];
      const lineCovered = (m) => ((m.combined_line_covered != null)
        ? (m.combined_line_covered || 0) : (m.line_covered || 0)) + (m.deferred_credited_lines || 0);
      const lineTotal = (m) => (m.combined_line_total != null)
        ? (m.combined_line_total || 0) : ((m.line_covered || 0) + (m.line_missed || 0));
      const projCovered = mods.length
        ? mods.reduce((acc, m) => acc + lineCovered(m), 0)
        : (summary.line_covered || 0);
      const projPending = mods.length
        ? mods.reduce((acc, m) => acc + Math.max(0, lineTotal(m) - lineCovered(m)), 0)
        : (summary.line_missed || 0);
      const axisColors = {{ tick: '#9aa7b8', grid: '#2a3441' }};
      const tooltipStyle = {{
        backgroundColor: '#101722', borderColor: '#2a3441', borderWidth: 1,
        titleColor: '#e8eef7', bodyColor: '#9aa7b8', padding: 10, cornerRadius: 8
      }};

      function buildCoverageChart() {{
        const el = document.getElementById('coverageChart');
        if (!el || el.__chartBuilt) return;
        el.__chartBuilt = true;
        new Chart(el, {{
          type: 'doughnut',
          data: {{
            labels: ['Covered (executed)', 'Actionable pending'],
            datasets: [{{
              data: [projCovered, projPending],
              backgroundColor: ['#3ecf8e', '#f07178']
            }}]
          }},
          options: {{
            animation: {{ duration: 1100, easing: 'easeOutQuart' }},
            plugins: {{
              title: {{ display: true, text: 'Project lines (Kover | JaCoCo union)', color: '#e8eef7' }},
              legend: {{ labels: {{ color: '#e8eef7', usePointStyle: true, boxWidth: 8, boxHeight: 8 }} }},
              tooltip: tooltipStyle
            }}
          }}
        }});
      }}

      function buildModuleChart() {{
        const el = document.getElementById('moduleChart');
        if (!el || el.__chartBuilt) return;
        el.__chartBuilt = true;
        new Chart(el, {{
          type: 'bar',
          data: {{
            labels: mods.map(m => m.module || '?'),
            datasets: [
              {{
                label: 'Covered',
                data: mods.map(lineCovered),
                backgroundColor: 'rgba(62,207,142,0.85)',
                borderRadius: 3, maxBarThickness: 46
              }},
              {{
                label: 'Pending',
                data: mods.map(m => Math.max(0, lineTotal(m) - lineCovered(m))),
                backgroundColor: 'rgba(240,113,120,0.85)',
                borderRadius: 3, maxBarThickness: 46
              }}
            ]
          }},
          options: {{
            animation: {{
              duration: 1000,
              easing: 'easeOutQuart',
              delay: (ctx) => (ctx.type === 'data' && ctx.mode === 'default') ? ctx.dataIndex * 80 : 0
            }},
            plugins: {{
              title: {{ display: true, text: 'Module lines (Kover | JaCoCo union)', color: '#e8eef7' }},
              legend: {{ labels: {{ color: '#e8eef7', usePointStyle: true, boxWidth: 8, boxHeight: 8 }} }},
              tooltip: tooltipStyle
            }},
            scales: {{
              x: {{ stacked: true, ticks: {{ color: axisColors.tick }}, grid: {{ color: axisColors.grid }} }},
              y: {{ stacked: true, ticks: {{ color: axisColors.tick }}, grid: {{ color: axisColors.grid }} }}
            }}
          }}
        }});
      }}

      // Animate charts only when scrolled into view; fall back to immediate build.
      function revealCharts() {{
        const panels = document.querySelectorAll('.charts .panel');
        const map = {{ coverageChart: buildCoverageChart, moduleChart: buildModuleChart }};
        if (!('IntersectionObserver' in window)) {{
          panels.forEach(p => {{ const b = map[p.querySelector('canvas')?.id]; if (b) b(); }});
          panels.forEach(p => p.classList.add('in-view'));
          return;
        }}
        const io = new IntersectionObserver((entries, obs) => {{
          entries.forEach(entry => {{
            if (!entry.isIntersecting) return;
            const canvas = entry.target.querySelector('canvas');
            const build = canvas && map[canvas.id];
            entry.target.classList.add('in-view');
            if (build) build();
            obs.unobserve(entry.target);
          }});
        }}, {{ threshold: 0.25 }});
        panels.forEach(p => io.observe(p));
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!reduce) {{
          panels.forEach(panel => {{
            panel.addEventListener('pointermove', (event) => {{
              const bounds = panel.getBoundingClientRect();
              const x = (event.clientX - bounds.left) / bounds.width - 0.5;
              const y = (event.clientY - bounds.top) / bounds.height - 0.5;
              panel.style.setProperty('--chart-tilt', (x * 4).toFixed(2) + 'deg');
              panel.style.setProperty('--chart-pitch', (y * -3).toFixed(2) + 'deg');
            }});
            panel.addEventListener('pointerleave', () => {{
              panel.style.setProperty('--chart-tilt', '0deg');
              panel.style.setProperty('--chart-pitch', '0deg');
            }});
          }});
        }}
      }}
      revealCharts();
    }}

    function openDetailRow(el, open) {{
      el.classList.toggle('open', open);
      const btn = el.querySelector('.expand-btn');
      if (btn) btn.textContent = open ? '−' : '+';
    }}

    function makeSortable(table, render, keyOf) {{
      let sortKey = null;
      let sortDir = 1;
      const headers = table.querySelectorAll('th[data-k]');
      const apply = () => {{
        headers.forEach(h => {{
          h.classList.remove('sorted-asc', 'sorted-desc');
          const arrow = h.querySelector('.sort-arrow');
          if (arrow) arrow.remove();
        }});
        render(sortKey, sortDir);
        if (sortKey) {{
          const active = [...headers].find(h => h.dataset.k === sortKey);
          if (!active) return;
          active.classList.add(sortDir === 1 ? 'sorted-asc' : 'sorted-desc');
          const arrow = document.createElement('span');
          arrow.className = 'sort-arrow';
          arrow.textContent = sortDir === 1 ? '▲' : '▼';
          active.appendChild(arrow);
        }}
      }};
      headers.forEach(h => {{
        h.title = 'Click to sort';
        h.addEventListener('click', () => {{
          const k = h.dataset.k;
          if (sortKey === k) sortDir = -sortDir;
          else {{ sortKey = k; sortDir = 1; }}
          apply();
        }});
      }});
      return apply;
    }}

    const compareRows = (a, b, key, dir, keyOf) => {{
      const av = keyOf(a, key);
      const bv = keyOf(b, key);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    }};
    const numericKeys = new Set(['files', 'line_coverage_pct', 'branch_coverage_pct', 'instruction_coverage_pct',
      'method_coverage_pct', 'class_coverage_pct', 'deferred_coverage_pct', 'effective_line_coverage_pct',
      'deferred_entries_count', 'kover_line_pct', 'kover_branch_pct', 'kover_class_pct', 'kover_method_pct',
      'kover_instruction_pct', 'jacoco_line_pct', 'jacoco_branch_pct', 'jacoco_class_pct', 'jacoco_method_pct',
      'jacoco_instruction_pct', 'combined_line_pct', 'combined_branch_pct', 'combined_class_pct',
      'combined_method_pct', 'combined_instruction_pct', 'total_line_pct', 'total_branch_pct']);
    const keyOf = (row, key) => {{
      const v = row[key];
      if (v === null || v === undefined) return null;
      if (typeof v === 'number') return v;
      if (numericKeys.has(key)) {{
        const n = parseFloat(v);
        return Number.isNaN(n) ? null : n;
      }}
      return v;
    }};

    const moduleRows = model.modules_projection || [];
    const moduleBody = document.querySelector('#moduleTable tbody');
    const moduleFoot = document.querySelector('#moduleTable tfoot');
    const MODULE_METRICS = ['class', 'method', 'branch', 'line', 'instruction'];
    function coveragePct(covered, missed) {{
      const c = Number(covered) || 0;
      const m = Number(missed) || 0;
      const total = c + m;
      // Nothing instrumented → NA (not 0%)
      if (!total) return null;
      return Math.round((1000 * c) / total) / 10;
    }}
    function metricDisplayPct(row, metric) {{
      const covered = Number(row[metric + '_covered']);
      const missed = Number(row[metric + '_missed']);
      if (!Number.isNaN(covered) && !Number.isNaN(missed) && (covered + missed) === 0) {{
        return null;
      }}
      const pct = row['combined_' + metric + '_pct'];
      if (pct === null || pct === undefined) return null;
      return pct;
    }}
    function cappedSumPct(left, right) {{
      if (left === null || left === undefined) {{
        if (right === null || right === undefined) return null;
        return Math.min(100, Math.round(Number(right) * 10) / 10);
      }}
      if (right === null || right === undefined) {{
        return Math.min(100, Math.round(Number(left) * 10) / 10);
      }}
      return Math.min(100, Math.round((Number(left) + Number(right)) * 10) / 10);
    }}
    function aggregateModuleOverall(rows) {{
      const sumKeys = [
        'files', 'line_covered', 'line_missed', 'branch_covered', 'branch_missed',
        'instruction_covered', 'instruction_missed', 'method_covered', 'method_missed',
        'class_covered', 'class_missed', 'deferred_entries_count', 'deferred_credited_lines',
        'combined_line_covered', 'combined_line_total',
      ];
      MODULE_METRICS.forEach(metric => {{
        ['kover', 'jacoco'].forEach(engine => {{
          sumKeys.push(engine + '_' + metric + '_covered');
          sumKeys.push(engine + '_' + metric + '_missed');
        }});
      }});
      const tot = Object.fromEntries(sumKeys.map(k => [k, 0]));
      (rows || []).forEach(r => {{
        sumKeys.forEach(k => {{ tot[k] += Number(r[k]) || 0; }});
      }});
      MODULE_METRICS.forEach(metric => {{
        tot['kover_' + metric + '_pct'] = coveragePct(
          tot['kover_' + metric + '_covered'], tot['kover_' + metric + '_missed']);
        tot['jacoco_' + metric + '_pct'] = coveragePct(
          tot['jacoco_' + metric + '_covered'], tot['jacoco_' + metric + '_missed']);
        const elementSets = (rows || []).reduce((acc, row) => {{
          const payload = row.metric_elements && row.metric_elements[metric];
          if (!payload) return acc;
          (payload.covered || []).forEach(value => acc.covered.add(String(value)));
          (payload.total || []).forEach(value => acc.total.add(String(value)));
          return acc;
        }}, {{ covered: new Set(), total: new Set() }});
        if (metric === 'line' && tot.combined_line_total > 0) {{
          tot['combined_line_pct'] = coveragePct(
            tot.combined_line_covered,
            tot.combined_line_total - tot.combined_line_covered);
        }} else if (elementSets.total.size) {{
          tot['combined_' + metric + '_pct'] = coveragePct(
            elementSets.covered.size,
            elementSets.total.size - elementSets.covered.size);
        }} else {{
          tot['combined_' + metric + '_pct'] = cappedSumPct(
            tot['kover_' + metric + '_pct'], tot['jacoco_' + metric + '_pct']);
        }}
      }});
      tot.class_coverage_pct = tot.combined_class_pct;
      tot.method_coverage_pct = tot.combined_method_pct;
      tot.branch_coverage_pct = tot.combined_branch_pct;
      tot.line_coverage_pct = tot.combined_line_pct;
      tot.instruction_coverage_pct = tot.combined_instruction_pct;
      // Effective (Overall) uses the true per-line union base when present, so it
      // matches the per-module Effective column.
      const unionTotal = tot.combined_line_total || 0;
      const unionCovered = tot.combined_line_covered || 0;
      const lineTotal = unionTotal > 0 ? unionTotal : tot.line_covered + tot.line_missed;
      const lineMissed = lineTotal - (unionTotal > 0 ? unionCovered : tot.line_covered);
      const deferredCredited = Math.min(tot.deferred_credited_lines, lineMissed);
      tot.deferred_credited_lines = deferredCredited;
      tot.deferred_coverage_pct = lineTotal
        ? Math.round((1000 * deferredCredited) / lineTotal) / 10 : null;
      tot.effective_line_coverage_pct = lineTotal
        ? Math.round((1000 * ((unionTotal > 0 ? unionCovered : tot.line_covered) + deferredCredited)) / lineTotal) / 10 : null;
      return tot;
    }}
    function moduleMetricCells(row) {{
      const cells = [];
      MODULE_METRICS.forEach((metric, mIdx) => {{
        const pct = metricDisplayPct(row, metric);
        const sep = (mIdx === 0) ? ' sep-left' : '';
        const band = ' ' + pctBandClass(pct);
        cells.push('<td class="pct-cell col-pct' + sep + band + ' g-comb">' + fmtPct(pct) + '</td>');
      }});
      return cells.join('');
    }}
    function renderModuleOverall() {{
      if (!moduleFoot) return;
      moduleFoot.innerHTML = '';
      if (!moduleRows.length) return;
      const tot = aggregateModuleOverall(moduleRows);
      const effectivePct = tot.effective_line_coverage_pct;
      const tr = document.createElement('tr');
      tr.className = 'row-overall ' + moduleEffectiveRowClass(effectivePct);
      const deferredCount = tot.deferred_entries_count || 0;
      const deferredCell = deferredCount > 0
        ? '<span class="chip deferred">' + deferredCount + ' · ' +
          (tot.deferred_credited_lines || 0) + ' credited</span>'
        : '<span class="meta">none</span>';
      const effBand = pctBandClass(
        (effectivePct === null || effectivePct === undefined) ? 0 : Number(effectivePct)
      );
      tr.innerHTML = '<td></td><td class="text-cell" title="Sum of all modules">Overall</td><td class="num-cell">' +
        (tot.files || 0) + '</td>' + moduleMetricCells(tot) +
        '<td class="pct-cell">' + fmtPct(tot.deferred_coverage_pct) + '</td>';
      moduleFoot.appendChild(tr);
    }}
    function renderModuleRows(sortKey, sortDir) {{
      const list = moduleRows.slice();
      if (sortKey) list.sort((a, b) => compareRows(a, b, sortKey, sortDir, keyOf));
      moduleBody.innerHTML = '';
      list.forEach((m, mIdx) => {{
        const tr = document.createElement('tr');
        const effectivePct = m.effective_line_coverage_pct;
        tr.className = moduleEffectiveRowClass(effectivePct);
        tr.style.animationDelay = Math.min(mIdx * 25, 300) + 'ms';
        const deferredCount = m.deferred_entries_count || 0;
        const deferredCell = deferredCount > 0
          ? '<span class="chip deferred" title="' + esc(m.deferred_lines_text || '') + '">' +
            deferredCount + ' · ' + (m.deferred_credited_lines || 0) + ' credited</span>'
          : '<span class="meta">none</span>';
        const effBand = pctBandClass(
          (effectivePct === null || effectivePct === undefined) ? 0 : Number(effectivePct)
        );
        tr.innerHTML = '<td>' + (deferredCount
          ? '<button type="button" class="expand-btn" data-mod-idx="' + mIdx + '">+</button>' : '') +
          '</td><td class="text-cell" title="' + esc(m.module) + '">' + esc(m.module) + '</td><td class="num-cell">' +
          (m.files||0) + '</td>' + moduleMetricCells(m) +
          '<td class="pct-cell">' + fmtPct(m.deferred_coverage_pct) + '</td>';
        moduleBody.appendChild(tr);

        if (deferredCount > 0) {{
          const detail = document.createElement('tr');
          detail.className = 'detail-row';
          detail.dataset.modDetailFor = String(mIdx);
          const cats = Object.entries(m.deferred_categories || {{}}).map(([k,v]) =>
            esc(k) + '=' + v).join(', ');
          detail.innerHTML = '<td colspan="10"><div class="detail-clip"><div class="detail-inner"><div class="detail">' +
            '<div class="meta">Deferred files: ' + (m.deferred_files_count || 0) +
            ' · unique lines: ' + (m.deferred_unique_lines_count || 0) +
            ' · credited to completed: ' + (m.deferred_credited_lines || 0) +
            ' · actionable pending: ' + (m.actionable_pending_lines || 0) +
            ' · deferred %: ' + (m.deferred_coverage_pct || 0) +
            '% · effective %: ' + (m.effective_line_coverage_pct || 0) + '%' +
            (cats ? ' · categories: ' + cats : '') +
            (m.deferred_lines_text ? ' · ranges: ' + lineNumsHtml(m.deferred_lines_text, true) : '') +
            '</div>' + deferredHtml(m.deferred_entries) + '</div></div></div></td>';
          moduleBody.appendChild(detail);
        }}
      }});
      renderModuleOverall();
    }}
    renderModuleRows('combined_line_pct', 1);
    makeSortable(document.getElementById('moduleTable'), renderModuleRows, keyOf);
    moduleBody.addEventListener('click', (ev) => {{
      const btn = ev.target.closest('button.expand-btn[data-mod-idx]');
      if (!btn) return;
      const idx = btn.getAttribute('data-mod-idx');
      const detail = moduleBody.querySelector('tr.detail-row[data-mod-detail-for="' + idx + '"]');
      if (!detail) return;
      openDetailRow(detail, !detail.classList.contains('open'));
    }});

    const deferredHost = document.getElementById('moduleDeferred');
    const deferredModules = moduleRows.filter(m => (m.deferred_entries_count || 0) > 0);
    if (!deferredModules.length) {{
      deferredHost.innerHTML = '<div class="panel meta">No deferred entries in unreachable_coverage.json.</div>';
    }} else {{
      deferredHost.innerHTML = deferredModules.map(m => {{
        const cats = Object.entries(m.deferred_categories || {{}}).map(([k,v]) =>
          '<span class="chip deferred">' + esc(k) + ' ×' + v + '</span>').join(' ');
        const byFile = {{}};
        (m.deferred_entries || []).forEach(e => {{
          const name = e.source_name || '(unknown)';
          (byFile[name] = byFile[name] || []).push(e);
        }});
        const fileBlocks = Object.keys(byFile).sort().map(name => {{
          const entries = byFile[name];
          const lines = [...new Set(entries.flatMap(e => e.lines || []))].sort((a,b) => a-b);
          const branches = [...new Set(entries.flatMap(e => e.branches || []))].sort((a,b) => a-b);
          const probeBits = [];
          if (lines.length) probeBits.push('lines: ' + lineNumsHtml(lines, true));
          if (branches.length) probeBits.push('branches: ' + lineNumsHtml(branches, true));
          return '<div class="deferred-item"><strong>' + esc(name) + '</strong>' +
            '<div class="meta">' + (probeBits.join(' · ') || 'probes: none') + '</div>' +
            deferredHtml(entries) + '</div>';
        }}).join('');
        return '<div class="panel" style="margin-bottom:12px">' +
          '<h3 style="margin:0 0 8px;font-size:1rem">' + esc(m.module) + '</h3>' +
          '<div class="meta" style="margin-bottom:8px">' +
          (m.deferred_entries_count || 0) + ' entries · ' +
          (m.deferred_files_count || 0) + ' files · ' +
          (m.deferred_unique_lines_count || 0) + ' unique lines · ' +
          (m.deferred_unique_branches_count || 0) + ' unique branches · ' +
          (m.deferred_credited_lines || 0) + ' credited · ' +
          (m.deferred_credited_branches || 0) + ' branches credited · ' +
          (m.deferred_coverage_pct || 0) + '% deferred · ' +
          (m.effective_line_coverage_pct || 0) + '% effective' +
          (m.deferred_lines_text ? ' · ' + lineNumsHtml(m.deferred_lines_text, true) : '') +
          '</div><div style="margin-bottom:8px">' + cats + '</div>' +
          fileBlocks + '</div>';
      }}).join('');
    }}

    const bugsHost = document.getElementById('suspectedBugs');
    const bugs = model.suspected_bugs || [];
    if (!bugs.length) {{
      bugsHost.innerHTML = '<div class="panel meta">No suspected assertion-trap bugs recorded.</div>';
    }} else {{
      bugsHost.innerHTML = '<div class="panel"><table><thead><tr>' +
        '<th>Source</th><th>Module</th><th>Lines</th><th>Sidecar</th><th>Snippet</th></tr></thead><tbody>' +
        bugs.map(b => '<tr>' +
          '<td><code>' + esc(b.source_name || b.source || '') + '</code></td>' +
          '<td>' + esc(b.module || '') + '</td>' +
          '<td>' + esc((b.lines || []).join(', ') || 'none') + '</td>' +
          '<td><code>' + esc(b.sidecar || '') + '</code></td>' +
          '<td class="meta">' + esc((b.gradle_snippet || '').slice(0, 280)) + '</td>' +
          '</tr>').join('') +
        '</tbody></table></div>';
    }}

    const projModuleFilter = document.getElementById('projModuleFilter');
    const coverageFilter = document.getElementById('coverageFilter');
    const fileRows = (model.files_projection || []).slice();
    [...new Set(fileRows.map(r => r.module || ''))].filter(Boolean).sort().forEach(m => {{
      const o = document.createElement('option'); o.value = m; o.textContent = m; projModuleFilter.appendChild(o);
    }});
    let fileSortKey = null;
    let fileSortDir = 1;

    function coverageSourceChips(r) {{
      if (r.gap_work_status === 'no_exec' || r.non_executable) {{
        return '<span class="chip noexec" title="Interface / @Binds module — no executable bytecode">no-exec</span>';
      }}
      const src = r.coverage_sources || [];
      if (!src.length) return '';
      return src.map(s => {{
        const zero = s === 'jacoco' && r.jacoco_zero_hits;
        const label = s === 'jacoco' ? (zero ? 'androidTest 0 hits' : 'androidTest')
          : (s === 'kover' ? 'unitTest' : s);
        const cls = s === 'jacoco' ? (zero ? 'chip deferred' : 'chip inprogress') : 'chip completed';
        return '<span class="' + cls + '">' + esc(label) + '</span>';
      }}).join(' ');
    }}

    function engineMetricBlock(name, present, linePct, branchPct, covered, pending, missingLabel) {{
      if (!present) {{
        return '<div><strong>' + name + '</strong> <span class="meta">' + esc(missingLabel) + '</span></div>';
      }}
      return '<div style="margin-top:8px"><strong>' + name + '</strong>' +
        '<span class="meta"> · line ' + fmtPct(linePct) + ' · branch ' + fmtPct(branchPct) + '</span>' +
        '<div>Covered lines: ' + lineNumsHtml(covered) + '</div>' +
        '<div>Need coverage: ' + lineNumsHtml(pending) + '</div></div>';
    }}

    function renderProjFiles() {{
      const q = document.getElementById('projSearch').value.toLowerCase();
      const selected = selectedValues(projModuleFilter);
      const cov = coverageFilter.value;
      const tbody = document.querySelector('#fileTable tbody');
      tbody.innerHTML = '';
      const visible = fileRows.filter(r => {{
        if (selected.size && !selected.has(r.module || '')) return false;
        if (cov === 'jacoco' && !(r.coverage_sources || []).includes('jacoco')) return false;
        if (cov === 'hide_no_exec' && (r.gap_work_status === 'no_exec' || r.non_executable)) return false;
        if (cov === 'no_exec_only' && !(r.gap_work_status === 'no_exec' || r.non_executable)) return false;
        const sources = r.coverage_sources || [];
        const hay = [r.module, r.package, r.source_name, r.kover_covered_lines_text,
          r.jacoco_covered_lines_text, r.kover_missed_lines_text, r.jacoco_missed_lines_text,
          r.both_covered_lines_text, r.uncovered_lines_text, r.gap_work_status,
          sources.join(' '), 'unittest', 'androidtest', 'no-exec', 'interface'].join(' ').toLowerCase();
        return !q || hay.includes(q);
      }});
      if (fileSortKey) {{
        visible.sort((a, b) => compareRows(a, b, fileSortKey, fileSortDir, keyOf));
      }} else {{
        visible.sort((a, b) => {{
          const av = a.total_line_pct == null ? -1 : a.total_line_pct;
          const bv = b.total_line_pct == null ? -1 : b.total_line_pct;
          if (av !== bv) return av - bv;
          const ab = a.total_branch_pct == null ? -1 : a.total_branch_pct;
          const bb = b.total_branch_pct == null ? -1 : b.total_branch_pct;
          if (ab !== bb) return ab - bb;
          return String(a.source_name || '').localeCompare(String(b.source_name || ''));
        }});
      }}
      visible.forEach((r, idx) => {{
        const sources = r.coverage_sources || [];
        const tr = document.createElement('tr');
        tr.style.animationDelay = Math.min(idx * 18, 300) + 'ms';
        tr.innerHTML = '<td><button type="button" class="expand-btn" data-idx="' + idx +
          '">+</button></td><td class="text-cell" title="' + esc(r.module) + '">' + esc(r.module) + '</td>' +
          '<td class="wide-text-cell" title="' + esc(r.source_name) + (r.package ? ' · ' + esc(r.package) : '') + '">' +
          esc(r.source_name) +
          '<div class="meta">' + esc(r.package) + '</div>' +
          '<div class="chip-row">' + coverageSourceChips(r) + '</div></td><td class="pct-cell ' + pctBandClass(r.total_line_pct) + '">' +
          fmtPct(r.total_line_pct) + '</td><td class="pct-cell ' + pctBandClass(r.total_branch_pct) + '">' +
          fmtPct(r.total_branch_pct) + '</td>';
        tbody.appendChild(tr);

        const detail = document.createElement('tr');
        detail.className = 'detail-row';
        detail.dataset.detailFor = String(idx);
        detail.innerHTML = '<td colspan="5"><div class="detail-clip"><div class="detail-inner">' +
          '<div><strong>Overall covered lines:</strong> ' +
          lineNumsHtml(r.both_covered_lines_text || r.both_covered_lines) + '</div>' +
          '<div><strong>Overall uncovered lines:</strong> ' +
          lineNumsHtml(r.uncovered_lines_text || r.uncovered_lines) + '</div>' +
          '</div></div></td>';
        tbody.appendChild(detail);
      }});
      tbody.querySelectorAll('.expand-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
          const id = btn.getAttribute('data-idx');
          const row = tbody.querySelector('tr.detail-row[data-detail-for="' + id + '"]');
          if (!row) return;
          openDetailRow(row, !row.classList.contains('open'));
        }});
      }});
    }}
    const fileTableApply = makeSortable(document.getElementById('fileTable'), (key, dir) => {{
      fileSortKey = key;
      fileSortDir = dir;
      renderProjFiles();
    }}, keyOf);
    document.getElementById('projSearch').addEventListener('input', renderProjFiles);
    projModuleFilter.addEventListener('change', renderProjFiles);
    coverageFilter.addEventListener('change', renderProjFiles);

    const reports = document.getElementById('reports');
    function reportGroup(title, paths) {{
      const items = (paths || []).map(path => '<li>' + esc(path) + '</li>').join('');
      return '<div style="margin-bottom:12px"><strong>' + esc(title) + '</strong>' +
        (items ? '<ul class="meta" style="margin:6px 0 0">' + items + '</ul>' :
          '<div class="meta">none found</div>') + '</div>';
    }}
    reports.innerHTML = reportGroup('Kover XML', model.kover_reports || []) +
      reportGroup('JaCoCo XML', model.jacoco_reports || []);
    if (!(model.kover_reports || []).length && !(model.jacoco_reports || []).length) {{
      reports.innerHTML = '<li>No Kover/JaCoCo XML reports found under build/reports.</li>';
    }}

    const moduleFilter = document.getElementById('moduleFilter');
    const outcomeFilter = document.getElementById('outcomeFilter');
    const modules = new Set(summary.modules || []);
    const outcomeSet = new Set();
    (model.files || []).forEach(file => {{
      if (file.module) modules.add(file.module);
      const statusKey = file.status || file.outcome || '';
      if (statusKey) outcomeSet.add(statusKey);
    }});
    [...modules].sort().forEach(m => {{ const o=document.createElement('option'); o.value=m; o.textContent=m; moduleFilter.appendChild(o); }});
    [...outcomeSet].sort().forEach(b => {{ const o=document.createElement('option'); o.value=b; o.textContent=b; outcomeFilter.appendChild(o); }});

    function statusChipHtml(file) {{
      const status = file.status || '';
      if (status === 'completed') return '<span class="chip completed">Fully covered</span>';
      if (status === 'inprogress') return '<span class="chip inprogress">Gaps remain</span>';
      if (status === 'untouched') return '<span class="chip untouched">Not processed</span>';
      return '<span class="chip rejected">' + esc(status === 'rejected' ? 'Rejected' : (status || file.outcome || 'Rejected')) + '</span>';
    }}

    function renderFiles() {{
      const q = document.getElementById('search').value.toLowerCase();
      const selectedModules = selectedValues(moduleFilter);
      const selectedOutcomes = selectedValues(outcomeFilter);
      const filesEl = document.getElementById('files');
      filesEl.innerHTML = '';
      let fileCardIdx = 0;
      (model.files || []).forEach(file => {{
        if (selectedModules.size && !selectedModules.has(file.module || '')) return;
        const statusKey = file.status || file.outcome || '';
        if (selectedOutcomes.size && !selectedOutcomes.has(statusKey)) return;
        const deferredReasons = (file.unreachable_entries || []).map(e => e.reason || e.category || '').join(' ');
        const fileHay = [file.name, file.module, file.path, file.outcome, file.status,
          file.coverage_note, file.last_run_note, file.evidence,
          JSON.stringify(file.coverage_summary || {{}}),
          file.missed_lines_text, file.missed_branches_text, deferredReasons].join(' ').toLowerCase();
        if (q && !fileHay.includes(q)) return;

        const card = document.createElement('div');
        card.className = 'file-card';
        card.style.animationDelay = Math.min(fileCardIdx * 30, 300) + 'ms';
        fileCardIdx += 1;
        const mapTicks = (file.line_map || []).map(pos => '<i style="left:' + pos + '%"></i>').join('')
          + (file.branch_map || []).map(pos => '<i class="branch" style="left:' + pos + '%"></i>').join('');
        const deferredChip = (file.unreachable_entries || []).length
          ? ' <span class="chip deferred">deferred</span>' : '';
        const workChip = file.gap_work_status
          ? ' ' + gapWorkStatusChip(file.gap_work_status) : '';
        card.innerHTML =
          '<div class="file-head"><div><strong>' + esc(file.name) + '</strong> ' + statusChipHtml(file) + workChip + deferredChip +
          '<br><span class="meta">' + esc(file.module) + ' · ' + esc(file.path) + '</span>' +
          '<div class="line-map">' + mapTicks + '</div></div><div>' +
          (file.line_covered || 0) + ' covered · <span class="cell-pending">' +
          (file.missed_lines_count || 0) + ' Kover missed lines</span> · ' +
          (file.missed_branches_count || 0) + ' Kover missed branches · ' +
          '<span class="' + ((file.actionable_missed_lines_count || 0) > 0 || (file.actionable_missed_branches_count || 0) > 0 ? 'cell-pending' : 'cell-covered') + '">' +
          (file.actionable_missed_lines_count || 0) + ' actionable lines</span></div>' +
          '<span class="file-chevron" aria-hidden="true">▸</span></div>' +
          '<div class="file-body"><div class="file-body-inner"></div></div>';
        const body = card.querySelector('.file-body-inner');
        const coverageNote = file.coverage_note || file.evidence || '';
        if (coverageNote) {{
          body.innerHTML += '<p><strong>Coverage:</strong> ' + esc(coverageNote) + '</p>';
        }}
        if (file.last_run_note) {{
          body.innerHTML += '<p><strong>Last run:</strong> ' + esc(file.last_run_note) + '</p>';
        }}
        body.innerHTML += coverageSummaryHtml(file.coverage_summary);
        const fileDeferred = (file.unreachable_entries || []).length > 0
          && (file.actionable_missed_lines_count || 0) === 0
          && (file.actionable_missed_branches_count || 0) === 0
          && ((file.missed_lines_count || 0) > 0 || (file.missed_branches_count || 0) > 0);
        body.innerHTML +=
          '<p><strong>Kover missed line numbers:</strong> <span class="pending-hl' + (fileDeferred ? ' deferred' : '') + '">' +
          lineNumsHtml(file.missed_lines_text || file.missed_lines, fileDeferred) + '</span></p>' +
          '<p><strong>Kover missed branch line numbers:</strong> <span class="pending-hl' + (fileDeferred ? ' deferred' : '') + '">' +
          lineNumsHtml(file.missed_branches_text || file.missed_branches, fileDeferred) + '</span></p>' +
          '<p><strong>Actionable line numbers:</strong> <span class="actionable-hl' + (fileDeferred ? ' deferred' : '') + '">' +
          lineNumsHtml(file.actionable_pending_lines || file.actionable_missed_lines, fileDeferred) + '</span></p>' +
          '<p><strong>Actionable branch line numbers:</strong> <span class="actionable-hl' + (fileDeferred ? ' deferred' : '') + '">' +
          lineNumsHtml(file.actionable_pending_branches || file.actionable_missed_branches, fileDeferred) + '</span></p>' +
          deferredHtml(file.unreachable_entries);
        const hasSummary = !!(file.coverage_summary && (
          (file.coverage_summary.attempted || []).length ||
          (file.coverage_summary.covered || []).length ||
          (file.coverage_summary.deferred || []).length ||
          file.coverage_summary.why_not_improved
        ));
        if ((file.unreachable_entries || []).length || file.status === 'inprogress' || hasSummary) {{
          card.classList.add('open');
        }}
        card.querySelector('.file-head').addEventListener('click', () => card.classList.toggle('open'));
        filesEl.appendChild(card);
      }});
    }}
    document.getElementById('search').addEventListener('input', renderFiles);
    moduleFilter.addEventListener('change', renderFiles);
    outcomeFilter.addEventListener('change', renderFiles);
    renderFiles();
    initSectionToggles();
    initRevealAnimations();

    (function initThemeSwitch() {{
      const root = document.documentElement;
      const key = 'koverDashboardTheme';
      let theme = 'dark';
      try {{ theme = localStorage.getItem(key) || 'dark'; }} catch (e) {{}}
      root.setAttribute('data-theme', theme);
      const buttons = document.querySelectorAll('.theme-switch [data-theme-set]');
      const sync = () => {{
        buttons.forEach(btn => {{
          btn.classList.toggle('active', btn.getAttribute('data-theme-set') === theme);
        }});
      }};
      sync();
      buttons.forEach(btn => {{
        btn.addEventListener('click', () => {{
          theme = btn.getAttribute('data-theme-set') || 'dark';
          root.setAttribute('data-theme', theme);
          try {{ localStorage.setItem(key, theme); }} catch (e) {{}}
          sync();
        }});
      }});
    }})();

    (function initModuleEngineToggles() {{
      const table = document.getElementById('moduleTable');
      const seg = document.getElementById('moduleEngineSeg');
      if (!table || !seg) return;
      const key = 'koverDashboardEngines';
      let state = {{ ut: true, at: true, comb: true }};
      try {{
        const raw = JSON.parse(localStorage.getItem(key) || 'null');
        if (raw && typeof raw === 'object') state = Object.assign(state, raw);
      }} catch (e) {{}}
      // Keep at least one engine section visible
      if (!state.ut && !state.at && !state.comb) state.comb = true;
      const apply = () => {{
        table.classList.toggle('hide-ut', !state.ut);
        table.classList.toggle('hide-at', !state.at);
        table.classList.toggle('hide-comb', !state.comb);
        seg.querySelectorAll('[data-engine]').forEach(btn => {{
          const eng = btn.getAttribute('data-engine');
          btn.classList.toggle('active', !!state[eng]);
        }});
        try {{ localStorage.setItem(key, JSON.stringify(state)); }} catch (e) {{}}
      }};
      apply();
      seg.addEventListener('click', (ev) => {{
        const btn = ev.target.closest('[data-engine]');
        if (!btn) return;
        const eng = btn.getAttribute('data-engine');
        const next = !state[eng];
        const activeCount = ['ut', 'at', 'comb'].filter(k => state[k]).length;
        if (!next && activeCount <= 1) return;
        state[eng] = next;
        apply();
      }});
    }})();
  </script>
</body>
</html>"""


def render_diagnostics_index(model: dict, written_paths: list[str] | None = None) -> str:
    summary = model.get("summary") or {}
    href = html.escape(str(model.get("href") or "diagnostics.html"))
    paths = written_paths or []
    path_items = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in paths)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Kover Reports</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f1419; color:#e8eef7; margin:0; padding:24px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; max-width:900px; }}
  .card {{ background:#171d25; border:1px solid #2a3441; border-radius:12px; padding:16px; }}
  .card-label {{ color:#9aa7b8; }} .card-value {{ font-size:2rem; font-weight:700; margin:8px 0; }}
  .meta {{ color:#9aa7b8; font-size:0.85rem; }}
</style></head><body>
<h1>Kover Coverage Reports</h1>
<div class="cards">
  <a class="card" href="{href}" style="text-decoration:none;color:inherit">
    <div class="card-label">{html.escape(str(model.get("title") or "Kover Coverage"))}</div>
    <div class="card-value">{summary.get("line_coverage_pct", 0)}%</div>
    <div class="meta">covered {summary.get("line_covered", 0)} ·
      pending {summary.get("line_missed", 0)} ·
      files {summary.get("files_in_kover", 0)}</div>
  </a>
</div>
<ul class="meta">{path_items or "<li>No HTML reports written yet.</li>"}</ul>
</body></html>"""

# --- helper/dashboard/build_diagnostics_report.py ---
import argparse
import shutil
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from UnitTest_gen.core.diagnostics import (  # noqa: E402
    DIAGNOSTICS_PATH,
    build_diagnostics,
    derive_coverage_status,
    derive_gap_work_status,
    write_diagnostics,
)
from UnitTest_gen.core.io import compact_ranges  # noqa: E402

DEFAULT_HTML_REPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "htmlreport"
HELPER_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
VENDOR_CHART_JS = HELPER_VENDOR_DIR / "chart.umd.min.js"
VENDOR_DASHBOARD_CSS = HELPER_VENDOR_DIR / "dashboard.css"
# Legacy path — migrate into helper/vendor if still present
_LEGACY_CHART_JS = DEFAULT_HTML_REPORT_DIR / "vendor" / "chart.umd.min.js"
CHART_JS_DOWNLOAD_URLS = (
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
    "https://unpkg.com/chart.js@4.4.1/dist/chart.umd.min.js",
)


def _line_map(missed_lines, max_line: int | None = None) -> list[float]:
    numbers = sorted({int(n) for n in (missed_lines or []) if str(n).isdigit() or isinstance(n, int)})
    if not numbers:
        return []
    ceiling = max(max_line or 0, numbers[-1], 1)
    return [round((n / ceiling) * 100, 2) for n in numbers]


def _last_run_note(*, evidence: str, outcome: str) -> str:
    evidence = str(evidence or "").strip()
    outcome = str(outcome or "").strip()
    if evidence and outcome:
        return f"{outcome}: {evidence}"
    return evidence or outcome


def _coverage_note_for_processed(
    *,
    status: str,
    missed_lines: list,
    missed_branches: list,
    actionable_lines: int,
    actionable_branches: int,
    unreachable_entries: list,
    store_evidence: str,
) -> str:
    """Coverage truth for the card — never trust stale store evidence alone."""
    if status == "completed":
        if not missed_lines and not missed_branches:
            return "Live coverage: fully covered (no missed lines or branches)."
        if actionable_lines == 0 and actionable_branches == 0 and unreachable_entries:
            return "Live coverage residuals are deferred as unreachable."
        return "Live coverage: fully covered (no actionable missed lines or branches)."
    if status == "inprogress":
        return "Live coverage still has uncovered line/branch numbers (see below)."
    if status == "rejected":
        note = str(store_evidence or "").strip()
        return note or "Last run was not accepted."
    return str(store_evidence or "").strip()


def diagnostics_to_dashboard_model(diagnostics: dict) -> dict:
    """Project the engine model into the HTML dashboard shape.

    Coverage counts/status for processed sources come from live unified ``coverage``
    (Kover + JaCoCo when available), minus deferred unreachable lines.
    """
    files = []
    for record in diagnostics.get("sources") or []:
        live = record.get("coverage") or record.get("kover") or {}
        missed_lines = list(live.get("missed_lines") or [])
        missed_branches = list(live.get("missed_branches") or [])
        line_covered = int(live.get("line_covered") or 0)
        coverage_sources = list(live.get("coverage_sources") or ["kover"])
        unreachable_entries = list(record.get("unreachable_entries") or [])
        store_evidence = str(record.get("evidence") or "")
        outcome = str(record.get("outcome") or "")
        derived = derive_coverage_status(
            accepted=bool(record.get("accepted")),
            outcome=outcome,
            missed_lines=missed_lines,
            missed_branches=missed_branches,
            unreachable_entries=unreachable_entries,
        )
        status = derived["status"]
        source_resolvable = bool(
            record.get("source") and Path(str(record.get("source"))).is_file()
        )
        gap_work_status = derive_gap_work_status(
            line_missed=len(missed_lines),
            branch_missed=len(missed_branches),
            actionable_lines_count=derived["actionable_missed_lines_count"],
            actionable_branches_count=derived["actionable_missed_branches_count"],
            source_resolvable=source_resolvable,
        )
        coverage_note = _coverage_note_for_processed(
            status=status,
            missed_lines=missed_lines,
            missed_branches=missed_branches,
            actionable_lines=derived["actionable_missed_lines_count"],
            actionable_branches=derived["actionable_missed_branches_count"],
            unreachable_entries=unreachable_entries,
            store_evidence=store_evidence,
        )
        last_run_note = _last_run_note(evidence=store_evidence, outcome=outcome)
        coverage_summary = record.get("coverage_summary")
        if not isinstance(coverage_summary, dict):
            coverage_summary = None
        files.append(
            {
                "name": record.get("source_name") or Path(str(record.get("source") or "")).name,
                "path": record.get("source") or "",
                "module": record.get("module") or live.get("module") or "",
                "accepted": bool(record.get("accepted")),
                "outcome": outcome,
                "status": status,
                "gap_work_status": gap_work_status,
                "source_resolvable": source_resolvable,
                "coverage_note": coverage_note,
                "last_run_note": last_run_note,
                "evidence": coverage_note,
                "coverage_summary": coverage_summary,
                "missed_lines_count": len(missed_lines),
                "missed_branches_count": len(missed_branches),
                "actionable_missed_lines_count": derived["actionable_missed_lines_count"],
                "actionable_missed_branches_count": derived["actionable_missed_branches_count"],
                "actionable_pending_lines": compact_ranges(derived["actionable_missed_lines"]),
                "actionable_pending_branches": compact_ranges(derived["actionable_missed_branches"]),
                "line_covered": line_covered,
                "missed_lines_text": compact_ranges(missed_lines),
                "missed_branches_text": compact_ranges(missed_branches),
                "line_map": _line_map(missed_lines),
                "branch_map": _line_map(missed_branches),
                "coverage_sources": coverage_sources,
                "unreachable_entries": unreachable_entries,
            }
        )

    untouched_note = "Not processed by the generator yet; numbers are from live coverage (Kover + JaCoCo when present)."
    for gap in diagnostics.get("untouched_gaps") or []:
        missed_lines = list(gap.get("missed_lines") or [])
        missed_branches = list(gap.get("missed_branches") or [])
        unreachable_entries = list(gap.get("unreachable_entries") or [])
        derived = derive_coverage_status(
            accepted=False,
            outcome="untouched",
            missed_lines=missed_lines,
            missed_branches=missed_branches,
            unreachable_entries=unreachable_entries,
        )
        gap_work_status = str(gap.get("gap_work_status") or derive_gap_work_status(
            line_missed=int(gap.get("line_missed") or 0),
            branch_missed=int(gap.get("branch_missed") or 0),
            actionable_lines_count=derived["actionable_missed_lines_count"],
            actionable_branches_count=derived["actionable_missed_branches_count"],
            source_resolvable=bool(gap.get("source_resolvable")),
        ))
        files.append(
            {
                "name": gap.get("source_name") or "",
                "path": gap.get("source_path") or f"{gap.get('package') or ''}/{gap.get('source_name') or ''}",
                "module": gap.get("module") or "",
                "accepted": False,
                "outcome": "untouched",
                "status": derived["status"],
                "gap_work_status": gap_work_status,
                "source_resolvable": bool(gap.get("source_resolvable")),
                "coverage_note": untouched_note,
                "last_run_note": "",
                "evidence": untouched_note,
                "coverage_summary": None,
                "missed_lines_count": int(gap.get("line_missed") or 0),
                "missed_branches_count": int(gap.get("branch_missed") or 0),
                "actionable_missed_lines_count": derived["actionable_missed_lines_count"],
                "actionable_missed_branches_count": derived["actionable_missed_branches_count"],
                "actionable_pending_lines": gap.get("actionable_pending_lines") or compact_ranges(
                    derived["actionable_missed_lines"]
                ),
                "actionable_pending_branches": gap.get("actionable_pending_branches") or compact_ranges(
                    derived["actionable_missed_branches"]
                ),
                "line_covered": int(gap.get("line_covered") or 0),
                "missed_lines_text": gap.get("pending_lines") or "",
                "missed_branches_text": gap.get("pending_branches") or "",
                "line_map": list(gap.get("line_map") or _line_map(missed_lines)),
                "branch_map": list(gap.get("branch_map") or _line_map(missed_branches)),
                "unreachable_entries": unreachable_entries,
            }
        )

    summary = dict(diagnostics.get("summary") or {})
    summary["completed"] = sum(1 for f in files if f.get("status") == "completed")
    summary["in_progress"] = sum(1 for f in files if f.get("status") == "inprogress")
    summary["rejected_status"] = sum(1 for f in files if f.get("status") == "rejected")
    summary["suspected_bugs_count"] = len(diagnostics.get("suspected_bugs") or [])

    return {
        "title": "Coverage Dashboard (Kover + JaCoCo)",
        "href": "diagnostics.html",
        "generated_at": diagnostics.get("generated_at"),
        "project_root": diagnostics.get("project_root"),
        "summary": summary,
        "kover_reports": diagnostics.get("kover_reports") or [],
        "jacoco_reports": diagnostics.get("jacoco_reports") or [],
        "modules_projection": diagnostics.get("modules_projection") or [],
        "files_projection": diagnostics.get("files_projection") or [],
        "files": files,
        "suspected_bugs": diagnostics.get("suspected_bugs") or [],
    }


def _is_real_chart_js(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 1000


def _write_chart_bytes(dest: Path, data: bytes, *, source: str) -> Path:
    if len(data) <= 1000:
        raise RuntimeError(f"Downloaded Chart.js too small ({len(data)} bytes) from {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def _download_chart_js(dest: Path) -> Path:
    """Download Chart.js UMD build into ``dest`` (urllib, then curl fallback)."""
    import subprocess
    import urllib.error
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    headers = {
        "User-Agent": "UnitTest_gen/1.0 (+https://github.com/; Chart.js vendor bootstrap)",
        "Accept": "*/*",
    }
    for url in CHART_JS_DOWNLOAD_URLS:
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return _write_chart_bytes(dest, response.read(), source=url)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc

    curl = shutil.which("curl")
    if curl:
        for url in CHART_JS_DOWNLOAD_URLS:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            try:
                subprocess.run(
                    [
                        curl,
                        "-fsSL",
                        "-A",
                        headers["User-Agent"],
                        "--max-time",
                        "60",
                        "-o",
                        str(tmp),
                        url,
                    ],
                    check=True,
                    capture_output=True,
                )
                data = tmp.read_bytes()
                return _write_chart_bytes(dest, data, source=f"curl:{url}")
            except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
                last_error = exc
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    raise FileNotFoundError(
        "Chart.js vendor missing and download failed. "
        f"Tried {', '.join(CHART_JS_DOWNLOAD_URLS)}. Last error: {last_error}"
    )


def ensure_chart_vendor(out_dir: Path) -> None:
    """Ensure Chart.js + dashboard CSS live under ``helper/vendor`` and are copied into the report."""
    HELPER_VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    dest_dir = (Path(out_dir) / "vendor").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_chart = (dest_dir / "chart.umd.min.js").resolve()
    vendor_chart = VENDOR_CHART_JS.resolve()

    # One-time migrate from legacy htmlreport/vendor → helper/vendor
    if not _is_real_chart_js(vendor_chart) and _is_real_chart_js(_LEGACY_CHART_JS):
        shutil.copy2(_LEGACY_CHART_JS, vendor_chart)

    if not _is_real_chart_js(vendor_chart):
        if _is_real_chart_js(dest_chart):
            shutil.copy2(dest_chart, vendor_chart)
        else:
            print(f"⬇️  Chart.js missing — downloading into {vendor_chart}")
            _download_chart_js(vendor_chart)

    if vendor_chart != dest_chart:
        shutil.copy2(vendor_chart, dest_chart)

    # Themes / motion stylesheet (source of truth under helper/vendor)
    css_src = VENDOR_DASHBOARD_CSS.resolve()
    css_dest = (dest_dir / "dashboard.css").resolve()
    if css_src.is_file():
        shutil.copy2(css_src, css_dest)
    elif not css_dest.is_file():
        raise FileNotFoundError(
            f"dashboard.css missing at {css_src} — expected under helper/vendor/"
        )


def write_diagnostic_reports(
    project_root: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> list[str]:
    root = Path(project_root).resolve()
    out_dir = Path(output_dir).resolve() if output_dir else DEFAULT_HTML_REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ensure_chart_vendor(out_dir)
    diagnostics = build_diagnostics(root)
    write_diagnostics(root, path=DIAGNOSTICS_PATH)
    model = diagnostics_to_dashboard_model(diagnostics)
    paths = []
    dash = out_dir / "diagnostics.html"
    dash.write_text(render_diagnostic_dashboard(model), encoding="utf-8")
    paths.append(str(dash))
    index = out_dir / "diagnostics_index.html"
    index.write_text(render_diagnostics_index(model, paths), encoding="utf-8")
    paths.append(str(index))
    hub = out_dir / "kover_reports_index.html"
    hub.write_text(render_diagnostics_index(model, paths), encoding="utf-8")
    paths.append(str(hub))
    return paths


def refresh_diagnostic_reports(project_root: str | Path) -> list[str]:
    return write_diagnostic_reports(project_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline hybrid (Kover + JaCoCo) coverage dashboard.")
    parser.add_argument("-R", "--project-root", required=True, help="Android/Gradle project root.")
    parser.add_argument("-o", "--output-dir", default=None, help="HTML output directory.")
    args = parser.parse_args(argv)
    written = write_diagnostic_reports(args.project_root, output_dir=args.output_dir)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




















