# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Umoja Hardware System** — a Django 6 (Channels/ASGI) ERP for a Tanzanian hardware retailer. Covers multi-branch
inventory, procurement (purchase orders → delivery cross-check → GRN), POS/sales/quotations with an approval →
dispatch flow, vehicle & truck fleets, finance (expenses/income/taxes/supplier payments/banks), HR (employees,
leave, attendance, payroll), a standalone CRM customer register, and role-based user management.

Three front-ends share one backend:
- **Server-rendered Django templates** — the primary UI (every role works here).
- **REST API** (`/api/`) — consumed by the templates' JavaScript *and* by the mobile app.
- **Flutter app** in `mobile/` — Android field-sales companion (login, POS, stock, customers, quotations) plus a
  desktop build that just hosts the production site in a WebView (`mobile/lib/screens/desktop_webview.dart`).

## Common Commands

All commands assume the project venv is active and `.env` is configured (copy from `.env.example`; Postgres is
required — `sms_project/settings.py` has no SQLite fallback).

```powershell
# Run the dev server (ASGI — required because Channels/WebSockets are wired in)
python manage.py runserver
# Production-style local run:
daphne -b 127.0.0.1 -p 8000 sms_project.asgi:application

# Migrations
python manage.py makemigrations
python manage.py migrate

# Seed role groups + permissions (must run after first migrate AND after any role/permission edit)
python manage.py create_roles

# Tests — pytest is configured via pytest.ini (uses --reuse-db, needs Postgres)
pytest                                       # full suite
pytest apps/crm/tests.py                     # one file
pytest apps/crm/tests.py::CrmAccessTest::test_other_roles_are_blocked   # one test
python manage.py test apps.crm               # Django test runner alternative

# Static files (needed before collectstatic in prod; WhiteNoise serves them)
python manage.py collectstatic --noinput

# API docs (DRF Spectacular)
# /api/docs/  (Swagger UI)   |   /api/schema/  (raw OpenAPI)
```

**Deploy.** `./deploy.sh` runs *on* the Linode host (`/var/www/app`): pulls main, installs deps, migrates, collects
static, restarts the `django_app` systemd unit + nginx. From a dev machine, drive it over SSH with
`python scripts/ssh_deploy.py exec "<command>"` / `put <local> <remote>` (reads `SSH_HOST`/`SSH_USER`/`SSH_PASS`
from env; needs `paramiko`). `deploy.sh` does **not** run `create_roles` — run it yourself after changing roles.
See `DEPLOYMENT.md` for one-time server setup.

## Architecture

