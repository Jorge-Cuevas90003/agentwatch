# AgentWatch — Demo Video Script (OBS)

**Total: ~2:45** · Narración en inglés (jueces) · Timing calibrado a ~2.4 palabras/s
**Tip de grabación:** Evals y A/B tardan (llamadas a Gemini). Pre-ejecútalos una vez
para "calentar", o graba y en edición corta/acelera la espera para que calce con el timing.

---

## ESCENA 1 — Intro / Hook · `0:00 – 0:13` (13s)

**Pantalla:** Recargar la página. Se ve la animación galaxy + "AgentWatch / Agent observability",
luego el dashboard vacío con el fondo de partículas.

**Acción OBS:** Empieza a grabar justo antes de recargar. Deja correr la intro (~2s).

**Narración (≈36 palabras):**
> "AI agents fail silently in production. They get slower, hallucinate, and burn tokens —
> and you find out from your users. This is AgentWatch: a reliability engineer for your agents,
> built with Google ADK and Gemini, integrated with Arize Phoenix for full observability."

---

## ESCENA 2 — Traces / Observabilidad · `0:13 – 0:34` (21s)

**Pantalla:** Click en **Trazas/Traces** (se abre el panel). Click **Load**.
Aparecen las trazas con latencia, errores, conteo de spans. Click en **una traza** para expandirla
y mostrar los spans (LLM, tool calls, excepciones).

**Acción OBS:** Click Traces → Load → esperar a que carguen → click en una fila para expandir.

**Narración (≈52 palabras):**
> "It connects to Arize Phoenix — via MCP — and pulls every trace. Latency, errors, token counts —
> all in one place. Click any trace to drill into its spans: the LLM calls, the tool calls,
> the exact exception that broke it. No more guessing why an agent failed."

---

## ESCENA 3 — Evals (LLM-as-judge) · `0:34 – 0:56` (22s)

**Pantalla:** Click **Análisis/Analysis** → sub-pestaña **Evals**. Elegir rúbrica (Hallu/QA) → **Run**.
Aparecen: Avg Score, distribución de labels, tabla por span.

**Acción OBS:** Click Analysis → Evals → Run. (Si tarda, corta la espera en edición.)

**Narración (≈48 palabras):**
> "But monitoring isn't enough. AgentWatch grades quality. It runs Gemini as an
> LLM-judge over your spans — checking for hallucination, relevance, correctness —
> and writes the scores straight back to Phoenix as annotations. Real evaluation,
> not just dashboards."

---

## ESCENA 4 — Cost + Trend · `0:56 – 1:22` (26s)

**Pantalla:** Click sub-pestaña **Cost** → **Analyze** (muestra tokens + costo USD por traza).
Luego click **Trend** → **Compare** (muestra el badge: IMPROVING / DEGRADING / **MIXED**).

**Acción OBS:** Click Cost → Analyze → (pausa breve) → click Trend → Compare.

**Narración (≈58 palabras):**
> "It tracks spend down to the dollar, so you see exactly which traces cost you money.
> And it answers the real question — is my agent getting better or worse? It compares
> time windows honestly: if errors drop but latency doubles, it won't lie and say
> 'improved'. It says 'mixed', and shows you both numbers."

---

## ESCENA 5 — ⭐ A/B Experiment (el diferenciador) · `1:22 – 2:12` (50s)

**Pantalla:** Click sub-pestaña **A/B**. Escribir en el textarea un prompt candidato,
p.ej. *"Answer in at most two short sentences. Be direct, no preamble."*
Elegir rúbrica **Concise** → **Run A/B**. Aparece el badge **CANDIDATE BETTER**,
las barras baseline vs candidate, y el delta por request con el output real del candidato.

**Acción OBS:** Click A/B → escribir el prompt (escríbelo despacio, se ve bien) →
Run A/B → esperar el resultado. **Este es el clímax — déjalo respirar.**

**Narración (≈92 palabras, habla un poco más pausado aquí):**
> "And here's what makes AgentWatch different. Most tools stop at 'here's a suggested fix.'
> AgentWatch proves it. Write a candidate prompt — and it runs a real A/B test against your
> production traffic. It takes your actual requests, runs the new prompt, and has Gemini judge
> the old output against the new one on the same rubric. The verdict comes back with evidence:
> candidate better, by this much, won two of three. It closes the self-improvement loop —
> diagnose, fix, and validate — automatically."

---

## ESCENA 6 — Chat Agent · `2:12 – 2:42` (30s)

**Pantalla:** El agente envía automáticamente *"Why is my agent failing? Root cause and concrete fix — cite trace IDs from Phoenix."*
Se ven las **tool calls en vivo**: `get_failure_traces` → `run_llm_evals` → respuesta en markdown con IDs de trazas reales.

**Acción OBS:** Solo observar — el director envía el mensaje y transmite las tool calls en tiempo real. **Esto muestra la integración MCP con Arize Phoenix.**

**Narración (≈55 palabras):**
> "All of it is also one conversation away. Ask in plain English — 'why is my agent failing?' —
> and the agent calls Arize Phoenix directly: pulls the traces, fetches the spans, runs the evals,
> and hands you a root cause with concrete fix — citing the exact trace IDs.
> It does the reliability work for you."

---

## ESCENA 7 — Cierre · `2:42 – 2:55` (13s)

**Pantalla:** Volver a una vista limpia (Traces cargado o el badge del A/B). Opcional: mostrar
la URL en vivo o el logo. Fade out suave.

**Acción OBS:** Vista final estática. Deja 2-3s de aire al final para el fade.

**Narración (≈34 palabras):**
> "AgentWatch — observe, evaluate, and improve your AI agents, with proof.
> Built with Google ADK, powered by Gemini, integrated with Arize Phoenix.
> It's live, it's open source, and it closes the loop automatically."

---

## Resumen de timing (para alinear el audio)

| # | Escena | Inicio | Fin | Dur |
|---|--------|--------|-----|-----|
| 1 | Intro / Hook | 0:00 | 0:13 | 13s |
| 2 | Traces | 0:13 | 0:34 | 21s |
| 3 | Evals | 0:34 | 0:56 | 22s |
| 4 | Cost + Trend | 0:56 | 1:22 | 26s |
| 5 | ⭐ A/B Experiment | 1:22 | 2:12 | 50s |
| 6 | Chat Agent (live tool calls) | 2:12 | 2:42 | 30s |
| 7 | Cierre | 2:42 | 2:55 | 13s |

**Total: 2:55**

### Notas de producción
- **Música:** fondo sutil, sube un poco en Escena 5 (clímax).
- **Cortes de espera:** en Evals (E3) y A/B (E5), si Gemini tarda >5s, corta o acelera 2x
  la espera para que el audio calce. El timing de arriba asume las respuestas ya cargadas.
- **Resolución:** graba 1920×1080. El dashboard se ve mejor en desktop (panel 420px + galaxy).
- **Si el free tier de Render está dormido:** abre la app 1 min antes para despertarla, o graba
  contra localhost para que todo sea instantáneo.
- **Mouse:** mueve lento y deliberado; los jueces siguen el cursor.
