import { useEffect, useRef, useCallback } from "react";

const SSE_BASE = "http://localhost:8000/api/events";

const TERMINAL_STATUSES = ["done", "error", "needs_review"];
const RECONNECT_DELAY = 3000;

// --- Per-product stream hook ---

interface UseProductStreamOptions {
  productId: number;
  onStatus?: (data: { status: string; current_step: string | null }) => void;
  onLog?: (entry: Record<string, unknown>) => void;
  onComplete?: () => void;
  enabled?: boolean;
}

export function useProductStream({
  productId,
  onStatus,
  onLog,
  onComplete,
  enabled = true,
}: UseProductStreamOptions) {
  // Use refs to avoid reconnecting when callbacks change
  const onStatusRef = useRef(onStatus);
  const onLogRef = useRef(onLog);
  const onCompleteRef = useRef(onComplete);

  useEffect(() => { onStatusRef.current = onStatus; }, [onStatus]);
  useEffect(() => { onLogRef.current = onLog; }, [onLog]);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);

  useEffect(() => {
    if (!enabled || !productId) return;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      es = new EventSource(`${SSE_BASE}/products/${productId}`);

      const handleStatusEvent = (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          onStatusRef.current?.({
            status: data.status,
            current_step: data.current_step ?? null,
          });
          if (TERMINAL_STATUSES.includes(data.status)) {
            onCompleteRef.current?.();
          }
        } catch { /* ignore parse errors */ }
      };

      es.addEventListener("snapshot", handleStatusEvent);
      es.addEventListener("status", handleStatusEvent);

      es.addEventListener("log", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          onLogRef.current?.(data.entry);
        } catch { /* ignore */ }
      });

      es.onerror = () => {
        es?.close();
        if (!closed) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY);
        }
      };
    }

    connect();

    return () => {
      closed = true;
      es?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [productId, enabled]);
}


// --- Global products stream hook ---

interface UseProductsStreamOptions {
  onStatusChange?: (data: {
    product_id: number;
    status: string;
    current_step: string | null;
  }) => void;
  enabled?: boolean;
}

export function useProductsStream({
  onStatusChange,
  enabled = true,
}: UseProductsStreamOptions) {
  const onStatusChangeRef = useRef(onStatusChange);
  useEffect(() => { onStatusChangeRef.current = onStatusChange; }, [onStatusChange]);

  useEffect(() => {
    if (!enabled) return;

    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      es = new EventSource(`${SSE_BASE}/products`);

      es.addEventListener("status", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          onStatusChangeRef.current?.({
            product_id: data.product_id,
            status: data.status,
            current_step: data.current_step ?? null,
          });
        } catch { /* ignore */ }
      });

      es.onerror = () => {
        es?.close();
        if (!closed) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY);
        }
      };
    }

    connect();

    return () => {
      closed = true;
      es?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [enabled]);
}
