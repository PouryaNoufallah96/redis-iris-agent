"""Seed a fictitious customer-support dataset into Redis for the Iris demo.

Company: **Northpeak Outfitters** — a made-up online outdoor-gear retailer.
Entities (stored as RedisJSON docs, one Redis key each):

    customer:{id}   orders belong to a customer
    product:{id}    catalog items
    order:{id}      -> customer_id, product_id
    shipment:{id}   -> order_id
    ticket:{id}     -> customer_id, order_id

Key templates use singular, space-free names so Context Retriever generates clean
tool names (get_customer_by_id, filter_order_by_status, find_order_by_total_range,
search_ticket_by_subject, ...). The field TYPES you set in the Context Retriever
console decide the tools — see NORTHPEAK_ENTITY_CONFIG at the bottom for the exact
TAG / TEXT / NUMERIC plan.

Usage (from the repo dir, with REDIS_URL set in your local environment):
    uv run python seed_northpeak.py            # load (upsert) — additive, recommended
    uv run python seed_northpeak.py --flush    # FLUSHDB first — see warning below
    uv run python seed_northpeak.py --verify   # just count + print samples

WARNING: --flush runs FLUSHDB, which wipes EVERY key in the database — including
the Agent Memory service's keys (they live in the same DB). Prefer the additive
load and just point the Context Retriever service at the 5 new entities; the old
sample keys sit unused and harmless. Only --flush if you truly want a blank DB.
"""
from __future__ import annotations

import os
import random
import sys

from dotenv import load_dotenv

try:
    import redis
except ModuleNotFoundError:  # pragma: no cover
    print("The 'redis' package isn't installed. Run:  uv add redis")
    raise SystemExit(1)

SEED = 42  # deterministic data => a reproducible demo

# --------------------------------------------------------------------------- #
# Curated building blocks (outdoor-gear retailer)
# --------------------------------------------------------------------------- #
FIRST = ["Jordan", "Riley", "Casey", "Morgan", "Avery", "Quinn", "Sasha", "Devin",
         "Harper", "Rowan", "Emerson", "Parker", "Reese", "Skyler", "Marlowe"]
LAST = ["Rivera", "Chen", "Okafor", "Larsen", "Nguyen", "Delgado", "Whitfield",
        "Barnes", "Sato", "Kowalski", "Abbott", "Ferreira", "Holt", "Osei", "Vance"]
CITIES = ["Denver", "Seattle", "Portland", "Austin", "Boulder", "Bozeman",
          "Asheville", "Bend", "Missoula", "Flagstaff"]
TIERS = ["free", "plus", "pro"]

PRODUCTS = [
    ("Summit 2-Person Tent", "tents", 389.00),
    ("Ridgeline 4-Person Tent", "tents", 549.00),
    ("Alpine Down Jacket", "jackets", 279.00),
    ("Stormshell Rain Jacket", "jackets", 189.00),
    ("Trailhead 65L Backpack", "packs", 229.00),
    ("Daybreak 30L Daypack", "packs", 119.00),
    ("Basecamp Sleeping Bag 20F", "sleeping", 199.00),
    ("Featherlite Sleeping Pad", "sleeping", 129.00),
    ("Cascade Hiking Boots", "footwear", 219.00),
    ("Riverford Trail Runners", "footwear", 149.00),
    ("Pocket Rocket Camp Stove", "cooking", 69.00),
    ("Mess Kit Titanium", "cooking", 89.00),
    ("Northpeak Insulated Bottle", "hydration", 39.00),
    ("Glacier 3L Hydration Pack", "hydration", 74.00),
    ("Trekpole Carbon Pair", "accessories", 129.00),
    ("Headlamp Pro 500", "accessories", 59.00),
    ("Merino Base Layer Top", "apparel", 89.00),
    ("Fleece Midlayer", "apparel", 99.00),
]

ORDER_STATUSES = ["processing", "shipped", "delivered", "delayed", "cancelled", "returned"]
SHIP_STATUSES = ["label_created", "in_transit", "out_for_delivery", "delivered", "delayed", "lost"]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL"]
TICKET_STATUS = ["open", "pending", "resolved", "closed"]
TICKET_PRIORITY = ["low", "normal", "high", "urgent"]

