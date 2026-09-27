import { expect, it, vi } from "vitest";
import { readSSE, watchSSE } from "../src/sse";

function streamResponse(text: string, splitBytes = false) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    if (splitBytes) for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    else controller.enqueue(bytes);
    controller.close();
  }}), { headers: { "Content-Type": "text/event-stream" } });
}

it("parses split UTF-8 and CRLF frames while ignoring heartbeat comments", async () => {
  const messages: unknown[] = [];
  await readSSE(streamResponse(': heartbeat\r\n\r\nevent: summary\r\ndata: {"text":"café"}\r\n\r\nevent: reviews_changed\r\ndata: one\r\ndata: two\r\n\r\n', true),
    (event) => messages.push(event), new AbortController().signal);
  expect(messages).toEqual([{event:"summary",data:'{"text":"café"}'}, {event:"reviews_changed",data:"one\ntwo"}]);
});

it("stops delivering buffered events after cancellation", async () => {
  const controller = new AbortController(); const message = vi.fn(() => controller.abort());
  await readSSE(streamResponse('event: summary\ndata: one\n\nevent: summary\ndata: two\n\n'), message, controller.signal);
  expect(message).toHaveBeenCalledTimes(1);
});

it("does not retry authentication failures", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('', {status:401}));
  await expect(watchSSE('/events', {Authorization:'Bearer test'}, vi.fn(), new AbortController().signal, fetcher)).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/events');
});

it("bounds reconnects even when each short connection sends an initial snapshot", async () => {
  vi.useFakeTimers();
  try {
    const fetcher = vi.fn(async () => streamResponse('event: summary\ndata: {}\n\n'));
    const result = watchSSE('/events', {}, vi.fn(), new AbortController().signal, fetcher).catch((error) => error);
    await vi.runAllTimersAsync();
    expect(await result).toBeInstanceOf(Error);
    expect(fetcher).toHaveBeenCalledTimes(5);
  } finally { vi.useRealTimers(); }
});
