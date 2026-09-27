export const VERCEL_API_ORIGIN = 'https://product-feedback-agent-api.vercel.app';
// Local development uses a server-side proxy so bearer tokens never reach the browser.
export const API_ORIGIN = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_ORIGIN || VERCEL_API_ORIGIN).replace(/\/$/, '');

function pause(ms: number, signal: AbortSignal) {
 return new Promise<void>((resolve, reject) => {
  signal.throwIfAborted();
  const abort = () => { clearTimeout(timer); reject(signal.reason); };
  const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
  signal.addEventListener('abort', abort, {once:true});
 });
}

/** Only the idempotent readiness probe is retried; submissions are never replayed. */
export async function waitForBackend(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<void> {
 for (let attempt=0; attempt<3; attempt++) {
  signal.throwIfAborted();
  try {
   const response = await fetcher(`${API_ORIGIN}/health/ready`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(12000)]), cache:'no-store',
   });
   if (response.ok && (await response.json()).status === 'ok') return;
  } catch { signal.throwIfAborted(); }
  if (attempt < 2) await pause(1000 * (attempt+1), signal);
 }
 throw new Error('The workspace could not start. The server may still be waking up. Please retry.');
}


export async function openDemoSession(signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<string> {
 const response = await fetcher(`${API_ORIGIN}/api/v1/demo/session`, {
  method:'POST', signal:AbortSignal.any([signal, AbortSignal.timeout(15000)]), cache:'no-store',
 });
 if (!response.ok) throw new Error('Demo access is not available yet. Please retry shortly.');
 const data = await response.json();
 if (typeof data.token !== 'string' || !data.token) throw new Error('The demo returned an invalid session.');
 return data.token;
}
