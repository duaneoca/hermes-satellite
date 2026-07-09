"""Wake word detector abstraction."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional


class WakeWordDetector(ABC):
    """Blocks until a wake word is heard."""

    @abstractmethod
    def wait_for_wake(
        self,
        is_muted: Callable[[], bool],
        cancel: Optional[threading.Event] = None,
    ) -> bool:
        """Block until the wake word is detected; return True on detection.

        While ``is_muted()`` returns True, all audio must be ignored (no wake).
        Returns False if :meth:`stop` is called, or ``cancel`` is set, before
        a wake word is heard. ``cancel`` is a *per-call* interruption (used by
        barge-in to stop listening when playback ends); unlike :meth:`stop`
        it must leave the detector usable for the next call.
        """

    def stop(self) -> None:
        """Interrupt a blocking :meth:`wait_for_wake` and release resources."""
