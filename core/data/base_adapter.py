from __future__ import annotations

from abc import ABC, abstractmethod
from core.models import StoreProduct


class DataAdapter(ABC):
    brand: str = ""

    @abstractmethod
    def search(self, keyword: str, user_lat: float, user_lng: float) -> list[StoreProduct]:
        raise NotImplementedError