### Project layout
- `sms_project/` — Django project (settings, root URLs, ASGI/WSGI, WebSocket routing).
- `apps/` — domain apps, each a standard Django app (`models.py`, `views.py`, `serializers.py`, `urls.py`,
  `admin.py`, `templates/`, plus optional `signals.py`, `consumers.py`, `permissions.py`).
  - `core` — dashboard (role-scoped, see below), `SystemSettings` (singleton: company name/logo/TIN/VRN,
    currency=TZS, tax rate), `SystemActivity` audit feed, `Notification` inbox + `notify()` helper,
    legacy DRF permissions (`IsStoreManager`, `IsSalesManager`, `IsAdminRole`), APK download endpoint.
  - `users` — custom `AUTH_USER_MODEL = users.User` with a `role` field **and** Django auth Groups; the
    `is_*` role properties; `apps/users/permissions.py` — the current DRF role-permission classes.
  - `inventory` — `Branch`, `Category` (carries `commission_percentage`), `Product` (auto-SKU on save), `Stock`
    (per-product-per-branch, `low_stock_threshold`), `Supplier`, `Purchase`, the `PurchaseOrder` delivery flow
    (`DeliveryCheck`/`DeliveryCheckItem`, see below), `GoodsReceivedNote`, `StockTransfer`, `StockAdjustment`,
    and a fleet sub-domain (`Truck`, `Driver`, `DriverIssue`, `TruckMaintenance`, `TruckAllocation`, `TruckCost`).
  - `sales` — `Customer`, `Vehicle` (outbound delivery fleet), `Sale` (`pending → approved → dispatched`/`cancelled`)
    with `SaleItem` (commission frozen at save), `Transaction` (payments against a sale), `Quotation`/`QuotationItem`,
    plus `utils.py` (PDF rendering) and `views_report.py`.
  - `finance` — `ExpenseCategory`, `Expense` (receipt image, paid-from `BankAccount`), `Income`, `BankAccount`,
    `SupplierPayment` (hangs off the `PurchaseOrder` it settles — the supplier is derived from the order, never
    picked freely; `/api/supplier-payments/payable_orders/` is the list the form is built from, and it carries
    every order with a supplier, drafts included, minus cancelled ones and the ones already paid off; the screen
    is the **cashier's alone** — not Afisa Ugavi, who raises the order, and not the accountant —
    `can_record_supplier_payment` and `CanRecordSupplierPayment` are the one rule. **A recorded payment is a
    request:** it lands `pending` and only an Admin's `approve` makes it `paid`. `reject` is not the end of the
    line — it goes back to the cashier, who amends the entry (only `pending`/`rejected` rows are editable, see
    `is_editable`) and `resubmit`s it as pending with a reply; the Admin's `decision_note` is kept through the
    loop beside the cashier's `cashier_note`. Only
    approved money reduces a balance — `payable_orders` and `by_supplier` report the rest as `pending_amount` —
    so never total `SupplierPayment.amount` without filtering `status='paid'`. Queue:
    `/finance/supplier-payments/approvals/`),
    `TaxPayment` (VAT/PAYE/SDL/…), `PaymentReceipt` (customer payment tracking / debtors),
    plus the **Cashier desk**: `PettyCashTransaction` (the counter float — 'in' top-ups vs 'out' vouchers, balance
    derived) and `OtherPayment` (payouts that are neither a supplier invoice nor a tax). One predicate,
    `apps/finance/views.py::can_use_cashier`, gates the template views, the API and the sidebar section.
    All three registers group their rows by month via `static/js/month_group.js` — months are read off the
    `YYYY-MM-DD` string, never a parsed `Date`, so money cannot slide between months on a timezone shift.
    `/api/supplier-payments/by_supplier/` backs the **By Supplier** screen (ordered vs paid vs still owed per
    supplier; `?month=` narrows the paid side only).
  - **Supplier credit** (`apps/finance/credit.py`) — overpay a 10m order by 10m and the supplier holds 10m of
    ours. Derived, never stored, like the CRM's customer credit:
    `credit = Σ per-order overpayment (approved cash only, never negative per order) − Σ approved
    from_credit payments`. Spending it is an ordinary `SupplierPayment` with `from_credit=True`: it settles the
    order it points at and draws the credit down, and it is excluded from the overpayment side so applying
    credit can never manufacture more of it. `spendable_credit()` also subtracts applications still awaiting
    approval, so two queued applications cannot both spend the same money. Afisa Ugavi sees the figure on the
    order form (`/api/purchase-orders/supplier_credit/?supplier=`), and creating an order for a supplier holding
    credit notifies the cashiers.
  - **Sales accounting** (`apps/finance/sales_ledger.py` + `signals.py`) — `SalesLedgerEntry`, one row per sale,
    mirrored from the till the way the CRM register is: linked **by invoice number**, nothing cascades. Every
    sale lands `pending` however it was settled (`settlement` = paid / part_paid / credit, derived from its
    `Transaction`s), and the accountant **posts** it (`post_entry`) or **queries** it with a note, which notifies
    the seller. A pending or queried row keeps following the sale; **a posted row freezes** — only `sale_status`
    still updates, so a sale cancelled or deleted after posting stays put, flagged for reversal, instead of
    vanishing. `python manage.py sync_sales_ledger` backfills or repairs. Access is admin + accountant via
    `apps/finance/views.py::can_use_accounting`; the API is read-only apart from the two actions.
  - `hr` — `Department`, `JobPosition`, `Employee` (NIDA/TIN/NSSF/NHIF, salary + allowances), `LeaveType`,
    `LeaveRequest`, `AttendanceRecord`, `PayrollPeriod`, `Payslip` (TZ statutory: NSSF, NHIF, PAYE, HESLB, WCF, SDL),
    `EmployeeDocument`, `PerformanceReview`, `DisciplinaryAction`. HR-only users are redirected to `hr:dashboard`.
  - `crm` — a **standalone** customer register (`CustomerRecord`, `CrmPayment`, `CrmCredit`) with Excel/CSV bulk
    import (`imports.py`), reportlab PDF statements (`reports.py`), pagination and per-customer drill-down.

### Dual routing — server-rendered + REST API
`sms_project/urls.py` mounts a single `DefaultRouter` at `/api/` registering **every** ViewSet across all apps.
Server-rendered template views live under per-app URL includes (`/inventory/`, `/sales/`, `/finance/`, `/hr/`,
`/crm/`, plus `apps.core.urls` and `apps.users.urls` mounted at `/`). Adding a resource typically means touching
**both** the app's `views.py` (a `ModelViewSet` for the API *and* a `TemplateView` for the UI) **and** registering
the ViewSet in `sms_project/urls.py`.

