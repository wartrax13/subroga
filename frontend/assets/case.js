// Tela /casos/:id — documentos | dados extraídos | conferência e ações.
import { api, download, authImage } from "./api.js";
import {
  $, $$, esc, toast, busy, copyText, statusPill, SCENARIOS, DOC_TYPES, DOC_LABEL, REQUIRED,
  FACTS, FACT_LABEL, docNo, brDate, moneyBR, moneyAPI, basename, plural,
} from "./ui.js";

const ACTIVE = new Set(["RECEBENDO", "PROCESSANDO"]);
const SEV_ORDER = { critical: 0, warn: 1, info: 2 };
const SEV_CLASS = { critical: "a-bad", warn: "a-warn", info: "a-info" };
const SEV_TEXT = { critical: "Crítico", warn: "Atenção", info: "Aviso" };

function goto(path) {
  history.pushState({}, "", path);
  dispatchEvent(new PopStateEvent("popstate"));
}

function pageRange(pages) {
  if (!pages || !pages.length) return "";
  const a = Math.min(...pages) + 1, b = Math.max(...pages) + 1;
  return a === b ? `p. ${a}` : `p. ${a}–${b}`;
}

export async function viewCase(root, [id], stale) {
  const base = `/cases/${encodeURIComponent(id)}`;
  let c = await api("GET", base);
  if (stale()) return;
  let confirmKind = null; // "approve" | "token" | "delete"
  let showLink = false;
  let pollTimer = null;
  let alive = true;

  const docById = () => Object.fromEntries((c.documents || []).map((d) => [String(d.id), d]));

  /* ---------- colunas ---------- */

  function docsCol() {
    const docs = c.documents || [];
    const body = docs.length ? docs.map((d) => {
      const low = d.needs_review;
      const conf = typeof d.confidence === "number" ? Math.round(d.confidence * 100) : null;
      const n = d.number ?? "?";
      return `<article class="doc ${low ? "low" : ""}" id="doc-${esc(d.id)}" tabindex="-1" aria-labelledby="dt-${esc(d.id)}">
        <img class="thumb" alt="" data-thumb="${esc(d.id)}" width="64" height="90">
        <div style="min-width:0">
          <div class="n">${docNo(n)}</div>
          <div class="t" id="dt-${esc(d.id)}">${esc(d.title || d.label || DOC_LABEL[d.doc_type])}</div>
          <div class="f">${esc(basename(d.source_file))}${d.pages?.length ? " · " + pageRange(d.pages) : ""} · ${plural(d.page_count ?? d.pages?.length ?? 0, "página", "páginas")}</div>
          <div class="ctl">
            <label class="sr" for="dtype-${esc(d.id)}">Tipo do ${docNo(n)}</label>
            <select id="dtype-${esc(d.id)}" data-doc="${esc(d.id)}">${DOC_TYPES.map(([k, v]) => `<option value="${k}" ${k === d.doc_type ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
            ${low ? `<span class="pill p-warn">${conf != null ? `confiança ${conf}%: ` : ""}revisar</span>`
              : conf != null ? `<span class="pill p-mut">confiança ${conf}%</span>` : ""}
          </div>
        </div>
      </article>`;
    }).join("") : `<p class="note">${ACTIVE.has(c.status) ? "Os documentos aparecem aqui assim que o processamento terminar." : "Nenhum documento recebido ainda."}</p>`;
    return `<section class="panel" aria-labelledby="hDocs">
      <div class="row"><h2 id="hDocs">Documentos detectados</h2><span class="spacer"></span><span class="note">na ordem do dossiê</span></div>
      <div>${body}</div>
    </section>`;
  }

  function factsCol() {
    const f = c.facts;
    if (!f) {
      return `<section class="panel" aria-labelledby="hFacts"><h2 id="hFacts">Dados extraídos</h2>
        <p class="note">${ACTIVE.has(c.status) ? "Extraindo os dados dos documentos…" : "Ainda não há dados extraídos."}</p></section>`;
    }
    const docs = docById();
    const edited = new Set(c.edited_fields || []);
    const conflicts = new Set((c.alerts || []).filter((a) => a.kind === "CONFLICT" && a.field).map((a) => a.field));
    const alts = f.valores_alternativos || {};
    const rows = FACTS.map(([k, label, kind]) => {
      const ff = f[k] || {};
      const raw = ff.value ?? "";
      const shown = kind === "money" ? moneyBR(raw) : raw;
      const src = ff.source_doc_id != null ? docs[String(ff.source_doc_id)] : null;
      const altList = (alts[k] || []).filter((a) => a && a.value);
      const altText = altList.map((a) => {
        const d = a.source_doc_id != null ? docs[String(a.source_doc_id)] : null;
        return `${kind === "money" ? "R$ " + moneyBR(a.value) : kind === "date" ? brDate(a.value) : a.value}${d ? ` (${docNo(d.number)})` : ""}`;
      }).join("; ");
      const isConflict = conflicts.has(k) || altList.length > 0;
      const input = kind === "long"
        ? `<textarea id="f-${k}" data-fact="${k}" data-kind="long" rows="3">${esc(shown)}</textarea>`
        : `<input id="f-${k}" data-fact="${k}" data-kind="${kind || "text"}" type="${kind === "date" ? "date" : "text"}" ${kind === "money" ? 'inputmode="decimal"' : ""} value="${esc(shown)}">`;
      return `<div class="fact ${isConflict ? "conflict" : ""}">
        <div class="top"><label for="f-${k}">${esc(label)}</label>
          ${edited.has(k) ? '<span class="tag" title="Editado manualmente; reprocessamentos não sobrescrevem">editado</span>' : ""}
          ${isConflict ? '<span class="tag warn">divergente</span>' : ""}</div>
        ${input}
        ${altText ? `<span class="hint">Outro documento traz: ${esc(altText)}</span>` : ""}
        ${src ? `<button type="button" class="src" data-src="${esc(src.id)}">fonte: ${docNo(src.number)}${ff.page != null ? ` · p. ${Number(ff.page) + 1}` : ""}</button>` : ""}
      </div>`;
    }).join("");
    const ev = (f.evidencias_culpa || []).filter((e) => e && e.fato);
    return `<section class="panel" aria-labelledby="hFacts">
      <div class="row"><h2 id="hFacts">Dados extraídos</h2><span class="spacer"></span><span class="note">salva ao sair do campo</span></div>
      <div class="facts">${rows}</div>
      ${ev.length ? `<hr class="divider"><h2>Evidências de culpa</h2><ul class="check">${ev.map((e) => {
        const d = e.doc_id != null ? docs[String(e.doc_id)] : null;
        return `<li><span class="mk">•</span><span>${esc(e.fato)}${d ? ` <button type="button" class="src" data-src="${esc(d.id)}">${docNo(d.number)}</button>` : ""}</span></li>`;
      }).join("")}</ul>` : ""}
    </section>`;
  }

  function checkCol() {
    const proc = ACTIVE.has(c.status);
    const p = c.prescription;
    const missing = c.missing || [];
    const missingTypes = new Set(missing.map((m) => m.doc_type));
    const req = [...(REQUIRED[c.scenario] || [])];
    missing.forEach((m) => { if (!req.includes(m.doc_type)) req.push(m.doc_type); });
    const labelOf = (t) => missing.find((m) => m.doc_type === t)?.label || DOC_LABEL[t] || t;
    const others = (c.alerts || [])
      .filter((a) => a.kind !== "PRESCRIPTION" && a.kind !== "MISSING_DOC")
      .sort((a, b) => (SEV_ORDER[a.severity] ?? 3) - (SEV_ORDER[b.severity] ?? 3));
    const missCritical = (c.alerts || []).some((a) => a.kind === "MISSING_DOC" && a.severity === "critical");

    let presc = "";
    if (p && p.days_left != null) {
      const d = p.days_left;
      const cls = d < 0 || p.severity === "critical" ? "a-bad" : p.severity === "warn" ? "a-warn" : "a-ok";
      presc = `<div class="alert ${cls}" role="note"><b>Prescrição: ${d < 0 ? "vencida" : `${d} dias restantes`}</b>
        <span>${esc(p.message || "")}</span>${p.deadline ? `<span>Prazo final: <b class="mono">${brDate(p.deadline)}</b></span>` : ""}</div>`;
    } else if (c.facts && !proc) {
      presc = `<div class="alert a-warn"><b>Prescrição não calculada</b><span>Informe a data do pagamento para calcular o prazo.</span></div>`;
    }

    const missBox = missing.length ? `<div class="alert ${missCritical ? "a-bad" : "a-warn"}">
      <b>Falta ${missing.length === 1 ? "1 documento obrigatório" : missing.length + " documentos obrigatórios"}</b>
      <span>${missing.map((m) => esc(m.label)).join(", ")}.</span>
      <span>Se um documento enviado for o que falta, corrija o tipo dele ao lado. Ou peça ao cliente pelo mesmo link.</span>
      <button class="btn small" type="button" id="copyLinkMiss">Copiar link do cliente</button></div>` : "";

    const alertsHtml = others.map((a) => `<div class="alert ${SEV_CLASS[a.severity] || "a-info"}">
      <b>${SEV_TEXT[a.severity] || "Aviso"}${a.field && FACT_LABEL[a.field] ? ` · ${esc(FACT_LABEL[a.field])}` : ""}</b>
      <span>${esc(a.message)}</span>
      ${a.kind === "CONFLICT" && a.field && c.facts ? `<button class="btn small" type="button" data-keep="${esc(a.field)}">Conferi, manter ${esc(keepLabel(a.field))}</button>` : ""}
    </div>`).join("");

    return `<section class="panel" aria-labelledby="hCheck">
      <h2 id="hCheck">Conferência</h2>
      ${presc}${missBox}${alertsHtml}
      ${!presc && !missBox && !alertsHtml && !proc ? '<div class="alert a-ok"><b>Nenhum alerta</b><span>Nada a conferir além da revisão de praxe.</span></div>' : ""}
      <h2>Checklist · ${esc(SCENARIOS[c.scenario] || c.scenario)}</h2>
      <ul class="check">${req.map((t) => {
        const no = missingTypes.has(t);
        return `<li class="${no ? "no" : "yes"}"><span class="mk" aria-hidden="true">${no ? "✕" : "✓"}</span><span>${esc(labelOf(t))}<span class="sr">${no ? ": falta" : ": recebido"}</span></span></li>`;
      }).join("")}</ul>
      <hr class="divider">
      ${actions(proc, missing)}
    </section>`;
  }

  function keepLabel(field) {
    const v = c.facts?.[field]?.value;
    if (v == null || v === "") return "valor atual";
    if (field === "valor_pago" || field === "franquia") return "R$ " + moneyBR(v);
    if (/data|vigencia/.test(field)) return brDate(v);
    return String(v).length > 28 ? "valor atual" : String(v);
  }

  function confirmBox(kind, text, okLabel, danger) {
    return `<div class="confirm ${danger ? "danger" : ""}" role="group" aria-label="Confirmação">
      <span>${text}</span>
      <div class="row"><button class="btn small ${danger ? "danger solid" : "primary"}" type="button" data-ok="${kind}">${okLabel}</button>
        <button class="btn small" type="button" data-cancel>Cancelar</button></div></div>`;
  }

  function actions(proc, missing) {
    const dis = proc ? "disabled" : "";
    const approved = c.status === "PRONTO";
    const approveArea = approved
      ? `<button class="btn" type="button" id="reopen" ${dis}>Reabrir para revisão</button>`
      : confirmKind === "approve"
        ? confirmBox("approve", missing.length
            ? `${missing.length === 1 ? "Falta 1 documento obrigatório" : `Faltam ${missing.length} documentos obrigatórios`}. Aprovar mesmo assim?`
            : "Aprovar o caso? Ele passa a constar como pronto para protocolo.", missing.length ? "Aprovar mesmo assim" : "Aprovar")
        : `<button class="btn" type="button" id="approve" ${dis}>Aprovar caso</button>`;
    const url = location.origin + (c.upload_url || `/enviar/${c.upload_token}`);
    return `<div class="actions">
      <button class="btn primary" type="button" id="genPdf" ${dis}>Gerar dossiê PDF</button>
      <label class="opt"><input type="checkbox" id="optSplit"> dividir em partes de 10 MB</label>
      <button class="btn" type="button" id="genDoc" ${dis}>Gerar minuta (.docx)</button>
      <label class="opt"><input type="checkbox" id="optAI"> redigir os fatos com IA</label>
      ${missing.length && !proc ? '<span class="note">Você pode gerar agora. O que falta fica sinalizado na minuta.</span>' : ""}
      ${proc ? '<span class="note">Aguarde o processamento terminar para gerar os arquivos.</span>' : ""}
      <hr class="divider">
      ${approveArea}
      <button class="btn" type="button" id="toggleLink" aria-expanded="${showLink}">${showLink ? "Ocultar link do cliente" : "Ver link do cliente"}</button>
      ${showLink ? `<div class="stack"><label class="lbl" for="caseLink">Link de envio</label>
        <div class="linkbox"><input id="caseLink" readonly value="${esc(url)}"><button class="btn small" type="button" id="copyLink">Copiar</button></div>
        ${c.token_expires_at ? `<span class="note">Vale até ${new Date(c.token_expires_at).toLocaleDateString("pt-BR")}.</span>` : ""}</div>` : ""}
      ${confirmKind === "token"
        ? confirmBox("token", "O link atual deixa de funcionar. Gerar um novo?", "Gerar novo link")
        : '<button class="btn" type="button" id="newToken">Gerar novo link</button>'}
      ${confirmKind === "delete"
        ? confirmBox("delete", "Excluir o caso e todos os arquivos? Não dá para desfazer.", "Excluir caso", true)
        : '<button class="btn danger" type="button" id="delCase">Excluir caso</button>'}
    </div>`;
  }

  /* ---------- página ---------- */

  function draw() {
    const pages = (c.documents || []).reduce((s, d) => s + (d.page_count ?? d.pages?.length ?? 0), 0);
    const banner = c.status === "PROCESSANDO"
      ? `<div class="panel processing" role="status"><span class="spin" aria-hidden="true"></span>
          <div class="stack"><b>Processando ${plural(c.n_files ?? c.files?.length ?? 0, "arquivo", "arquivos")}</b>
          <span class="note">Classificando páginas e extraindo dados. Esta página se atualiza sozinha.</span></div></div>`
      : c.status === "RECEBENDO"
        ? `<div class="panel processing" role="status"><span class="spin" aria-hidden="true"></span>
          <div class="stack"><b>Aguardando os documentos do cliente</b>
          <span class="note">Mande o link de envio ao cliente. Quando os PDFs chegarem, o processamento começa e esta página se atualiza.</span></div>
          <span class="spacer"></span><button class="btn small" type="button" id="copyLinkTop">Copiar link</button></div>`
        : "";
    root.innerHTML = `
      <div class="casehead">
        <div class="row"><a href="/casos" class="note" data-link>← Casos</a></div>
        <div class="row"><h1>${esc(c.name)}</h1>${statusPill(c.status)}</div>
        <div class="meta"><span>${esc(SCENARIOS[c.scenario] || c.scenario)}</span>
          <span>${plural(c.files?.length ?? c.n_files ?? 0, "arquivo", "arquivos")} · ${plural((c.documents || []).length, "documento", "documentos")} · ${plural(pages, "página", "páginas")}</span>
          ${c.facts?.sinistro_numero?.value ? `<span class="mono">Sinistro ${esc(c.facts.sinistro_numero.value)}</span>` : ""}
          ${c.created_at ? `<span>Criado em ${new Date(c.created_at).toLocaleDateString("pt-BR")}</span>` : ""}</div>
      </div>
      ${c.error ? `<div class="alert a-bad" role="alert"><b>Falha no processamento</b><span>${esc(c.error)}</span></div>` : ""}
      ${banner}
      <div class="cols">${docsCol()}${factsCol()}${checkCol()}</div>`;
    bind();
    loadThumbs();
  }

  // redesenha preservando foco e o que estiver sendo digitado em outros campos
  function redraw(skipFact) {
    const act = document.activeElement;
    const focusId = act && root.contains(act) ? act.id : null;
    const sel = focusId && "selectionStart" in act ? [act.selectionStart, act.selectionEnd] : null;
    const dirty = {};
    $$("[data-fact]", root).forEach((el) => { if (el.dataset.fact !== skipFact && el.value !== el.defaultValue) dirty[el.id] = el.value; });
    const y = window.scrollY;
    draw();
    for (const [k, v] of Object.entries(dirty)) { const el = document.getElementById(k); if (el) el.value = v; }
    if (focusId) {
      const el = document.getElementById(focusId);
      if (el) { el.focus({ preventScroll: true }); try { if (sel) el.setSelectionRange(...sel); } catch { /* date input */ } }
    }
    window.scrollTo(0, y);
  }

  function loadThumbs() {
    $$("img[data-thumb]", root).forEach((img) => {
      authImage(`${base}/documents/${encodeURIComponent(img.dataset.thumb)}/pages/0.png?scale=0.3`)
        .then((u) => { if (img.isConnected) img.src = u; })
        .catch(() => { if (img.isConnected) img.alt = "Prévia indisponível"; });
    });
  }

  /* ---------- ações ---------- */

  async function act(btn, fn, okMsg) {
    busy(btn, true);
    try {
      const r = await fn();
      if (r && r.id) c = r;
      confirmKind = null;
      if (okMsg) toast(okMsg);
      redraw();
      schedulePoll();
    } catch (e) {
      toast(e.message, "bad");
      busy(btn, false);
    }
  }

  async function saveFact(el) {
    const k = el.dataset.fact, kind = el.dataset.kind;
    if (el.value === el.defaultValue || el.dataset.saving) return;
    let v = el.value.trim();
    if (kind === "money" && v) {
      const m = moneyAPI(v);
      if (m == null) { toast(`Valor inválido em "${FACT_LABEL[k]}". Use o formato 48.320,00.`, "bad"); return; }
      v = m;
    }
    const wrap = el.closest(".fact");
    wrap.classList.add("saving");
    el.dataset.saving = "1";
    try {
      c = await api("PATCH", `${base}/facts`, { body: { [k]: v === "" ? { value: null } : v } });
      toast(`${FACT_LABEL[k]} salvo.`);
      redraw(k);
    } catch (e) {
      wrap.classList.remove("saving");
      delete el.dataset.saving;
      toast(e.message, "bad");
    }
  }

  async function gen(btn, kind) {
    busy(btn, true, "Gerando…");
    try {
      if (kind === "pdf") {
        const split = $("#optSplit").checked;
        const res = await download("POST", `${base}/dossie`, split ? { max_mb: 10 } : null, `dossie_${c.id}.pdf`);
        toast((res.headers.get("Content-Type") || "").includes("zip") ? "Dossiê baixado em partes (.zip)." : "Dossiê baixado.");
      } else {
        const ai = $("#optAI").checked;
        const res = await download("POST", `${base}/peticao`, ai ? { narrative: "true" } : null, `peticao_${c.id}.docx`);
        if (ai && (res.headers.get("X-Narrative") || "").toLowerCase() === "fallback") {
          toast("Minuta baixada. A IA não respondeu, então os fatos usam o texto padrão.", "bad");
        } else toast("Minuta baixada.");
      }
    } catch (e) {
      toast(e.message, "bad");
    } finally {
      busy(btn, false);
    }
  }

  function scrollToDoc(docId) {
    const el = document.getElementById("doc-" + docId);
    if (!el) return;
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    el.focus({ preventScroll: true });
  }

  function bind() {
    const linkUrl = () => location.origin + (c.upload_url || `/enviar/${c.upload_token}`);
    $$("select[data-doc]", root).forEach((s) => (s.onchange = async () => {
      s.disabled = true;
      try {
        c = await api("PATCH", `${base}/documents/${encodeURIComponent(s.dataset.doc)}`, { body: { doc_type: s.value } });
        toast(`Tipo alterado para "${DOC_LABEL[s.value]}". Numeração refeita.`);
        redraw();
      } catch (e) { toast(e.message, "bad"); s.disabled = false; }
    }));
    $$("[data-fact]", root).forEach((el) => {
      el.addEventListener("blur", () => saveFact(el));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (el.tagName !== "TEXTAREA" || e.ctrlKey || e.metaKey)) { e.preventDefault(); saveFact(el); }
        if (e.key === "Escape") { el.value = el.defaultValue; }
      });
    });
    $$("[data-src]", root).forEach((b) => (b.onclick = () => scrollToDoc(b.dataset.src)));
    $$("[data-keep]", root).forEach((b) => (b.onclick = () => {
      const k = b.dataset.keep;
      const v = c.facts?.[k]?.value ?? null;
      act(b, () => api("PATCH", `${base}/facts`, { body: { [k]: v == null ? { value: null } : v } }), "Valor conferido e mantido.");
    }));
    const on = (sel, fn) => { const el = $(sel, root); if (el) el.onclick = () => fn(el); };
    on("#genPdf", (b) => gen(b, "pdf"));
    on("#genDoc", (b) => gen(b, "doc"));
    on("#approve", () => { confirmKind = "approve"; redraw(); $("[data-ok=approve]", root)?.focus(); });
    on("#newToken", () => { confirmKind = "token"; redraw(); $("[data-ok=token]", root)?.focus(); });
    on("#delCase", () => { confirmKind = "delete"; redraw(); $("[data-cancel]", root)?.focus(); });
    on("#reopen", (b) => act(b, () => api("POST", `${base}/reopen`), "Caso reaberto para revisão."));
    on("#toggleLink", () => { showLink = !showLink; redraw(); });
    on("#copyLink", () => copyText(linkUrl(), $("#caseLink")));
    on("#copyLinkMiss", () => copyText(linkUrl()));
    on("#copyLinkTop", () => copyText(linkUrl()));
    $$("[data-cancel]", root).forEach((b) => (b.onclick = () => { const k = confirmKind; confirmKind = null; redraw(); $(k === "approve" ? "#approve" : k === "token" ? "#newToken" : "#delCase", root)?.focus(); }));
    $$("[data-ok]", root).forEach((b) => (b.onclick = () => {
      const k = b.dataset.ok;
      if (k === "approve") {
        act(b, () => api("POST", `${base}/approve`, { body: { force: (c.missing || []).length > 0 } }), "Caso aprovado.");
      } else if (k === "token") {
        act(b, async () => {
          const s = await api("POST", `${base}/token`);
          Object.assign(c, { upload_token: s.upload_token, upload_url: s.upload_url, token_expires_at: s.token_expires_at });
          showLink = true;
          return null;
        }, "Novo link gerado. O anterior não funciona mais.");
      } else if (k === "delete") {
        busy(b, true, "Excluindo…");
        api("DELETE", base).then(() => { toast("Caso excluído."); goto("/casos"); })
          .catch((e) => { toast(e.message, "bad"); busy(b, false); });
      }
    }));
  }

  /* ---------- polling ---------- */

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!alive || !ACTIVE.has(c.status)) return;
    pollTimer = setTimeout(async () => {
      if (!alive) return;
      if (!document.hidden) {
        try {
          const n = await api("GET", base);
          if (!alive) return;
          if (JSON.stringify(n) !== JSON.stringify(c)) {
            const was = c.status;
            c = n;
            redraw();
            if (ACTIVE.has(was) && !ACTIVE.has(c.status)) toast("Processamento concluído.");
          }
        } catch (e) {
          if (e.status === 404) { toast("Este caso não existe mais.", "bad"); goto("/casos"); return; }
        }
      }
      schedulePoll();
    }, 3000);
  }

  draw();
  schedulePoll();
  return () => { alive = false; clearTimeout(pollTimer); };
}
