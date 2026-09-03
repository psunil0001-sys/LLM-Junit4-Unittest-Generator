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
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="vendor/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0f1419; --panel: #171d25; --text: #e8eef7; --muted: #9aa7b8;
      --good: #3ecf8e; --warn: #f0b429; --bad: #f07178; --accent: #6cb6ff; --border: #2a3441;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(180deg, #0b1016 0%, #121820 100%); color: var(--text); }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    h1, h2 {{ margin: 0 0 12px; font-weight: 600; }}
    h2 {{ font-size: 1.05rem; color: var(--accent); margin-top: 24px; }}
    .meta {{ color: var(--muted); margin-bottom: 18px; line-height: 1.5; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0 22px; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; }}
    .card-label {{ color: var(--muted); font-size: 0.82rem; }}
    .card-value {{ font-size: 1.6rem; font-weight: 700; margin: 6px 0; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; overflow:auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; cursor: pointer; }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }}
    .filters input, .filters select {{
      background: #101722; color: var(--text); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 10px; min-width: 180px;
    }}
    .chip {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 0.75rem;
      margin: 2px 4px 2px 0; border: 1px solid var(--border); color: var(--muted); }}
    .chip.completed {{ background: rgba(62,207,142,0.18); color: var(--good); border-color: var(--good); }}
    .chip.inprogress {{ background: rgba(240,180,41,0.18); color: var(--warn); border-color: var(--warn); }}
    .chip.rejected {{ background: rgba(240,113,120,0.18); color: var(--bad); border-color: var(--bad); }}
    .chip.untouched {{ background: rgba(108,182,255,0.14); color: var(--accent); border-color: var(--accent); }}
    .chip.covered {{ background: rgba(62,207,142,0.18); color: var(--good); border-color: var(--good); }}
    .chip.pending {{ background: rgba(240,113,120,0.18); color: var(--bad); border-color: var(--bad); }}
    .chip.actionable {{ background: rgba(240,113,120,0.28); color: #ffb4b8; border-color: var(--bad); font-weight: 600; }}
    .chip.deferred {{ background: rgba(240,180,41,0.18); color: var(--warn); border-color: var(--warn); }}
    .chip.generated {{ background: rgba(108,182,255,0.14); color: var(--accent); border-color: var(--accent); }}
    .deferred-block {{ margin-top: 10px; padding: 10px 12px; border: 1px solid rgba(240,180,41,0.35);
      border-radius: 8px; background: rgba(240,180,41,0.08); }}
    .deferred-block .reason {{ color: var(--text); margin: 6px 0 0; line-height: 1.45; }}
    .deferred-item {{ margin-top: 10px; }}
    .deferred-item:first-child {{ margin-top: 0; }}
    .file-card {{ border: 1px solid var(--border); border-radius: 12px; margin-bottom: 12px; background: #121820; }}
    .file-head {{ padding: 12px 14px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }}
    .file-body {{ display: none; padding: 0 14px 14px; border-top: 1px solid var(--border); }}
    .file-card.open .file-body {{ display: block; }}
    .line-map {{ height: 10px; background: #243041; border-radius: 999px; position: relative; margin: 8px 0; }}
    .line-map i {{ position: absolute; width: 2px; height: 100%; background: var(--bad); top: 0; }}
    .line-map i.branch {{ background: var(--warn); }}
    tr.row-pending {{ background: rgba(240,113,120,0.08); }}
    tr.row-actionable {{ background: rgba(240,113,120,0.12); }}
    tr.row-deferred {{ background: rgba(240,180,41,0.08); }}
    tr.row-generated {{ background: rgba(108,182,255,0.06); }}
    tr.row-covered {{ background: rgba(62,207,142,0.06); }}
    td.cell-pending {{ color: var(--bad); font-weight: 600; }}
    td.cell-covered {{ color: var(--good); }}
    td.cell-deferred {{ color: var(--warn); font-weight: 600; }}
    .line-nums {{
      display: inline-block; max-width: min(420px, 36vw); overflow-x: auto;
      white-space: nowrap; vertical-align: top; line-height: 1.35;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.82rem;
    }}
    .line-nums.deferred {{ color: var(--warn); }}
    .pending-hl.deferred, .actionable-hl.deferred {{ color: var(--warn); }}
    td.col-line-nums {{ max-width: 420px; }}
    .expand-btn {{ background: transparent; border: 1px solid var(--border); color: var(--accent);
      border-radius: 6px; padding: 2px 8px; cursor: pointer; font-size: 0.75rem; }}
    .detail-row td {{ background: #101722; color: var(--muted); font-size: 0.85rem; }}
    .detail-row .pending-hl {{ color: var(--bad); font-weight: 600; }}
    .detail-row .actionable-hl {{ color: #ffb4b8; font-weight: 600; }}
    .detail-row .meta-note {{ color: var(--muted); margin-top: 8px; line-height: 1.45; }}
    .dashboard-section {{ margin-bottom: 22px; border: 1px solid var(--border); border-radius: 12px; background: rgba(23,29,37,0.35); }}
    .section-header {{
      display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer;
      border-bottom: 1px solid transparent; user-select: none;
    }}
    .dashboard-section:not(.collapsed) .section-header {{ border-bottom-color: var(--border); }}
    .section-header h2 {{ margin: 0; flex: 1; font-size: 1.05rem; color: var(--accent); }}
    .section-toggle {{
      background: transparent; border: 1px solid var(--border); color: var(--accent);
      border-radius: 6px; width: 28px; height: 28px; cursor: pointer; font-size: 1rem; line-height: 1;
      flex-shrink: 0;
    }}
    .section-body {{ padding: 14px; }}
    .dashboard-section.collapsed .section-body {{ display: none; }}
    .dashboard-section.collapsed .section-header {{ border-bottom-color: transparent; }}
    .chip-legend {{ display: grid; gap: 8px; margin: 0 0 14px; padding: 12px 14px;
      background: var(--panel); border: 1px solid var(--border); border-radius: 10px; font-size: 0.85rem; }}
    .chip-legend-row {{ display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px 12px; }}
    .chip-legend-row .chip {{ flex-shrink: 0; margin: 0; }}
    .chip-legend-desc {{ color: var(--muted); line-height: 1.4; flex: 1; min-width: 200px; }}
    @media (max-width: 900px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav" style="margin-bottom:14px"><a href="diagnostics_index.html" style="color:#6cb6ff">← Reports hub</a></div>
    <h1>{title}</h1>
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
    <div class="panel"><table id="moduleTable"><thead>
      <tr><th></th><th data-k="module">Module</th><th data-k="files">Files</th>
      <th data-k="class_coverage_pct">Class %</th>
      <th data-k="method_coverage_pct">Method %</th>
      <th data-k="branch_coverage_pct">Branch %</th>
      <th data-k="line_coverage_pct">Line %</th>
      <th data-k="instruction_coverage_pct">Instruction %</th>
      <th data-k="deferred_coverage_pct">Deferred %</th>
      <th data-k="effective_line_coverage_pct">Effective %</th>
      <th data-k="deferred_entries_count">Deferred</th></tr>
    </thead><tbody></tbody></table></div>
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

    <section class="dashboard-section" id="section-file-coverage" data-section="file-coverage">
      <div class="section-header" role="button" tabindex="0" aria-expanded="true" aria-controls="section-file-coverage-body">
        <button type="button" class="section-toggle" aria-label="Collapse file coverage">−</button>
        <h2>File coverage (Kover + JaCoCo unified)</h2>
      </div>
      <div class="section-body" id="section-file-coverage-body">
    <div class="chip-legend" id="work-status-legend">
      <strong>Work status chips</strong> <span class="meta">(column in table below — what each means for the test generator)</span>
      <div class="chip-legend-row"><span class="chip actionable">actionable</span>
        <span class="chip-legend-desc">Real Kover gaps remain after deferrals. The orchestrator would queue this <code>src/main</code> file for new tests.</span></div>
      <div class="chip-legend-row"><span class="chip deferred">deferred only</span>
        <span class="chip-legend-desc">Kover still reports missed lines/branches, but every gap is recorded in <code>unreachable_coverage.json</code> with a reason. Counts toward deferred/effective %.</span></div>
      <div class="chip-legend-row"><span class="chip pending">pending</span>
        <span class="chip-legend-desc">Raw Kover gaps with no deferral entry yet — needs tests or an explicit deferral after attempt.</span></div>
      <div class="chip-legend-row"><span class="chip covered">covered</span>
        <span class="chip-legend-desc">No missed lines or branches in the latest Kover XML for this file.</span></div>
      <div class="chip-legend-row"><span class="chip generated">generated</span>
        <span class="chip-legend-desc">Generated or build output (e.g. Apollo) — not under <code>src/main</code>; orchestrator skips it.</span></div>
    </div>
    <div class="filters">
      <input id="projSearch" type="search" placeholder="Search module/package/file..." />
      <select id="projModuleFilter" multiple aria-label="Modules"></select>
      <select id="coverageFilter" aria-label="Coverage filter">
        <option value="all" selected>All files</option>
        <option value="actionable">Actionable (orchestrator queue)</option>
        <option value="deferred_only">Deferred only</option>
        <option value="generated">Generated / not targeted</option>
        <option value="pending">Any raw Kover pending</option>
        <option value="covered">Fully covered</option>
      </select>
    </div>
    <div class="panel"><table id="fileTable"><thead>
      <tr><th></th><th data-k="module">Module</th><th data-k="source_name">File</th>
      <th data-k="line_covered">Line covered</th><th data-k="line_missed">Line pending</th>
      <th data-k="line_coverage_pct">Line %</th>
      <th data-k="branch_covered">Br covered</th><th data-k="branch_missed">Br pending</th>
      <th data-k="branch_coverage_pct">Branch %</th>
      <th data-k="gap_work_status">Work status</th>
      <th data-k="pending_lines">Kover pending lines</th>
      <th data-k="actionable_pending_lines">Actionable lines</th></tr>
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
    <ul id="reports" class="meta" style="margin-top:0"></ul>
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
    const cardData = [
      ['Lines covered', summary.line_covered || 0],
      ['Lines deferred (credited)', summary.line_deferred_credited || 0],
      ['Lines actionable pending', summary.line_actionable_pending || 0],
      ['Line coverage (unified)', (summary.line_coverage_pct || 0) + '%'],
      ['Deferred %', (summary.deferred_coverage_pct || 0) + '%'],
      ['Effective line %', (summary.effective_line_coverage_pct || 0) + '%'],
      ['Branches covered', summary.branch_covered || 0],
      ['Branches pending', summary.branch_missed || 0],
      ['Files tracked', summary.files_in_kover || 0],
      ['Open gap files', summary.sources_with_open_gaps || 0],
      ['Actionable files', summary.files_actionable || 0],
      ['Deferred-only files', summary.files_deferred_only || 0],
      ['Generated pending', summary.files_generated_pending || 0],
      ['Deferred entries', summary.unreachable_entries_count || 0],
      ['Sources processed', summary.sources_processed || 0],
      ['Completed', summary.completed || 0],
      ['In progress', summary.in_progress || 0],
      ['Rejected', summary.rejected_status || summary.rejected || 0]
    ];
    cards.innerHTML = cardData.map(([label, value]) =>
      '<div class="card"><div class="card-label">' + label + '</div><div class="card-value">' + value + '</div></div>'
    ).join('');

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
      if (status === 'covered') {{
        return '<span class="chip covered">covered</span>';
      }}
      return '<span class="chip pending">pending</span>';
    }}
    function rowClassForGapStatus(status) {{
      if (status === 'actionable') return 'row-actionable';
      if (status === 'deferred_only') return 'row-deferred';
      if (status === 'generated') return 'row-generated';
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
        function setCollapsed(collapsed) {{
          section.classList.toggle('collapsed', collapsed);
          btn.textContent = collapsed ? '+' : '−';
          header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
          btn.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + (id || 'section'));
          if (id) {{
            saved[id] = collapsed;
            try {{ sessionStorage.setItem(storageKey, JSON.stringify(saved)); }} catch (e) {{}}
          }}
        }}
        if (id && saved[id] === true) setCollapsed(true);
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
    function deferredHtml(entries) {{
      const rows = entries || [];
      if (!rows.length) return '';
      const items = rows.map(e => {{
        const lines = compactRanges(e.lines || []);
        const methods = (e.methods || []).join(', ') || 'none';
        const evidence = e.evidence ? ' · evidence=' + esc(e.evidence) : '';
        const reason = String(e.reason || '').trim() || 'No reason recorded.';
        return '<div class="deferred-item">' +
          '<span class="chip deferred">' + esc(e.category || 'deferred') + '</span>' +
          '<div class="meta">lines: ' + lineNumsHtml(lines === 'none' ? [] : (e.lines || []), true) +
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
      try {{
        new Chart(document.getElementById('coverageChart'), {{
          type: 'doughnut',
          data: {{
            labels: ['Covered (executed)', 'Deferred (completed)', 'Actionable pending'],
            datasets: [{{
              data: [
                summary.line_covered || 0,
                summary.line_deferred_credited || 0,
                summary.line_actionable_pending || 0
              ],
              backgroundColor: ['#3ecf8e', '#f0b429', '#f07178']
            }}]
          }},
          options: {{
            plugins: {{
              title: {{
                display: true,
                text: 'Project lines (deferred counts as completed)',
                color: '#e8eef7'
              }},
              legend: {{ labels: {{ color: '#e8eef7' }} }}
            }}
          }}
        }});
        const mods = model.modules_projection || [];
        new Chart(document.getElementById('moduleChart'), {{
          type: 'bar',
          data: {{
            labels: mods.map(m => m.module || '?'),
            datasets: [
              {{
                label: 'Covered',
                data: mods.map(m => m.line_covered || 0),
                backgroundColor: '#3ecf8e'
              }},
              {{
                label: 'Deferred (completed)',
                data: mods.map(m => m.deferred_credited_lines || 0),
                backgroundColor: '#f0b429'
              }},
              {{
                label: 'Actionable pending',
                data: mods.map(m => m.actionable_pending_lines || 0),
                backgroundColor: '#f07178'
              }}
            ]
          }},
          options: {{
            plugins: {{
              title: {{
                display: true,
                text: 'Module lines (deferred counts as completed)',
                color: '#e8eef7'
              }},
              legend: {{ labels: {{ color: '#e8eef7' }} }}
            }},
            scales: {{
              x: {{ stacked: true, ticks: {{ color: '#9aa7b8' }}, grid: {{ color: '#2a3441' }} }},
              y: {{ stacked: true, ticks: {{ color: '#9aa7b8' }}, grid: {{ color: '#2a3441' }} }}
            }}
          }}
        }});
      }} catch (err) {{
        console.error('Chart render failed', err);
      }}
    }}

    const moduleRows = model.modules_projection || [];
    const moduleBody = document.querySelector('#moduleTable tbody');
    moduleRows.forEach((m, mIdx) => {{
      const tr = document.createElement('tr');
      const pending = (m.line_missed || 0) > 0 || (m.branch_missed || 0) > 0
        || (m.instruction_missed || 0) > 0 || (m.method_missed || 0) > 0
        || (m.class_missed || 0) > 0;
      const deferredCount = m.deferred_entries_count || 0;
      if (deferredCount > 0) tr.className = 'row-deferred';
      else if (pending) tr.className = 'row-pending';
      else tr.className = 'row-covered';
      const pct = (key) => (m[key] || 0) + '%';
      const deferredCell = deferredCount > 0
        ? '<span class="chip deferred" title="' + esc(m.deferred_lines_text || '') + '">' +
          deferredCount + ' · ' + (m.deferred_credited_lines || 0) + ' credited</span>'
        : '<span class="meta">none</span>';
      tr.innerHTML = '<td>' + (deferredCount
        ? '<button type="button" class="expand-btn" data-mod-idx="' + mIdx + '">+</button>' : '') +
        '</td><td>' + esc(m.module) + '</td><td>' + (m.files||0) + '</td><td>' +
        pct('class_coverage_pct') + '</td><td>' + pct('method_coverage_pct') + '</td><td>' +
        pct('branch_coverage_pct') + '</td><td>' + pct('line_coverage_pct') + '</td><td>' +
        pct('instruction_coverage_pct') + '</td><td>' + pct('deferred_coverage_pct') + '</td><td>' +
        pct('effective_line_coverage_pct') + '</td><td>' + deferredCell + '</td>';
      moduleBody.appendChild(tr);

      if (deferredCount > 0) {{
        const detail = document.createElement('tr');
        detail.className = 'detail-row';
        detail.style.display = 'none';
        detail.dataset.modDetailFor = String(mIdx);
        const cats = Object.entries(m.deferred_categories || {{}}).map(([k,v]) =>
          esc(k) + '=' + v).join(', ');
        detail.innerHTML = '<td colspan="11"><div class="detail">' +
          '<div class="meta">Deferred files: ' + (m.deferred_files_count || 0) +
          ' · unique lines: ' + (m.deferred_unique_lines_count || 0) +
          ' · credited to completed: ' + (m.deferred_credited_lines || 0) +
          ' · actionable pending: ' + (m.actionable_pending_lines || 0) +
          ' · deferred %: ' + (m.deferred_coverage_pct || 0) +
          '% · effective %: ' + (m.effective_line_coverage_pct || 0) + '%' +
          (cats ? ' · categories: ' + cats : '') +
          (m.deferred_lines_text ? ' · ranges: ' + lineNumsHtml(m.deferred_lines_text, true) : '') +
          '</div>' + deferredHtml(m.deferred_entries) + '</div></td>';
        moduleBody.appendChild(detail);
      }}
    }});
    moduleBody.addEventListener('click', (ev) => {{
      const btn = ev.target.closest('button.expand-btn[data-mod-idx]');
      if (!btn) return;
      const idx = btn.getAttribute('data-mod-idx');
      const detail = moduleBody.querySelector('tr.detail-row[data-mod-detail-for="' + idx + '"]');
      if (!detail) return;
      const open = detail.style.display !== 'none';
      detail.style.display = open ? 'none' : '';
      btn.textContent = open ? '+' : '−';
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
          return '<div class="deferred-item"><strong>' + esc(name) + '</strong>' +
            '<div class="meta">lines: ' + lineNumsHtml(lines, true) + '</div>' +
            deferredHtml(entries) + '</div>';
        }}).join('');
        return '<div class="panel" style="margin-bottom:12px">' +
          '<h3 style="margin:0 0 8px;font-size:1rem">' + esc(m.module) + '</h3>' +
          '<div class="meta" style="margin-bottom:8px">' +
          (m.deferred_entries_count || 0) + ' entries · ' +
          (m.deferred_files_count || 0) + ' files · ' +
          (m.deferred_unique_lines_count || 0) + ' unique lines · ' +
          (m.deferred_credited_lines || 0) + ' credited · ' +
          (m.deferred_coverage_pct || 0) + '% deferred · ' +
          (m.effective_line_coverage_pct || 0) + '% effective' +
          (m.deferred_lines_text ? ' · ' + lineNumsHtml(m.deferred_lines_text, true) : '') +
          '</div><div style="margin-bottom:8px">' + cats + '</div>' +
          fileBlocks + '</div>';
      }}).join('');
    }}

    const projModuleFilter = document.getElementById('projModuleFilter');
    const coverageFilter = document.getElementById('coverageFilter');
    const fileRows = model.files_projection || [];
    [...new Set(fileRows.map(r => r.module || ''))].filter(Boolean).sort().forEach(m => {{
      const o = document.createElement('option'); o.value = m; o.textContent = m; projModuleFilter.appendChild(o);
    }});

    function lineMapHtml(r) {{
      const lineTicks = (r.line_map || []).map(pos => '<i style="left:' + pos + '%"></i>').join('');
      const branchTicks = (r.branch_map || []).map(pos => '<i class="branch" style="left:' + pos + '%"></i>').join('');
      if (!lineTicks && !branchTicks) return '<div class="meta">No pending lines</div>';
      return '<div class="line-map">' + lineTicks + branchTicks + '</div>';
    }}

    function renderProjFiles() {{
      const q = document.getElementById('projSearch').value.toLowerCase();
      const selected = selectedValues(projModuleFilter);
      const cov = coverageFilter.value;
      const tbody = document.querySelector('#fileTable tbody');
      tbody.innerHTML = '';
      fileRows.forEach((r, idx) => {{
        if (selected.size && !selected.has(r.module || '')) return;
        const workStatus = gapWorkStatus(r);
        const pending = hasPending(r);
        if (cov === 'actionable' && workStatus !== 'actionable') return;
        if (cov === 'deferred_only' && workStatus !== 'deferred_only') return;
        if (cov === 'generated' && workStatus !== 'generated') return;
        if (cov === 'pending' && !pending) return;
        if (cov === 'covered' && pending) return;
        const hay = [r.module, r.package, r.source_name, r.pending_lines, r.pending_branches,
          r.actionable_pending_lines, r.actionable_pending_branches, r.gap_work_status].join(' ').toLowerCase();
        if (q && !hay.includes(q)) return;
        const tr = document.createElement('tr');
        tr.className = rowClassForGapStatus(workStatus);
        const statusChip = gapWorkStatusChip(workStatus);
        const linePending = (r.line_missed || 0) > 0;
        const branchPending = (r.branch_missed || 0) > 0;
        const actionablePending = (r.actionable_missed_lines_count || 0) > 0
          || (r.actionable_missed_branches_count || 0) > 0;
        const deferredGap = isDeferredGap(r);
        const lineCellClass = deferredGap ? 'cell-deferred' : (linePending ? 'cell-pending' : '');
        const actionableCellClass = deferredGap ? 'cell-deferred' : (actionablePending ? 'cell-pending' : 'cell-covered');
        tr.innerHTML = '<td><button type="button" class="expand-btn" data-idx="' + idx +
          '">+</button></td><td>' + esc(r.module) + '</td><td>' + esc(r.source_name) +
          '<div class="meta">' + esc(r.package) + '</div></td><td class="cell-covered">' +
          (r.line_covered||0) + '</td><td class="' + (linePending ? (deferredGap ? 'cell-deferred' : 'cell-pending') : '') + '">' +
          (r.line_missed||0) + '</td><td>' + (r.line_coverage_pct||0) +
          '%</td><td class="cell-covered">' + (r.branch_covered||0) +
          '</td><td class="' + (branchPending ? (deferredGap ? 'cell-deferred' : 'cell-pending') : '') + '">' +
          (r.branch_missed||0) + '</td><td>' + (r.branch_coverage_pct||0) +
          '%</td><td>' + statusChip + '</td><td class="col-line-nums ' + lineCellClass + '">' +
          lineNumsHtml(r.pending_lines || r.missed_lines, deferredGap) + '</td><td class="col-line-nums ' + actionableCellClass + '">' +
          lineNumsHtml(r.actionable_pending_lines || r.actionable_missed_lines, deferredGap && !actionablePending) + '</td>';
        tbody.appendChild(tr);

        const detail = document.createElement('tr');
        detail.className = 'detail-row';
        detail.style.display = 'none';
        detail.dataset.detailFor = String(idx);
        detail.innerHTML = '<td colspan="12">' +
          '<div class="meta-note">' + esc(gapStatusHelp(workStatus)) + '</div>' +
          '<div><strong>Kover pending lines:</strong> <span class="pending-hl' + (deferredGap ? ' deferred' : '') + '">' +
          lineNumsHtml(r.pending_lines || r.missed_lines, deferredGap) + '</span></div>' +
          '<div><strong>Kover pending branches:</strong> <span class="pending-hl' + (deferredGap ? ' deferred' : '') + '">' +
          lineNumsHtml(r.pending_branches || r.missed_branches, deferredGap) + '</span></div>' +
          '<div><strong>Actionable lines:</strong> <span class="actionable-hl' + (deferredGap && !actionablePending ? ' deferred' : '') + '">' +
          lineNumsHtml(r.actionable_pending_lines || r.actionable_missed_lines, deferredGap && !actionablePending) + '</span></div>' +
          '<div><strong>Actionable branches:</strong> <span class="actionable-hl' + (deferredGap && !actionablePending ? ' deferred' : '') + '">' +
          lineNumsHtml(r.actionable_pending_branches || r.actionable_missed_branches, deferredGap && !actionablePending) + '</span></div>' +
          '<div><strong>Branches covered / pending:</strong> ' +
          (r.branch_covered||0) + ' / ' + (r.branch_missed||0) + '</div>' +
          '<div style="margin-top:8px"><strong>Uncovered map</strong> (red=lines, yellow=branches)</div>' +
          lineMapHtml(r) +
          deferredHtml(r.unreachable_entries) +
          '</td>';
        tbody.appendChild(detail);
      }});
      tbody.querySelectorAll('.expand-btn').forEach(btn => {{
        btn.addEventListener('click', () => {{
          const id = btn.getAttribute('data-idx');
          const row = tbody.querySelector('tr.detail-row[data-detail-for="' + id + '"]');
          if (!row) return;
          const open = row.style.display !== 'none';
          row.style.display = open ? 'none' : 'table-row';
          btn.textContent = open ? '+' : '−';
        }});
      }});
    }}
    document.getElementById('projSearch').addEventListener('input', renderProjFiles);
    projModuleFilter.addEventListener('change', renderProjFiles);
    coverageFilter.addEventListener('change', renderProjFiles);
    renderProjFiles();

    const reports = document.getElementById('reports');
    const reportPaths = [].concat(model.kover_reports || [], model.jacoco_reports || []);
    reportPaths.forEach(path => {{
      const li = document.createElement('li');
      li.textContent = path;
      reports.appendChild(li);
    }});
    if (!reportPaths.length) {{
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
          (file.actionable_missed_lines_count || 0) + ' actionable lines</span></div></div>' +
          '<div class="file-body"></div>';
        const body = card.querySelector('.file-body');
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
VENDOR_CHART_JS = DEFAULT_HTML_REPORT_DIR / "vendor" / "chart.umd.min.js"
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
    """Ensure ``vendor/chart.umd.min.js`` is a real Chart.js blob (download if missing)."""
    dest_dir = (Path(out_dir) / "vendor").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = (dest_dir / "chart.umd.min.js").resolve()
    vendor = VENDOR_CHART_JS.resolve()

    if _is_real_chart_js(dest):
        if vendor != dest and not _is_real_chart_js(vendor):
            vendor.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, vendor)
        return
    if _is_real_chart_js(vendor) and vendor != dest:
        shutil.copy2(vendor, dest)
        return
    if _is_real_chart_js(vendor) and vendor == dest:
        return

    print(f"⬇️  Chart.js missing — downloading into {vendor}")
    _download_chart_js(vendor)
    if dest != vendor:
        shutil.copy2(vendor, dest)


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




















