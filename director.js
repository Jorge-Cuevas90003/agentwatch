/* AgentWatch — DIRECTOR MODE LITE (free tier safe)
 * Pega en consola (F12) sobre la app cargada y dale Enter.
 * Solo pre-carga datos rápidos (sin Gemini). Evals, A/B y Chat
 * corren EN VIVO durante la grabación — auténtico y sin timeouts.
 *
 * ?offset=30  segundos antes de iniciar (default 30)
 * ?marker=1   muestra marcador de escena en esquina
 */
(async function AgentWatchDirector(){
  const Q = new URLSearchParams(location.search);
  const CONFIG = {
    offset:    +(Q.get('offset') || 30),
    marker:    Q.get('marker') === '1',
    candidate: "Answer in at most two short sentences. Be direct. No preamble.",
    evalType:  "hallucination",
    abEval:    "conciseness",
  };

  const wait  = ms => new Promise(r => setTimeout(r, ms));
  const $     = s  => document.querySelector(s);
  const click = s  => { const e=$(s); if(e) e.click(); };
  const log   = (...a) => console.log('%c[director]','color:#2997FF;font-weight:700',...a);

  if(typeof openPanel!=='function'||typeof api!=='function'){
    alert('Abre AgentWatch primero (funciones no encontradas).');return;
  }
  if(!G.project){ await wait(2000); }

  // ── overlay ──────────────────────────────────────────────────────────────
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;z-index:9999;display:flex;align-items:center;'+
    'justify-content:center;flex-direction:column;gap:14px;background:rgba(12,12,20,.92);'+
    'backdrop-filter:blur(8px);font-family:Inter,sans-serif;color:#F5F5F7;text-align:center;transition:opacity .4s';
  ov.innerHTML='<div id="dir-big" style="font-size:64px;font-weight:700;letter-spacing:-.03em">…</div>'+
    '<div id="dir-msg" style="font-size:15px;color:rgba(245,245,247,.55);max-width:420px;line-height:1.5"></div>';
  document.body.appendChild(ov);
  const big=$('#dir-big'),msg=$('#dir-msg');

  const mark=document.createElement('div');
  mark.style.cssText='position:fixed;bottom:14px;left:14px;z-index:9998;font-family:monospace;'+
    'font-size:11px;color:rgba(245,245,247,.5);background:rgba(12,12,20,.7);padding:5px 9px;'+
    'border-radius:6px;border:1px solid rgba(255,255,255,.08);'+(CONFIG.marker?'':'display:none');
  document.body.appendChild(mark);
  const setMark=t=>{mark.textContent=t;};

  // ── PHASE 1 — prefetch solo lo rápido (sin Gemini) ───────────────────────
  big.textContent='⏳';
  msg.textContent='Cargando trazas, costo y tendencia (sin Gemini — rápido)…';

  async function untilFilled(sel,hasSel,timeout=25000){
    const t0=Date.now();
    while(Date.now()-t0<timeout){
      const el=$(sel);
      if(el&&(hasSel?el.querySelector(hasSel):el.children.length)&&!el.querySelector('.sp')) return true;
      await wait(400);
    }
    return false;
  }

  try{
    G.traceMode='recent';
    click('#btnTraces');
    await untilFilled('#tracesTable','table');
    log('traces ✓'); await wait(600);

    click('#btnCost');
    await untilFilled('#costBars','.crow,.estate');
    log('cost ✓'); await wait(600);

    click('#btnTrend');
    await untilFilled('#trendOut','.verd2');
    log('trend ✓'); await wait(400);

    // Pre-configura A/B y Evals sin ejecutar
    if($('#expPrompt')) $('#expPrompt').value=CONFIG.candidate;
    if($('#expLim'))    $('#expLim').value='1';
    [...document.querySelectorAll('#expEvalPills .pill')].forEach(p=>p.classList.toggle('active',p.dataset.v===CONFIG.abEval));
    G.expEval=CONFIG.abEval;
    [...document.querySelectorAll('#evalPills .pill')].forEach(p=>p.classList.toggle('active',p.dataset.v===CONFIG.evalType));
    G.evalType=CONFIG.evalType;

    msg.innerHTML='✅ Listo. Evals y A/B correrán <b>en vivo</b> durante la grabación.';
  }catch(e){ log('prefetch error (continúo):', e.message); }

  if(G.panelOpen){ click('#panelClose'); await wait(400); }

  // ── PHASE 2 — countdown ──────────────────────────────────────────────────
  msg.innerHTML='Datos listos. <b>Inicia la grabación en OBS ahora.</b><br>El demo arranca solo al llegar a 0.';
  for(let s=CONFIG.offset;s>0;s--){ big.textContent=s; await wait(1000); }
  ov.style.opacity='0'; await wait(420); ov.remove();

  // ── PHASE 3 — playback ───────────────────────────────────────────────────
  const T0=Date.now();
  const at=async(ms,name,fn)=>{
    const due=T0+ms-Date.now(); if(due>0) await wait(due);
    setMark(name); try{ fn(); }catch(e){ log('scene err',name,e.message); }
  };

  function replayIntro(){
    const intro=$('#intro'); if(!intro) return;
    intro.style.display='flex'; intro.style.opacity='1';
    $('#introLogo').style.opacity='0'; $('#introSub').style.opacity='0';
    anime.timeline()
      .add({targets:'#introLogo',opacity:[0,1],translateY:[12,0],duration:700,easing:'easeOutQuart'})
      .add({targets:'#introSub',opacity:[0,1],translateY:[8,0],duration:500,easing:'easeOutQuart'},'-=400')
      .add({targets:'#intro',opacity:[1,0],duration:400,easing:'easeInQuad',
        complete(){intro.style.display='none';}},'+=800');
  }

  const showAV=v=>{
    [...document.querySelectorAll('#analysisPills .pill')].forEach(p=>p.classList.toggle('active',p.dataset.av===v));
    ['evals','trend','cost','experiment'].forEach(x=>{
      const el=$(`#av-${x}`); if(el) el.style.display=x===v?'block':'none';
    });
    G.analysisView=v;
  };

  // ── Timeline ─────────────────────────────────────────────────────────────
  // Evals y A/B se EJECUTAN EN VIVO — auténtico para los jueces
  await at(    0,'E1 · Intro',       ()=> replayIntro());
  await at(13000,'E2 · Traces',      ()=> openPanel('traces'));
  await at(24000,'E2 · Expand',      ()=>{ const r=$('#tracesTable tbody tr:not(.xrow)'); if(r) r.click(); });
  await at(34000,'E3 · Evals →RUN',  ()=>{ openPanel('analysis'); showAV('evals'); click('#btnEval'); });
  await at(60000,'E4 · Cost',        ()=>{ openPanel('analysis'); showAV('cost'); });
  await at(74000,'E4 · Trend',       ()=>{ openPanel('analysis'); showAV('trend'); });
  await at(86000,'E5 · A/B →RUN',   ()=>{ openPanel('analysis'); showAV('experiment'); click('#btnExp'); });
  await at(136000,'E6 · Chat →LIVE', ()=>{
    openPanel('chat');
    const m=$('#msgs'); if(m) m.innerHTML='';
    G.sessionId=null;
    if(typeof sendMsg==='function')
      sendMsg("Why is my agent failing? Root cause and concrete fix — cite trace IDs from Phoenix.");
  });
  await at(166000,'E7 · Cierre',    ()=>{ openPanel('analysis'); showAV('experiment'); });
  await at(178000,'— fin —',        ()=>{ setMark('● grabación lista — corta aquí'); });
  log('DEMO COMPLETO 2:58. Detén la grabación en OBS.');
})();
