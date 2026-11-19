// Entrada do SPA: roteamento por history.pushState, barra superior e telas de conta/equipe.
import { api, getToken, setToken, session, setUnauthorizedHandler, clearImageCache } from "./api.js";
import { $, $$, esc, toast, busy } from "./ui.js";
import { viewCases } from "./cases.js";
import { viewCase } from "./case.js";
import { viewUpload } from "./upload.js";

const app = $("#app");
let cleanup = null;
let renderSeq = 0;

const ROUTES = [
  [/^\/login\/?$/, viewLogin, { public: true, bare: true }],
  [/^\/cadastro\/?$/, viewSignup, { public: true, bare: true }],
  [/^\/enviar\/([^/]+)\/?$/, viewUpload, { public: true, bare: true }],
  [/^\/casos\/?$/, viewCases, {}],
  [/^\/casos\/([^/]+)\/?$/, viewCase, {}],
  [/^\/equipe\/?$/, viewTeam, { admin: true }],
];

export function navigate(path, { replace = false } = {}) {
  if (path === location.pathname + location.search) { render(); return; }
  history[replace ? "replaceState" : "pushState"]({}, "", path);
  render();
}

async function loadSession() {
  if (session.user || !getToken()) return !!session.user;
  try {
    const me = await api("GET", "/auth/me");
    session.user = me.user;
    session.org = me.org;
    return true;
  } catch {
    return false;
  }
}

function renderTopbar(path) {
  const bar = $("#topbar");
  const u = session.user, o = session.org;
  const cur = (p) => (path.startsWith(p) ? ' aria-current="page"' : "");
  bar.innerHTML = `<div class="in">
    <a class="brand" href="/casos" data-link>Sub-Roga <small>regressivas</small></a>
    <span class="org">${esc(o?.name)}</span>
    <nav aria-label="Principal">
      <a href="/casos" data-link${cur("/casos")}>Casos</a>
      ${u?.role === "admin" ? `<a href="/equipe" data-link${cur("/equipe")}>Equipe</a>` : ""}
      <span class="who" title="${esc(u?.email)}">${esc(u?.name)}${u?.role === "admin" ? " · admin" : ""}</span>
      <button type="button" id="logout">Sair</button>
    </nav></div>`;
  bar.hidden = false;
  $("#logout").onclick = () => {
    setToken(null);
    session.user = session.org = null;
    clearImageCache();
    navigate("/login");
  };
}

async function render() {
  const seq = ++renderSeq;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  let path = location.pathname;
  if (path === "/" || path === "") {
    path = getToken() ? "/casos" : "/login";
    history.replaceState({}, "", path);
  }
  let match = null, view = null, opts = {};
  for (const [re, fn, o] of ROUTES) {
    const m = re.exec(path);
    if (m) { match = m; view = fn; opts = o; break; }
  }
  if (!view) {
    $("#topbar").hidden = true;
    app.innerHTML = `<div class="empty"><div class="ico">?</div><h1>Página não encontrada</h1>
      <p>O endereço <span class="mono">${esc(path)}</span> não existe.</p><a class="btn primary" href="/casos" data-link>Ir para os casos</a></div>`;
    return;
  }
  if (!opts.public) {
    const ok = await loadSession();
    if (seq !== renderSeq) return;
    if (!ok) { navigate("/login", { replace: true }); return; }
    if (opts.admin && session.user.role !== "admin") { navigate("/casos", { replace: true }); return; }
    renderTopbar(path);
  } else if (opts.bare) {
    $("#topbar").hidden = true;
    if ((view === viewLogin || view === viewSignup) && getToken() && (await loadSession())) {
      if (seq !== renderSeq) return;
      navigate("/casos", { replace: true });
      return;
    }
  }
  app.innerHTML = '<p class="loading">Carregando…</p>';
  try {
    const c = await view(app, match.slice(1).map(decodeURIComponent), () => seq !== renderSeq);
    if (seq !== renderSeq) { if (typeof c === "function") c(); return; }
    cleanup = typeof c === "function" ? c : null;
  } catch (e) {
    if (seq !== renderSeq || e.status === 401) return;
    app.innerHTML = `<div class="empty"><div class="ico">!</div><h1>${e.status === 404 ? "Não encontrado" : "Algo deu errado"}</h1>
      <p>${esc(e.message)}</p><a class="btn" href="/casos" data-link>Voltar aos casos</a></div>`;
  }
  document.title = (app.querySelector("h1")?.textContent?.trim() || "Sub-Roga") + " · Sub-Roga";
}

