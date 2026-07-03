# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Kover XML coverage summaries as interactive HTML dashboards.
"""Build interactive coverage-summary HTML from Kover XML reports."""

from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from UnitTest_gen.helper.dashboard.blocked_coverage_kover_report import (
    DEFAULT_HTML_REPORT_DIR,
    discover_kover_xml_groups,
    prepare_html_report_dir,
    variant_name_from_xml_path,
)
from UnitTest_gen.kotlin.coverage_analysis import counter_pair, module_hint_from_kover_xml

COUNTER_TYPES = ("LINE", "INSTRUCTION", "BRANCH", "METHOD", "CLASS")


def _coverage_pct(missed: int, covered: int) -> float:
    total = missed + covered
    return round(covered / total * 100.0, 1) if total else 100.0


def _counter_block(node) -> dict[str, dict[str, int | float]]:
    block: dict[str, dict[str, int | float]] = {}
    for ctype in COUNTER_TYPES:
        missed, covered = counter_pair(node, ctype)
        block[ctype.lower()] = {
            "missed": missed,
            "covered": covered,
            "total": missed + covered,
            "pct": _coverage_pct(missed, covered),
        }
    return block


def _class_name_from_source(source_name: str) -> str:
    for suffix in (".kt", ".java"):
        if source_name.endswith(suffix):
            return source_name[: -len(suffix)]
    return source_name


def parse_kover_summary_xml(path: Path, root: Path) -> dict:
    xml_root = ET.parse(path).getroot()
    module = module_hint_from_kover_xml(path, root)
    packages: list[dict] = []
    files: list[dict] = []

    for package in xml_root.findall("package"):
        package_name = package.attrib.get("name", "")
        line = _counter_block(package)["line"]
        branch = _counter_block(package)["branch"]
        sourcefiles = package.findall("sourcefile")
        packages.append(
            {
                "module": module,
                "package": package_name,
                "line_missed": int(line["missed"]),
                "line_covered": int(line["covered"]),
                "line_pct": float(line["pct"]),
                "branch_pct": float(_counter_block(package)["branch"]["pct"]),
                "file_count": len(sourcefiles),
            }
        )
        for sourcefile in sourcefiles:
            source_name = sourcefile.attrib.get("name", "")
            sf_line = _counter_block(sourcefile)["line"]
            sf_branch = _counter_block(sourcefile)["branch"]
            files.append(
                {
                    "module": module,
                    "package": package_name,
                    "file": source_name,
                    "class_name": _class_name_from_source(source_name),
                    "line_missed": int(sf_line["missed"]),
                    "line_covered": int(sf_line["covered"]),
                    "line_pct": float(sf_line["pct"]),
                    "branch_pct": float(sf_branch["pct"]),
                }
            )

    return {
        "module": module,
        "variant": variant_name_from_xml_path(path),
        "report_name": xml_root.attrib.get("name", path.name),
        "xml_path": str(path.resolve()),
        "overall": _counter_block(xml_root),
        "packages": packages,
        "files": files,
    }


def _merge_counter_blocks(blocks: list[dict]) -> dict[str, dict[str, int | float]]:
    merged: dict[str, dict[str, int | float]] = {}
    for ctype in COUNTER_TYPES:
        key = ctype.lower()
        missed = sum(int(block[key]["missed"]) for block in blocks)
        covered = sum(int(block[key]["covered"]) for block in blocks)
        merged[key] = {
            "missed": missed,
            "covered": covered,
            "total": missed + covered,
            "pct": _coverage_pct(missed, covered),
        }
    return merged


def build_variant_coverage_summary(root: Path, xml_paths: list[Path]) -> dict:
    reports = [parse_kover_summary_xml(path, root) for path in xml_paths]
    modules = []
    packages: list[dict] = []
    files: list[dict] = []
    for report in reports:
        line = report["overall"]["line"]
        modules.append(
            {
                "module": report["module"],
                "report_name": report["report_name"],
                "xml_path": report["xml_path"],
                "line_pct": float(line["pct"]),
                "instruction_pct": float(report["overall"]["instruction"]["pct"]),
                "branch_pct": float(report["overall"]["branch"]["pct"]),
                "package_count": len(report["packages"]),
                "file_count": len(report["files"]),
            }
        )
        packages.extend(report["packages"])
        for row in report["files"]:
            files.append({**row, "variant": report["variant"]})

    overall = _merge_counter_blocks([report["overall"] for report in reports])
    lowest_files = sorted(
        files,
        key=lambda row: (row["line_pct"], -row["line_missed"]),
    )[:25]
    package_chart = sorted(packages, key=lambda row: row["line_pct"])[:12]

    return {
        "variant": reports[0]["variant"] if reports else "",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "root": str(root.resolve()),
        "kover_xmls": [str(path) for path in xml_paths],
        "module_count": len(reports),
        "overall": overall,
        "modules": modules,
        "packages": sorted(packages, key=lambda row: (row["line_pct"], row["package"])),
        "files": sorted(files, key=lambda row: (row["line_pct"], -row["line_missed"])),
        "lowest_files": lowest_files,
        "package_chart": package_chart,
    }


