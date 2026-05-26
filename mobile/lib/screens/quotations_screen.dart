import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_client.dart';
import '../data/repositories.dart';
import '../models/quotation.dart';
import '../screens/home_shell.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

class QuotationsScreen extends ConsumerWidget {
  const QuotationsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final quotes = ref.watch(quotationListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Quotations'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: () => ref.invalidate(quotationListProvider),
            icon: const Icon(Icons.refresh_rounded),
          ),
          buildProfileMenu(ref),
        ],
      ),
      body: SafeArea(
        child: quotes.when(
          data: (list) {
            if (list.isEmpty) {
              return const EmptyState(
                icon: Icons.description_outlined,
                title: 'No quotations yet',
                message:
                    'Quotations created on the web will show up here. Mobile create flow coming soon.',
              );
            }
            return RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(quotationListProvider);
                await ref.read(quotationListProvider.future);
              },
              child: ListView.separated(
                padding: const EdgeInsets.all(AppSpacing.lg),
                itemCount: list.length,
                separatorBuilder: (_, __) =>
                    const SizedBox(height: AppSpacing.sm),
                itemBuilder: (_, i) => _QuotationRow(q: list[i]),
              ),
            );
          },
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => ErrorState(
            message: describeDioError(e),
            onRetry: () => ref.invalidate(quotationListProvider),
          ),
        ),
      ),
    );
  }
}

class _QuotationRow extends StatelessWidget {
  const _QuotationRow({required this.q});
  final Quotation q;

  Color get _statusColor {
    switch (q.status) {
      case 'sent':
        return AppColors.info500;
      case 'converted':
        return AppColors.success500;
      case 'expired':
        return AppColors.danger500;
      default:
        return AppColors.slate500;
    }
  }

  Color get _statusBg {
    switch (q.status) {
      case 'sent':
        return AppColors.info50;
      case 'converted':
        return AppColors.success50;
      case 'expired':
        return AppColors.danger50;
      default:
        return AppColors.slate100;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.slate0,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.slate200),
      ),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: AppColors.brand50,
              borderRadius: BorderRadius.circular(AppRadius.sm),
            ),
            child: const Icon(Icons.description_rounded,
                color: AppColors.brand600, size: 20),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  q.customerName,
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    color: AppColors.slate900,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: _statusBg,
                        borderRadius: BorderRadius.circular(AppRadius.pill),
                      ),
                      child: Text(
                        q.status,
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                          color: _statusColor,
                          letterSpacing: 0.4,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (q.createdAt != null)
                      Text(
                        '${q.createdAt!.year}-${q.createdAt!.month.toString().padLeft(2, '0')}-${q.createdAt!.day.toString().padLeft(2, '0')}',
                        style: const TextStyle(
                            fontSize: 11.5, color: AppColors.slate500),
                      ),
                  ],
                ),
              ],
            ),
          ),
          Text(
            formatTzs(q.totalAmount),
            style: const TextStyle(
                fontWeight: FontWeight.w800, color: AppColors.slate900),
          ),
        ],
      ),
    );
  }
}
