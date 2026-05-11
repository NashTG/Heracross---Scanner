"""
Polymarket Up/Down Market Analyzer – Flask Web UI
Run: pip install flask requests && python app.py
"""
import json, time, io, sys, traceback
from flask import Flask, render_template_string, request, jsonify, Response

from polymarket_core import (
    ASSETS, INTERVALS,
    fetch_range, run_diagnostics,
    analyze, validate_oos, simulate_strategy,
    cache_count, cache_clear,
    save_snapshot, load_snapshots,
    export_markets, import_markets,
    validate_date_range, validate_import_payload,
)

app = Flask(__name__)

# ─── State ─────────────────────────────────────────────────────────────
progress_state = {"running": False, "phase": "", "done": 0, "total": 0}

def _error_response(exc, status=500, public_msg=None):
    traceback.print_exc(file=sys.stderr)
    msg = public_msg if public_msg is not None else "Internal error"
    return jsonify({"error": msg}), status
last_results = []       # last fetched market data
last_analysis = None    # last analysis output

def progress_cb(done, total):
    global progress_state
    progress_state.update({"running": True, "phase": "Fetching", "done": done, "total": total})

# ─── HTML ──────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Polymarket Up/Down Analyzer</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>.tab-btn{transition:all .15s}[data-active="true"]{background:rgba(16,185,129,.15);color:#34d399}</style>
</head>
<body class="bg-[#0a0c14] text-gray-100 font-sans min-h-screen">
<header class="border-b border-gray-800/60 bg-[#0e1019] px-5 py-4">
  <div class="max-w-6xl mx-auto flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="h-9 w-9 rounded-lg bg-gradient-to-br from-emerald-500 to-cyan-600 flex items-center justify-center text-sm font-black shadow-lg shadow-emerald-900/40">₿</div>
      <div><h1 class="text-lg font-semibold tracking-tight">Polymarket Up/Down Analyzer</h1><p class="text-xs text-gray-500">Historic prices, strategies, backtesting & validation</p></div>
    </div>
    <div class="flex rounded-lg border border-gray-700/60 bg-[#0a0c14] overflow-hidden text-xs" id="tabs">
      <button onclick="showTab('analyze')" data-tab="analyze" data-active="true" class="tab-btn px-3 py-1.5 font-medium">Analyze</button>
      <button onclick="showTab('diagnose')" data-tab="diagnose" class="tab-btn px-3 py-1.5 font-medium border-l border-gray-700/60">Diagnose</button>
      <button onclick="showTab('oos')" data-tab="oos" class="tab-btn px-3 py-1.5 font-medium border-l border-gray-700/60">OOS</button>
      <button onclick="showTab('simulate')" data-tab="simulate" class="tab-btn px-3 py-1.5 font-medium border-l border-gray-700/60">Simulate</button>
      <button onclick="showTab('snapshots')" data-tab="snapshots" class="tab-btn px-3 py-1.5 font-medium border-l border-gray-700/60">Snapshots</button>
      <button onclick="showTab('backlog')" data-tab="backlog" class="tab-btn px-3 py-1.5 font-medium border-l border-gray-700/60">Backlog</button>
    </div>
  </div>
</header>
<main class="max-w-6xl mx-auto px-5 py-6 space-y-5">