TICKET_TEMPLATES = [
    ("Where is my order?", "Hi, my order was supposed to arrive days ago and the tracking hasn't moved. Can you check on it?"),
    ("Sizing help on boots", "The Cascade Hiking Boots run narrow for me. What's the return/exchange process for a wider size?"),
    ("Tent pole snapped", "One of the tent poles snapped on the second night. Is this covered under warranty?"),
    ("Reship expedited, not refund", "Whenever an order is delayed, please reship it expedited rather than refunding me. I'd rather have the gear before my trip."),
    ("Missing item in package", "My backpack arrived but the rain cover that was supposed to be included is missing."),
    ("Refund status", "I returned the rain jacket last week. When will my refund post?"),
    ("Change shipping address", "I moved. Can you update the shipping address on my open order before it ships?"),
    ("Zipper defect on jacket", "The main zipper on the Alpine Down Jacket separates at the bottom. Looking for a replacement."),
    ("Price match request", "I saw the Trailhead 65L cheaper elsewhere within your price-match window. Can you match it?"),
    ("Loyalty tier question", "How many orders until I move up a tier? I order from you a few times a season."),
]


def _money(x: float) -> float:
    return round(x, 2)


def build_dataset() -> dict[str, list[dict]]:
    rng = random.Random(SEED)

    customers: list[dict] = []
    for i in range(15):
        cid = f"C{1000 + i}"
        first = FIRST[i]
        last = LAST[i]
        customers.append({
            "id": cid,
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}@example.com",
            "tier": rng.choice(TIERS),
            "city": rng.choice(CITIES),
            "signup_date": f"202{rng.randint(3,5)}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            "lifetime_orders": 0,  # filled in after orders are built
            "notes": "",
        })

    products: list[dict] = []
    for i, (name, cat, price) in enumerate(PRODUCTS):
        products.append({
            "id": f"P{200 + i}",
            "name": name,
            "category": cat,
            "price": _money(price),
            "in_stock": rng.random() > 0.15,
        })

    orders: list[dict] = []
    shipments: list[dict] = []
    order_counter = 5000
    ship_counter = 9000
    per_customer: dict[str, int] = {c["id"]: 0 for c in customers}

    for _ in range(42):
        cust = rng.choice(customers)
        prod = rng.choice(products)
        oid = f"O{order_counter}"
        order_counter += 1
        status = rng.choices(
            ORDER_STATUSES, weights=[10, 18, 40, 12, 8, 12], k=1
        )[0]
        qty = rng.randint(1, 3)
        order = {
            "id": oid,
            "customer_id": cust["id"],
            "product_id": prod["id"],
            "product_name": prod["name"],
            "status": status,
            "quantity": qty,
            "total": _money(prod["price"] * qty),
            "order_date": f"2026-0{rng.randint(1,6)}-{rng.randint(1,28):02d}",
        }
        orders.append(order)
        per_customer[cust["id"]] += 1

        # Ship anything that left processing/cancelled.
        if status in ("shipped", "delivered", "delayed", "returned"):
            sid = f"S{ship_counter}"
            ship_counter += 1
            if status == "delivered":
                sstatus = "delivered"
            elif status == "delayed":
                sstatus = "delayed"
            elif status == "returned":
                sstatus = "delivered"
            else:
                sstatus = rng.choice(["in_transit", "out_for_delivery", "label_created"])
            shipments.append({
                "id": sid,
                "order_id": oid,
                "carrier": rng.choice(CARRIERS),
                "status": sstatus,
                "tracking_number": f"1Z{rng.randint(10**9, 10**10 - 1)}",
                "estimated_delivery": f"2026-0{rng.randint(1,7)}-{rng.randint(1,28):02d}",
                "days_delayed": rng.randint(2, 9) if sstatus == "delayed" else 0,
            })

    tickets: list[dict] = []
    ticket_counter = 7000
    for _ in range(22):
        cust = rng.choice(customers)
        subject, body = rng.choice(TICKET_TEMPLATES)
        # tie roughly half of tickets to one of the customer's orders
        cust_orders = [o for o in orders if o["customer_id"] == cust["id"]]
        oid = rng.choice(cust_orders)["id"] if cust_orders and rng.random() > 0.4 else ""
        tickets.append({
            "id": f"T{ticket_counter}",
            "customer_id": cust["id"],
            "order_id": oid,
            "subject": subject,
            "body": body,
            "status": rng.choice(TICKET_STATUS),
            "priority": rng.choices(TICKET_PRIORITY, weights=[3, 6, 3, 1], k=1)[0],
            "created_date": f"2026-0{rng.randint(1,6)}-{rng.randint(1,28):02d}",
        })
        ticket_counter += 1

    # ---- Curated HERO scenario (deterministic ids) so the demo is reliable ----
    hero = next(c for c in customers if c["id"] == "C1004")
    hero["name"] = "Jordan Rivera"
    hero["email"] = "jordan.rivera@example.com"
    hero["tier"] = "pro"
    hero["city"] = "Boulder"
    hero["notes"] = "Frequent backcountry buyer. Prefers reshipment over refunds."

    orders.append({
        "id": "O5099", "customer_id": "C1004", "product_id": "P200",
        "product_name": "Summit 2-Person Tent", "status": "delayed",
        "quantity": 1, "total": _money(389.00), "order_date": "2026-06-20",
    })
    per_customer["C1004"] += 1
    shipments.append({
        "id": "S9099", "order_id": "O5099", "carrier": "UPS", "status": "delayed",
        "tracking_number": "1Z9998887776", "estimated_delivery": "2026-06-28",
        "days_delayed": 4,
    })
    tickets.append({
        "id": "T7099", "customer_id": "C1004", "order_id": "",
        "subject": "Reship expedited, not refund",
        "body": ("For any delayed order, please always reship it expedited instead "
                 "of issuing a refund. I'd rather have the gear before my trip."),
        "status": "resolved", "priority": "high", "created_date": "2026-04-11",
    })

    for c in customers:
        c["lifetime_orders"] = per_customer[c["id"]]

    return {
        "customer": customers,
        "product": products,
        "order": orders,
        "shipment": shipments,
        "ticket": tickets,
    }