def render_coverage_summary_dashboard(model: dict) -> str:
    variant = html.escape(str(model.get("variant", "Kover")))
    title = f"Kover Coverage — {variant}"
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
      --bg:#0f1419; --panel:#171d25; --text:#e8eef7; --muted:#9aa7b8;
      --good:#3ecf8e; --warn:#f0b429; --bad:#f07178; --accent:#6cb6ff; --border:#2a3441;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Segoe UI",system-ui,sans-serif;
      background:linear-gradient(180deg,#0b1016 0%,#121820 100%); color:var(--text); }}
    .wrap {{ max-width:1320px; margin:0 auto; padding:24px; }}
    h1,h2 {{ margin:0 0 12px; font-weight:600; }}
    h2 {{ font-size:1.05rem; color:var(--accent); margin-top:24px; }}
    .meta {{ color:var(--muted); margin-bottom:18px; line-height:1.5; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:16px 0 22px; }}
    .card {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px; }}
    .card-label {{ color:var(--muted); font-size:0.82rem; }}
    .card-value {{ font-size:1.7rem; font-weight:700; margin:6px 0; }}
    .card-sub {{ color:var(--muted); font-size:0.8rem; }}
    .charts {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:20px; }}
    .panel {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px; margin-bottom:14px; }}
    .filters {{ display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; }}
    .filters input,.filters select {{
      background:#101722; color:var(--text); border:1px solid var(--border);
      border-radius:8px; padding:8px 10px; min-width:180px;
    }}
    table {{ width:100%; border-collapse:collapse; font-size:0.9rem; }}
    th,td {{ border-bottom:1px solid var(--border); padding:8px 10px; text-align:left; vertical-align:middle; }}
    th {{ color:var(--muted); }}
  tr:hover td {{ background:rgba(108,182,255,0.05); }}
    .bar {{ height:8px; background:#243041; border-radius:999px; overflow:hidden; display:inline-block; width:110px; margin-right:8px; }}
    .bar span {{ display:block; height:100%; border-radius:999px; }}
    .bar.good span {{ background:var(--good); }} .bar.warn span {{ background:var(--warn); }} .bar.bad span {{ background:var(--bad); }}
    .pct {{ color:var(--muted); font-size:0.82rem; }}
    .nav {{ margin-bottom:16px; }} .nav a {{ color:var(--accent); margin-right:14px; }}
    @media (max-width:900px) {{ .charts {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="nav"><a href="kover_reports_index.html">← All variants</a></div>
    <h1>{title}</h1>
    <div class="meta" id="meta"></div>
    <div class="cards" id="summary-cards"></div>
    <div class="charts">
      <div class="panel"><canvas id="overallChart"></canvas></div>
      <div class="panel"><canvas id="lowestChart"></canvas></div>
    </div>
    <div class="panel"><canvas id="packageChart" height="120"></canvas></div>
    <div class="filters">
      <input id="search" type="search" placeholder="Search package, file, module..." />
      <select id="moduleFilter"><option value="">All modules</option></select>
    </div>
    <h2>Modules</h2>
    <div class="panel"><table id="modules-table"><thead><tr>
      <th>Module</th><th>Line</th><th>Instruction</th><th>Branch</th><th>Packages</th><th>Files</th>
    </tr></thead><tbody></tbody></table></div>
    <h2>Coverage by package</h2>
    <div class="panel"><table id="packages-table"><thead><tr>
      <th>Package</th><th>Module</th><th>Covered</th><th>Missed</th><th>Line</th><th>Branch</th><th>Files</th>
    </tr></thead><tbody></tbody></table></div>
    <h2>Coverage by source file</h2>
    <div class="panel"><table id="files-table"><thead><tr>
      <th>File</th><th>Package</th><th>Module</th><th>Covered</th><th>Missed</th><th>Line</th><th>Branch</th>
    </tr></thead><tbody></tbody></table></div>
  </div>
  <script type="application/json" id="report-data">{payload}</script>
  <script>
    const model = JSON.parse(document.getElementById('report-data').textContent);
    const meta = document.getElementById('meta');
    meta.innerHTML = [
      'Generated: ' + (model.generated_at || ''),
      'Modules: ' + (model.module_count || 0),
      'Kover XMLs: ' + ((model.kover_xmls || []).length),
      'Root: <code>' + (model.root || '') + '</code>'
    ].join(' · ');

    function esc(s) {{ return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
    function barClass(pct) {{ return pct >= 80 ? 'good' : (pct >= 50 ? 'warn' : 'bad'); }}
    function barCell(pct) {{
      return '<div class="bar ' + barClass(pct) + '"><span style="width:' + pct + '%"></span></div><span class="pct">' + pct + '%</span>';
    }}

    const overall = model.overall || {{}};
  const cardTypes = ['line','instruction','branch','method','class'];
    document.getElementById('summary-cards').innerHTML = cardTypes.map(key => {{
      const row = overall[key] || {{}};
      return '<div class="card"><div class="card-label">' + key + '</div><div class="card-value">' +
        (row.pct || 0) + '%</div><div class="card-sub">' + (row.covered || 0) + ' covered / ' +
        (row.missed || 0) + ' missed (' + (row.total || 0) + ' total)</div>' +
        barCell(row.pct || 0) + '</div>';
    }}).join('');

    const lineMix = [overall.line?.covered || 0, overall.line?.missed || 0];
    new Chart(document.getElementById('overallChart'), {{
      type:'doughnut',
      data:{{ labels:['Line covered','Line missed'], datasets:[{{ data:lineMix, backgroundColor:['#3ecf8e','#f07178'], borderWidth:0 }}] }},
      options:{{ plugins:{{ title:{{ display:true, text:'Overall line mix', color:'#e8eef7' }}, legend:{{ labels:{{ color:'#e8eef7' }} }} }} }}
    }});

    const pkgChart = model.package_chart || [];
    new Chart(document.getElementById('packageChart'), {{
      type:'bar',
      data:{{ labels:pkgChart.map(p => p.package.split('/').slice(-2).join('/')),
        datasets:[{{ label:'Line %', data:pkgChart.map(p => p.line_pct), backgroundColor:'#6cb6ff' }}] }},
      options:{{ plugins:{{ title:{{ display:true, text:'Lowest packages (line %)', color:'#e8eef7' }}, legend:{{ display:false }} }},
        scales:{{ x:{{ ticks:{{ color:'#9aa7b8' }} }}, y:{{ min:0, max:100, ticks:{{ color:'#9aa7b8' }} }} }} }}
    }});

    const lowest = model.lowest_files || [];
    new Chart(document.getElementById('lowestChart'), {{
      type:'bar',
      data:{{ labels:lowest.map(f => f.file), datasets:[{{ label:'Line %', data:lowest.map(f => f.line_pct), backgroundColor:'#f07178' }}] }},
      options:{{ indexAxis:'y', plugins:{{ title:{{ display:true, text:'Lowest-covered files', color:'#e8eef7' }}, legend:{{ display:false }} }},
        scales:{{ x:{{ min:0, max:100, ticks:{{ color:'#9aa7b8' }} }}, y:{{ ticks:{{ color:'#9aa7b8' }} }} }} }}
    }});

    const moduleFilter = document.getElementById('moduleFilter');
    [...new Set((model.packages || []).map(p => p.module))].sort().forEach(m => {{
      const opt = document.createElement('option'); opt.value = m; opt.textContent = m; moduleFilter.appendChild(opt);
    }});

    function renderTables() {{
      const q = document.getElementById('search').value.toLowerCase();
      const module = moduleFilter.value;
      const match = row => {{
        if (module && row.module !== module) return false;
        const hay = Object.values(row).join(' ').toLowerCase();
        return !q || hay.includes(q);
      }};
      document.querySelector('#modules-table tbody').innerHTML = (model.modules || []).filter(match).map(m =>
        '<tr><td>' + esc(m.module) + '</td><td>' + m.line_pct + '%</td><td>' + m.instruction_pct +
        '%</td><td>' + m.branch_pct + '%</td><td>' + m.package_count + '</td><td>' + m.file_count + '</td></tr>'
      ).join('');
      document.querySelector('#packages-table tbody').innerHTML = (model.packages || []).filter(match).map(p =>
        '<tr><td>' + esc(p.package) + '</td><td>' + esc(p.module) + '</td><td>' + p.line_covered + '</td><td>' +
        p.line_missed + '</td><td>' + barCell(p.line_pct) + '</td><td>' + p.branch_pct + '%</td><td>' + p.file_count + '</td></tr>'
      ).join('');
      document.querySelector('#files-table tbody').innerHTML = (model.files || []).filter(match).slice(0, 200).map(f =>
        '<tr><td>' + esc(f.file) + '</td><td>' + esc(f.package) + '</td><td>' + esc(f.module) + '</td><td>' +
        f.line_covered + '</td><td>' + f.line_missed + '</td><td>' + barCell(f.line_pct) + '</td><td>' + f.branch_pct + '%</td></tr>'
      ).join('');
    }}
    document.getElementById('search').addEventListener('input', renderTables);
    moduleFilter.addEventListener('change', renderTables);
    renderTables();
  </script>
</body>
</html>"""


def render_coverage_summary_index(models: list[dict]) -> str:
    cards = []
    for model in models:
        variant = html.escape(str(model.get("variant", "")))
        line_pct = float((model.get("overall") or {}).get("line", {}).get("pct", 0))
        line = (model.get("overall") or {}).get("line", {})
        href = html.escape(f"coverage_summary_{model.get('variant', '')}.html")
        cards.append(
            f'<a class="card" href="{href}" style="text-decoration:none;color:inherit">'
            f'<div class="card-label">{variant}</div>'
            f'<div class="card-value">{line_pct}%</div>'
            f'<div class="meta">{model.get("module_count", 0)} modules · '
            f'{line.get("covered", 0)} covered / {line.get("missed", 0)} missed lines</div></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Kover Coverage Summaries</title>
<style>
  body {{ font-family:system-ui,sans-serif;background:#0f1419;color:#e8eef7;margin:0;padding:24px; }}
  .nav a {{ color:#6cb6ff; }} .cards {{ display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;max-width:900px; }}
  .card {{ background:#171d25;border:1px solid #2a3441;border-radius:12px;padding:16px; }}
  .card-label {{ color:#9aa7b8; }} .card-value {{ font-size:2rem;font-weight:700;margin:8px 0; }} .meta {{ color:#9aa7b8;font-size:0.85rem; }}
</style></head><body>
<div class="nav"><a href="kover_reports_index.html">← Reports hub</a></div>
<h1>Kover Coverage Summaries</h1>
<div class="cards">{''.join(cards)}</div>
</body></html>"""


def render_kover_reports_hub(summary_models: list[dict], gap_models: list[dict]) -> str:
    rows = []
    variants = sorted({str(m.get("variant", "")) for m in summary_models + gap_models if m.get("variant")})
    summary_by_variant = {m["variant"]: m for m in summary_models}
    gap_by_variant = {m["variant"]: m for m in gap_models}
    for variant in variants:
        summary = summary_by_variant.get(variant, {})
        gap = gap_by_variant.get(variant, {})
        line_pct = float((summary.get("overall") or {}).get("line", {}).get("pct", 0))
        gap_lines = int((gap.get("summary") or {}).get("missed_lines", 0))
        rows.append(
            f"<tr><td>{html.escape(variant)}</td>"
            f"<td><strong>{line_pct}%</strong></td>"
            f"<td>{gap_lines}</td>"
            f'<td><a href="coverage_summary_{html.escape(variant)}.html">Coverage</a> · '
            f'<a href="kover_gaps_{html.escape(variant)}.html">Gaps</a></td></tr>'
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Kover Reports Hub</title>
<style>
  body {{ font-family:system-ui,sans-serif;background:#0f1419;color:#e8eef7;margin:0;padding:24px; }}
  table {{ border-collapse:collapse;width:min(900px,100%); }}
  th,td {{ border-bottom:1px solid #2a3441;padding:10px;text-align:left; }}
  th {{ color:#9aa7b8; }} a {{ color:#6cb6ff; }}
  h1 {{ margin-bottom:8px; }} p {{ color:#9aa7b8; }}
</style></head><body>
<h1>Kover Reports Hub</h1>
<p>Discovered Kover variants — coverage summaries and planner gap dashboards</p>
<table><thead><tr><th>Variant</th><th>Line coverage</th><th>Planner gap lines</th><th>Reports</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def write_coverage_summary_reports(
    root: Path,
    *,
    variants: list[str] | None = None,
    output_dir: Path | None = None,
    clear_output: bool = True,
) -> tuple[list[Path], list[dict]]:
    root = root.resolve()
    out_dir = output_dir or (root / DEFAULT_HTML_REPORT_DIR)
    prepare_html_report_dir(out_dir, clear=clear_output)
    written: list[Path] = []
    models: list[dict] = []
    variant_filter = set(variants) if variants else None
    for variant, xml_paths in discover_kover_xml_groups(root).items():
        if variant_filter is not None and variant not in variant_filter:
            continue
        model = build_variant_coverage_summary(root, xml_paths)
        path = out_dir / f"coverage_summary_{variant}.html"
        path.write_text(render_coverage_summary_dashboard(model), encoding="utf-8")
        written.append(path)
        models.append(model)
    if models:
        index_path = out_dir / "coverage_summary_index.html"
        index_path.write_text(render_coverage_summary_index(models), encoding="utf-8")
        written.append(index_path)
    return written, models