`apps/inventory/urls.py` and `apps/finance/urls.py` each keep a *second* local router under `<app>/api/`. The
central `/api/` router is the one the frontend and mobile app use — register new ViewSets there.

The Django admin path is obscured: `ADMIN_URL` env var (defaults to `admin/` only when `DEBUG=True`, else
`manage-panel/`).

### Authentication
- DRF defaults: `TokenAuthentication` + `SessionAuthentication`, `IsAuthenticated` globally. The browsable API
  renderer is enabled **only** in DEBUG (stack fingerprinting); production serves JSON only.
- Mobile app obtains a token at `POST /api-token-auth/` (wired in `apps/users/urls.py`).
- Browser sessions: `LOGIN_URL=/login/`, 20-minute rolling sessions (`SESSION_COOKIE_AGE=1200`,
  `SESSION_SAVE_EVERY_REQUEST=True`), expire on browser close. Cookies are renamed (`umoja_sid`, `umoja_csrf`),
  HttpOnly and SameSite=Lax — the CSRF token reaches JS via the `CSRF_TOKEN` global in `base.html`, not the cookie.
- `CORS_ALLOW_ALL_ORIGINS = True` is intentional (mobile clients) — don't tighten without coordinating with mobile.

### Roles & permissions (three layers — keep them in sync)
1. **`User.ROLE_CHOICES`** (`apps/users/models.py`) — admin, manager, staff, afisa_ugavi (procurement),
   stock_controller, sales_rep, store_manager, accountant, store_keeper, hr_officer, hr_manager,
   sales_credit_manager, cashier.
2. **Django auth Groups + permissions**, seeded by `python manage.py create_roles` (`ROLE_MAP` + `PERMISSIONS`
   dicts). Most ViewSets use `permissions.DjangoModelPermissions`, so these grants are what actually gates the API.
3. **Role properties** on `User` (`is_manager`, `is_sales_rep`, `is_accountant`, `is_hr`, …) that check the `role`
   field **and** group membership — this is how a user can effectively hold multiple roles.

`apps/users/permissions.py` is the current home of DRF role classes (`_RolePermission` base; `is_privileged()`
gives superusers and the `admin` role blanket access so specialists never lock admins out). Use these
(`CanApproveSales`, `CanManageFleet`, `CanHandleGRN`, `CanManagePurchaseOrders`, `CanManageVehicles`,
`CanRecordSupplierPayment`, …) for anything role-gated rather than the older `apps/core/permissions.py` trio.

Template views gate with `UserPassesTestMixin` (`test_func`); the sidebar (`core/templates/partials/sidebar_content.html`)
gates on `perms.*` and the `user.is_*` properties. **When adding a role or screen, update all of: `ROLE_CHOICES`,
`create_roles.py` (`ROLE_MAP` + `PERMISSIONS`), the `is_*` property, the sidebar, and the view's permission class —
then re-run `create_roles` locally and on prod.** Mismatches here are the historical source of 403 bugs.

### Purchase order delivery flow (three roles, and stock waits for the Admin)
A PO can be delivered in instalments, and each round is a `DeliveryCheck` row with a `DeliveryCheckItem` per line:

```
draft/sent --confirm (Afisa Ugavi)--> awaiting_check --everything arrived--> received  (stock updated)
                                            |
                                            +--short--> discrepancy   (Afisa Ugavi comments)
                                                            |
                                                            v
                                                      awaiting_admin
                                          reject <-------+       +-------> confirm
                                     (back to discrepancy,        (delivered qty -> stock;
                                      no stock moves)              balance still owed)
                                                                          |
                                                            received <----+----> partial
                                                          (nothing owed)   (Afisa Ugavi confirms
                                                                            again when the rest
                                                                            arrives -> next round)
```

- **Nothing reaches stock on a short delivery until an Admin confirms it** — not even the lines that arrived in
  full. A complete delivery skips the Admin and is received immediately.
- `PurchaseOrderItem.received_quantity` is the cumulative accepted quantity; `outstanding` (= `quantity −
  received_quantity`) is what the next round is checked against. Over-delivery is clamped at cross-check so the
  balance loop always terminates.
- `_receive_delivery()` in `apps/inventory/views.py` is the single place stock is credited from a delivery — call
  it once per round, inside a transaction.