def get_client() -> "redis.Redis":
    load_dotenv()
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        print("REDIS_URL is not set. Add your Redis Cloud connection string to your "
              "local environment file (Redis Cloud console -> your database -> "
              "Connect -> Redis client), then rerun.")
        raise SystemExit(2)
    client = redis.from_url(url, decode_responses=True)
    client.ping()
    return client


def load(client: "redis.Redis", data: dict[str, list[dict]], flush: bool) -> None:
    if flush:
        client.flushdb()
        print("FLUSHDB done (sample data cleared).")
    total = 0
    for entity, rows in data.items():
        for row in rows:
            client.json().set(f"{entity}:{row['id']}", "$", row)
            total += 1
        print(f"  loaded {len(rows):>3} {entity} keys")
    print(f"Loaded {total} JSON keys into Redis.")


def verify(client: "redis.Redis") -> None:
    print("\n=== verify ===")
    for entity in ("customer", "product", "order", "shipment", "ticket"):
        keys = list(client.scan_iter(match=f"{entity}:*", count=1000))
        print(f"  {entity:<9} {len(keys):>3} keys")
    hero = client.json().get("customer:C1004")
    print("\nHero customer (customer:C1004):")
    print(" ", hero)
    orders = [client.json().get(k) for k in client.scan_iter(match="order:*", count=1000)]
    hero_orders = [o for o in orders if o and o.get("customer_id") == "C1004"]
    print(f"\nHero's orders ({len(hero_orders)}):")
    for o in hero_orders:
        print("  ", o["id"], o["product_name"], "-", o["status"], f"${o['total']}")
    print("\nDelayed order O5099 shipment:")
    print(" ", client.json().get("shipment:S9099"))


def main() -> int:
    args = set(sys.argv[1:])
    client = get_client()
    if "--verify" in args:
        verify(client)
        return 0
    data = build_dataset()
    load(client, data, flush="--flush" in args)
    verify(client)
    return 0


# ---------------------------------------------------------------------------
# Context Retriever entity/field plan (set these in the console after loading, or
# via the admin SDK). Field TYPE decides the tool that gets generated:
#   PK (key)  -> get_<entity>_by_id
#   TAG       -> filter_<entity>_by_<field>   (exact match)
#   TEXT      -> search_<entity>_by_text      (full-text)
#   NUMERIC   -> find_<entity>_by_<field>_range
# A field is exactly ONE index type.
# ---------------------------------------------------------------------------
NORTHPEAK_ENTITY_CONFIG = {
    "customer":  {"key_template": "customer:{id}",
                  "TAG": ["tier", "city"], "TEXT": ["name", "notes"],
                  "NUMERIC": ["lifetime_orders"]},
    "product":   {"key_template": "product:{id}",
                  "TAG": ["category", "in_stock"], "TEXT": ["name"],
                  "NUMERIC": ["price"]},
    "order":     {"key_template": "order:{id}",
                  "TAG": ["customer_id", "product_id", "status"],
                  "TEXT": ["product_name"], "NUMERIC": ["total", "quantity"]},
    "shipment":  {"key_template": "shipment:{id}",
                  "TAG": ["order_id", "carrier", "status"],
                  "TEXT": [], "NUMERIC": ["days_delayed"]},
    "ticket":    {"key_template": "ticket:{id}",
                  "TAG": ["customer_id", "order_id", "status", "priority"],
                  "TEXT": ["subject", "body"], "NUMERIC": []},
}


if __name__ == "__main__":
    raise SystemExit(main())