// links internos sem recarregar a página
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(a.getAttribute("href"));
  app.focus({ preventScroll: true });
  window.scrollTo(0, 0);
});
window.addEventListener("popstate", render);
setUnauthorizedHandler(() => {
  if (!location.pathname.startsWith("/login") && !location.pathname.startsWith("/enviar/")) {
    toast("Sua sessão expirou. Entre novamente.", "bad");
    navigate("/login");
  }
});

/* ---------- login e cadastro ---------- */

function authShell(title, sub, form, foot) {
  return `<div class="auth">
    <div class="stack"><span class="brand">Sub-Roga <small>regressivas</small></span></div>
    <div class="panel">
      <div class="stack"><h1>${title}</h1><p class="sub">${sub}</p></div>
      ${form}
    </div>
    <p class="note" style="text-align:center">${foot}</p>
  </div>`;
}

function bindAuthForm(form, submit) {
  const err = $(".err", form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.hidden = true;
    const btn = $("button[type=submit]", form);
    busy(btn, true, "Aguarde…");
    try {
      const data = Object.fromEntries(new FormData(form));
      const r = await submit(data);
      setToken(r.access_token);
      session.user = r.user;
      session.org = r.org;
      navigate("/casos");
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      busy(btn, false);
    }
  };
}

async function viewLogin(root) {
  root.innerHTML = authShell("Entrar", "Acesse os casos do seu escritório.",
    `<form class="form" novalidate>
      <div class="field"><label for="lEmail">Email</label><input id="lEmail" name="email" type="email" autocomplete="username" required></div>
      <div class="field"><label for="lPass">Senha</label><input id="lPass" name="password" type="password" autocomplete="current-password" required></div>
      <p class="err" role="alert" hidden></p>
      <button class="btn primary" type="submit">Entrar</button>
    </form>`,
    `Escritório novo? <a href="/cadastro" data-link>Crie uma conta</a>`);
  const form = $("form", root);
  bindAuthForm(form, (d) => api("POST", "/auth/login", { body: { email: d.email.trim(), password: d.password }, auth: false }));
  $("#lEmail").focus();
}

async function viewSignup(root) {
  root.innerHTML = authShell("Criar conta do escritório", "Você será o administrador e poderá convidar a equipe depois.",
    `<form class="form" novalidate>
      <div class="field"><label for="sOrg">Nome do escritório</label><input id="sOrg" name="org_name" autocomplete="organization" required></div>
      <div class="field"><label for="sName">Seu nome</label><input id="sName" name="name" autocomplete="name" required></div>
      <div class="field"><label for="sEmail">Email</label><input id="sEmail" name="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="sPass">Senha</label><input id="sPass" name="password" type="password" autocomplete="new-password" minlength="8" required aria-describedby="sPassHint">
        <span class="hint" id="sPassHint">Pelo menos 8 caracteres.</span></div>
      <p class="err" role="alert" hidden></p>
      <button class="btn primary" type="submit">Criar conta</button>
    </form>`,
    `Já tem conta? <a href="/login" data-link>Entrar</a>`);
  const form = $("form", root);
  bindAuthForm(form, (d) => {
    if (!d.org_name.trim() || !d.name.trim() || !d.email.trim()) throw new Error("Preencha todos os campos.");
    if (d.password.length < 8) throw new Error("A senha precisa ter pelo menos 8 caracteres.");
    return api("POST", "/auth/signup", { body: { org_name: d.org_name.trim(), name: d.name.trim(), email: d.email.trim(), password: d.password }, auth: false });
  });
  $("#sOrg").focus();
}

/* ---------- equipe ---------- */