- Screens: Store Manager `/inventory/deliveries/verify/` (list) → `/inventory/deliveries/verify/<pk>/` (one order,
  checked item by item); Admin `/inventory/deliveries/approvals/`; Afisa Ugavi acts from the PO list.
- The flow is covered end-to-end in `apps/inventory/tests.py`.

### Role-scoped dashboards
`apps/core/views.py::DashboardView` branches on role: accountant, procurement (Afisa Ugavi), sales rep, stock
controller and store keeper each get a self-contained dashboard that **returns early**; admins/managers get the full
operations dashboard. Each has its own partial under `core/templates/partials/dashboard_*.html`. HR-only users are
redirected to the HR dashboard.

### Realtime (Channels) and notifications
- `ASGI_APPLICATION = sms_project.asgi.application`; WebSocket routes in `sms_project/routing.py`
  (`ws/stock/`, `ws/inventory/`), both served by `apps/inventory/consumers.py::StockConsumer` (group `stock_updates`).
- `apps/inventory/signals.py` broadcasts `stock_update` + `low_stock_alert` on every `Stock.save()`.
- `apps/sales/signals.py` pushes `sales_notification` on sale create/update to the same group.
- `apps/core/signals.py` writes `SystemActivity` rows (sales, stock adjustments, transfers, expenses) and broadcasts
  `activity_update` for the live activity feed.
- **Persistent inbox:** `apps.core.notify.notify(user, title, message, url=, level=)` creates a `Notification`
  (e.g. PO delivery discrepancy → notifies the Afisa Ugavi who raised it). Exposed at `/api/notifications/`.
- Channel layer is `InMemoryChannelLayer` (single-process). A `channels_redis` config is commented out in settings
  for scaling out — switching requires a running Redis.

### Documents: PDF & Excel
- **Invoices, delivery notes, quotations, purchase orders** → `apps/sales/utils.py::render_to_pdf` (xhtml2pdf over a
  Django template; `link_callback` resolves `{% static %}`/media URIs so the logo and approval stamp embed).
  Templates: `sales/pdf_document.html`, `sales/pdf_invoice.html`, `sales/pdf_delivery_note.html`.
  `apps/inventory/views.py` imports this same helper for PO PDFs.
- **Tabular reports** (expense report, purchase report, CRM statements) → **reportlab**, built inline in
  `apps/finance/views.py::_export_pdf`, `apps/inventory/views.py` and `apps/crm/reports.py`.
- **Excel exports/imports** → **openpyxl**, imported lazily inside the view functions.

### History / audit
`django-simple-history` is installed and `HistoricalRecords()` is attached to `Product`, `Stock`, `Purchase`, and
`Sale`. `HistoryRequestMiddleware` is in `MIDDLEWARE`, so historical rows record the acting user automatically —
preserve this when adding new tracked models.

### Frontend conventions
- Every page extends `apps/core/templates/base.html`, which defines the `CSRF_TOKEN` JS global and loads
  `static/js/loading_overlay.js`, `socket_service.js`, `notification_handler.js`. Blocks: `title`, `extra_css`,
  `page_header`, `content`, `extra_js`.
- `static/js/api_service.js` wraps `fetch` against `/api` with the CSRF header and unwraps DRF error payloads
  (`error` / `detail`). Page JS talks to the REST API rather than posting forms, in most screens.
- Bootstrap 5 (CDN) + Bootstrap Icons; money is rendered in whole TZS with thousands separators.
- **Every long table pages itself.** `static/js/table_pager.js` is loaded from `base.html` for all pages and
  works at the DOM level — it watches each `<tbody>` and, after whatever drew the rows (a fetch, a Django loop,
  a keystroke re-render), shows one page and draws 10/25/50/100 controls. Nothing per-page is needed. A table
  with 10 rows or fewer is left exactly as it was, a placeholder row ("Loading…", "No data found") is never
  paged, tables inside a `.modal` are skipped, and `data-no-paginate` on a `<table>` opts out — used where every
  row must be on screen at once (a cart being built, the items of one order being cross-checked, a payslip) or
  where the screen already paginates server-side (CRM, the stock movement report).
- **Template namespacing is inconsistent:** `apps/core/templates/` holds *unnamespaced* templates
  (`dashboard.html`, `product_list.html`, `inventory_*.html`, `settings.html`, …) while every other app namespaces
  under `templates/<app>/`. Follow the namespaced pattern for new templates.

