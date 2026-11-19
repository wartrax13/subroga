// Utilidades de interface e constantes de domínio (rótulos espelham backend/app/doc_types.py).

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const DOC_TYPES = [
  ["PROCURACAO", "Procuração"],
  ["DOCS_SEGURADORA", "Documentos da seguradora"],
  ["APOLICE", "Apólice"],
  ["AVISO_SINISTRO", "Aviso de sinistro"],
  ["BOLETIM_OCORRENCIA", "Boletim de ocorrência"],
  ["LAUDO_TECNICO", "Laudo técnico"],
  ["ORCAMENTO_NF_REPARO", "Orçamentos e notas fiscais"],
  ["FOTOS", "Registro fotográfico"],
  ["COMPROVANTE_PAGAMENTO", "Comprovante de pagamento da indenização"],
  ["TERMO_QUITACAO_SUBROGACAO", "Termo de quitação e sub-rogação"],
  ["DOCS_TRANSPORTE", "Documentos de transporte"],
  ["DOCS_VEICULO_CONDUTOR", "Documentos do veículo e do condutor"],
  ["RECLAMACAO_ADMINISTRATIVA", "Reclamação administrativa"],
  ["OUTROS", "Outros documentos"],
];
export const DOC_LABEL = Object.fromEntries(DOC_TYPES);

export const SCENARIOS = { AUTO: "Auto (colisão)", ELETRICO: "Danos elétricos", TRANSPORTE: "Transporte de carga" };

// Checklist por cenário (backend/app/checklists.py). A fonte da verdade do que falta é `missing` da API;
// isto só serve para listar também o que já foi recebido.
const COMMON = ["PROCURACAO", "APOLICE", "AVISO_SINISTRO", "COMPROVANTE_PAGAMENTO", "TERMO_QUITACAO_SUBROGACAO"];
export const REQUIRED = {
  AUTO: [...COMMON, "BOLETIM_OCORRENCIA", "ORCAMENTO_NF_REPARO"],
  ELETRICO: [...COMMON, "LAUDO_TECNICO", "ORCAMENTO_NF_REPARO"],
  TRANSPORTE: [...COMMON, "DOCS_TRANSPORTE"],
};

export const STATUS = {
  RECEBENDO: ["Recebendo", "p-mut"],
  PROCESSANDO: ["Processando", "p-acc"],
  PENDENTE: ["Pendente", "p-warn"],
  REVISAR: ["Revisar", "p-acc"],
  PRONTO: ["Pronto", "p-ok"],
};
export const statusPill = (s) => {
  const [t, c] = STATUS[s] || [s || "—", "p-mut"];
  return `<span class="pill ${c}">${esc(t)}</span>`;
};

// Campos de CaseFacts (backend/app/schemas.py) na ordem de exibição.
export const FACTS = [
  ["seguradora_nome", "Seguradora (autora)"],
  ["seguradora_cnpj", "CNPJ da seguradora"],
  ["segurado_nome", "Segurado"],
  ["segurado_cpf_cnpj", "CPF/CNPJ do segurado"],
  ["apolice_numero", "Nº da apólice"],
  ["apolice_ramo", "Ramo"],
  ["apolice_vigencia_inicio", "Vigência: início", "date"],
  ["apolice_vigencia_fim", "Vigência: fim", "date"],
  ["sinistro_numero", "Nº do sinistro"],
  ["sinistro_data", "Data do sinistro", "date"],
  ["sinistro_local", "Local do sinistro"],
  ["sinistro_descricao", "Descrição do sinistro", "long"],
  ["causador_nome", "Causador (réu)"],
  ["causador_cpf_cnpj", "CPF/CNPJ do causador"],
  ["causador_endereco", "Endereço do causador"],
  ["causador_placa", "Placa do veículo causador"],
  ["valor_pago", "Valor pago (R$)", "money"],
  ["data_pagamento", "Data do pagamento", "date"],
  ["franquia", "Franquia (R$)", "money"],
];
export const FACT_LABEL = Object.fromEntries(FACTS.map(([k, l]) => [k, l]));

export const docNo = (n) => "Doc. " + String(n).padStart(2, "0");

export function brDate(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

// "48320.00" -> "48.320,00"; aceita também texto já formatado.
export function moneyBR(v) {
  if (v == null || v === "") return "";
  if (!/^-?\d+(\.\d+)?$/.test(String(v))) return String(v);
  const n = Number(v);
  return n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
// "48.320,00" / "R$ 48320,5" / "48320.00" -> "48320.00"; devolve null se não reconhecer.
export function moneyAPI(s) {
  let t = String(s || "").replace(/[R$\s]/g, "");
  if (!t) return "";
  if (/,\d{1,2}$/.test(t)) t = t.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  else t = t.replace(/,/g, "");
  if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
  return Number(t).toFixed(2);
}

let toastTimer;
export function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), kind === "bad" ? 5000 : 3000);
}

export function busy(btn, on, label) {
  if (!btn) return;
  if (on) {
    btn.dataset.label = btn.textContent;
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
    if (label) btn.textContent = label;
  } else {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
    if (btn.dataset.label) btn.textContent = btn.dataset.label;
  }
}

export async function copyText(text, input) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Link copiado.");
  } catch {
    if (input) { input.focus(); input.select(); }
    toast("Selecionei o link. Use Ctrl+C para copiar.");
  }
}

// nome de exibição: sem diretório e sem o prefixo de hash que o backend põe no arquivo salvo
export const basename = (p) => String(p || "").split(/[\\/]/).pop().replace(/^[0-9a-f]{12}_/, "");
export const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;
