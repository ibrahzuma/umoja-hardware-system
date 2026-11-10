import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_client.dart';
import '../data/approvals_repository.dart';
import '../models/approval.dart';

class QueueScreen extends ConsumerWidget {
  const QueueScreen({super.key, required this.queue});
  final ApprovalQueue queue;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(queueProvider(queue));
    return Scaffold(
      appBar: AppBar(
        title: Text(queue.title),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(queueProvider(queue)),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(queueProvider(queue)),
        child: async.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => ListView(
            children: [
              const SizedBox(height: 80),
              const Icon(Icons.cloud_off, size: 48, color: Colors.black26),
              const SizedBox(height: 12),
              Text('$e', textAlign: TextAlign.center),
            ],
          ),
          data: (rows) {
            if (rows.isEmpty) {
              return ListView(
                children: const [
                  SizedBox(height: 100),
                  Icon(Icons.check_circle_outline,
                      size: 56, color: Colors.green),
                  SizedBox(height: 12),
                  Center(child: Text('Nothing waiting for approval')),
                ],
              );
            }
            return ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: rows.length,
              separatorBuilder: (_, _) => const SizedBox(height: 12),
              itemBuilder: (_, i) => _ApprovalCard(queue: queue, item: rows[i]),
            );
          },
        ),
      ),
    );
  }
}

class _ApprovalCard extends ConsumerStatefulWidget {
  const _ApprovalCard({required this.queue, required this.item});
  final ApprovalQueue queue;
  final Json item;

  @override
  ConsumerState<_ApprovalCard> createState() => _ApprovalCardState();
}

class _ApprovalCardState extends ConsumerState<_ApprovalCard> {
  bool _busy = false;

  Future<void> _decide({required bool approve}) async {
    final q = widget.queue;
    final mode = approve ? q.approveReason : q.rejectReason;
    final verb = approve ? q.approveLabel : q.rejectLabel;

    String note = '';
    if (mode != ReasonMode.none) {
      final entered = await _askNote(
        title: '$verb — ${q.lineTitle(widget.item)}',
        required: mode == ReasonMode.mandatory,
      );
      if (entered == null) return; // cancelled
      note = entered;
    } else {
      final ok = await _confirm('$verb this?');
      if (!ok) return;
    }

    setState(() => _busy = true);
    try {
      final repo = ref.read(approvalsRepositoryProvider);
      if (approve) {
        await repo.approve(q, widget.item, note);
      } else {
        await repo.reject(q, widget.item, note);
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: approve ? Colors.green.shade700 : Colors.red.shade700,
          content: Text('${q.lineTitle(widget.item)} — ${verb.toLowerCase()}d'),
        ),
      );
      ref.invalidate(queueProvider(q));
      ref.invalidate(countsProvider);
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: Colors.red.shade800,
          content: Text(describeDioError(e)),
        ),
      );
    }
  }

  Future<bool> _confirm(String message) async {
    final r = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(message),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Yes')),
        ],
      ),
    );
    return r ?? false;
  }

  Future<String?> _askNote(
      {required String title, required bool required}) async {
    final ctrl = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) {
        String? error;
        return StatefulBuilder(
          builder: (ctx, setLocal) => AlertDialog(
            title: Text(title, style: const TextStyle(fontSize: 16)),
            content: TextField(
              controller: ctrl,
              autofocus: true,
              maxLines: 3,
              decoration: InputDecoration(
                labelText: required ? 'Reason (required)' : 'Note (optional)',
                errorText: error,
                border: const OutlineInputBorder(),
              ),
            ),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: const Text('Cancel')),
              FilledButton(
                onPressed: () {
                  final text = ctrl.text.trim();
                  if (required && text.isEmpty) {
                    setLocal(() => error = 'A reason is required.');
                    return;
                  }
                  Navigator.pop(ctx, text);
                },
                child: const Text('Submit'),
              ),
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final q = widget.queue;
    final item = widget.item;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(q.icon, color: q.color, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(q.lineTitle(item),
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.bold)),
                ),
              ],
            ),
            const SizedBox(height: 2),
            Text(q.lineSubtitle(item),
                style: const TextStyle(color: Colors.black54)),
            const Divider(height: 20),
            for (final d in q.details(item))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 110,
                      child: Text(d.key,
                          style: const TextStyle(color: Colors.black54)),
                    ),
                    Expanded(
                      child: Text(d.value,
                          style:
                              const TextStyle(fontWeight: FontWeight.w500)),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 10),
            if (_busy)
              const Center(
                child: Padding(
                  padding: EdgeInsets.all(8),
                  child: SizedBox(
                      height: 22,
                      width: 22,
                      child: CircularProgressIndicator(strokeWidth: 2)),
                ),
              )
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _decide(approve: false),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.red.shade700,
                        side: BorderSide(color: Colors.red.shade200),
                        padding: const EdgeInsets.symmetric(vertical: 12),
                      ),
                      icon: const Icon(Icons.close, size: 18),
                      label: Text(q.rejectLabel),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: () => _decide(approve: true),
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.green.shade700,
                        padding: const EdgeInsets.symmetric(vertical: 12),
                      ),
                      icon: const Icon(Icons.check, size: 18),
                      label: Text(q.approveLabel),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
