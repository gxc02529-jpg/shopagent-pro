from typing import Protocol

from shopagent.domain.models import Product


class ProductRepository(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[Product]: ...

    async def get(self, product_id: str) -> Product | None: ...

    async def stock(self, product_id: str) -> int | None: ...
