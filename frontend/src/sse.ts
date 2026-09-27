export interface SSEMessage { event: string; data: string }
const MAX_EVENT_BYTES = 1_000_000;

export async function readSSE(response: Response, onMessage: (message: SSEMessage) => void, signal: AbortSignal): Promise<void> {
  if (!response.ok || !response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body)
    throw new Error("Live updates are unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > MAX_EVENT_BYTES) throw new Error("Live update exceeded the allowed size.");
      buffer = buffer.replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith(":")) continue;
          const split = line.indexOf(":");
          const field = split < 0 ? line : line.slice(0, split);
          const value = split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
          if (field === "event") event = value;
          if (field === "data") data.push(value);
        }
        if (signal.aborted) return;
        if (data.length) onMessage({ event, data: data.join("\n") });
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const timer = setTimeout(finish, ms);
    function finish() { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); }
    signal.addEventListener("abort", finish, { once: true });
  });
}

class TerminalStreamError extends Error {}

export async function watchSSE(url: string, headers: Record<string, string>, onMessage: (message: SSEMessage) => void,
  signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<void> {
  let failures = 0;
  while (!signal.aborted && failures < 5) {
    let received = false;
    const started = Date.now();
    try {
      const response = await fetcher(url, { headers: { Accept: "text/event-stream", ...headers }, signal, cache: "no-store" });
      if (response.status >= 400 && response.status < 500 && response.status !== 429)
        throw new TerminalStreamError("Live updates are not authorized or available.");
      await readSSE(response, (message) => { received = true; onMessage(message); }, signal);
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof TerminalStreamError || failures === 4) throw error;
    }
    if (signal.aborted) return;
    failures = received && Date.now() - started >= 30000 ? 0 : failures + 1;
    await pause(Math.min(1000 * 2 ** Math.min(failures, 4), 16000), signal);
  }
  if (!signal.aborted) throw new Error("Live updates are unavailable.");
}
