// Where an MCP server's login sends the owner's browser back to (see ahab/connections/mcp_auth.py).
//
// The login page redirects here with ?code=...&state=... . This files the pair in the
// ahab_oauth table, and Ahab, polling from its own machine, swaps the code for a token.
// Passing the code through a public page is safe: it's useless without the PKCE secret,
// which never leaves Ahab's machine. The state is a random string Ahab made for this one
// login, so a stray or forged visit files nothing Ahab will use.
//
// Deploy with JWT verification off (the browser arrives with no Supabase login). Supabase
// serves functions on its own domain as plain text, so the answers here are plain text.

const STATE = /^[A-Za-z0-9_-]{20,128}$/;

function answer(status: number, text: string): Response {
  return new Response(text + "\n", {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const state = url.searchParams.get("state") ?? "";
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error_description") ?? url.searchParams.get("error");
  if (req.method !== "GET" || !STATE.test(state) || (!code && !error)) {
    return answer(400, "This isn't a login Ahab started, so nothing happened.");
  }
  const base = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const filed = await fetch(`${base}/rest/v1/ahab_oauth`, {
    method: "POST",
    headers: {
      apikey: key ?? "",
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal",
    },
    body: JSON.stringify({
      state,
      code: code ? code.slice(0, 4000) : null,
      error: error ? error.slice(0, 500) : null,
    }),
  });
  if (!filed.ok) {
    return answer(500, "Couldn't pass the login on to Ahab. Try the link again in a minute.");
  }
  return answer(200, error
    ? "The login was cancelled or refused, so nothing was connected. You can close this tab."
    : "Done. Ahab finishes connecting in a few seconds. You can close this tab.");
});