<!-- ══════════════ ANALYZE TAB ══════════════ -->
<div id="tab_analyze">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-3">
    <p class="text-[11px] text-gray-500">Fetch all markets for a crypto/window/date-range, enrich with Binance price data, then discover profit-aware strategies. Results are cached in SQLite — re-fetching the same range is instant.</p>
    <div class="grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
      <div><label class="text-xs text-gray-500 mb-1 block">Asset</label><select id="asset" class="w-full rounded border border-gray-700/60 bg-[#0a0c14] px-2 py-2 text-sm font-mono">{% for a in assets %}<option>{{a}}</option>{% endfor %}</select></div>
      <div><label class="text-xs text-gray-500 mb-1 block">Window</label><select id="interval" class="w-full rounded border border-gray-700/60 bg-[#0a0c14] px-2 py-2 text-sm font-mono">{% for i in intervals %}<option>{{i}}</option>{% endfor %}</select></div>
      <div><label class="text-xs text-gray-500 mb-1 block">Start (ET)</label><input type="date" id="sd" value="2026-04-17" class="w-full rounded border border-gray-700/60 bg-[#0a0c14] px-2 py-2 text-sm font-mono"></div>
      <div><label class="text-xs text-gray-500 mb-1 block">End (ET)</label><input type="date" id="ed" value="2026-04-18" class="w-full rounded border border-gray-700/60 bg-[#0a0c14] px-2 py-2 text-sm font-mono"></div>
      <div class="flex gap-2 items-end"><button onclick="doFetch()" id="btn_fetch" class="bg-gradient-to-r from-emerald-600 to-cyan-600 px-4 py-2 rounded text-sm font-semibold hover:opacity-90 whitespace-nowrap disabled:opacity-40">Fetch & Analyze</button></div>
      <div class="flex gap-2 items-end flex-wrap">
        <button onclick="doExport()" class="border border-gray-700/60 bg-[#0a0c14] px-2 py-2 rounded text-[10px] hover:border-cyan-700">Export</button>
        <label class="border border-gray-700/60 bg-[#0a0c14] px-2 py-2 rounded text-[10px] hover:border-cyan-700 cursor-pointer">Import<input type="file" accept=".json" class="hidden" onchange="doImport(event)"></label>
        <button onclick="doClearCache()" class="border border-gray-700/60 bg-[#0a0c14] px-2 py-2 rounded text-[10px] hover:border-red-700 text-gray-500">Clear Cache</button>
      </div>
    </div>
    <div id="progress" class="hidden"><div class="flex justify-between text-xs text-gray-500 mb-1"><span id="phase"></span><span id="pcount"></span></div><div class="h-1.5 rounded-full bg-gray-800 overflow-hidden"><div id="pbar" class="h-full bg-gradient-to-r from-emerald-500 to-cyan-500 transition-all" style="width:0%"></div></div></div>
  </div>
  <div id="summary_box" class="hidden rounded-xl border border-gray-800/60 bg-[#12141f] p-4 grid grid-cols-2 md:grid-cols-6 gap-3 text-sm mt-5">
    <div><span class="text-gray-500 text-xs">Total</span><p id="s_total" class="font-semibold">0</p></div>
    <div><span class="text-gray-500 text-xs">Resolved</span><p id="s_ok" class="font-semibold text-emerald-400">0</p></div>
    <div><span class="text-gray-500 text-xs">Failed</span><p id="s_fail" class="font-semibold text-yellow-500">0</p></div>
    <div><span class="text-gray-500 text-xs">Cached</span><p id="s_cache" class="font-semibold text-cyan-400">0</p></div>
    <div><span class="text-gray-500 text-xs">UP / DOWN</span><p id="s_updown" class="font-semibold">-</p></div>
    <div><span class="text-gray-500 text-xs">Correlation</span><p id="s_corr" class="font-semibold font-mono">-</p></div>
  </div>
  <div id="failures_box" class="hidden rounded-xl border border-yellow-900/30 bg-yellow-950/10 p-4 mt-5"><h3 class="text-xs font-semibold text-yellow-500 uppercase mb-2">Failed Markets (first 20)</h3><div id="fail_list" class="space-y-1 font-mono text-[11px] text-yellow-200/70 max-h-40 overflow-y-auto"></div></div>
  <!-- Time of Day -->
  <div id="tod_box" class="hidden rounded-xl border border-gray-800/60 bg-[#12141f] overflow-hidden mt-5">
    <div class="p-4 border-b border-gray-800/60"><h2 class="text-sm font-semibold">Time-of-Day Breakdown</h2><p class="text-[11px] text-gray-500">UP win-rate by 4-hour ET session. Helps identify if certain hours are biased.</p></div>
    <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-gray-800/40 text-[10px] uppercase text-gray-600"><th class="px-3 py-2 text-left">Session</th><th class="px-3 py-2 text-right">Markets</th><th class="px-3 py-2 text-right">UP</th><th class="px-3 py-2 text-right">DOWN</th><th class="px-3 py-2 text-right">UP %</th></tr></thead><tbody id="tod_body"></tbody></table></div>
  </div>
  <!-- Strategies -->
  <div id="strat_box" class="hidden rounded-xl border border-gray-800/60 bg-[#12141f] overflow-hidden mt-5">
    <div class="p-4 border-b border-gray-800/60"><h2 class="text-sm font-semibold">Strategies</h2><p class="text-[11px] text-gray-500">Fee-adjusted (PM 7.2% + Polygon 1%). <b>Outcome:</b> buy winning side, hold to close. <b>Volatility:</b> buy cheap, sell rally. ≥70% confidence + positive net P&L only.</p></div>
    <div class="grid md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-gray-800/60">
      <div class="p-4"><h3 class="text-xs font-semibold uppercase text-emerald-500 mb-3">🎯 Outcome</h3><div id="oi_list" class="space-y-4 text-xs"></div></div>
      <div class="p-4"><h3 class="text-xs font-semibold uppercase text-cyan-500 mb-3">📈 Volatility</h3><div id="pi_list" class="space-y-4 text-xs"></div></div>
    </div>
  </div>
  <!-- Sample -->
  <div id="sample_box" class="hidden rounded-xl border border-gray-800/60 bg-[#12141f] overflow-hidden mt-5">
    <div class="p-4 border-b border-gray-800/60"><h2 class="text-sm font-semibold">Sample Markets (10)</h2></div>
    <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-gray-800/40 text-[10px] uppercase text-gray-600"><th class="px-3 py-2 text-left">Window</th><th class="px-3 py-2 text-center" colspan="3">🟢 UP</th><th class="px-3 py-2 text-center" colspan="3">🔴 DOWN</th><th class="px-3 py-2 text-center" colspan="3">Δ Price</th></tr></thead><tbody id="sample_body"></tbody></table></div>
  </div>
