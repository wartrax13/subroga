// Cliente da API (contrato em docs/API.md). Mesma origem, base /api.

const KEY = "subroga.token";
let memToken = null; // fallback se localStorage falhar

export function getToken() {
  try { return localStorage.getItem(KEY) || memToken; } catch { return memToken; }
}
export function setToken(t) {
  memToken = t;
  try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch { /* modo privado */ }
}

export const session = { user: null, org: null };

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

function detailText(body, status) {
  const d = body && body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; "); // 422 padrão do FastAPI
  if (status === 0) return "Sem conexão com o servidor. Tente de novo.";
  if (status >= 500) return "Erro no servidor. Tente de novo em instantes.";
  return `Erro ${status}.`;
}

async function raw(method, path, { body, auth = true, query } = {}) {
  const headers = {};
  if (auth && getToken()) headers.Authorization = "Bearer " + getToken();
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let url = "/api" + path;
  if (query) {
    const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== false));
    if ([...q].length) url += "?" + q;
  }
  let res;
  try {
    res = await fetch(url, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, detailText(null, 0));
  }
  if (res.status === 401 && auth) {
    setToken(null);
    session.user = session.org = null;
    onUnauthorized();
    throw new ApiError(401, "Sua sessão expirou. Entre novamente.");
  }
  if (!res.ok) {
    let b = null;
    try { b = await res.json(); } catch { /* sem corpo */ }
    throw new ApiError(res.status, detailText(b, res.status));
  }
  return res;
}

export async function api(method, path, opts) {
  const res = await raw(method, path, opts);
  if (res.status === 204) return null;
  const ct = res.headers.get("Content-Type") || "";
  return ct.includes("json") ? res.json() : res;
}

// Baixa um arquivo com Authorization e dispara o download. Devolve a Response (para ler headers).
export async function download(method, path, query, fallbackName) {
  const res = await raw(method, path, { query });
  const blob = await res.blob();
  let name = fallbackName;
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd);
  if (m) name = decodeURIComponent(m[1] || m[2]);
  else if ((res.headers.get("Content-Type") || "").includes("zip")) name = name.replace(/\.\w+$/, ".zip");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return res;
}

// Carrega imagem protegida como blob URL. Cache por URL para não refazer a cada render.
// Fila serial: uma renderização de página por vez poupa o servidor (renderizar PDF em paralelo é caro e
// já derrubou o backend em teste).
const imgCache = new Map();
let imgQueue = Promise.resolve();
export async function authImage(path) {
  if (imgCache.has(path)) return imgCache.get(path);
  const p = (imgQueue = imgQueue.catch(() => {}).then(() => raw("GET", path)))
    .then((r) => r.blob()).then((b) => URL.createObjectURL(b));
  imgCache.set(path, p);
  p.catch(() => imgCache.delete(path));
  return p;
}
export function clearImageCache(prefix) {
  for (const [k, p] of imgCache) {
    if (!prefix || k.startsWith(prefix)) {
      p.then((u) => URL.revokeObjectURL(u)).catch(() => {});
      imgCache.delete(k);
    }
  }
}

// Upload público com progresso real (XMLHttpRequest). Um arquivo por request.
export function uploadFile(token, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/public/upload/${encodeURIComponent(token)}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* vazio */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body || {});
      else reject(new ApiError(xhr.status, detailText(body, xhr.status)));
    };
    xhr.onerror = () => reject(new ApiError(0, detailText(null, 0)));
    const fd = new FormData();
    fd.append("files", file, file.name);
    xhr.send(fd);
  });
}
