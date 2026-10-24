"""Log in as each role, capture each role's dashboard + their primary action page.

Produces docs/screens/role_<code>_<slug>.png for the PPT to embed per-role views.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://umoja.ehub.co.tz"
SHOTS = Path(__file__).resolve().parents[1] / "docs" / "screens"
SHOTS.mkdir(parents=True, exist_ok=True)


# (role code, username, password, pages-to-capture [(slug, path)])
ROLES = [
    ("admin",        "umoja",       "Umoja@2026",         [
        ("dashboard",          "/dashboard/"),
        ("users_list",         "/users/"),
        ("delivery_approvals", "/inventory/deliveries/approvals/"),
        ("settings",           "/settings/"),
    ]),
    ("store_manager","smanager",    "Manager@2026",       [
        ("dashboard",          "/dashboard/"),
        ("deliveries_verify",  "/inventory/deliveries/verify/"),
        ("order_management",   "/sales/orders/"),
        ("dispatch_board",     "/sales/dispatch/"),
        ("vehicles",           "/sales/vehicles/"),
        ("stock_transfer",     "/inventory/transfer/"),
    ]),
    ("sales_rep",    "salesrep",    "Sales@2026",         [
        ("dashboard",          "/dashboard/"),
        ("pos",                "/sales/pos/"),
        ("sales_list",         "/sales/sales/"),
        ("customers",          "/sales/customers/"),
        ("quotations",         "/sales/quotations/"),
    ]),
    ("procurement",  "procurement", "Procurement@2026",   [
        ("dashboard",          "/dashboard/"),
        ("suppliers",          "/inventory/suppliers/"),
        ("purchase_orders",    "/inventory/purchase-orders/"),
        ("po_create",          "/inventory/purchase-orders/create/"),
        ("trucks",             "/inventory/trucks/"),
    ]),
    ("stock_controller","stockctrl","StockCtl@2026",      [
        ("dashboard",          "/dashboard/"),
        ("grns",               "/inventory/grns/"),
        ("grn_create",         "/inventory/grns/create/"),
        ("inventory",          "/inventory/"),
    ]),
    ("store_keeper", "keeper",      "Keeper@2026",        [
        ("dashboard",          "/dashboard/"),
        ("dispatch",           "/sales/dispatch/"),
        ("sales_list",         "/sales/sales/"),
    ]),
    ("accountant",   "accountant",  "Accountant@2026",    [
        ("dashboard",          "/dashboard/"),
        ("expenses",           "/finance/expenses/"),
        ("expense_create",     "/finance/expenses/create/"),
        ("income",             "/finance/income/"),
        ("taxes",              "/finance/taxes/"),
        ("debtors",            "/finance/debtors/"),
        ("reports",            "/reports/"),
    ]),
]


async def login(page, username, password):
    await page.goto(f"{BASE}/login/", wait_until="domcontentloaded")
    await page.fill("input[name=username]", username)
    await page.fill("input[name=password]", password)
    await page.click("button[type=submit], input[type=submit]")
    # Wait for URL to leave /login/ — dashboards have a long-poll
    # so networkidle won't fire.
    await page.wait_for_url(lambda u: "/login/" not in u, timeout=20_000)


async def logout(page):
    # Easiest reliable logout: clear cookies for this context
    await page.context.clear_cookies()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for code, user, pw, pages in ROLES:
            ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await ctx.new_page()
            print(f"\n[{code}]")
            try:
                await login(page, user, pw)
                print(f"  logged in as {user}: {page.url}")
            except Exception as e:
                print(f"  LOGIN FAILED: {e}")
                await ctx.close()
                continue
            for slug, path in pages:
                out = SHOTS / f"role_{code}_{slug}.png"
                try:
                    await page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=20_000)
                    # Give JS a moment to render but don't wait for networkidle
                    await page.wait_for_timeout(1500)
                    await page.screenshot(path=str(out), full_page=False)
                    print(f"  ✓ {slug}  -> {out.name}")
                except Exception as e:
                    print(f"  ✗ {slug}  {type(e).__name__}: {e}")
            await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
