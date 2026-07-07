"""Wake word detector abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class WakeWordDetector(ABC):
    """Blocks until a wake word is heard."""

    @abstractmethod
    def wait_for_wake(self, is_muted: Callable[[], bool]) -> bool:
        """Block until the wake word is detected; return True on detection.

        While ``is_muted()`` returns True, all audio must be ignored (no wake).
        Returns False if :meth:`stop` is called before a wake word is heard.
        """

    def stop(self) -> None:
        """Interrupt a blocking :meth:`wait_for_wake` and release resources."""
