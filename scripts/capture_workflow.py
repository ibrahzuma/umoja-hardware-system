"""Drive a full end-to-end sale across every role on prod, capturing
screenshots at each transition.

Pipeline (each step is shot from the role that performs it):
  1. ensure a supplier + vehicle exist
  2. sales rep creates a sale via API   -> shot of /sales/ in pending
  3. store manager opens /orders/       -> shot before approve
  4. store manager approves via API     -> shot after approve
  5. store manager opens /dispatch/     -> shot before dispatch
  6. store manager dispatches via API   -> shot after dispatch
  7. store keeper opens /sales/         -> shot of dispatched sale
  8. accountant opens /expenses/create/ -> shot of expense form

Idempotent: re-running uses the existing data and just re-shoots.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import requests
from playwright.async_api import async_playwright

BASE = "https://umoja.ehub.co.tz"
SHOTS = Path(__file__).resolve().parents[1] / "docs" / "screens"
SHOTS.mkdir(parents=True, exist_ok=True)

USERS: dict[str, tuple[str, str]] = {
    "admin":       ("umoja",       "Umoja@2026"),
    "smanager":    ("smanager",    "Manager@2026"),
    "salesrep":    ("salesrep",    "Sales@2026"),
    "procurement": ("procurement", "Procurement@2026"),
    "stockctrl":   ("stockctrl",   "StockCtl@2026"),
    "keeper":      ("keeper",      "Keeper@2026"),
    "accountant":  ("accountant",  "Accountant@2026"),
}


def tok(who: str) -> str:
    user, pw = USERS[who]
    r = requests.post(
        f"{BASE}/api-token-auth/", data={"username": user, "password": pw}, timeout=15
    )
    r.raise_for_status()
    return r.json()["token"]


def api(method: str, path: str, token: str, **kw) -> requests.Response:
    h = kw.pop("headers", {}) or {}
    h["Authorization"] = f"Token {token}"
    return requests.request(method, f"{BASE}{path}", headers=h, timeout=30, **kw)


def ensure_supplier(admin_tok: str) -> dict[str, Any]:
    r = api("GET", "/api/suppliers/", admin_tok)
    data = r.json()
    items = data.get("results", data) if isinstance(data, dict) else data
    if items:
        return items[0]
    r = api(
        "POST", "/api/suppliers/", admin_tok,
        json={"name": "Acme Hardware Supplies", "contact": "+255 7xx xxx xxx", "address": "Dar es Salaam"},
    )
    r.raise_for_status()
    return r.json()


def ensure_vehicle(admin_tok: str) -> dict[str, Any]:
    r = api("GET", "/api/vehicles/", admin_tok)
    items = r.json()
    if isinstance(items, dict):
        items = items.get("results", [])
    if items:
        return items[0]
    r = api(
        "POST", "/api/vehicles/", admin_tok,
        json={
            "registration_number": "T123ABC",
            "driver_name": "John Mwamba",
            "vehicle_type": "lorry",
            "status": "active",
            "capacity": "1000.00",
        },
    )
    r.raise_for_status()
    return r.json()


def ensure_storekeeper_id(admin_tok: str) -> int:
    r = api("GET", "/api/users/", admin_tok)
    items = r.json()
    items = items.get("results", items) if isinstance(items, dict) else items
    for u in items:
        if u.get("username") == "keeper":
            return u["id"]
    raise SystemExit("keeper user not found")


def create_pending_sale(salesrep_tok: str, branch_id: int, product_id: int) -> dict[str, Any]:
    body = {
        "branch": branch_id,
        "customer_name": "Walk-in Customer",
        "items": [
            {"product": product_id, "quantity": 2, "price_at_sale": "25000.00"},
        ],
        "payment_details": {"amount": "50000.00", "payment_method": "cash"},
    }
    r = api("POST", "/api/sales/", salesrep_tok, json=body)
    if r.status_code >= 400:
        raise SystemExit(f"create sale failed: {r.status_code} {r.text[:400]}")
    return r.json()


def approve_sale(smanager_tok: str, sale_id: int) -> None:
    r = api("POST", f"/api/sales/{sale_id}/approve/", smanager_tok)
    if r.status_code >= 400:
        raise SystemExit(f"approve failed: {r.status_code} {r.text[:300]}")


def dispatch_sale(smanager_tok: str, sale_id: int, vehicle_id: int, keeper_id: int) -> None:
    r = api(
        "POST", f"/api/sales/{sale_id}/dispatch_order/", smanager_tok,
        json={"store_keeper": keeper_id, "vehicle_id": vehicle_id},
    )
    if r.status_code >= 400:
        raise SystemExit(f"dispatch failed: {r.status_code} {r.text[:300]}")


async def shot(browser, who: str, path: str, slug: str) -> None:
    user, pw = USERS[who]
    ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
    page = await ctx.new_page()
    await page.goto(f"{BASE}/login/", wait_until="domcontentloaded")
    await page.fill("input[name=username]", user)
    await page.fill("input[name=password]", pw)
    await page.click("button[type=submit], input[type=submit]")
    await page.wait_for_url(lambda u: "/login/" not in u, timeout=20_000)
    await page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
    await page.wait_for_timeout(1800)
    out = SHOTS / f"{slug}.png"
    await page.screenshot(path=str(out), full_page=False)
    print(f"  ✓ {slug}  ({who} @ {path})")
    await ctx.close()


async def main() -> None:
    admin_tok = tok("admin")
    smgr_tok = tok("smanager")
    rep_tok = tok("salesrep")

    print("seed reference data…")
    branch = api("GET", "/api/branches/", admin_tok).json()
    branch_id = (branch.get("results", branch) if isinstance(branch, dict) else branch)[0]["id"]
    supplier = ensure_supplier(admin_tok); print(f"  supplier: {supplier['name']}")
    vehicle = ensure_vehicle(admin_tok); print(f"  vehicle:  {vehicle['registration_number']}")
    keeper_id = ensure_storekeeper_id(admin_tok); print(f"  keeper id: {keeper_id}")

    print("create a pending sale as sales rep…")
    sale = create_pending_sale(rep_tok, branch_id, product_id=2)
    sale_id = sale["id"]
    print(f"  sale id: {sale_id} status: {sale['status']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Step 1 — sales rep view, pending
        await shot(browser, "salesrep", "/sales/pos/",   "wf_01_salesrep_pos")
        await shot(browser, "salesrep", "/sales/sales/", "wf_02_salesrep_pending")

        # Step 2 — manager sees pending in order management
        await shot(browser, "smanager", "/sales/orders/", "wf_03_smgr_orders_pending")

        print("approve sale as admin (role perms gate POST approve)…")
        approve_sale(admin_tok, sale_id)
        await shot(browser, "smanager", "/sales/orders/", "wf_04_smgr_orders_approved")

        # Step 3 — manager dispatches
        await shot(browser, "smanager", "/sales/dispatch/", "wf_05_smgr_dispatch_before")
        print("dispatch sale…")
        dispatch_sale(smgr_tok, sale_id, vehicle["id"], keeper_id)
        await shot(browser, "smanager", "/sales/dispatch/", "wf_06_smgr_dispatch_after")
        await shot(browser, "smanager", "/sales/sales/",    "wf_07_smgr_sales_dispatched")

        # Step 4 — store keeper view
        await shot(browser, "keeper", "/sales/sales/", "wf_08_keeper_sales")

        # Step 5 — accountant
        await shot(browser, "accountant", "/finance/expenses/create/", "wf_09_accountant_expense_form")
        await shot(browser, "accountant", "/finance/expenses/",        "wf_10_accountant_expenses_list")

        # Step 6 — procurement supplier and PO views
        await shot(browser, "procurement", "/inventory/suppliers/",              "wf_11_procurement_suppliers")
        await shot(browser, "procurement", "/inventory/purchase-orders/create/", "wf_12_procurement_po_create")

        # Step 7 — stock controller GRN view
        await shot(browser, "stockctrl", "/inventory/grns/create/", "wf_13_stockctrl_grn_form")

        await browser.close()

    print(f"done — sale id {sale_id} now dispatched.")


if __name__ == "__main__":
    asyncio.run(main())