### Docs & screenshots
`docs/` holds the generated training/user guide (`Umoja_Training_Guide.{md,html,pdf}`, `Umoja_User_Guide.pptx`) and
`docs/screens/*.png`. The `scripts/capture_*.py` scripts drive **Playwright against the live production site** to
re-capture screenshots; `scripts/build_user_guide.py` / `build_guide_pdf.py` rebuild the documents from them. These
are untracked, ad-hoc tooling — they hardcode prod URLs and credentials, so don't wire them into anything automatic.

## Conventions and gotchas

- **Postgres-only by config.** `DATABASES` reads `DB_*` env vars and uses `django.db.backends.postgresql`. There is
  no SQLite fallback — local dev and pytest both need Postgres running (see `.env.example`).
- **Two fleet models exist.** `inventory.Truck`/`Driver` (procurement/inbound, with `TruckCost` transport
  accounting) and `sales.Vehicle` (outbound dispatch). Deliberately separate — don't merge them.
- **CRM mirrors sales, but is never upstream of them.** Every POS sale syncs into the register automatically
  (`apps/crm/signals.py` -> `apps/crm/sync.py`), carrying its payment state: paid / part paid / on credit.
  Recording a payment anywhere on the sales side updates the same CRM row, and cancelling or deleting a sale
  takes its row back out. The link is **by invoice number, not a FK** (`CustomerRecord.source_invoice`), so
  nothing cascades: rows leave the register because `sync.drop_sale()` decided they should. That is what lets
  a row somebody has filed — a hand-entered payment, an EFD receipt number, a TIN — outlive the sale, while a
  sale deleted as a mistake takes its row with it.
  - Idempotency: mirrored payments carry `CrmPayment.source_transaction` (the sales `Transaction` id).
    Payments typed in by hand leave it null and the sync never touches them.
  - Fields people maintain (`tin`, `efd_receipt_number`, `receipt_number`) are written once on create and
    never overwritten; `date`, `customer_name` and `sales_amount` follow the sale.
  - Editing a CRM row never writes back to a sale. `python manage.py sync_crm_from_sales` backfills or repairs.
  - Access is admin + accountant only — one predicate, `apps/crm/views.py::can_use_crm`, is used by the
    template view, the API and the sidebar.
- **CRM balances are derived, never stored.** `amount_paid`/`balance`/`payment_status` come from the payments table;
  list views annotate `paid_total` so a page costs one query. Customer credit = credits in − `CrmPayment`s with
  `from_credit=True` (`crm/models.py::credit_balances`).
- **`SaleItem` commission is frozen at save time.** `SaleItem.save()` only computes `commission_amount` when it's
  `0`, so historical sales keep their commission if `Category.commission_percentage` later changes. Preserve this.
- **Stock is deducted at dispatch, not at sale creation** (`SaleViewSet.dispatch_order`), and restored in
  `perform_destroy` only for dispatched sales. Back-orders are therefore possible by design.
- **Credit sales take an optional deposit** in the POS (`apps/sales/templates/sales/pos.html`), recorded as a
  `Transaction`; the "credit" filter on `/api/sales/?status=credit` annotates paid totals and returns underpaid sales.
- **`Sale.total_amount` is declared twice in the model** (`apps/sales/models.py`). Known quirk — the second
  declaration wins; don't "fix" it as a no-op cleanup without checking migration history.
- **`SystemSettings` is a singleton.** Its `save()` blocks creation of a second row. Read it via
  `SystemSettings.objects.first()`.
- **The live POS is `apps/sales/templates/sales/pos.html`** (`POSView.template_name`). A dead
  `core/templates/sales_pos.html` stub used to shadow it in searches; it has been deleted.
- **Test coverage is thin and uneven.** `apps/crm/tests.py` (~840 lines) and `apps/inventory/tests.py` (the PO
  delivery flow) are the real suites and the model to copy. The other apps' `tests.py` are empty stubs, and
  `tests/test_sales_flow.py` is a smoke test.
- **`annotate()` with an aggregate silently drops `Meta.ordering`.** The resulting queryset has *no* ORDER BY,
  so paginating it returns rows in an arbitrary order per page — duplicates on one page, omissions on the next.
  This bit the CRM list for real. Always `.order_by(...)` with a unique tiebreaker (id) after annotating
  anything you intend to paginate; see `apps/crm/views.py::_with_payments`.
- **One-off scripts live in `scripts/`.** Mostly `verify_*.py` / `reproduce_*.py` debugging aids, server
  provisioning/hardening shell scripts, seeders, and the docs capture tooling — not part of the runtime.
  Don't import from them.
- **Security scan artifacts live in `reports/security/`** (Bandit, Safety). Treat as outputs; regenerate rather
  than hand-edit.