</div>

<!-- ══════════════ DIAGNOSE TAB ══════════════ -->
<div id="tab_diagnose" class="hidden">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-3">
    <p class="text-[11px] text-gray-500">Tests one known-good market per asset × window against Gamma + CLOB APIs. Identifies which combinations are working before running a full fetch.</p>
    <button onclick="doDiagnose()" id="btn_diag" class="bg-gradient-to-r from-yellow-600 to-orange-600 px-5 py-2 rounded text-sm font-semibold text-white disabled:opacity-40">Run Diagnosis</button>
  </div>
  <div id="diag_box" class="hidden rounded-xl border border-gray-800/60 bg-[#12141f] overflow-hidden mt-5">
    <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-gray-800/40 text-[10px] uppercase text-gray-600"><th class="px-3 py-2 text-left">Pair</th><th class="px-3 py-2 text-left">Slug</th><th class="px-3 py-2">Status</th><th class="px-3 py-2 text-left">Error</th></tr></thead><tbody id="diag_body"></tbody></table></div>
  </div>
</div>

<!-- ══════════════ OOS TAB ══════════════ -->
<div id="tab_oos" class="hidden">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-3">
    <p class="text-[11px] text-gray-500">Splits fetched data in half chronologically: discovers strategies on the first half (training), validates on the second (validation). If a strategy scores 85% on training but 55% on validation, it's overfitting — not robust. Run a fetch first.</p>
    <button onclick="doOOS()" class="bg-gradient-to-r from-purple-600 to-indigo-600 px-5 py-2 rounded text-sm font-semibold text-white disabled:opacity-40">Run OOS Validation</button>
  </div>
  <div id="oos_box" class="hidden mt-5 grid md:grid-cols-2 gap-4"></div>
</div>

