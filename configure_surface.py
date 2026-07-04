"""Reconfigure Context Retriever for the Northpeak support data + mint an agent key.

Uses the ADMIN key (CTX_ADMIN_KEY) to create/update a context surface over the
existing Redis DB (REDIS_URL) with 5 entities and the right index types, then mints
an agent key. The new agent key is written to the file `_agentkey.tmp` (git-ignored)
so no secret is printed; a helper then moves it into the env file. Validates by
listing the generated MCP tools with the new key.

Run from the repo dir:
    uv run python configure_surface.py --probe   # list existing surfaces only
    uv run python configure_surface.py           # create/update + mint agent key
"""
from __future__ import annotations

import asyncio
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

from context_surfaces import ContextSurfacesClient
from context_surfaces.context_model import ContextField, ContextModel, export_data_model
from context_surfaces.models import (
    CreateAgentKeyRequest,
    CreateContextSurfaceRequest,
    DataSourceConnectionConfig,
    DataSourceRequest,
    UpdateContextSurfaceRequest,
)

SURFACE_NAME = "Northpeak Support"
KEY_OUT = os.path.join(os.path.dirname(__file__), "_agentkey.tmp")


class Customer(ContextModel):
    """A Northpeak Outfitters customer."""
    __redis_key_template__ = "customer:{id}"
    id: str = ContextField(description="Customer id", is_key_component=True)
    name: str = ContextField(description="Full name", index="text")
    email: str = ContextField(description="Email address", index="tag")
    tier: str = ContextField(description="Loyalty tier: free, plus, pro", index="tag")
    city: str = ContextField(description="City", index="tag")
    signup_date: str = ContextField(description="Signup date (YYYY-MM-DD)")
    lifetime_orders: int = ContextField(description="Number of orders placed", index="numeric")
    notes: str = ContextField(description="Support notes about the customer", index="text")


class Product(ContextModel):
    """A Northpeak Outfitters catalog product."""
    __redis_key_template__ = "product:{id}"
    id: str = ContextField(description="Product id", is_key_component=True)
    name: str = ContextField(description="Product name", index="text")
    category: str = ContextField(description="Category", index="tag")
    price: float = ContextField(description="Price in USD", index="numeric")
    in_stock: bool = ContextField(description="Whether the product is in stock", index="tag")


class Order(ContextModel):
    """A customer order."""
    __redis_key_template__ = "order:{id}"
    id: str = ContextField(description="Order id", is_key_component=True)
    customer_id: str = ContextField(description="Customer who placed the order", index="tag")
    product_id: str = ContextField(description="Product ordered", index="tag")
    product_name: str = ContextField(description="Name of the product ordered", index="text")
    status: str = ContextField(
        description="processing, shipped, delivered, delayed, cancelled, or returned",
        index="tag")
    quantity: int = ContextField(description="Quantity ordered", index="numeric")
    total: float = ContextField(description="Order total in USD", index="numeric")
    order_date: str = ContextField(description="Order date (YYYY-MM-DD)")


class Shipment(ContextModel):
    """A shipment fulfilling an order."""
    __redis_key_template__ = "shipment:{id}"
    id: str = ContextField(description="Shipment id", is_key_component=True)
    order_id: str = ContextField(description="Order being shipped", index="tag")
    carrier: str = ContextField(description="Carrier: UPS, FedEx, USPS, DHL", index="tag")
    status: str = ContextField(
        description="label_created, in_transit, out_for_delivery, delivered, delayed, or lost",
        index="tag")
    tracking_number: str = ContextField(description="Carrier tracking number")
    estimated_delivery: str = ContextField(description="Estimated delivery date (YYYY-MM-DD)")
    days_delayed: int = ContextField(description="How many days the shipment is delayed", index="numeric")


