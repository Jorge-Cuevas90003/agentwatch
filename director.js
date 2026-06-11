/* ============================================================================
 * AgentWatch — DIRECTOR MODE  (demo auto-pilot for OBS recording)
 * ----------------------------------------------------------------------------
 * Pega TODO este archivo en la consola del navegador (F12 → Console) sobre la
 * app YA cargada y dale Enter. Hace esto:
 *   1. Pre-carga TODAS las respuestas reales (traces, evals, cost, trend, A/B,
 *      chat) en segundo plano — usa datos 100% reales de Gemini/Phoenix.
 *   2. Cuenta un offset (default 20s) para que arranques OBS.
 *   3. Ejecuta las 7 escenas en los timestamps EXACTOS del guión.
 *
 * Config por URL:  ?offset=20   (segundos antes de empezar)
 *                  ?marker=1    (muestra un marcador de escena en una esquina)
 * O edita CONFIG abajo.
 * ========================================================================== */
(async function AgentWatchDirector(){
  const Q = new URLSearchParams(location.search);
  const CONFIG = {
    offset:   +(Q.get('offset') || 20),         // segundos de cuenta atrás
    marker:   Q.get('marker') === '1',           // marcador de escena visible
    candidate:"Answer in at most two short sentences. Be direct and concrete. No preamble.",
    evalType: "hallucination",                    // rúbrica para la pestaña Evals
    abEval:   "conciseness",                       // rúbrica para el A/B
  };

  // ---- helpers -------------------------------------------------------------
  const wait  = ms => new Promise(r => setTimeout(r, ms));
  const $     = s => document.querySelector(s);
  const click = s => { const e = $(s); if (e) e.click(); };
  const log   = (...a) => console.log('%c[director]', 'color:#2997FF;font-weight:700', ...a);

  if (typeof openPanel !== 'function' || typeof api !== 'function') {
    alert('Director: abre la app de AgentWatch primero (no se encontraron sus funciones).');
    return;
  }
  if (!G.project) { log('esperando proyecto…'); await wait(1500); }

  const P = encodeURIComponent(G.project);

  // ---- overlay (countdown + scene marker) ----------------------------------
  const ov = document.createElement('div');
  ov.id = 'dir-ov';
  ov.style.cssText =
    'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;'+
    'flex-direction:column;gap:14px;background:rgba(12,12,20,.92);backdrop-filter:blur(8px);'+
    'font-family:Inter,sans-serif;color:#F5F5F7;text-align:center;transition:opacity .4s';
  ov.innerHTML =
    '<div id="dir-big" style="font-size:64px;font-weight:700;letter-spacing:-.03em">…</div>'+
    '<div id="dir-msg" style="font-size:15px;color:rgba(245,245,247,.55);max-width:420px;line-height:1.5"></div>';
  document.body.appendChild(ov);
  const big = $('#dir-big'), msg = $('#dir-msg');

  // small corner marker shown during playback (optional)
  const mark = document.createElement('div');
  mark.style.cssText =
    'position:fixed;bottom:14px;left:14px;z-index:9998;font-family:JetBrains Mono,monospace;'+
    'font-size:11px;color:rgba(245,245,247,.5);background:rgba(12,12,20,.7);padding:5px 9px;'+
    'border-radius:6px;border:1px solid rgba(255,255,255,.08);'+(CONFIG.marker?'':'display:none');
  document.body.appendChild(mark);
  const setMark = t => { mark.textContent = t; };

  // ==========================================================================
  // PHASE 1 — PREFETCH everything (real calls) while overlay is up
  // ==========================================================================
  big.textContent = '⏳';
  msg.textContent = 'Pre-cargando datos reales (traces, evals, costo, tendencia, A/B, chat)… esto tarda según Gemini.';

  // Fire the real handlers / API calls. They render into their (hidden) panels.
  async function untilFilled(sel, hasSel, timeout=120000){
    const t0 = Date.now();
    while (Date.now()-t0 < timeout){
      const out = $(sel);
      if (out && (hasSel ? out.querySelector(hasSel) : out.children.length) && !out.querySelector('.sp')) return true;
      await wait(300);
    }
    return false;
  }

  try {
    // Traces (recent)
    G.traceMode = 'recent';
    click('#btnTraces');
    await untilFilled('#tracesTable', 'table');
    log('traces listo');

    // Evals — set rubric pill then run
    [...document.querySelectorAll('#evalPills .pill')].forEach(p=>p.classList.toggle('active', p.dataset.v===CONFIG.evalType));
    G.evalType = CONFIG.evalType;
    click('#btnEval');
    await untilFilled('#evalOut', '.em-grid, .estate');
    log('evals listo');

    // Cost
    click('#btnCost');
    await untilFilled('#costBars', '.crow, .estate');
    log('cost listo');

    // Trend
    click('#btnTrend');
    await untilFilled('#trendOut', '.verd2');
    log('trend listo');

    // A/B experiment — fill prompt, set rubric, run
    $('#expPrompt').value = CONFIG.candidate;
    [...document.querySelectorAll('#expEvalPills .pill')].forEach(p=>p.classList.toggle('active', p.dataset.v===CONFIG.abEval));
    G.expEval = CONFIG.abEval;
    click('#btnExp');
    await untilFilled('#expOut', '.abcmp');
    log('A/B listo');

    // Chat — send a real diagnostic prompt (renders into #msgs)
    if (typeof sendMsg === 'function') {
      await sendMsg(`Diagnose failures in '${G.project}'. Root cause + concrete fix, cite trace IDs.`);
      log('chat listo');
    }
  } catch (e) { log('prefetch error (sigo igual):', e.message); }

  // reset visible state to a clean start (panel closed) before recording
  if (G.panelOpen) { click('#panelClose'); await wait(400); }
  ['#av-trend','#av-cost','#av-experiment'].forEach(s=>{ if($(s)) $(s).style.display='none'; });
  if ($('#av-evals')) $('#av-evals').style.display='block';
  [...document.querySelectorAll('#analysisPills .pill')].forEach(p=>p.classList.toggle('active',p.dataset.av==='evals'));

  // ==========================================================================
  // PHASE 2 — COUNTDOWN (start OBS now)
  // ==========================================================================
  msg.innerHTML = 'Datos listos. <b>Inicia la grabación en OBS ahora.</b><br>El demo arranca solo al llegar a 0.';
  for (let s = CONFIG.offset; s > 0; s--){
    big.textContent = s;
    await wait(1000);
  }
  // hide overlay -> recording begins
  ov.style.opacity = '0';
  await wait(420);
  ov.remove();

  // ==========================================================================
  // PHASE 3 — SCENE PLAYBACK at exact timestamps (ms from T0)
  // ==========================================================================
  const T0 = Date.now();
  const at = async (ms, name, fn) => {
    const due = T0 + ms - Date.now();
    if (due > 0) await wait(due);
    setMark(name);
    try { fn(); } catch(e){ log('scene err', name, e.message); }
  };

  // Re-fire the intro animation as Scene 1
  function replayIntro(){
    const intro = $('#intro');
    if (!intro) return;
    intro.style.display = 'flex';
    intro.style.opacity = '1';
    $('#introLogo').style.opacity = '0';
    $('#introSub').style.opacity = '0';
    anime.timeline()
      .add({targets:'#introLogo',opacity:[0,1],translateY:[12,0],duration:700,easing:'easeOutQuart'})
      .add({targets:'#introSub',opacity:[0,1],translateY:[8,0],duration:500,easing:'easeOutQuart'},'-=400')
      .add({targets:'#intro',opacity:[1,0],duration:400,easing:'easeInQuad',
        complete(){ intro.style.display='none'; }},'+=800');
  }
  const showAV = v => {
    [...document.querySelectorAll('#analysisPills .pill')].forEach(p=>p.classList.toggle('active',p.dataset.av===v));
    ['evals','trend','cost','experiment'].forEach(x=>{ const el=$(`#av-${x}`); if(el) el.style.display = x===v?'block':'none'; });
    G.analysisView = v;
  };

  // ---- the timeline (matches DEMO_SCRIPT.md) ----
  await at(    0, 'E1 · Intro',          () => replayIntro());
  await at(13000, 'E2 · Traces',         () => openPanel('traces'));
  await at(24000, 'E2 · Expand trace',   () => { const r=$('#tracesTable tbody tr:not(.xrow)'); if(r) r.click(); });
  await at(34000, 'E3 · Evals',          () => { openPanel('analysis'); showAV('evals'); });
  await at(56000, 'E4 · Cost',           () => { openPanel('analysis'); showAV('cost'); });
  await at(70000, 'E4 · Trend',          () => { openPanel('analysis'); showAV('trend'); });
  await at(82000, 'E5 · A/B experiment', () => { openPanel('analysis'); showAV('experiment'); });
  await at(132000,'E6 · Chat',           () => { openPanel('chat'); const m=$('#msgs'); if(m) m.scrollTop=0;
                                                 // gentle scroll through the answer
                                                 let y=0; const iv=setInterval(()=>{ if(!m){clearInterval(iv);return;} y+=m.scrollHeight/40; m.scrollTop=y; if(y>=m.scrollHeight)clearInterval(iv); },400); });
  await at(154000,'E7 · Cierre',         () => { openPanel('analysis'); showAV('experiment'); });
  await at(168000,'— fin —',             () => { setMark('● grabación lista — corta aquí'); });
  log('DEMO COMPLETO (2:48). Detén la grabación.');
})();