<!-- ══════════════ SIMULATE TAB ══════════════ -->
<div id="tab_simulate" class="hidden">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-3">
    <p class="text-[11px] text-gray-500">Pick a discovered strategy and simulate placing fixed-size bets across all fetched markets. Shows total P&L, max drawdown, hit rate — all after fees. Turns abstract confidence into dollar expectations.</p>
    <div class="flex gap-3 items-end"><label class="text-xs text-gray-500">Bet size ($):</label><input type="number" id="bet_size" value="100" class="w-24 rounded border border-gray-700/60 bg-[#0a0c14] px-2 py-1 text-sm font-mono"></div>
    <div id="sim_strategies" class="space-y-2 max-h-60 overflow-y-auto"></div>
  </div>
  <div id="sim_result" class="hidden rounded-xl border border-cyan-900/30 bg-[#12141f] p-4 mt-5 grid grid-cols-2 md:grid-cols-4 gap-3 text-sm"></div>
</div>

<!-- ══════════════ SNAPSHOTS TAB ══════════════ -->
<div id="tab_snapshots" class="hidden">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-3">
    <p class="text-[11px] text-gray-500">Every fetch saves a snapshot with metadata. Track how strategy counts evolve across runs and date ranges. Stored on disk.</p>
  </div>
  <div id="snap_box" class="rounded-xl border border-gray-800/60 bg-[#12141f] overflow-hidden mt-5">
    <div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-gray-800/40 text-[10px] uppercase text-gray-600"><th class="px-3 py-2 text-left">ID</th><th class="px-3 py-2 text-left">When</th><th class="px-3 py-2">Asset</th><th class="px-3 py-2">Window</th><th class="px-3 py-2 text-left">Range</th><th class="px-3 py-2 text-right">Outcome</th><th class="px-3 py-2 text-right">Volatility</th></tr></thead><tbody id="snap_body"></tbody></table></div>
  </div>
</div>

<!-- ══════════════ BACKLOG TAB ══════════════ -->
<div id="tab_backlog" class="hidden">
  <div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4 space-y-4">
    <h2 class="text-sm font-semibold">Development Backlog</h2>
    <div><h3 class="text-xs font-semibold text-emerald-500 uppercase mb-2">Done</h3><ul class="space-y-1 text-xs text-gray-400">
      <li>✓ Fee-adjusted P&L (Polymarket 7.2% + Polygon 1% buy/sell)</li>
      <li>✓ Out-of-Sample validation</li><li>✓ Export/Import dataset (JSON)</li>
      <li>✓ Analysis run snapshots</li><li>✓ Strategy backtest simulator</li>
      <li>✓ Time-of-day segmentation</li><li>✓ Multi-asset (BTC/ETH/SOL/XRP)</li>
      <li>✓ Multi-window (5m/15m/1h/4h/1d)</li><li>✓ SQLite caching</li>
    </ul></div>
    <div><h3 class="text-xs font-semibold text-gray-600 uppercase mb-2">Someday</h3><ul class="space-y-1 text-xs text-gray-700">
      <li>○ Consecutive outcome streaks (momentum detection)</li>
      <li>○ Cross-asset correlation (BTC → SOL lead/lag)</li>
      <li>○ Volatility regime detection (low/med/high vol)</li>
      <li>○ Live market monitor with strategy alerts</li>
      <li>○ Multi-range side-by-side comparison</li>
      <li>○ Retry with exponential backoff</li>
    </ul></div>
  </div>
</div>

<!-- Log -->
<div class="rounded-xl border border-gray-800/60 bg-[#12141f] p-4"><h3 class="text-xs font-semibold text-gray-600 uppercase mb-2">Log</h3><div id="log_el" class="space-y-0.5 font-mono text-[11px] text-gray-600 max-h-40 overflow-y-auto"></div></div>
</main>
<footer class="border-t border-gray-800/60 bg-[#0e1019] py-3 text-center text-[11px] text-gray-700">Polymarket Gamma/CLOB + Binance · Prices in cents · UP+DOWN≈100¢ · Python/Flask backend</footer>

