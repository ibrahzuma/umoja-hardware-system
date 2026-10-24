"""Capture screenshots of the live Umoja Hardware site for the user guide.

Logs in as the `umoja` superuser, visits each page in the route list, waits
for it to settle, and writes a PNG to docs/screens/<slug>.png.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://umoja.ehub.co.tz"
USERNAME = "umoja"
PASSWORD = "Umoja@2026"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screens"
OUT.mkdir(parents=True, exist_ok=True)

# (slug, path, wait_selector_or_none). wait selector helps avoid capturing
# pages mid-load. None falls back to "networkidle".
PAGES: list[tuple[str, str, str | None]] = [
    ("01_login",            "/login/",                   "form input[name=username]"),
    ("02_dashboard",        "/dashboard/",               ".page-header"),
    ("03_branches",         "/inventory/branches/",      None),
    ("04_categories",       "/inventory/categories/",    None),
    ("05_inventory_list",   "/inventory/",               None),
    ("06_products",         "/inventory/products/",      None),
    ("07_product_create",   "/inventory/products/create/", "form"),
    ("08_suppliers",        "/inventory/suppliers/",     None),
    ("09_purchase_orders",  "/inventory/purchase-orders/", None),
    ("10_grns",             "/inventory/grns/",          None),
    ("11_stock_transfer",   "/inventory/transfer/",      None),
    ("12_trucks",           "/inventory/trucks/",        None),
    ("13_drivers",          "/inventory/drivers/",       None),
    ("14_vehicles",         "/sales/vehicles/",          None),
    ("15_pos",              "/sales/pos/",               None),
    ("16_sales_list",       "/sales/sales/",             None),
    ("17_order_management", "/sales/orders/",            None),
    ("18_dispatch_board",   "/sales/dispatch/",          None),
    ("19_customers",        "/sales/customers/",         None),
    ("20_quotations",       "/sales/quotations/",        None),
    ("21_expenses",         "/finance/expenses/",        None),
    ("22_income",           "/finance/income/",          None),
    ("23_taxes",            "/finance/taxes/",           None),
    ("24_supplier_payments","/finance/supplier-payments/", None),
    ("25_reports",          "/reports/",                 None),
    ("26_users",            "/users/",                   None),
    ("27_roles",            "/roles/",                   None),
    ("28_profile",          "/profile/",                 None),
    ("29_settings",         "/settings/",                None),
    # Delivery cross-check + admin sign-off, and the screens that were redesigned
    ("30_deliveries_verify",  "/inventory/deliveries/verify/",    None),
    ("31_delivery_approvals", "/inventory/deliveries/approvals/", None),
    ("32_debtors",            "/finance/debtors/",                None),
    ("33_crm",                "/crm/",                            None),
    # The cash desk (Cashier) and Admin approval queues
    ("34_petty_cash",            "/finance/petty-cash/",                 None),
    ("35_petty_cash_requests",   "/finance/petty-cash/requests/",        None),
    ("36_petty_cash_approvals",  "/finance/petty-cash/approvals/",       None),
    ("37_supplier_payment_approvals", "/finance/supplier-payments/approvals/", None),
    ("38_by_supplier",           "/finance/supplier-payments/by-supplier/", None),
    ("39_other_payments",        "/finance/other-payments/",             None),
    ("40_banks",                 "/finance/banks/",                      None),
    # Accounting: the sales ledger, the three statements, the books
    ("41_sales_ledger",          "/finance/sales-ledger/",               None),
    ("42_profit_loss",           "/finance/profit-loss/",                None),
    ("43_cash_flow",             "/finance/cash-flow/",                  None),
    ("44_balance_sheet",         "/finance/balance-sheet/",              None),
    ("45_vouchers",              "/finance/vouchers/",                   None),
    ("46_voucher_sales",         "/finance/vouchers/new/sales/",         None),
    ("47_voucher_receipt",       "/finance/vouchers/new/receipt/",       None),
    ("48_general_ledger",        "/finance/general-ledger/",             None),
    ("49_chart_of_accounts",     "/finance/accounts/",                   None),
    # Inventory reports
    ("50_stock_movement",        "/inventory/reports/stock-movements/",  None),
    ("51_transport",             "/inventory/transport/",                None),
    ("52_services",              "/inventory/services/",                 None),
    ("53_stock_adjustment",      "/inventory/stock/adjustment/",         None),
]


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()

        # Login flow
        await page.goto(f"{BASE}/login/", wait_until="domcontentloaded")
        await page.fill("input[name=username]", USERNAME)
        await page.fill("input[name=password]", PASSWORD)
        await page.click("button[type=submit], input[type=submit]")
        await page.wait_for_url(lambda u: "/login/" not in u, timeout=20_000)
        print("logged in:", page.url)

        for slug, path, wait_sel in PAGES:
            out = OUT / f"{slug}.png"
            try:
                await page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=30_000)
                if wait_sel:
                    try:
                        await page.wait_for_selector(wait_sel, timeout=5_000)
                    except Exception:
                        pass
                await page.wait_for_timeout(1500)
                await page.screenshot(path=str(out), full_page=False)
                print(f"  ✓ {slug}  {path}  -> {out.name}")
            except Exception as e:
                print(f"  ✗ {slug}  {path}  {type(e).__name__}: {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
