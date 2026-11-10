import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

typedef Json = Map<String, dynamic>;

/// Whether the Approve / Reject button collects a note, and if it is required.
enum ReasonMode { none, optional, mandatory }

/// One HTTP call: a path and a JSON body.
class ApprovalRequest {
  const ApprovalRequest(this.path, this.body);
  final String path;
  final Json body;
}

/// A queue of things an admin approves — where to list them, how to show one,
/// and the two calls that decide it. All backend paths are existing production
/// endpoints; nothing here changes the backend.
class ApprovalQueue {
  const ApprovalQueue({
    required this.key,
    required this.title,
    required this.icon,
    required this.color,
    required this.listPath,
    this.listQuery,
    required this.isPending,
    required this.lineTitle,
    required this.lineSubtitle,
    required this.details,
    this.approveLabel = 'Approve',
    this.rejectLabel = 'Reject',
    this.approveReason = ReasonMode.none,
    this.rejectReason = ReasonMode.optional,
    required this.approveRequest,
    required this.rejectRequest,
  });

  final String key;
  final String title;
  final IconData icon;
  final Color color;
  final String listPath;
  final Map<String, dynamic>? listQuery;
  final bool Function(Json) isPending;
  final String Function(Json) lineTitle;
  final String Function(Json) lineSubtitle;
  final List<MapEntry<String, String>> Function(Json) details;
  final String approveLabel;
  final String rejectLabel;
  final ReasonMode approveReason;
  final ReasonMode rejectReason;
  final ApprovalRequest Function(Json item, String note) approveRequest;
  final ApprovalRequest Function(Json item, String note) rejectRequest;
}

// --- small helpers ---------------------------------------------------------

final NumberFormat _int = NumberFormat.decimalPattern();

String _s(Json j, String key, [String fallback = '']) {
  final v = j[key];
  if (v == null) return fallback;
  final s = v.toString();
  return s.isEmpty ? fallback : s;
}

String money(dynamic v) => 'TZS ${_int.format((num.tryParse('$v') ?? 0).round())}';

String dateOf(dynamic v) {
  if (v == null) return '';
  final d = DateTime.tryParse(v.toString());
  if (d == null) return v.toString();
  return DateFormat('d MMM y, HH:mm').format(d.toLocal());
}

String dayOf(dynamic v) {
  if (v == null) return '';
  final d = DateTime.tryParse(v.toString());
  if (d == null) return v.toString();
  return DateFormat('d MMM y').format(d);
}

Json _asJson(dynamic e) => Map<String, dynamic>.from(e as Map);

// --- the queues ------------------------------------------------------------

