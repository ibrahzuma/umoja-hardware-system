import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/approval.dart';
import 'api_client.dart';

/// Reads the approval queues and sends the decisions. Every call goes to an
/// existing production endpoint; approving or rejecting here changes live data,
/// exactly as the website would.
class ApprovalsRepository {
  ApprovalsRepository(this._api);
  final ApiClient _api;

  /// The pending rows of one queue.
  Future<List<Json>> pending(ApprovalQueue q) async {
    final raw = await _api.list(q.listPath, query: q.listQuery);
    return pendingOf(q, raw);
  }

  Future<void> approve(ApprovalQueue q, Json item, String note) async {
    final r = q.approveRequest(item, note.trim());
    await _api.post(r.path, r.body);
  }

  Future<void> reject(ApprovalQueue q, Json item, String note) async {
    final r = q.rejectRequest(item, note.trim());
    await _api.post(r.path, r.body);
  }
}

final approvalsRepositoryProvider = Provider<ApprovalsRepository>(
  (ref) => ApprovalsRepository(ref.watch(apiClientProvider)),
);

/// Pending rows for a single queue (used by the queue screen).
final queueProvider = FutureProvider.family<List<Json>, ApprovalQueue>(
  (ref, q) => ref.watch(approvalsRepositoryProvider).pending(q),
);

/// Pending counts for every queue (used by the home screen). A queue that
/// errors (e.g. a role without access to it) reports -1 rather than sinking
/// the whole dashboard.
final countsProvider = FutureProvider<Map<String, int>>((ref) async {
  final repo = ref.watch(approvalsRepositoryProvider);
  final entries = await Future.wait(
    kApprovalQueues.map((q) async {
      try {
        final rows = await repo.pending(q);
        return MapEntry(q.key, rows.length);
      } catch (_) {
        return MapEntry(q.key, -1);
      }
    }),
  );
  return Map.fromEntries(entries);
});