<script>
const $ = id => document.getElementById(id);
function log(m){$('log_el').innerHTML='<div>['+new Date().toLocaleTimeString()+'] '+m+'</div>'+$('log_el').innerHTML}
function showTab(t){document.querySelectorAll('[id^="tab_"]').forEach(el=>el.classList.add('hidden'));$('tab_'+t).classList.remove('hidden');document.querySelectorAll('[data-tab]').forEach(b=>{b.dataset.active=b.dataset.tab===t?'true':'false'})}
function renderInsight(ins,color){
  const cc=color==='emerald'?'text-emerald-400':'text-cyan-400';
  let samp='';
  if(ins.samples&&ins.samples.length){samp='<div class="mt-1.5 space-y-0.5 font-mono text-[10px] text-gray-700">'+ins.samples.map(s=>'<div>'+s.slug.slice(-12)+' entry:'+s.entry.toFixed(0)+'c '+(s.btc_d!==undefined?'Δ$'+s.btc_d.toFixed(0)+' ':'')+' net:'+s.net.toFixed(1)+'c</div>').join('')+'</div>'}
  return '<div><div class="flex justify-between gap-2"><span class="font-medium text-gray-300">'+ins.title+'</span><span class="font-mono '+cc+'">'+(ins.confidence*100).toFixed(0)+'%</span></div><p class="mt-1 text-gray-400">'+ins.bottomline+'</p><p class="mt-0.5 text-gray-700 font-mono text-[10px]">n='+ins.support+' · avg entry '+ins.avg_entry.toFixed(0)+'c · net '+ins.avg_net_pnl.toFixed(1)+'c</p>'+samp+'</div>'
}
function mkCells(arr,cls){
  if(!arr||!arr.length)return'<td colspan="3" class="px-2 py-1 text-center text-gray-700">—</td>';
  const pts=arr.length<=3?arr:[arr[0],arr[Math.floor(arr.length/2)],arr[arr.length-1]];
  return pts.map(p=>'<td class="px-2 py-1 text-center font-mono '+cls+'">'+(p.p*100).toFixed(1)+'¢</td>').join('')
}
function mkBtc(arr){
  if(!arr||!arr.length)return'<td colspan="3" class="px-2 py-1 text-center text-gray-700">—</td>';
  const pts=arr.length<=3?arr:[arr[0],arr[Math.floor(arr.length/2)],arr[arr.length-1]];
  return pts.map(p=>{const d=p.delta||0;const c=d>=0?'text-cyan-400':'text-orange-400';return'<td class="px-2 py-1 text-center font-mono '+c+'">'+(d>=0?'+':'')+d.toFixed(0)+'</td>'}).join('')
}
function etTime(ts){return new Date(ts*1000).toLocaleTimeString('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hour12:false})}

function doFetch(){
  $('btn_fetch').disabled=true;$('progress').classList.remove('hidden');
  ['summary_box','failures_box','tod_box','strat_box','sample_box'].forEach(id=>$(id).classList.add('hidden'));
  const body={asset:$('asset').value,interval:$('interval').value,start_date:$('sd').value,end_date:$('ed').value};
  log('Fetching '+JSON.stringify(body));
  fetch('/api/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(r=>r.json()).then(d=>{
    $('btn_fetch').disabled=false;$('progress').classList.add('hidden');
    if(d.error){log('ERROR: '+d.error);return}
    log('Done. '+d.total+' markets, '+d.ok+' ok, '+d.fail+' failed');
    $('s_total').textContent=d.total;$('s_ok').textContent=d.ok;$('s_fail').textContent=d.fail;$('s_cache').textContent=d.cached;
    const a=d.analysis;
    $('s_updown').innerHTML=a?'<span class="text-emerald-400">'+a.up_closes+'</span> / <span class="text-red-400">'+a.down_closes+'</span>':'-';
    $('s_corr').textContent=a&&a.correlation!==null?a.correlation.toFixed(3):'N/A';
    $('summary_box').classList.remove('hidden');
    if(d.failed_reasons&&d.failed_reasons.length){$('fail_list').innerHTML=d.failed_reasons.map(f=>'<div><span class="text-yellow-500">'+f.slug+'</span> — '+f.error+'</div>').join('');$('failures_box').classList.remove('hidden')}
    if(a&&a.tod_segments){const tb=$('tod_body');tb.innerHTML='';a.tod_segments.forEach(s=>{tb.innerHTML+='<tr class="border-b border-gray-800/30"><td class="px-3 py-1 font-mono text-gray-300">'+s.range+'</td><td class="px-3 py-1 text-right text-gray-400">'+s.markets+'</td><td class="px-3 py-1 text-right text-emerald-400">'+s.up+'</td><td class="px-3 py-1 text-right text-red-400">'+s.down+'</td><td class="px-3 py-1 text-right font-mono text-gray-200">'+s.up_pct.toFixed(1)+'%</td></tr>'});$('tod_box').classList.remove('hidden')}
    if(a){
      const oi=a.outcome_insights||[],pi=a.profit_insights||[];
      $('oi_list').innerHTML=oi.length?oi.map(i=>renderInsight(i,'emerald')).join(''):'<p class="text-gray-600 italic">None found.</p>';
      $('pi_list').innerHTML=pi.length?pi.map(i=>renderInsight(i,'cyan')).join(''):'<p class="text-gray-600 italic">None found.</p>';
      $('strat_box').classList.remove('hidden');
      // Populate simulate tab
      const all=[...oi,...pi];
      $('sim_strategies').innerHTML=all.length?all.map((ins,i)=>'<button onclick="doSim('+i+')" class="w-full text-left rounded border border-gray-800/40 p-2 hover:bg-white/5"><div class="flex justify-between"><span class="text-xs text-gray-300">'+ins.title+'</span><span class="text-xs font-mono text-cyan-400">'+(ins.confidence*100).toFixed(0)+'%</span></div></button>').join(''):'<p class="text-xs text-gray-600">Run a fetch first.</p>';
      window._insights=all;
    }
    if(d.sample&&d.sample.length){const tb=$('sample_body');tb.innerHTML='';d.sample.forEach(m=>{tb.innerHTML+='<tr class="border-b border-gray-800/30"><td class="px-3 py-1 font-mono text-gray-400 whitespace-nowrap">'+etTime(m.window_start_ts)+'–'+etTime(m.window_end_ts)+'</td>'+mkCells(m.up_snapshots,'text-emerald-400')+mkCells(m.down_snapshots,'text-red-400')+mkBtc(m.btc_snapshots)+'</tr>'});$('sample_box').classList.remove('hidden')}
  }).catch(e=>{$('btn_fetch').disabled=false;$('progress').classList.add('hidden');log('Error: '+e)})
}

function doDiagnose(){$('btn_diag').disabled=true;$('diag_box').classList.add('hidden');log('Diagnosing...');fetch('/api/diagnose').then(r=>r.json()).then(d=>{$('btn_diag').disabled=false;const tb=$('diag_body');tb.innerHTML='';d.forEach(r=>{tb.innerHTML+='<tr class="border-b border-gray-800/30"><td class="px-3 py-1 font-mono text-gray-300">'+r.asset+' '+r.interval+'</td><td class="px-3 py-1 font-mono text-[10px] text-gray-600">'+r.slug+'</td><td class="px-3 py-1">'+(r.ok?'<span class="text-emerald-400">✓</span>':'<span class="text-yellow-500">✗</span>')+'</td><td class="px-3 py-1 text-gray-500 text-[10px]">'+(r.error||'')+'</td></tr>'});$('diag_box').classList.remove('hidden');log('Diagnosis done')})}

function doOOS(){log('Running OOS...');fetch('/api/oos').then(r=>r.json()).then(d=>{
  if(d.error){log('OOS: '+d.error);return}
  const box=$('oos_box');box.innerHTML='';
  function renderHalf(label,data,color,size){
    const bc=color==='emerald'?'border-emerald-900/30':'border-cyan-900/30';
    const tc=color==='emerald'?'text-emerald-400':'text-cyan-400';
    let h='<div class="rounded-xl border '+bc+' bg-[#12141f] p-4 space-y-2"><h3 class="text-sm font-semibold '+tc+'">'+label+' ('+size+' markets)</h3>';
    if(data){h+='<p class="text-xs text-gray-400">UP: '+data.up_closes+' DOWN: '+data.down_closes+'</p>';h+='<p class="text-xs text-gray-400">Outcome rules: '+(data.outcome_insights||[]).length+' | Volatility: '+(data.profit_insights||[]).length+'</p>';
      (data.outcome_insights||[]).slice(0,3).forEach(i=>{h+='<div class="text-xs text-gray-500"><span class="font-mono '+tc+'">'+(i.confidence*100).toFixed(0)+'%</span> '+i.title+'</div>'})
    }else{h+='<p class="text-xs text-gray-600">No usable data.</p>'}
    h+='</div>';return h
  }
  box.innerHTML=renderHalf('Training (First Half)',d.train,'emerald',d.train_size)+renderHalf('Validation (Second Half)',d.validation,'cyan',d.val_size);
  box.classList.remove('hidden');log('OOS done')
})}

window._insights=[];
function doSim(idx){
  const ins=window._insights[idx];if(!ins)return;
  const bet=$('bet_size').value||100;
  log('Simulating: '+ins.title+' @ $'+bet);
  fetch('/api/simulate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({insight:ins,bet_size:parseFloat(bet)})})
  .then(r=>r.json()).then(d=>{
    if(d.error){log('Sim error: '+d.error);return}
    const box=$('sim_result');
    box.innerHTML='<div class="col-span-full text-xs font-semibold text-cyan-400 mb-1">'+ins.title+'</div>'+
      [['Trades',d.total_trades],['Hit Rate',d.hit_rate.toFixed(0)+'%'],['Total P&L','$'+d.total_pnl.toFixed(0)],['P&L %',d.total_pnl_pct.toFixed(1)+'%'],['Max Drawdown','-$'+d.max_drawdown.toFixed(0)],['Worst Loss','$'+d.worst_loss.toFixed(2)],['Wins',d.wins],['Losses',d.losses]].map(([l,v])=>'<div><span class="text-gray-500 text-xs">'+l+'</span><p class="font-mono text-gray-200">'+v+'</p></div>').join('');
    box.classList.remove('hidden');log('Sim done: '+d.total_trades+' trades, P&L $'+d.total_pnl.toFixed(0))
  })
}

function doExport(){fetch('/api/export').then(r=>r.blob()).then(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='markets_export.json';a.click()})}
function doImport(e){const f=e.target.files[0];if(!f)return;const r=new FileReader();r.onload=ev=>{fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:ev.target.result}).then(r=>r.json()).then(d=>{log('Imported '+d.count+' markets');doFetch()})};r.readAsText(f)}
function doClearCache(){fetch('/api/clear_cache',{method:'POST'}).then(r=>r.json()).then(d=>log(d.message))}

