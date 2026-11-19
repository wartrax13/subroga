// Tela /casos: KPIs, tabela ordenada pela API e criação de caso com link de envio.
import { api } from "./api.js";
import { $, $$, esc, toast, busy, copyText, statusPill, SCENARIOS, plural } from "./ui.js";

function sevOf(c) {
  const p = c.prescription;
  if (p && p.days_left != null) {
    if (p.days_left < 90) return "bad";
    if (p.days_left < 365) return "warn";
  }
  if (c.critical_alerts > 0) return "bad";
  return "ok";
}

function prescCell(p) {
  if (!p || p.days_left == null) return '<span class="note">sem data</span>';
  const d = p.days_left;
  if (d < 0) return `<span class="pill p-bad" title="${esc(p.message)}">vencida</span>`;
  const cls = d < 90 ? "p-bad" : d < 365 ? "p-warn" : "p-mut";
  return `<span class="pill ${cls}" title="${esc(p.message)}">${d} dias</span>`;
}

export async function viewCases(root, _params, stale) {
  let cases = await api("GET", "/cases");
  if (stale()) return;
  let created = null; // último caso criado (mostra link)
  let formOpen = false;
  let timer = null;

  function draw() {
    const urgent = cases.filter((c) => c.prescription && c.prescription.days_left != null && c.prescription.days_left < 90).length;
    const pending = cases.filter((c) => c.status === "PENDENTE").length;
    const ready = cases.filter((c) => c.status === "PRONTO").length;
    root.innerHTML = `
      <div class="row"><div class="stack"><h1>Casos de regressiva</h1>
        <p class="sub">Ordenados pelo prazo de prescrição. Os mais urgentes primeiro.</p></div>
        <span class="spacer"></span><button class="btn primary" id="newCase" aria-expanded="${formOpen}" aria-controls="newBox">Novo caso</button></div>
      <div id="newBox" aria-live="polite">${newBox()}</div>
      ${cases.length ? `
      <div class="kpis">
        <div class="kpi"><b>${cases.length}</b><span>casos</span></div>
        <div class="kpi ${urgent ? "bad" : ""}"><b>${urgent}</b><span>prescrevem em menos de 90 dias</span></div>
        <div class="kpi"><b>${pending}</b><span>aguardando documento do cliente</span></div>
        <div class="kpi"><b>${ready}</b><span>prontos para protocolo</span></div>
      </div>
      <div class="table-wrap"><table>
        <caption class="sr">Lista de casos</caption>
        <thead><tr><th class="sev"><span class="sr">Severidade</span></th><th>Caso</th><th>Cenário</th><th>Documentos</th><th>Pendências</th><th>Status</th><th>Prescrição</th></tr></thead>
        <tbody>${cases.map((c) => `
          <tr class="link" data-id="${esc(c.id)}">
            <td class="sev ${sevOf(c)}"></td>
            <td><a class="case-link" href="/casos/${encodeURIComponent(c.id)}" data-link>${esc(c.name)}</a></td>
            <td>${esc(SCENARIOS[c.scenario] || c.scenario)}</td>
            <td class="mono">${c.n_docs ?? 0} <span class="note">de ${plural(c.n_files ?? 0, "arquivo", "arquivos")}</span></td>
            <td>${c.missing_count ? `<span class="pill p-warn">faltam ${c.missing_count}</span>` : '<span class="note">—</span>'}
                ${c.critical_alerts ? `<span class="pill p-bad">${plural(c.critical_alerts, "alerta crítico", "alertas críticos")}</span>` : ""}</td>
            <td>${statusPill(c.status)}</td>
            <td>${prescCell(c.prescription)}</td>
          </tr>`).join("")}</tbody></table></div>` : `
      <div class="panel empty">
        <div class="ico">§</div>
        <h2>Nenhum caso ainda</h2>
        <p>Crie um caso para gerar o link de envio. A seguradora sobe os PDFs por esse link, sem login, e o caso aparece aqui já organizado.</p>
        <button class="btn primary" id="newCaseEmpty">Criar o primeiro caso</button>
      </div>`}`;
    bind();
  }

  function newBox() {
    if (created) {
      const url = location.origin + created.upload_url;
      return `<div class="panel newcase">
        <div class="row"><h2>Caso criado: ${esc(created.name)}</h2><span class="spacer"></span><button class="btn small" id="closeNew">Fechar</button></div>
        <label class="lbl" for="ncLink">Link de envio para o cliente</label>
        <div class="linkbox"><input id="ncLink" readonly value="${esc(url)}"><button class="btn" id="ncCopy">Copiar link</button>
          <a class="btn" href="/casos/${encodeURIComponent(created.id)}" data-link>Abrir caso</a></div>
        <span class="note">Mande este link ao cliente. Ele sobe os PDFs sem precisar de login.${created.token_expires_at ? ` Vale até ${new Date(created.token_expires_at).toLocaleDateString("pt-BR")}.` : ""}</span>
      </div>`;
    }
    if (!formOpen) return "";
    return `<form class="panel newcase form" id="ncForm" novalidate>
      <h2>Novo caso</h2>
      <div class="grid2">
        <div class="field"><label for="ncName">Nome do caso</label><input id="ncName" name="name" placeholder="Ex.: Atlântica × Rodaviva" required></div>
        <div class="field"><label for="ncScen">Cenário</label><select id="ncScen" name="scenario">${Object.entries(SCENARIOS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
      </div>
      <p class="err" role="alert" hidden></p>
      <div class="row"><button class="btn primary" type="submit">Criar e gerar link</button><button class="btn" type="button" id="ncCancel">Cancelar</button></div>
    </form>`;
  }

  function bind() {
    const open = () => { created = null; formOpen = true; draw(); $("#ncName")?.focus(); };
    $("#newCase").onclick = open;
    if ($("#newCaseEmpty")) $("#newCaseEmpty").onclick = open;
    if ($("#ncCancel")) $("#ncCancel").onclick = () => { formOpen = false; draw(); $("#newCase").focus(); };
    if ($("#closeNew")) $("#closeNew").onclick = () => { created = null; draw(); $("#newCase").focus(); };
    if ($("#ncCopy")) $("#ncCopy").onclick = () => copyText($("#ncLink").value, $("#ncLink"));
    const form = $("#ncForm");
    if (form) form.onsubmit = async (e) => {
      e.preventDefault();
      const err = $(".err", form);
      const name = $("#ncName").value.trim();
      if (!name) { err.textContent = "Dê um nome ao caso (ex.: seguradora × causador)."; err.hidden = false; $("#ncName").focus(); return; }
      const btn = $("button[type=submit]", form);
      busy(btn, true, "Criando…");
      try {
        created = await api("POST", "/cases", { body: { name, scenario: $("#ncScen").value } });
        formOpen = false;
        cases = await api("GET", "/cases");
        draw();
        $("#ncCopy")?.focus();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; busy(btn, false); }
    };
    $$("tr.link", root).forEach((tr) => {
      tr.onclick = (e) => { if (!e.target.closest("a")) tr.querySelector("a.case-link").click(); };
    });
  }

  draw();
  // atualiza quando há casos recebendo/processando (não redesenha com formulário aberto)
  timer = setInterval(async () => {
    if (formOpen || document.hidden || !cases.some((c) => c.status === "PROCESSANDO")) return;
    try { cases = await api("GET", "/cases"); if (!formOpen) { const f = document.activeElement?.id; draw(); if (f) document.getElementById(f)?.focus(); } }
    catch (e) { if (e.status !== 401) toast(e.message, "bad"); }
  }, 5000);
  return () => clearInterval(timer);
}
