// Tela pública /enviar/:token — o cliente sobe os PDFs do sinistro, sem login.
import { api, uploadFile } from "./api.js";
import { $, esc, SCENARIOS, plural } from "./ui.js";

const MAX = 50 * 1024 * 1024;
// o servidor pode sanitizar nomes ("doc final.pdf" -> "doc_final.pdf"); compara de forma tolerante
const norm = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9.]+/g, "_");
const count = (x) => (Array.isArray(x) ? x.length : Number(x) || 0);

export async function viewUpload(root, [token]) {
  const path = `/public/upload/${encodeURIComponent(token)}`;
  let info;
  try {
    info = await api("GET", path, { auth: false });
  } catch (e) {
    const expired = e.status === 410;
    root.innerHTML = `<div class="client"><div class="panel empty">
      <div class="ico">${expired ? "⌛" : "?"}</div>
      <h1>${expired ? "Este link expirou" : e.status === 404 ? "Link inválido" : "Não foi possível abrir o link"}</h1>
      <p>${expired ? "Peça um novo link ao escritório que enviou este endereço." : e.status === 404 ? "Confira se o endereço foi copiado inteiro. Se o problema continuar, peça um novo link ao escritório." : esc(e.message)}</p>
    </div></div>`;
    return;
  }

  const uploads = []; // {id, name, size, p, state: queued|sending|done|dup|error, msg}
  let seq = 0;
  let queue = Promise.resolve();
  let pollTimer = null;
  let alive = true;
  let pending = 0;

  root.innerHTML = `<div class="client">
    <div class="stack" style="gap:6px">
      <span class="client-brand">${esc(info.org_name)}</span>
      <h1>Envie os documentos do caso ${esc(info.case_name)}</h1>
      <p class="sub">Pode mandar os PDFs do jeito que estão, com qualquer nome. A gente separa e organiza. Se faltar algo, avisamos aqui mesmo.</p>
      ${info.scenario ? `<span class="note">Tipo de sinistro: ${esc(SCENARIOS[info.scenario] || info.scenario)}</span>` : ""}
    </div>
    <label class="drop" id="drop">
      <b>Arraste os PDFs aqui ou toque para escolher</b>
      <span>Vários arquivos de uma vez · só PDF · até 50 MB cada</span>
      <input id="fileIn" class="sr" type="file" accept="application/pdf,.pdf" multiple>
    </label>
    <section aria-labelledby="hSent" id="sentBox" hidden>
      <h2 id="hSent" style="margin-bottom:8px">Arquivos</h2>
      <ul class="files" id="files"></ul>
    </section>
    <div id="result" aria-live="polite"></div>
    <p class="note">Envio seguro. Os arquivos vão direto para o escritório ${esc(info.org_name)}.</p>
  </div>`;

  const drop = $("#drop", root), inp = $("#fileIn", root);
  inp.onchange = () => { addFiles([...inp.files]); inp.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles([...(e.dataTransfer?.files || [])]));
  // evita que soltar fora da área abra o PDF no navegador
  const stop = (e) => { if (!drop.contains(e.target)) e.preventDefault(); };
  window.addEventListener("dragover", stop);
  window.addEventListener("drop", stop);

  function addFiles(list) {
    for (const f of list) {
      const u = { id: ++seq, name: f.name, size: f.size, p: 0, state: "queued", msg: "" };
      uploads.push(u);
      const isPdf = /\.pdf$/i.test(f.name) || f.type === "application/pdf";
      if (!isPdf) { u.state = "error"; u.msg = "Não é PDF. Só aceitamos arquivos PDF."; continue; }
      if (f.size > MAX) { u.state = "error"; u.msg = "Arquivo maior que 50 MB."; continue; }
      pending++;
      queue = queue.then(() => send(u, f));
    }
    renderFiles();
    renderResult();
  }

  async function send(u, f) {
    if (!alive) return;
    u.state = "sending";
    renderFiles();
    try {
      const r = await uploadFile(token, f, (frac) => { u.p = frac; renderBar(u); });
      u.p = 1;
      if (count(r.accepted) > 0) u.state = "done";
      else if (count(r.duplicates) > 0) { u.state = "dup"; u.msg = "Este arquivo já tinha sido enviado."; }
      else if (count(r.rejected) > 0) { u.state = "error"; u.msg = "Não reconhecemos este arquivo como PDF."; }
      else u.state = "done";
      if (r.status) info.status = r.status;
    } catch (e) {
      u.state = "error";
      u.msg = e.status === 413 ? "Arquivo maior que 50 MB." : e.status === 415 ? "Não reconhecemos este arquivo como PDF." : e.status === 410 ? "O link expirou durante o envio. Peça um novo ao escritório." : e.message;
    }
    pending--;
    renderFiles();
    renderResult();
    if (pending === 0) refresh();
  }

  const STATE = {
    queued: ["na fila", "p-mut"], sending: ["enviando", "p-acc"], done: ["recebido", "p-ok"],
    dup: ["já recebido", "p-mut"], error: ["não enviado", "p-bad"],
  };

  function renderBar(u) {
    const i = document.getElementById("bar-" + u.id);
    if (i) { i.style.width = Math.round(u.p * 100) + "%"; i.parentElement.setAttribute("aria-valuenow", String(Math.round(u.p * 100))); }
  }

  function renderFiles() {
    const ul = $("#files", root);
    if (!ul) return;
    // arquivos que já estavam no caso (de envios anteriores) e não foram enviados nesta sessão
    const mine = new Set(uploads.map((u) => norm(u.name)));
    const prev = (info.files || []).filter((f) => !mine.has(norm(f.filename)));
    const procByName = Object.fromEntries((info.files || []).map((f) => [norm(f.filename), f.processed]));
    $("#sentBox", root).hidden = !uploads.length && !prev.length;
    ul.innerHTML = [
      ...uploads.map((u) => {
        let [t, cls] = STATE[u.state];
        if (u.state === "done" && procByName[norm(u.name)] === false) [t, cls] = ["lendo", "p-acc"];
        return `<li><div class="top"><span class="nm">${esc(u.name)}</span><span class="pill ${cls}">${t}</span></div>
          ${u.state === "sending" || u.state === "queued" ? `<div class="bar" role="progressbar" aria-label="Envio de ${esc(u.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(u.p * 100)}"><i id="bar-${u.id}" style="width:${Math.round(u.p * 100)}%"></i></div>` : ""}
          ${u.msg ? `<span class="msg">${esc(u.msg)}</span>` : ""}</li>`;
      }),
      ...prev.map((f) => `<li><div class="top"><span class="nm">${esc(f.filename)}</span>
        <span class="pill ${f.processed ? "p-ok" : "p-acc"}">${f.processed ? "recebido" : "lendo"}</span></div></li>`),
    ].join("");
  }

  function renderResult() {
    const res = $("#result", root);
    if (pending > 0) { res.innerHTML = `<div class="alert a-info"><b>Enviando ${plural(pending, "arquivo", "arquivos")}…</b><span>Não feche esta página até terminar.</span></div>`; return; }
    const n = (info.files || []).length;
    if (!n) { res.innerHTML = ""; return; }
    if (info.status === "PROCESSANDO" || (info.status === "RECEBENDO" && uploads.some((u) => u.state === "done"))) {
      res.innerHTML = `<div class="alert a-info"><div class="processing"><span class="spin" aria-hidden="true"></span>
        <div class="stack"><b>Recebemos ${plural(n, "arquivo", "arquivos")}. Estamos lendo os documentos…</b>
        <span>Leva alguns instantes. Você pode esperar aqui para ver se falta algo.</span></div></div></div>`;
      return;
    }
    const miss = info.missing || [];
    res.innerHTML = miss.length
      ? `<div class="alert a-warn"><b>Recebemos ${plural(n, "arquivo", "arquivos")}. Ainda falta:</b>
          <span>${miss.map((m) => esc(typeof m === "string" ? m : m.label)).join(", ")}.</span>
          <span>Se tiver, envie por aqui mesmo. O escritório já foi avisado do que chegou.</span></div>`
      : `<div class="alert a-ok"><b>Tudo certo. Recebemos todos os documentos necessários.</b><span>O escritório vai revisar e entrar em contato.</span></div>`;
  }

  async function refresh() {
    clearTimeout(pollTimer);
    if (!alive) return;
    try {
      info = await api("GET", path, { auth: false });
    } catch (e) {
      if (e.status === 410 || e.status === 404) {
        $("#result", root).innerHTML = `<div class="alert a-bad"><b>${e.status === 410 ? "Este link expirou." : "Este link não é mais válido."}</b><span>Peça um novo link ao escritório.</span></div>`;
        return;
      }
    }
    renderFiles();
    renderResult();
    if (alive && pending === 0 && (info.status === "PROCESSANDO" || (info.files || []).some((f) => !f.processed))) {
      pollTimer = setTimeout(refresh, 3000);
    }
  }

  renderFiles();
  renderResult();
  if (info.status === "PROCESSANDO") refresh();

  return () => {
    alive = false;
    clearTimeout(pollTimer);
    window.removeEventListener("dragover", stop);
    window.removeEventListener("drop", stop);
  };
}
