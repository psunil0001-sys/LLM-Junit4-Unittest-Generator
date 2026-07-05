#!/usr/bin/env python3
# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Renders interactive Kover gap dashboards.
"""Render interactive HTML dashboards from Kover gap report models."""

from __future__ import annotations

import html
import json
from pathlib import Path


def render_kover_gap_dashboard(model: dict) -> str:
    variant = html.escape(str(model.get("variant", "Kover")))
    title = f"Kover Gap Dashboard — {variant}"
    payload = json.dumps(model, ensure_ascii=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0f1419; --panel: #171d25; --text: #e8eef7; --muted: #9aa7b8;
      --good: #3ecf8e; --warn: #f0b429; --bad: #f07178; --accent: #6cb6ff; --border: #2a3441;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(180deg, #0b1016 0%, #121820 100%); color: var(--text); }}
    .wrap {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
    h1, h2 {{ margin: 0 0 12px; font-weight: 600; }}
    h2 {{ font-size: 1.05rem; color: var(--accent); margin-top: 24px; }}
    .meta {{ color: var(--muted); margin-bottom: 18px; line-height: 1.5; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0 22px; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; }}
    .card-label {{ color: var(--muted); font-size: 0.82rem; }}
    .card-value {{ font-size: 1.7rem; font-weight: 700; margin: 6px 0; }}
    .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 20px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 14px; }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; align-items: center; }}
    .filters input, .filters select {{
      background: #101722; color: var(--text); border: 1px solid var(--border);
      border-radius: 8px; padding: 8px 10px; min-width: 180px;
    }}
    .filters select[multiple] {{ min-height: 96px; }}
    .chip {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 0.75rem;
      margin: 2px 4px 2px 0; border: 1px solid var(--border); color: var(--muted); }}
    .chip.cat {{ border-color: #3d5a80; color: #9ec5fe; }}
    .chip.contract {{ border-color: #5a3d80; color: #d4b5ff; }}
    .chip.blocked {{ background: rgba(240,113,120,0.15); color: var(--bad); border-color: var(--bad); }}
    .chip.safe {{ background: rgba(62,207,142,0.12); color: var(--good); border-color: var(--good); }}
    .chip.attemptable {{ background: rgba(240,180,41,0.12); color: var(--warn); border-color: var(--warn); }}
    .chip.excluded {{ background: rgba(154,167,184,0.15); color: #c5d0dc; border-color: #6b7c93; }}
    .chip.attempted_but_failed {{ background: rgba(240,113,120,0.12); color: #ffb4ba; border-color: #d96a72; }}
    .chip.acceptable_branch_gap {{ background: rgba(154,167,184,0.15); color: #c5d0dc; border-color: #6b7c93; }}
    .file-card {{ border: 1px solid var(--border); border-radius: 12px; margin-bottom: 12px; background: #121820; }}
    .file-head {{ padding: 12px 14px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }}
    .file-body {{ display: none; padding: 0 14px 14px; border-top: 1px solid var(--border); }}
    .file-card.open .file-body {{ display: block; }}
    .line-map {{ height: 10px; background: #243041; border-radius: 999px; position: relative; margin: 8px 0; }}
    .line-map i {{ position: absolute; width: 2px; height: 100%; background: var(--bad); top: 0; }}
    .action-card {{ border-left: 4px solid var(--accent); background: var(--panel); border-radius: 8px;
      padding: 10px 12px; margin: 10px 0; }}
    .action-card.blocked {{ border-left-color: var(--bad); }}
    .action-card.safe {{ border-left-color: var(--good); }}
    .action-card.attemptable {{ border-left-color: var(--warn); }}
    .action-card.excluded {{ border-left-color: #6b7c93; opacity: 0.92; }}
    .action-card.attempted_but_failed {{ border-left-color: #d96a72; }}
    .action-card.acceptable_branch_gap {{ border-left-color: #6b7c93; opacity: 0.92; }}
    .action-card h4 {{ margin: 0 0 6px; font-size: 0.95rem; }}
    .action-card p {{ margin: 4px 0; color: var(--muted); font-size: 0.88rem; line-height: 1.45; }}
    .action-card strong {{ color: var(--text); }}
    details.evidence {{ margin-top: 6px; color: var(--muted); font-size: 0.82rem; }}
    .unassigned {{ border: 1px dashed var(--warn); border-radius: 10px; padding: 12px; margin-top: 10px; }}
    .hidden {{ display: none !important; }}
    @media (max-width: 900px) {{ .charts {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav" style="margin-bottom:14px"><a href="kover_reports_index.html" style="color:#6cb6ff">← Reports hub</a></div>
    <h1>{title}</h1>
    <div class="meta" id="meta"></div>
    <div class="cards" id="summary-cards"></div>
    <div class="charts">
      <div class="panel"><canvas id="reasonChart"></canvas></div>
      <div class="panel"><canvas id="fileChart"></canvas></div>
    </div>
    <div class="filters">
      <input id="search" type="search" placeholder="Search file, entry, reason..." />
      <select id="moduleFilter" multiple aria-label="Modules"></select>
      <select id="bucketFilter" multiple aria-label="Buckets"></select>
      <select id="reasonFilter" multiple aria-label="Reason codes"></select>
    </div>
    <h2>Source files</h2>
    <div id="files"></div>
  </div>
  <script type="application/json" id="report-data">{payload}</script>
  <script>
    const model = JSON.parse(document.getElementById('report-data').textContent);
    const meta = document.getElementById('meta');
    const xmlList = Array.isArray(model.kover_xmls) && model.kover_xmls.length
      ? model.kover_xmls
      : (model.kover_xml ? [model.kover_xml] : []);
    const xmlMeta = xmlList.length > 1
      ? 'Kover XMLs (' + xmlList.length + ' modules):<br>' + xmlList.map(p => '<code>' + p + '</code>').join('<br>')
      : 'Kover XML: <code>' + (xmlList[0] || '') + '</code>';
    meta.innerHTML = [
      'Generated: ' + (model.generated_at || ''),
      xmlMeta,
      'Root: <code>' + (model.root || '') + '</code>'
    ].join('<br>');

    const summary = model.summary || {{}};
    const cards = document.getElementById('summary-cards');
    const cardData = [
      ['Missed lines', summary.missed_lines || 0],
      ['Missed branches', summary.missed_branches || 0],
      ['Files with gaps', summary.file_count || 0],
      ['Blocked items', (summary.by_bucket || {{}}).blocked || 0],
      ['Safe items', (summary.by_bucket || {{}}).safe || 0],
      ['Attemptable', (summary.by_bucket || {{}}).attemptable || 0],
      ['Attempted but failed', (summary.by_bucket || {{}}).attempted_but_failed || 0],
      ['Acceptable branch gap', (summary.by_bucket || {{}}).acceptable_branch_gap || 0],
      ['Excluded items', (summary.by_bucket || {{}}).excluded || 0],
      ['Planner coverage', (summary.planner_coverage_pct || 0) + '%']
    ];
    cards.innerHTML = cardData.map(([label, value]) =>
      '<div class="card"><div class="card-label">' + label + '</div><div class="card-value">' + value + '</div></div>'
    ).join('');

    const reasonLabels = Object.keys(summary.by_reason || {{}});
    const reasonValues = reasonLabels.map(k => summary.by_reason[k]);
    new Chart(document.getElementById('reasonChart'), {{
      type: 'doughnut',
      data: {{ labels: reasonLabels, datasets: [{{ data: reasonValues, backgroundColor: ['#6cb6ff','#3ecf8e','#f0b429','#f07178','#b392f0','#79c0ff'] }}] }},
      options: {{ plugins: {{ title: {{ display: true, text: 'Gaps by reason code', color: '#e8eef7' }} }}, maintainAspectRatio: true }}
    }});

    const topFiles = (model.files || []).slice().sort((a,b) => (b.missed_lines_count||0) - (a.missed_lines_count||0)).slice(0, 10);
    new Chart(document.getElementById('fileChart'), {{
      type: 'bar',
      data: {{
        labels: topFiles.map(f => f.name),
        datasets: [{{ label: 'Missed lines', data: topFiles.map(f => f.missed_lines_count || 0), backgroundColor: '#6cb6ff' }}]
      }},
      options: {{
        plugins: {{ title: {{ display: true, text: 'Top files by missed lines', color: '#e8eef7' }}, legend: {{ labels: {{ color: '#e8eef7' }} }} }},
        scales: {{ x: {{ ticks: {{ color: '#9aa7b8' }} }}, y: {{ ticks: {{ color: '#9aa7b8' }} }} }}
      }}
    }});

    const bucketFilter = document.getElementById('bucketFilter');
    const reasonFilter = document.getElementById('reasonFilter');
    const moduleFilter = document.getElementById('moduleFilter');
    const buckets = new Set();
    const reasons = new Set();
    const modules = new Set();
    (model.flat_items || []).forEach(item => {{
      buckets.add(item.bucket);
      reasons.add(item.reason_code);
    }});
    (model.modules || []).forEach(module => modules.add(module));
    (model.files || []).forEach(file => {{
      if (file.module) modules.add(file.module);
    }});
    [...modules].sort().forEach(m => {{
      const opt = document.createElement('option'); opt.value = m; opt.textContent = m; moduleFilter.appendChild(opt);
    }});
    [...buckets].sort().forEach(b => {{
      const opt = document.createElement('option'); opt.value = b; opt.textContent = bucketLabel(b); bucketFilter.appendChild(opt);
    }});
    [...reasons].sort().forEach(r => {{
      const opt = document.createElement('option'); opt.value = r; opt.textContent = r; reasonFilter.appendChild(opt);
    }});

    function esc(s) {{ return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
    function bucketLabel(bucket) {{
      if (bucket === 'attempted_but_failed') return 'Attempted but failed';
      if (bucket === 'acceptable_branch_gap') return 'Acceptable branch gap';
      return bucket || '';
    }}

    function selectedValues(select) {{
      return new Set([...select.selectedOptions].map(option => option.value));
    }}

    function renderFiles() {{
      const q = document.getElementById('search').value.toLowerCase();
      const selectedModules = selectedValues(moduleFilter);
      const selectedBuckets = selectedValues(bucketFilter);
      const selectedReasons = selectedValues(reasonFilter);
      const filesEl = document.getElementById('files');
      filesEl.innerHTML = '';
      (model.files || []).forEach(file => {{
        if (selectedModules.size && !selectedModules.has(file.module)) return;
        const items = (file.items || []).filter(item => {{
          if (selectedBuckets.size && !selectedBuckets.has(item.bucket)) return false;
          if (selectedReasons.size && !selectedReasons.has(item.reason_code)) return false;
          const hay = [file.name, file.module, file.path, item.entry, item.why, item.fix, item.reason_code].join(' ').toLowerCase();
          return !q || hay.includes(q);
        }});
        if (!items.length) return;
        const card = document.createElement('div');
        card.className = 'file-card';
        const mapTicks = (file.line_map || []).map(pos =>
          '<i style="left:' + pos + '%"></i>').join('');
        const cats = (file.categories || []).map(c => '<span class="chip cat">' + esc(c) + '</span>').join('');
        const contracts = (file.contracts || []).map(c => '<span class="chip contract">' + esc(c) + '</span>').join('');
        card.innerHTML =
          '<div class="file-head"><div><strong>' + esc(file.name) + '</strong><br><span class="meta">' +
          esc(file.module) + ' · ' + esc(file.path) + '</span><div>' + cats + contracts + '</div>' +
          '<div class="line-map">' + mapTicks + '</div></div><div>' + (file.missed_lines_count || 0) + ' lines</div></div>' +
          '<div class="file-body"></div>';
        const body = card.querySelector('.file-body');
        items.forEach(item => {{
          const ac = document.createElement('div');
          ac.className = 'action-card ' + item.bucket;
          ac.innerHTML =
            '<h4>' + esc(item.entry) + ' <span class="chip ' + item.bucket + '">' + esc(bucketLabel(item.bucket)) + '</span> ' +
            '<span class="chip">' + esc(item.reason_code) + '</span></h4>' +
            '<p><strong>Lines:</strong> ' + esc(item.lines_text) + ' · <strong>Branches:</strong> ' + esc(item.branches_text) + '</p>' +
            '<p><strong>Why:</strong> ' + esc(item.why) + '</p>' +
            '<p><strong>Recommended action:</strong> ' + esc(item.fix) + '</p>' +
            '<p><strong>What to test next:</strong> ' + esc(item.next_test) + '</p>' +
            (item.provenance ? '<p><strong>Provenance:</strong> ' + esc(item.provenance) + '</p>' : '') +
            (item.attempt_count ? '<p><strong>Attempts:</strong> ' + esc(item.attempt_count) + '</p>' : '') +
            (item.evidence ? '<details class="evidence"><summary>Evidence</summary>' + esc(item.evidence) + '</details>' : '');
          body.appendChild(ac);
        }});
        if (file.unassigned_lines_text) {{
          const ua = document.createElement('div');
          ua.className = 'unassigned';
          ua.innerHTML = '<strong>Unassigned Kover lines</strong><p>' + esc(file.unassigned_lines_text) +
            '</p><p>These lines are in Kover but the planner did not classify them. Run testgen on this file.</p>';
          body.appendChild(ua);
        }}
        card.querySelector('.file-head').addEventListener('click', () => card.classList.toggle('open'));
        filesEl.appendChild(card);
      }});
    }}

    document.getElementById('search').addEventListener('input', renderFiles);
    moduleFilter.addEventListener('change', renderFiles);
    bucketFilter.addEventListener('change', renderFiles);
    reasonFilter.addEventListener('change', renderFiles);
    renderFiles();
  </script>
</body>
</html>"""


def render_kover_gap_index(variant_models: list[dict], output_dir: Path) -> str:
    cards = []
    for model in variant_models:
        variant = html.escape(str(model.get("variant", "")))
        summary = model.get("summary") or {}
        href = html.escape(f"kover_gaps_{model.get('variant', '')}.html")
        cards.append(
            f'<a class="card" href="{href}" style="text-decoration:none;color:inherit">'
            f'<div class="card-label">{variant}</div>'
            f'<div class="card-value">{summary.get("missed_lines", 0)}</div>'
            f'<div class="meta">missed lines · {summary.get("file_count", 0)} files · '
            f'{summary.get("module_count", 0)} modules · {summary.get("kover_xml_count", 0)} XMLs</div></a>'
        )
    body = "\n".join(cards)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Kover Gap Dashboards</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0f1419; color:#e8eef7; margin:0; padding:24px; }}
  .nav a {{ color:#6cb6ff; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; max-width:900px; }}
  .card {{ background:#171d25; border:1px solid #2a3441; border-radius:12px; padding:16px; }}
  .card-label {{ color:#9aa7b8; }} .card-value {{ font-size:2rem; font-weight:700; margin:8px 0; }}
  .meta {{ color:#9aa7b8; font-size:0.85rem; }}
</style></head><body>
<div class="nav"><a href="kover_reports_index.html">← Reports hub</a></div>
<h1>Kover Gap Dashboards</h1>
<p class="meta">Select a release variant</p>
<div class="cards">{body}</div>
</body></html>"""
