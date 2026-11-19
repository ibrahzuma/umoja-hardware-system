"""Bank statement upload + reconciliation screens.

A thin layer on top of `apps/finance/reconciliation.py`: list the uploads for
a bank, open one to reconcile it against the ledger, upload a new statement,
match/unmatch rows. Access is the same admin + accountant predicate the
rest of the books use.
"""
import io
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import TemplateView, View

from . import reconciliation
from .models import (
    BankAccount, BankStatementUpload, BankStatementLine, GeneralLedgerEntry,
    LedgerAccount,
)
from .views import can_use_accounting


class _Reconciler(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return can_use_accounting(self.request.user)


class BankReconciliationListView(_Reconciler, TemplateView):
    """Index: one card per bank account with a summary of its uploaded
    statements and the ones still being reconciled."""
    template_name = 'finance/bank_reconciliation_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        banks = []
        for bank in BankAccount.objects.filter(is_active=True).order_by('name'):
            uploads = bank.statements.order_by('-uploaded_at')[:5]
            banks.append({
                'bank': bank,
                'uploads': uploads,
                'upload_count': bank.statements.count(),
            })
        ctx['banks'] = banks
        return ctx


class BankReconciliationUploadView(_Reconciler, View):
    """Accept a statement file for a bank account, parse it, store the lines."""
    def post(self, request):
        bank_id = request.POST.get('bank_account')
        file = request.FILES.get('file')
        if not bank_id or not file:
            messages.error(request, "Pick a bank account and a statement file.")
            return redirect('finance:bank_reconciliation')

        bank = get_object_or_404(BankAccount, pk=bank_id)

        # Read the whole file first — the parser needs the full bytes.
        raw = file.read()
        try:
            lines = reconciliation.parse(raw, file.content_type, file.name)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('finance:bank_reconciliation')

        if not lines:
            messages.warning(
                request,
                "No rows were recognised in that file. Check that it has columns "
                "for Date, Debit/Credit (or a single Amount), and Description.",
            )
            return redirect('finance:bank_reconciliation')

        upload = BankStatementUpload.objects.create(
            bank_account=bank,
            file=file,
            original_name=file.name[:255],
            content_type=(file.content_type or '')[:80],
            opening_balance=_decimal_or_none(request.POST.get('opening_balance')),
            closing_balance=_decimal_or_none(request.POST.get('closing_balance')),
            uploaded_by=request.user,
            note=request.POST.get('note', '')[:1000],
        )
        # The file has already been read into memory by `.read()`; rewind the
        # underlying stream to zero so the FileField save re-reads it clean.
        try:
            file.seek(0)
        except Exception:
            pass
        reconciliation.store_lines(upload, lines)
        messages.success(
            request,
            f"{len(lines)} statement line(s) imported for {bank.name}.")
        return redirect('finance:bank_reconciliation_detail', pk=upload.id)


class BankReconciliationDetailView(_Reconciler, TemplateView):
    """One statement side-by-side with the ledger for its bank, with match
    buttons on each pair."""
    template_name = 'finance/bank_reconciliation_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        upload = get_object_or_404(BankStatementUpload, pk=kwargs['pk'])
        ctx['upload'] = upload
        ctx['statement_lines'] = (upload.lines.select_related('matched_entry__voucher')
                                  .order_by('date', 'id'))

        ledger_qs = reconciliation.ledger_entries_for(
            upload.bank_account.name, upload.period_start, upload.period_end)
        ctx['ledger_entries'] = list(ledger_qs[:500])
        ctx['no_ledger'] = not LedgerAccount.objects.filter(
            name=upload.bank_account.name).exists()

        # Summary totals so the accountant sees where the gap is without
        # ticking every row.
        statement_credits = sum((line.credit for line in upload.lines.all()), Decimal('0'))
        statement_debits = sum((line.debit for line in upload.lines.all()), Decimal('0'))
        matched = upload.lines.filter(matched_entry__isnull=False).count()
        unmatched = upload.lines.filter(matched_entry__isnull=True).count()
        ctx.update({
            'statement_credits': statement_credits,
            'statement_debits': statement_debits,
            'statement_net': statement_credits - statement_debits,
            'matched_count': matched,
            'unmatched_count': unmatched,
            'ledger_unmatched_count': ledger_qs.count(),
        })

        # Suggest automatic matches (amount + date within a week).
        suggestions = reconciliation.suggest_matches(upload)
        ctx['suggestion_count'] = sum(1 for _, cands in suggestions if len(cands) == 1)
        return ctx


class BankReconciliationMatchView(_Reconciler, View):
    """AJAX: match one statement line to one GL row, or clear the match."""
    def post(self, request, pk):
        line = get_object_or_404(BankStatementLine, pk=pk)
        action = request.POST.get('action', 'match')
        if action == 'unmatch':
            reconciliation.unmatch(line, request.user)
            return JsonResponse({'ok': True, 'matched': False})

        gl_id = request.POST.get('gl_entry')
        if not gl_id:
            return JsonResponse({'ok': False, 'error': 'Pick a ledger row.'}, status=400)
        gl = get_object_or_404(GeneralLedgerEntry, pk=gl_id)
        reconciliation.match(line, gl, request.user)
        return JsonResponse({
            'ok': True, 'matched': True,
            'voucher': gl.voucher_number,
            'voucher_id': gl.voucher_id,
        })


class BankReconciliationTemplateView(_Reconciler, View):
    """Hand out an empty .xlsx with the columns the parser expects — the same
    layout the TZ banks export, so the user can paste rows straight in."""
    def get(self, request):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment

        wb = Workbook()
        ws = wb.active
        ws.title = 'Statement'

        headers = reconciliation.TEMPLATE_HEADERS
        ws.append(list(headers))

        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill('solid', fgColor='1F4E78')
        center = Alignment(horizontal='center', vertical='center')
        for col_idx, _ in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

        # Reasonable column widths so Narration isn't squeezed.
        widths = (12, 12, 14, 42, 14, 14, 14, 14, 16)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

        ws.freeze_panes = 'A2'

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        response = HttpResponse(
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = (
            'attachment; filename="bank_statement_template.xlsx"'
        )
        return response


class BankReconciliationAutoMatchView(_Reconciler, View):
    """Apply every unambiguous suggestion in one go."""
    def post(self, request, pk):
        upload = get_object_or_404(BankStatementUpload, pk=pk)
        done = 0
        for line, cands in reconciliation.suggest_matches(upload):
            if len(cands) == 1 and line.matched_entry_id is None:
                reconciliation.match(line, cands[0], request.user)
                done += 1
        messages.success(request, f"{done} line(s) matched automatically.")
        return redirect('finance:bank_reconciliation_detail', pk=upload.id)


def _decimal_or_none(raw):
    if raw in (None, ''):
        return None
    try:
        return Decimal(str(raw).replace(',', '').strip())
    except Exception:
        return None
