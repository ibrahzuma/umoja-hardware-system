import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_client.dart';
import '../data/repositories.dart';
import '../models/customer.dart';
import '../models/sale.dart';
import '../screens/home_shell.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

class CustomersScreen extends ConsumerStatefulWidget {
  const CustomersScreen({super.key});

  @override
  ConsumerState<CustomersScreen> createState() => _CustomersScreenState();
}

class _CustomersScreenState extends ConsumerState<CustomersScreen> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final customers = ref.watch(customerListProvider(_query));

    return Scaffold(
      appBar: AppBar(
        title: const Text('Customers'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: () => ref.invalidate(customerListProvider(_query)),
            icon: const Icon(Icons.refresh_rounded),
          ),
          buildProfileMenu(ref),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                  AppSpacing.lg, AppSpacing.md, AppSpacing.lg, AppSpacing.sm),
              child: TextField(
                decoration: const InputDecoration(
                  hintText: 'Search by name or phone…',
                  prefixIcon: Icon(Icons.search_rounded),
                  isDense: true,
                ),
                onChanged: (v) => setState(() => _query = v.trim()),
              ),
            ),
            Expanded(
              child: customers.when(
                data: (list) {
                  if (list.isEmpty) {
                    return const EmptyState(
                      icon: Icons.people_outline_rounded,
                      title: 'No customers found',
                      message: 'Try a different search.',
                    );
                  }
                  return RefreshIndicator(
                    onRefresh: () async {
                      ref.invalidate(customerListProvider(_query));
                      await ref.read(customerListProvider(_query).future);
                    },
                    child: ListView.separated(
                      padding: const EdgeInsets.fromLTRB(
                          AppSpacing.lg, 0, AppSpacing.lg, AppSpacing.lg),
                      itemCount: list.length,
                      separatorBuilder: (_, __) =>
                          const SizedBox(height: AppSpacing.sm),
                      itemBuilder: (_, i) => _CustomerCard(customer: list[i]),
                    ),
                  );
                },
                loading: () =>
                    const Center(child: CircularProgressIndicator()),
                error: (e, _) => ErrorState(
                  message: describeDioError(e),
                  onRetry: () => ref.invalidate(customerListProvider(_query)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CustomerCard extends StatelessWidget {
  const _CustomerCard({required this.customer});
  final Customer customer;

  String get _initials {
    final parts = customer.name.trim().split(RegExp(r'\s+'));
    if (parts.isEmpty || parts.first.isEmpty) return '?';
    if (parts.length == 1) return parts.first.substring(0, 1).toUpperCase();
    return (parts.first.substring(0, 1) + parts.last.substring(0, 1))
        .toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CustomerDetailScreen(customer: customer),
      )),
      borderRadius: BorderRadius.circular(AppRadius.md),
      child: Container(
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
                gradient: const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [AppColors.brand500, AppColors.brand700],
                ),
                borderRadius: BorderRadius.circular(AppRadius.pill),
              ),
              alignment: Alignment.center,
              child: Text(
                _initials,
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                ),
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    customer.name.isEmpty ? '—' : customer.name,
                    style: const TextStyle(
                      fontWeight: FontWeight.w600,
                      color: AppColors.slate900,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    [customer.phone, customer.email]
                        .where((s) => s.isNotEmpty)
                        .join(' · '),
                    style: const TextStyle(
                        fontSize: 12, color: AppColors.slate500),
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right_rounded, color: AppColors.slate400),
          ],
        ),
      ),
    );
  }
}

class CustomerDetailScreen extends ConsumerWidget {
  const CustomerDetailScreen({super.key, required this.customer});
  final Customer customer;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final salesAsync = ref.watch(customerSalesProvider(customer.id));

    return Scaffold(
      appBar: AppBar(title: Text(customer.name)),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          children: [
            Container(
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: AppColors.slate0,
                borderRadius: BorderRadius.circular(AppRadius.lg),
                border: Border.all(color: AppColors.slate200),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    customer.name,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      color: AppColors.slate900,
                    ),
                  ),
                  if (customer.phone.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    _DetailLine(
                        icon: Icons.phone_outlined, text: customer.phone),
                  ],
                  if (customer.email.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    _DetailLine(
                        icon: Icons.email_outlined, text: customer.email),
                  ],
                  if (customer.address.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    _DetailLine(
                        icon: Icons.location_on_outlined,
                        text: customer.address),
                  ],
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            const Text(
              'Recent sales',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: AppColors.slate500,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            salesAsync.when(
              data: (sales) {
                if (sales.isEmpty) {
                  return const EmptyState(
                    icon: Icons.receipt_long_outlined,
                    title: 'No sales recorded yet',
                  );
                }
                return Column(
                  children: [
                    for (final s in sales) ...[
                      _SaleRow(sale: s),
                      const SizedBox(height: AppSpacing.sm),
                    ]
                  ],
                );
              },
              loading: () => const Padding(
                padding: EdgeInsets.symmetric(vertical: 32),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (e, _) => ErrorState(
                message: describeDioError(e),
                onRetry: () =>
                    ref.invalidate(customerSalesProvider(customer.id)),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(icon, size: 16, color: AppColors.slate500),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            text,
            style: const TextStyle(color: AppColors.slate700, fontSize: 13.5),
          ),
        ),
      ],
    );
  }
}

class _SaleRow extends StatelessWidget {
  const _SaleRow({required this.sale});
  final Sale sale;

  Color get _statusColor {
    switch (sale.paymentStatus) {
      case 'Paid':
        return AppColors.success700;
      case 'Partial':
        return AppColors.warning700;
      default:
        return AppColors.danger700;
    }
  }

  Color get _statusBg {
    switch (sale.paymentStatus) {
      case 'Paid':
        return AppColors.success50;
      case 'Partial':
        return AppColors.warning50;
      default:
        return AppColors.danger50;
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '#${sale.invoiceNumber}',
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    color: AppColors.slate900,
                  ),
                ),
              ),
              Text(
                formatTzs(sale.totalAmount),
                style: const TextStyle(
                    fontWeight: FontWeight.w800, color: AppColors.slate900),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: _statusBg,
                  borderRadius: BorderRadius.circular(AppRadius.pill),
                ),
                child: Text(
                  sale.paymentStatus,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: _statusColor,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Text(
                sale.status,
                style: const TextStyle(
                    fontSize: 12, color: AppColors.slate500),
              ),
              const Spacer(),
              if (sale.createdAt != null)
                Text(
                  '${sale.createdAt!.year}-${sale.createdAt!.month.toString().padLeft(2, '0')}-${sale.createdAt!.day.toString().padLeft(2, '0')}',
                  style: const TextStyle(
                      fontSize: 11.5, color: AppColors.slate400),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
