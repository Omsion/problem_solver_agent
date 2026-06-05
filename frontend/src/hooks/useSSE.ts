import { useEffect, useRef, useCallback } from "react";
import type { SSEEvent } from "../types";

interface UseSSEOptions {
  url: string | null;
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Event) => void;
  enabled?: boolean;
}

/**
 * Custom hook for managing an EventSource SSE connection.
 * Auto-connects/disconnects based on url and enabled flag.
 * Returns a disconnect function for imperative teardown.
 */
export function useSSE({ url, onEvent, onError, enabled = true }: UseSSEOptions) {
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const esRef = useRef<EventSource | null>(null);
  onEventRef.current = onEvent;
  onErrorRef.current = onError;

  const disconnect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!url || !enabled) return;

    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e: MessageEvent) => {
      try {
        const data: SSEEvent = JSON.parse(e.data);
        onEventRef.current(data);
      } catch {
        // Ignore unparseable messages
      }
    };

    es.onerror = (e: Event) => {
      onErrorRef.current?.(e);
      disconnect();
    };

    return () => {
      disconnect();
    };
  }, [url, enabled, disconnect]);

  return { disconnect };
}
