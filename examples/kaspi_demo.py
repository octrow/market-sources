"""Kaspi search, then the five cheapest offers. Recorded as docs/demo.gif (asciinema + agg)."""

import asyncio
import time

from market_sources import KaspiSource


async def main():
    kaspi = KaspiSource()
    t = time.perf_counter()
    products = await kaspi.search("iphone 15")
    print(f"search: {len(products)} products in {time.perf_counter() - t:.1f}s")
    p = products[0]
    offers = sorted(await kaspi.offers(p.product_id), key=lambda o: o.price)
    print(f"{p.title}: {len(offers)} offers\n")
    for o in offers[:5]:
        print(
            f"  {o.price:>9,.0f} {o.currency}  {o.seller[:22]:<22} {(f'{o.merchant_rating}★' if o.merchant_rating else '  —'):>4}  {o.delivery}"
        )
    await kaspi.aclose()


asyncio.run(main())