/// Every approval an admin handles, each pointing at a live production API.
final List<ApprovalQueue> kApprovalQueues = [
  // 1. Sales orders waiting for approval before dispatch.
  ApprovalQueue(
    key: 'sales',
    title: 'Sales orders',
    icon: Icons.receipt_long,
    color: const Color(0xFF2563EB),
    listPath: '/api/sales/',
    isPending: (j) => _s(j, 'status') == 'pending',
    lineTitle: (j) => _s(j, 'invoice_number', 'Sale'),
    lineSubtitle: (j) =>
        '${_s(j, 'customer_name', 'Walk-in')} · ${money(j['total_amount'])}',
    details: (j) => [
      MapEntry('Customer', _s(j, 'customer_name', 'Walk-in Customer')),
      MapEntry('Amount', money(j['total_amount'])),
      MapEntry('Seller', _s(j, 'created_by_name', '-')),
      MapEntry('Items', '${(j['items_response'] as List?)?.length ?? 0}'),
      MapEntry('Branch', 'Branch ${_s(j, 'branch', '-')}'),
      MapEntry('Raised', dateOf(j['created_at'])),
    ],
    approveLabel: 'Approve',
    rejectLabel: 'Decline',
    approveReason: ReasonMode.none,
    rejectReason: ReasonMode.none,
    approveRequest: (j, _) =>
        ApprovalRequest('/api/sales/${j['id']}/approve/', const {}),
    rejectRequest: (j, _) =>
        ApprovalRequest('/api/sales/${j['id']}/decline/', const {}),
  ),

  // 2. Supplier payments the cashier recorded, waiting for admin sign-off.
  ApprovalQueue(
    key: 'supplier_payments',
    title: 'Supplier payments',
    icon: Icons.payments,
    color: const Color(0xFF7C3AED),
    listPath: '/api/supplier-payments/pending/',
    isPending: (j) => _s(j, 'status') == 'pending',
    lineTitle: (j) => _s(j, 'supplier_name', 'Supplier'),
    lineSubtitle: (j) =>
        '${money(j['amount'])} · ${_s(j, 'method', '').toUpperCase()}',
    details: (j) => [
      MapEntry('Supplier', _s(j, 'supplier_name', '-')),
      MapEntry('Amount', money(j['amount'])),
      MapEntry('Method', _s(j, 'method', '-')),
      MapEntry('Order', _s(j, 'po_number', '-')),
      MapEntry('Recorded by', _s(j, 'created_by_name', '-')),
      if (_s(j, 'cashier_note').isNotEmpty)
        MapEntry('Cashier note', _s(j, 'cashier_note')),
    ],
    approveReason: ReasonMode.optional,
    rejectReason: ReasonMode.optional,
    approveRequest: (j, note) => ApprovalRequest(
      '/api/supplier-payments/${j['id']}/approve/',
      note.isEmpty ? const {} : {'note': note},
    ),
    rejectRequest: (j, note) => ApprovalRequest(
      '/api/supplier-payments/${j['id']}/reject/',
      note.isEmpty ? const {} : {'note': note},
    ),
  ),

  // 3. Petty cash requests from any staff member.
  ApprovalQueue(
    key: 'petty_cash',
    title: 'Petty cash requests',
    icon: Icons.account_balance_wallet,
    color: const Color(0xFF059669),
    listPath: '/api/petty-cash-requests/',
    listQuery: const {'status': 'pending'},
    isPending: (j) => _s(j, 'status') == 'pending',
    lineTitle: (j) => _s(j, 'requested_by_name', 'Staff'),
    lineSubtitle: (j) => '${money(j['amount'])} · ${_s(j, 'purpose', '')}',
    details: (j) => [
      MapEntry('Requested by', _s(j, 'requested_by_name', '-')),
      MapEntry('Amount', money(j['amount'])),
      MapEntry('Purpose', _s(j, 'purpose', '-')),
      MapEntry('Category', _s(j, 'category_name', '-')),
      MapEntry('Branch', _s(j, 'branch_name', '-')),
    ],
    approveReason: ReasonMode.optional,
    rejectReason: ReasonMode.optional,
    approveRequest: (j, note) => ApprovalRequest(
      '/api/petty-cash-requests/${j['id']}/approve/',
      note.isEmpty ? const {} : {'note': note},
    ),
    rejectRequest: (j, note) => ApprovalRequest(
      '/api/petty-cash-requests/${j['id']}/reject/',
      note.isEmpty ? const {} : {'note': note},
    ),
  ),

  // 4. Short purchase-order deliveries awaiting an admin decision.
  ApprovalQueue(
    key: 'deliveries',
    title: 'Short deliveries',
    icon: Icons.local_shipping,
    color: const Color(0xFFD97706),
    listPath: '/api/purchase-orders/',
    isPending: (j) => _s(j, 'status') == 'awaiting_admin',
    lineTitle: (j) => 'PO-${'${j['id']}'.padLeft(5, '0')}',
    lineSubtitle: (j) => _s(j, 'supplier_name', 'Supplier'),
    details: (j) => [
      MapEntry('Supplier', _s(j, 'supplier_name', '-')),
      MapEntry('Order', 'PO-${'${j['id']}'.padLeft(5, '0')}'),
      MapEntry('Amount', money(j['total_amount'])),
      if (_s(j, 'store_note').isNotEmpty)
        MapEntry('Store note', _s(j, 'store_note')),
    ],
    approveLabel: 'Confirm',
    rejectLabel: 'Reject',
    approveReason: ReasonMode.optional,
    rejectReason: ReasonMode.mandatory,
    approveRequest: (j, note) => ApprovalRequest(
      '/api/purchase-orders/${j['id']}/admin-decision/',
      {'decision': 'confirm', if (note.isNotEmpty) 'note': note},
    ),
    rejectRequest: (j, note) => ApprovalRequest(
      '/api/purchase-orders/${j['id']}/admin-decision/',
      {'decision': 'reject', 'note': note},
    ),
  ),

  // 5. Staff leave requests.
  ApprovalQueue(
    key: 'leave',
    title: 'Leave requests',
    icon: Icons.event_busy,
    color: const Color(0xFF0891B2),
    listPath: '/api/leave-requests/',
    isPending: (j) => _s(j, 'status') == 'pending',
    lineTitle: (j) => _s(j, 'employee_name', 'Employee'),
    lineSubtitle: (j) =>
        '${_s(j, 'leave_type_name', 'Leave')} · ${_s(j, 'days', '?')} day(s)',
    details: (j) => [
      MapEntry('Employee', _s(j, 'employee_name', '-')),
      MapEntry('Type', _s(j, 'leave_type_name', '-')),
      MapEntry('From', dayOf(j['start_date'])),
      MapEntry('To', dayOf(j['end_date'])),
      MapEntry('Days', _s(j, 'days', '-')),
      if (_s(j, 'reason').isNotEmpty) MapEntry('Reason', _s(j, 'reason')),
    ],
    approveReason: ReasonMode.optional,
    rejectReason: ReasonMode.optional,
    approveRequest: (j, note) => ApprovalRequest(
      '/api/leave-requests/${j['id']}/approve/',
      note.isEmpty ? const {} : {'note': note},
    ),
    rejectRequest: (j, note) => ApprovalRequest(
      '/api/leave-requests/${j['id']}/reject/',
      note.isEmpty ? const {} : {'note': note},
    ),
  ),
];

/// Parse a raw DRF list into typed rows, keeping only this queue's pending ones.
List<Json> pendingOf(ApprovalQueue q, List<dynamic> raw) =>
    raw.map(_asJson).where(q.isPending).toList();
