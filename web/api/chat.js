// Vercel Serverless Function — proxies chat requests to Groq using a
// server-side API key. The key never leaves the server, so the browser
// bundle stays safe to publish.
//
// Env vars (set in Vercel dashboard → Settings → Environment Variables):
//   GROQ_APIKEY   — required. Format: gsk_...
//
// Request:  POST /api/chat  { messages: [{role, content}, ...] }
// Response: 200  { content: "..." }
//           4xx/5xx { error: "..." }

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed" });
  }

  const key = process.env.GROQ_APIKEY;
  if (!key) {
    return res.status(500).json({
      error: "GROQ_APIKEY is not set on the server. Add it in Vercel → Settings → Environment Variables."
    });
  }

  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const messages = Array.isArray(body && body.messages) ? body.messages : null;
  if (!messages || messages.length === 0) {
    return res.status(400).json({ error: "messages[] is required" });
  }

  try {
    const resp = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key
      },
      body: JSON.stringify({
        model: "llama-3.3-70b-versatile",
        temperature: 0.2,
        messages
      })
    });

    const data = await resp.json();
    if (!resp.ok) {
      return res.status(resp.status).json({
        error: (data && data.error && data.error.message) || "Groq API error",
        status: resp.status
      });
    }

    const content = data && data.choices && data.choices[0]
      && data.choices[0].message && data.choices[0].message.content;
    return res.status(200).json({ content: content || "(empty response)" });
  } catch (err) {
    return res.status(500).json({ error: "Proxy error: " + (err && err.message || String(err)) });
  }
}
