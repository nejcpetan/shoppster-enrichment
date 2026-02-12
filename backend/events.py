import asyncio
import json
import threading
import time
from collections import defaultdict


class EventBus:
    """Thread-safe event bus using asyncio.Queue for SSE broadcasting.

    Pipeline threads publish events via loop.call_soon_threadsafe(),
    which safely delivers them to the asyncio.Queues consumed by SSE endpoints
    on the main event loop.
    """

    def __init__(self):
        self._channels: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Register the main event loop (call once on startup)."""
        self._loop = loop

    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._channels[channel].append(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        with self._lock:
            try:
                self._channels[channel].remove(queue)
            except ValueError:
                pass
            if not self._channels[channel]:
                del self._channels[channel]

    def _safe_put(self, queue: asyncio.Queue, event: dict):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop event for slow client

    def publish(self, channel: str, event: dict):
        """Thread-safe publish to all subscribers on a channel."""
        with self._lock:
            queues = list(self._channels.get(channel, []))
        for queue in queues:
            if self._loop is not None:
                try:
                    self._loop.call_soon_threadsafe(self._safe_put, queue, event)
                except RuntimeError:
                    pass  # Loop is closed
            else:
                self._safe_put(queue, event)

    def publish_product_event(self, product_id: int, event: dict):
        """Publish to both the global 'products' channel and the per-product channel."""
        event = {**event, "product_id": product_id, "ts": time.time()}
        self.publish("products", event)
        self.publish(f"product:{product_id}", event)


def format_sse(event_type: str, data: dict) -> str:
    """Format a dict as an SSE message string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# Singleton
event_bus = EventBus()