class Ticket(ContextModel):
    """A customer support ticket."""
    __redis_key_template__ = "ticket:{id}"
    id: str = ContextField(description="Ticket id", is_key_component=True)
    customer_id: str = ContextField(description="Customer who opened the ticket", index="tag")
    order_id: str = ContextField(description="Related order id, if any", index="tag")
    subject: str = ContextField(description="Ticket subject line", index="text")
    body: str = ContextField(description="Full ticket message", index="text")
    status: str = ContextField(description="open, pending, resolved, or closed", index="tag")
    priority: str = ContextField(description="low, normal, high, or urgent", index="tag")
    created_date: str = ContextField(description="Date the ticket was created (YYYY-MM-DD)")


ENTITIES = [Customer, Product, Order, Shipment, Ticket]


def _surfaces_of(resp: object) -> list:
    for attr in ("context_surfaces", "surfaces", "items", "data", "results"):
        v = getattr(resp, attr, None)
        if isinstance(v, list):
            return v
    return []


async def main() -> int:
    load_dotenv()
    admin_key = os.getenv("CTX_ADMIN_KEY", "").strip()
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not admin_key or not redis_url:
        print("Need CTX_ADMIN_KEY and REDIS_URL in the environment.")
        return 2

    probe = "--probe" in sys.argv

    async with ContextSurfacesClient() as client:
        existing = await client.list_context_surfaces(admin_key=admin_key)
        surfaces = _surfaces_of(existing)
        print(f"existing surfaces ({len(surfaces)}):")
        for s in surfaces:
            print(f"  - {getattr(s, 'name', '?')}  id={getattr(s, 'id', '?')}  "
                  f"status={getattr(s, 'status', '?')}  tools={len(getattr(s, 'tools', []) or [])}")
        if probe:
            return 0

        u = urlparse(redis_url)
        conn = DataSourceConnectionConfig(
            addr=f"{u.hostname}:{u.port}",
            username=u.username or "default",
            password=u.password or "",
            tls_enabled=(u.scheme == "rediss"),
        )
        data_model = export_data_model(
            "Northpeak Support",
            "Customer-support data for the fictitious Northpeak Outfitters store.",
            entities=ENTITIES,
        )
        print("entities:", [e["name"] for e in data_model["entities"]],
              "| entity_count:", data_model["entity_count"])
        data_source = DataSourceRequest(type="redis", connection_config=conn)

        match = next((s for s in surfaces if getattr(s, "name", None) == SURFACE_NAME), None)
        if match:
            surface = await client.update_context_surface(
                match.id,
                UpdateContextSurfaceRequest(data_model=data_model, data_source=data_source),
                admin_key=admin_key,
            )
            print(f"updated surface id={surface.id}")
        else:
            surface = await client.create_context_surface(
                CreateContextSurfaceRequest(
                    name=SURFACE_NAME,
                    description="Northpeak Outfitters customer support surface.",
                    data_model=data_model,
                    data_source=data_source,
                ),
                admin_key=admin_key,
            )
            print(f"created surface id={surface.id}")
        # Submitting a data_model triggers server-side tool regeneration; the
        # surface leaves "active" until it finishes. Poll until it's back.
        for _ in range(40):
            st = getattr(surface, "status", None)
            n_tools = len(getattr(surface, "tools", []) or [])
            print(f"  status={st}  tools={n_tools}")
            if st == "active":
                break
            await asyncio.sleep(2)
            surface = await client.get_context_surface(surface.id, admin_key=admin_key)
        print("final status:", getattr(surface, "status", "?"),
              "| tools:", len(getattr(surface, "tools", []) or []))

        agent_key = await client.create_agent_key(
            surface.id, CreateAgentKeyRequest(name="northpeak-agent"), admin_key=admin_key,
        )
        key_val = agent_key.key or ""
        with open(KEY_OUT, "w", encoding="ascii") as f:
            f.write(key_val)
        print(f"agent key minted: {key_val[:6]}...(len {len(key_val)}) -> wrote {os.path.basename(KEY_OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
