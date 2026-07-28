// odoo-invoice-es-copy — webhook receiver.
// Odoo automation rule fires this the moment a CD customer invoice is posted.
// It renders the invoice PDF in Spanish via Odoo's real report pipeline
// (web session — the only path that titles the PDF "Factura", not proforma)
// and attaches it to the invoice as "<INV name> ES.pdf".
//
// Safety: partner language is flipped to es_ES only around the render and
// ALWAYS reverted (finally). Idempotent on attachment name. Skips partners
// already in Spanish. Auth: ?token= must match ES_COPY_TOKEN.

const ODOO_URL = Deno.env.get("ODOO_URL")!;
const ODOO_DB = Deno.env.get("ODOO_DB")!;
const ODOO_LOGIN = Deno.env.get("ODOO_LOGIN")!;
const ODOO_API_KEY = Deno.env.get("ODOO_API_KEY")!;
const ODOO_SESSION_PASSWORD = Deno.env.get("ODOO_SESSION_PASSWORD")!;
const ES_COPY_TOKEN = Deno.env.get("ES_COPY_TOKEN")!;

let uidCache: number | null = null;

async function rpc(model: string, method: string, args: unknown[], kwargs: Record<string, unknown> = {}) {
  if (uidCache === null) {
    const auth = await jsonrpc("common", "authenticate", [ODOO_DB, ODOO_LOGIN, ODOO_API_KEY, {}]);
    if (!auth) throw new Error("Odoo RPC auth failed");
    uidCache = auth as number;
  }
  return jsonrpc("object", "execute_kw", [ODOO_DB, uidCache, ODOO_API_KEY, model, method, args, kwargs]);
}

async function jsonrpc(service: string, method: string, args: unknown[]) {
  const r = await fetch(`${ODOO_URL}/jsonrpc`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: { service, method, args }, id: 1 }),
  });
  const j = await r.json();
  if (j.error) throw new Error("Odoo RPC error: " + JSON.stringify(j.error.data?.message ?? j.error).slice(0, 300));
  return j.result;
}

async function fetchSpanishPdf(moveId: number): Promise<Uint8Array> {
  const authResp = await fetch(`${ODOO_URL}/web/session/authenticate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: { db: ODOO_DB, login: ODOO_LOGIN, password: ODOO_SESSION_PASSWORD } }),
  });
  const authJson = await authResp.json();
  if (authJson.error) throw new Error("Odoo session auth failed");
  const cookies = authResp.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
  const pdfResp = await fetch(`${ODOO_URL}/report/pdf/account.report_invoice_with_payments/${moveId}`, {
    headers: { Cookie: cookies },
  });
  if (!pdfResp.ok) throw new Error(`report fetch HTTP ${pdfResp.status}`);
  const bytes = new Uint8Array(await pdfResp.arrayBuffer());
  if (bytes[0] !== 0x25 || bytes[1] !== 0x50) throw new Error("response was not a PDF");
  return bytes;
}

function b64(bytes: Uint8Array): string {
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  if (url.searchParams.get("token") !== ES_COPY_TOKEN) {
    return new Response("forbidden", { status: 403 });
  }
  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { /* empty body */ }
  const moveId = Number(body["_id"] ?? body["id"]);
  if (!moveId) return new Response("no move id in payload", { status: 400 });

  try {
    const [mv] = await rpc("account.move", "read", [[moveId], ["name", "state", "move_type", "partner_id"]]) as Array<Record<string, unknown>>;
    if (!mv) return new Response("move not found", { status: 200 });
    if (mv.state !== "posted" || mv.move_type !== "out_invoice") {
      return new Response(`skip: ${mv.state}/${mv.move_type}`, { status: 200 });
    }
    const partnerId = (mv.partner_id as [number, string])[0];
    const [partner] = await rpc("res.partner", "read", [[partnerId], ["lang"]]) as Array<Record<string, unknown>>;
    if (partner.lang === "es_ES") return new Response("skip: partner already Spanish", { status: 200 });

    const fname = String(mv.name).replaceAll("/", "_") + " ES.pdf";
    const existing = await rpc("ir.attachment", "search_count",
      [[["res_model", "=", "account.move"], ["res_id", "=", moveId], ["name", "=", fname]]]) as number;
    if (existing) return new Response("skip: ES copy already attached", { status: 200 });

    await rpc("res.partner", "write", [[partnerId], { lang: "es_ES" }]);
    let pdf: Uint8Array;
    try {
      pdf = await fetchSpanishPdf(moveId);
    } finally {
      await rpc("res.partner", "write", [[partnerId], { lang: partner.lang }]);
    }
    await rpc("ir.attachment", "create", [{
      name: fname,
      res_model: "account.move",
      res_id: moveId,
      mimetype: "application/pdf",
      datas: b64(pdf),
    }]);
    return new Response(`attached ${fname} (${pdf.length} bytes)`, { status: 200 });
  } catch (e) {
    console.error("es-copy failed for move", moveId, e);
    return new Response("error: " + String(e).slice(0, 300), { status: 500 });
  }
});