async function viewTeam(root) {
  let users = await api("GET", "/org/users");
  let confirmId = null;

  function draw() {
    root.innerHTML = `
      <div class="stack"><h1>Equipe</h1><p class="sub">Quem tem acesso aos casos de ${esc(session.org?.name)}. Administradores também gerenciam a equipe.</p></div>
      <div class="table-wrap"><table style="min-width:560px">
        <thead><tr><th>Nome</th><th>Email</th><th>Papel</th><th><span class="sr">Ações</span></th></tr></thead>
        <tbody>${users.map((u) => `<tr>
          <td>${esc(u.name)}${u.id === session.user.id ? ' <span class="note">(você)</span>' : ""}</td>
          <td class="mono">${esc(u.email)}</td>
          <td><span class="pill ${u.role === "admin" ? "p-acc" : "p-mut"}">${u.role === "admin" ? "Admin" : "Advogado"}</span></td>
          <td style="text-align:right">${u.id === session.user.id ? "" : confirmId === u.id
            ? `<span class="row" style="justify-content:flex-end"><span class="note">Remover ${esc(u.name)}?</span>
                 <button class="btn small danger solid" data-del="${esc(u.id)}">Remover</button><button class="btn small" data-cancel>Cancelar</button></span>`
            : `<button class="btn small danger" data-ask="${esc(u.id)}">Remover</button>`}</td></tr>`).join("")}
        </tbody></table></div>
      <form class="panel form" id="addUser" novalidate>
        <h2>Adicionar pessoa</h2>
        <div class="grid2">
          <div class="field"><label for="uName">Nome</label><input id="uName" name="name" required></div>
          <div class="field"><label for="uEmail">Email</label><input id="uEmail" name="email" type="email" autocomplete="off" required></div>
          <div class="field"><label for="uPass">Senha inicial</label><input id="uPass" name="password" type="text" autocomplete="new-password" minlength="8" required aria-describedby="uPassHint">
            <span class="hint" id="uPassHint">Mínimo 8 caracteres. Passe para a pessoa por um canal seguro.</span></div>
          <div class="field"><label for="uRole">Papel</label><select id="uRole" name="role"><option value="advogado">Advogado</option><option value="admin">Admin</option></select></div>
        </div>
        <p class="err" role="alert" hidden></p>
        <div class="row"><button class="btn primary" type="submit">Adicionar</button></div>
      </form>`;

    $$("[data-ask]", root).forEach((b) => (b.onclick = () => { confirmId = idOf(b.dataset.ask); draw(); $("[data-del]", root)?.focus(); }));
    $$("[data-cancel]", root).forEach((b) => (b.onclick = () => { confirmId = null; draw(); }));
    $$("[data-del]", root).forEach((b) => (b.onclick = async () => {
      busy(b, true);
      try {
        await api("DELETE", `/org/users/${encodeURIComponent(b.dataset.del)}`);
        users = users.filter((u) => String(u.id) !== b.dataset.del);
        confirmId = null;
        toast("Pessoa removida.");
        draw();
      } catch (e) { toast(e.message, "bad"); busy(b, false); }
    }));
    const form = $("#addUser", root);
    form.onsubmit = async (e) => {
      e.preventDefault();
      const err = $(".err", form);
      err.hidden = true;
      const d = Object.fromEntries(new FormData(form));
      if (!d.name.trim() || !d.email.trim()) { err.textContent = "Preencha nome e email."; err.hidden = false; return; }
      if (d.password.length < 8) { err.textContent = "A senha inicial precisa ter pelo menos 8 caracteres."; err.hidden = false; return; }
      const btn = $("button[type=submit]", form);
      busy(btn, true, "Adicionando…");
      try {
        const u = await api("POST", "/org/users", { body: { name: d.name.trim(), email: d.email.trim(), password: d.password, role: d.role } });
        users = [...users, u];
        toast(`${u.name} foi adicionado(a).`);
        draw();
        $("#uName").focus();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; busy(btn, false); }
    };
  }
  const idOf = (s) => users.find((u) => String(u.id) === s)?.id;
  draw();
}

render();