// Snapshots loader
function loadSnaps(){fetch('/api/snapshots').then(r=>r.json()).then(d=>{const tb=$('snap_body');tb.innerHTML='';d.forEach(s=>{tb.innerHTML+='<tr class="border-b border-gray-800/30"><td class="px-3 py-1 font-mono text-gray-500 text-[10px]">'+s.id+'</td><td class="px-3 py-1 text-gray-400 text-[10px]">'+new Date(s.timestamp*1000).toLocaleString()+'</td><td class="px-3 py-1 text-gray-300">'+s.asset+'</td><td class="px-3 py-1 text-gray-300">'+s.window+'</td><td class="px-3 py-1 font-mono text-gray-500 text-[10px]">'+s.date_range+'</td><td class="px-3 py-1 text-right text-emerald-400">'+s.outcome_count+'</td><td class="px-3 py-1 text-right text-cyan-400">'+s.profit_count+'</td></tr>'})})}
loadSnaps();

// Progress polling
setInterval(()=>{fetch('/api/progress').then(r=>r.json()).then(p=>{if(p.running){$('progress').classList.remove('hidden');$('phase').textContent=p.phase;$('pcount').textContent=p.done+'/'+p.total;$('pbar').style.width=(p.total>0?(p.done/p.total*100):0)+'%'}}).catch(()=>{})},800)
</script>
</body>
</html>"""

# ─── Routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML, assets=ASSETS, intervals=INTERVALS)

@app.route("/api/progress")
def api_progress():
    return jsonify(progress_state)

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    global progress_state, last_results, last_analysis
    body = request.get_json() or {}
    asset = body.get("asset") or "BTC"
    interval = body.get("interval") or "5m"
    sd = body.get("start_date") or "2026-04-17"
    ed = body.get("end_date") or "2026-04-18"
    try:
        validate_date_range(sd, ed)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    progress_state = {"running": True, "phase": "Starting", "done": 0, "total": 0}
    try:
        results = fetch_range(sd, ed, asset, interval, progress_cb)
        last_results = results
        ok = [r for r in results if not r.get("error")]
        fail = [r for r in results if r.get("error")]
        analysis_result = analyze(results, asset)
        last_analysis = analysis_result
        # Save snapshot
        if analysis_result:
            save_snapshot({
                "asset": asset, "window": interval, "date_range": f"{sd} to {ed}",
                "outcome_count": len(analysis_result.get("outcome_insights", [])),
                "profit_count": len(analysis_result.get("profit_insights", [])),
            })
        progress_state["running"] = False
        return jsonify({
            "total": len(results), "ok": len(ok), "fail": len(fail),
            "cached": cache_count(), "analysis": analysis_result,
            "sample": ok[:10],
            "failed_reasons": [{"slug": r["slug"], "error": r["error"]} for r in fail[:20]],
        })
    except Exception as e:
        progress_state["running"] = False
        return _error_response(e)

@app.route("/api/diagnose")
def api_diagnose():
    return jsonify(run_diagnostics())

@app.route("/api/oos")
def api_oos():
    if not last_results:
        return jsonify({"error": "Run a fetch first."})
    asset = request.args.get("asset", "BTC")
    result = validate_oos(last_results, asset)
    if not result:
        return jsonify({"error": "Not enough data for OOS split."})
    return jsonify(result)

@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    if not last_results:
        return jsonify({"error": "Run a fetch first."})
    body = request.get_json() or {}
    insight = body.get("insight")
    bet_size = body.get("bet_size", 100)
    if not insight or not isinstance(insight, dict):
        return jsonify({"error": "No insight provided."}), 400
    if not isinstance(bet_size, (int, float)) or bet_size <= 0:
        return jsonify({"error": "bet_size must be a positive number."}), 400
    try:
        result = simulate_strategy(last_results, insight, bet_size)
        return jsonify(result)
    except Exception as e:
        return _error_response(e)

@app.route("/api/export")
def api_export():
    if not last_results:
        return Response("[]", mimetype="application/json")
    return Response(
        export_markets(last_results),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=markets_export.json"}
    )

@app.route("/api/import", methods=["POST"])
def api_import():
    global last_results
    data = request.get_json()
    try:
        validate_import_payload(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    last_results = data
    return jsonify({"count": len(data)})

@app.route("/api/snapshots")
def api_snapshots():
    return jsonify(load_snapshots())

@app.route("/api/clear_cache", methods=["POST"])
def api_clear_cache():
    cache_clear()
    return jsonify({"message": "Cache cleared."})

if __name__ == "__main__":
    print("=" * 60)
    print(" Polymarket Up/Down Analyzer")
    print(" Open http://127.0.0.1:8080 in your browser")
    print(" Press Ctrl+C to stop")
    print("=" * 60)
    app.run(debug=False, port=8080)
