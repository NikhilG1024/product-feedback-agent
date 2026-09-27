import { afterEach, expect, it, vi } from 'vitest';
import { waitForBackend } from '../src/startup';
afterEach(() => vi.useRealTimers());
it('warms the backend and retries a transient cold-start failure', async () => {
 vi.useFakeTimers();
 const fetcher = vi.fn().mockResolvedValueOnce(new Response('', {status:503}))
  .mockResolvedValueOnce(new Response(JSON.stringify({status:'ok'})));
 const ready = waitForBackend(new AbortController().signal, fetcher);
 await vi.runAllTimersAsync(); await ready;
 expect(fetcher).toHaveBeenCalledTimes(2);
 expect(fetcher.mock.calls[0][0]).toBe('/health/ready');
});
it('stops retries when startup is cancelled', async () => {
 const controller = new AbortController(); controller.abort();
 const fetcher = vi.fn();
 await expect(waitForBackend(controller.signal, fetcher)).rejects.toThrow();
 expect(fetcher).not.toHaveBeenCalled();
});
it('bounds failed readiness attempts and exposes a retryable error', async () => {
 vi.useFakeTimers();
 const fetcher=vi.fn().mockResolvedValue(new Response('',{status:503}));
 const result=waitForBackend(new AbortController().signal,fetcher).catch(e=>e);
 await vi.runAllTimersAsync();
 expect((await result).message).toMatch(/could not start/);
 expect(fetcher).toHaveBeenCalledTimes(3);
});

it('obtains a temporary demo session without sending shared bearer credentials', async () => {
 const {openDemoSession}=await import('../src/startup');
 const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({token:'temporary-guest',expires_at:'2026-09-29T00:00:00Z'})));
 await expect(openDemoSession(new AbortController().signal,fetcher)).resolves.toBe('temporary-guest');
 expect(fetcher.mock.calls[0][0]).toBe('/api/v1/demo/session');
 expect(fetcher.mock.calls[0][1]).toMatchObject({method:'POST',cache:'no-store'});
 expect(fetcher.mock.calls[0][1].headers).toBeUndefined();
});
