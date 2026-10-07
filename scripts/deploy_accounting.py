"""Deploy to production and check it actually worked.

Run from a dev machine once the SSH credentials are in `.env`:

    python scripts/deploy_accounting.py            # deploy, then verify
    python scripts/deploy_accounting.py --check    # verify only, change nothing

`deploy.sh` on the host does the pull, the migrate, `open_books` and
collectstatic. This wrapper exists because of two things it does *not* do:

  * it exits 0 even when a step failed, so its own output cannot be trusted —
    every step is therefore re-checked here against the server's real state;
  * it never runs `create_roles`, which is what grants the accountant the
    voucher permissions. Without it the new screens 403.

Nothing here is destructive: it pulls, migrates forward, syncs permissions and
reads back the result.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH = [sys.executable, str(ROOT / "scripts" / "ssh_deploy.py"), "exec"]
APP = "/var/www/app"
PY = f"{APP}/venv/bin/python"

# The migrations this release adds. Checked by name so a partial apply is
# obvious rather than something to infer from a success message.
EXPECTED_MIGRATIONS = [
    "0020_currency_exchangerate_financialyear_invoice_and_more",
    "0021_open_the_books",
    "0022_remove_invoice_unique_customer_invoice_number_and_more",
    "0023_voucher_sales_entry",
]


def run(command: str, *, label: str = "", quiet: bool = False) -> tuple[int, str]:
    """One command on the production host. Returns (exit code, output)."""
    if label and not quiet:
        print(f"\n--- {label} " + "-" * max(0, 60 - len(label)))
    result = subprocess.run(SSH + [command], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    if not quiet:
        print(output.rstrip() or "(no output)")
    return result.returncode, output


def manage(args: str, **kwargs) -> tuple[int, str]:
    return run(f"cd {APP} && {PY} manage.py {args}", **kwargs)


def deploy() -> None:
    print("=" * 70)
    print("DEPLOY")
    print("=" * 70)

    # The pull. `core.fileMode false` is set because differing file modes
    # between Windows and the host have blocked a pull here before.
    run(f"cd {APP} && git config core.fileMode false && git fetch --all && "
        f"git reset --hard origin/main && git log --oneline -1",
        label="pull origin/main")

    run(f"cd {APP} && {APP}/venv/bin/pip install -q -r requirements.txt && echo 'deps ok'",
        label="dependencies")

    manage("migrate --noinput", label="migrate")

    # The two steps deploy.sh leaves out or cannot be trusted on.
    manage("open_books", label="open the books")
    manage("create_roles", label="create_roles (deploy.sh skips this)")

    manage("collectstatic --noinput", label="collectstatic")

    run("sudo systemctl restart django_app && sleep 3 && "
        "systemctl is-active django_app", label="restart django_app")
    run("sudo systemctl reload nginx && systemctl is-active nginx", label="reload nginx")


def check() -> int:
    """Read the server's actual state back. Returns the number of problems."""
    print("\n" + "=" * 70)
    print("VERIFY")
    print("=" * 70)
    problems: list[str] = []

    code, out = run(f"cd {APP} && git log --oneline -1", label="commit on the host")
    if code != 0:
        problems.append("could not read the deployed commit")

    code, out = manage("showmigrations finance", label="finance migrations")
    for name in EXPECTED_MIGRATIONS:
        applied = f"[X] {name}" in out
        print(f"   {'OK  ' if applied else 'MISS'} {name}")
        if not applied:
            problems.append(f"migration not applied: {name}")

    # Is anything else outstanding anywhere?
    code, out = manage("migrate --check --noinput", label="any migration outstanding?")
    if code != 0 or "not applied" in out.lower():
        problems.append("migrations are still outstanding")
    else:
        print("   OK   nothing outstanding")

    code, out = manage("check --deploy --fail-level ERROR", label="django check --deploy")
    if code != 0:
        problems.append("django check --deploy reported an error")

    # The books, and the permissions the new screens need.
    probe = (
        "from apps.finance.models import *;"
        "from django.contrib.auth.models import Group;"
        "base = Currency.base();"
        "year = FinancialYear.current();"
        "g = Group.objects.filter(name='Accountant').first();"
        "perms = set(g.permissions.values_list('codename', flat=True)) if g else set();"
        "print('base_currency', base.code if base else 'MISSING');"
        "print('financial_year', year.code if year else 'MISSING');"
        "print('voucher_types', VoucherType.objects.count());"
        "print('ledgers', LedgerAccount.objects.count());"
        "print('controls', LedgerAccount.objects.filter(is_customer_control=True).count(),"
        "      LedgerAccount.objects.filter(is_supplier_control=True).count());"
        "print('vouchers', Voucher.objects.count(), 'gl', GeneralLedgerEntry.objects.count());"
        "print('orphan_vouchers', Voucher.objects.filter(financial_year__isnull=True).count());"
        "print('orphan_gl', GeneralLedgerEntry.objects.filter(financial_year__isnull=True).count());"
        "print('accountant_perms', sorted(p for p in perms if 'voucher' in p));"
    )
    code, out = manage(f'shell -c "{probe}"', label="the books on production")
    for marker, label in (("base_currency MISSING", "no base currency"),
                          ("financial_year MISSING", "no financial year")):
        if marker in out:
            problems.append(label)
    if "controls 1 1" not in out:
        problems.append("the two control accounts are not both there")
    for needed in ("post_voucher", "cancel_voucher", "reverse_voucher"):
        if needed not in out:
            problems.append(f"the Accountant group is missing {needed}")
    # Every voucher and ledger row must have been filed under a year by 0021.
    for marker in ("orphan_vouchers 0", "orphan_gl 0"):
        if marker not in out:
            problems.append(f"migration 0021 left rows unfiled ({marker.split()[0]})")

    # The trial balance is the one figure that proves the ledger is coherent.
    code, out = manage(
        'shell -c "from apps.finance import vouchers as v; t = v.trial_balance();'
        ' print(\'trial_balance difference\', t[\'difference\'])"',
        label="does the trial balance balance?")
    if "difference 0.00" not in out and "difference 0" not in out:
        problems.append("the trial balance does not balance")

    # And the pages themselves, over HTTPS, from the host.
    urls = ["/finance/accounting/", "/finance/accounting/vouchers/",
            "/finance/accounting/reports/", "/finance/accounting/ledgers/",
            "/finance/profit-loss/", "/finance/balance-sheet/", "/login/"]
    checks = " ; ".join(
        f"printf '%s ' '{url}'; curl -s -o /dev/null -w '%{{http_code}}\\n' "
        f"-L --max-time 20 https://umoja.ehub.co.tz{url}" for url in urls)
    code, out = run(checks, label="the pages, over HTTPS")
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].startswith('/'):
            url, status = parts
            # A signed-out visitor is redirected to the login page, so 200 on
            # /login/ and 200-after-redirect elsewhere are both healthy; a 5xx
            # is not.
            if status.startswith('5') or status == '000':
                problems.append(f"{url} returned {status}")

    code, out = run("sudo journalctl -u django_app --since '-3 min' --no-pager "
                    "| grep -iE 'error|traceback|exception' | tail -15",
                    label="recent errors in the service log")
    if out.strip() and "no output" not in out:
        print("   ^ worth reading — these may predate the deploy")

    print("\n" + "=" * 70)
    if problems:
        print(f"{len(problems)} PROBLEM(S):")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print("All checks passed.")
    print("=" * 70)
    return len(problems)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify only; change nothing on the host")
    options = parser.parse_args()
    if not options.check:
        deploy()
    raise SystemExit(1 if check() else 0)
