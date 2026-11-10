# Umoja Approvals (admin mobile app)

A small Flutter app that lets an **admin** clear every approval queue from a
phone. It talks to the same Django backend as the website over the REST API,
using token auth — there is **no separate backend**, and it changes nothing on
the server.

## What it approves

Each queue reads a live production endpoint and sends the same decision the
website would:

| Queue | Pending source | Approve | Reject |
|-------|----------------|---------|--------|
| Sales orders | `GET /api/sales/` (status `pending`) | `POST /api/sales/{id}/approve/` | `POST /api/sales/{id}/decline/` |
| Supplier payments | `GET /api/supplier-payments/pending/` | `POST /api/supplier-payments/{id}/approve/` | `.../reject/` |
| Petty cash requests | `GET /api/petty-cash-requests/?status=pending` | `POST /api/petty-cash-requests/{id}/approve/` | `.../reject/` |
| Short deliveries (PO) | `GET /api/purchase-orders/` (status `awaiting_admin`) | `POST /api/purchase-orders/{id}/admin-decision/` `{decision:confirm}` | `{decision:reject, note}` |
| Leave requests | `GET /api/leave-requests/` (status `pending`) | `POST /api/leave-requests/{id}/approve/` | `.../reject/` |

A queue the signed-in account cannot access simply shows "Not available for
your account" instead of failing the whole dashboard.

## Run it

```bash
cd mobile_admin
flutter pub get

# Against production (the default base URL is https://umoja.ehub.co.tz):
flutter run

# Against a local backend:
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000   # Android emulator
```

Sign in with an **admin** (or superuser) account. Non-admin roles will see
"not allowed" when they try to act — the server is the authority.

## Build an APK

```bash
flutter build apk --release
# -> build/app/outputs/flutter-apk/app-release.apk
```

## Test / analyze

```bash
flutter analyze
flutter test        # unit tests for the queue logic (no network/platform needed)
```

## Layout

```
lib/
  config.dart                  # API base URL (prod by default)
  data/
    api_client.dart            # Dio + token auth
    token_storage.dart         # secure token store
    auth_controller.dart       # sign in / out (Riverpod)
    approvals_repository.dart   # fetch queues + send decisions
  models/approval.dart         # the five queues: paths, display, decisions
  screens/                     # login, home (counts), queue (approve/reject)
  theme/app_theme.dart
```
