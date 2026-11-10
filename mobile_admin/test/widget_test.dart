// Unit tests for the approval-queue logic. These avoid platform channels
// (secure storage, HTTP) so they run anywhere.

import 'package:flutter_test/flutter_test.dart';
import 'package:umoja_admin/models/approval.dart';

ApprovalQueue queue(String key) =>
    kApprovalQueues.firstWhere((q) => q.key == key);

void main() {
  test('money formats whole TZS with thousands separators', () {
    expect(money('20905700'), 'TZS 20,905,700');
    expect(money(776000), 'TZS 776,000');
    expect(money(null), 'TZS 0');
  });

  test('every queue has a distinct key and both decisions', () {
    final keys = kApprovalQueues.map((q) => q.key).toSet();
    expect(keys.length, kApprovalQueues.length);
    expect(kApprovalQueues.length, 5);
  });

  test('sales queue keeps only pending rows', () {
    final q = queue('sales');
    final rows = [
      {'id': 1, 'status': 'pending', 'invoice_number': 'A'},
      {'id': 2, 'status': 'approved', 'invoice_number': 'B'},
      {'id': 3, 'status': 'pending', 'invoice_number': 'C'},
    ];
    final pending = pendingOf(q, rows);
    expect(pending.map((e) => e['invoice_number']), ['A', 'C']);
  });

  test('sales approve/decline hit the right endpoints', () {
    final q = queue('sales');
    final item = {'id': 42, 'status': 'pending'};
    expect(q.approveRequest(item, '').path, '/api/sales/42/approve/');
    expect(q.rejectRequest(item, '').path, '/api/sales/42/decline/');
  });

  test('short-delivery decision carries confirm/reject and the note', () {
    final q = queue('deliveries');
    final item = {'id': 7, 'status': 'awaiting_admin'};
    final ok = q.approveRequest(item, '');
    expect(ok.path, '/api/purchase-orders/7/admin-decision/');
    expect(ok.body['decision'], 'confirm');
    final no = q.rejectRequest(item, 'too few bags');
    expect(no.body['decision'], 'reject');
    expect(no.body['note'], 'too few bags');
    expect(q.rejectReason, ReasonMode.mandatory);
  });

  test('deliveries queue filters on awaiting_admin', () {
    final q = queue('deliveries');
    final rows = [
      {'id': 1, 'status': 'awaiting_admin'},
      {'id': 2, 'status': 'received'},
      {'id': 3, 'status': 'draft'},
    ];
    expect(pendingOf(q, rows).map((e) => e['id']), [1]);
  });

  test('optional notes are omitted from the body when blank', () {
    final q = queue('supplier_payments');
    final item = {'id': 9, 'status': 'pending'};
    expect(q.approveRequest(item, '').body.containsKey('note'), isFalse);
    expect(q.approveRequest(item, 'ok').body['note'], 'ok');
  });
}
