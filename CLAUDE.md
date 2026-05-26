# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Umoja Hardware System** — a Django 6 (Channels/ASGI) ERP for a Tanzanian hardware retailer. Handles multi-branch inventory, sales/quotations, vehicle dispatch, finance (expenses/income/taxes/supplier payments), and role-based user management. Serves both server-rendered Django templates and a REST API consumed by a mobile app.

## Common Commands

All commands assume the project venv is active and `.env` is configured (copy from `.env.example`; Postgres is required — `sms_project/settings.py` has no SQLite fallback).

```powershell
# Run the dev server (ASGI — required because Channels/WebSockets are wired in)
python manage.py runserver
# For production-style local run:
daphne -b 127.0.0.1 -p 8000 sms_project.asgi:application

# Migrations
python manage.py makemigrations
python manage.py migrate

# Seed role groups + permissions (must run after first migrate)
python manage.py create_roles

# Tests — pytest is configured via pytest.ini (uses --reuse-db)
pytest                                    # full suite
pytest tests/test_sales_flow.py           # one file
pytest apps/inventory/tests.py::ClassName::test_name   # one test
python manage.py test apps.sales          # Django test runner alternative

# Static files (needed before collectstatic in prod; WhiteNoise serves them)
python manage.py collectstatic --noinput

# API docs (DRF Spectacular)
# /api/docs/  (Swagger UI)   |   /api/schema/  (raw OpenAPI)
```

Production deploy is automated via `./deploy.sh` on the Linode host (`/var/www/app`); it pulls main, installs deps, migrates, collects static, and restarts the `django_app` systemd unit + nginx. See `DEPLOYMENT.md` for one-time server setup.

## Architecture

### Project layout
- `sms_project/` — Django project (settings, root URLs, ASGI/WSGI, WebSocket routing).
- `apps/` — domain apps, each a standard Django app (`models.py`, `views.py`, `serializers.py`, `urls.py`, `admin.py`, plus optional `signals.py`, `consumers.py`, `permissions.py`).
  - `core` — dashboard, `SystemSettings` (singleton: company name, currency=TZS, tax rate), `SystemActivity` audit log, shared DRF permissions (`IsStoreManager`, `IsSalesManager`, `IsAdminRole`).
  - `users` — custom `AUTH_USER_MODEL = users.User` with a `role` field **and** Django auth Groups. Role properties (`is_manager`, `is_sales_rep`, etc.) check both. Each user is bound to a `Branch`.
  - `inventory` — `Branch`, `Category` (carries `commission_percentage`), `Product` (auto-generated SKU on save), `Stock` (per-product-per-branch with `low_stock_threshold`), `Supplier`, `Purchase`, full `PurchaseOrder`/`GoodsReceivedNote` flow, `StockTransfer`, `StockAdjustment`, and a fleet sub-domain (`Truck`, `Driver`, `TruckMaintenance`, `TruckAllocation`).
  - `sales` — `Customer`, `Vehicle` (sales-side delivery fleet, separate from inventory `Truck`), `Sale` (status flow: `pending → approved → dispatched`/`cancelled`) with `SaleItem` (commission auto-calculated from category % at save time and frozen), `Transaction` (payment per sale), `Quotation`/`QuotationItem`.
  - `finance` — `Expense` (with receipt image upload), `Income`, `SupplierPayment`, `TaxPayment` (VAT/PAYE/SDL/etc.), `ExpenseCategory`.

### Dual routing — server-rendered + REST API
`sms_project/urls.py` mounts a single `DefaultRouter` at `/api/` registering every ViewSet across apps. Server-rendered template views live under per-app URL includes (`/inventory/`, `/sales/`, `/finance/`, plus `apps.core.urls` and `apps.users.urls` mounted at `/`). When adding a new resource you typically touch **both** the app's `views.py` (a `ModelViewSet` for the API and a template view for the UI) and register the ViewSet in `sms_project/urls.py`.

### Authentication
- DRF defaults: `TokenAuthentication` + `SessionAuthentication`, `IsAuthenticated` required globally (`sms_project/settings.py`).
- Mobile app obtains a token at `POST /api-token-auth/` (wired in `apps/users/urls.py`).
- Browser sessions: `LOGIN_URL=/login/`, 20-minute rolling sessions (`SESSION_COOKIE_AGE=1200`, `SESSION_SAVE_EVERY_REQUEST=True`), expire on browser close.
- `CORS_ALLOW_ALL_ORIGINS = True` is intentional (mobile clients) — don't tighten without coordinating with the mobile app.

### Roles & permissions
Roles live in two places that must stay in sync: `User.ROLE_CHOICES` (the `role` CharField) and Django auth `Group`s seeded by `python manage.py create_roles`. The `is_*` properties on `User` (`apps/users/models.py`) check **both** the role field and group membership — when adding a new role, update both `ROLE_CHOICES` and the `ROLE_MAP`/`PERMISSIONS` dict in `apps/users/management/commands/create_roles.py`, then run the command.

### Realtime (Channels)
- `ASGI_APPLICATION = sms_project.asgi.application`; WebSocket routes in `sms_project/routing.py` (`ws/stock/`, `ws/inventory/`).
- `apps/inventory/consumers.py` joins clients to the `stock_updates` group.
- **`apps/inventory/signals.py`** broadcasts `stock_update` and `low_stock_alert` events on every `Stock.save()`.
- **`apps/sales/signals.py`** reuses the same `stock_updates` group to push `sales_notification` events on sale create/update.
- Channel layer is `InMemoryChannelLayer` (single-process). A `channels_redis` config is commented out in settings for when scaling out — switching requires a running Redis.

### History / audit
`django-simple-history` is installed and `HistoricalRecords()` is attached to `Product`, `Stock`, `Purchase`, and `Sale`. `HistoryRequestMiddleware` is in `MIDDLEWARE`, so historical rows record the acting user automatically — preserve this when adding new tracked models.

### Static & media
WhiteNoise serves static in all envs (`CompressedManifestStaticFilesStorage`). Media uploads (receipt images, company logo) go under `MEDIA_ROOT=BASE_DIR/media`; in DEBUG, `sms_project/urls.py` serves them, in prod nginx aliases `/media/` directly.

## Conventions and gotchas

- **Postgres-only by config.** `DATABASES` reads `DB_*` env vars and uses `django.db.backends.postgresql`. There is no SQLite fallback — local dev needs Postgres running (see `.env.example`).
- **Two fleet models exist.** `inventory.Truck`/`Driver` (procurement/inbound) and `sales.Vehicle` (outbound dispatch). They are deliberately separate — don't merge them.
- **SaleItem commission is frozen at save time.** `SaleItem.save()` only computes `commission_amount` when it's `0`, so historical sales retain their commission even if `Category.commission_percentage` later changes. Preserve this behavior.
- **`Sale.total_amount` is duplicated in the model** (`models.py:66-67` declares the same field twice). This is a known quirk — the second declaration wins; don't "fix" it as a no-op cleanup without checking migration history.
- **`SystemSettings` is a singleton.** Its `save()` blocks creation of a second row. Read it via `SystemSettings.objects.first()`.
- **One-off scripts live in `scripts/`.** Mostly `verify_*.py` / `reproduce_*.py` debugging aids — not part of the runtime. Don't import from them.
- **Security scan artifacts live in `reports/security/`** (Bandit, Safety). Treat as outputs; regenerate rather than hand-edit.
