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
 */
export function useSSE({ url, onEvent, onError, enabled = true }: UseSSEOptions) {
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  onEventRef.current = onEvent;
  onErrorRef.current = onError;

  const cleanup = useCallback(() => {
    // Managed by the ref — no explicit close needed here
    // since useEffect cleanup handles it
  }, []);

  useEffect(() => {
    if (!url || !enabled) return;

    const es = new EventSource(url);

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
      es.close();
    };

    return () => {
      es.close();
    };
  }, [url, enabled]);

  return { disconnect: cleanup };
}
