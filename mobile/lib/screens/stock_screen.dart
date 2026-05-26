import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_client.dart';
import '../data/repositories.dart';
import '../models/product.dart';
import '../models/stock.dart';
import '../screens/home_shell.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

class StockScreen extends ConsumerStatefulWidget {
  const StockScreen({super.key});

  @override
  ConsumerState<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends ConsumerState<StockScreen> {
  String _query = '';
  bool _lowOnly = false;

  @override
  Widget build(BuildContext context) {
    final stocksAsync = ref.watch(stockListProvider);
    final productsAsync = ref.watch(productListProvider(''));

    return Scaffold(
      appBar: AppBar(
        title: const Text('Stock'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: () {
              ref.invalidate(stockListProvider);
              ref.invalidate(productListProvider(''));
            },
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
                  AppSpacing.lg, AppSpacing.md, AppSpacing.lg, 0),
              child: Column(
                children: [
                  TextField(
                    decoration: const InputDecoration(
                      hintText: 'Search by product name…',
                      prefixIcon: Icon(Icons.search_rounded),
                      isDense: true,
                    ),
                    onChanged: (v) => setState(() => _query = v.trim()),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Row(
                    children: [
                      FilterChip(
                        label: const Text('Low stock only'),
                        avatar: Icon(
                          _lowOnly ? Icons.check_rounded : Icons.warning_amber_rounded,
                          size: 16,
                          color: _lowOnly ? AppColors.brand700 : AppColors.warning700,
                        ),
                        selected: _lowOnly,
                        onSelected: (v) => setState(() => _lowOnly = v),
                        backgroundColor: AppColors.warning50,
                        selectedColor: AppColors.brand50,
                        labelStyle: TextStyle(
                          color: _lowOnly ? AppColors.brand700 : AppColors.warning700,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            Expanded(
              child: stocksAsync.when(
                data: (stocks) {
                  return productsAsync.when(
                    data: (products) {
                      final byId = {for (final p in products) p.id: p};
                      final filtered = stocks.where((s) {
                        if (_lowOnly && !s.isLow) return false;
                        if (_query.isEmpty) return true;
                        final p = byId[s.productId];
                        return p != null &&
                            p.name.toLowerCase().contains(_query.toLowerCase());
                      }).toList()
                        ..sort((a, b) {
                          if (a.isLow && !b.isLow) return -1;
                          if (!a.isLow && b.isLow) return 1;
                          return a.quantity.compareTo(b.quantity);
                        });

                      if (filtered.isEmpty) {
                        return const EmptyState(
                          icon: Icons.inventory_2_outlined,
                          title: 'No stock to show',
                          message: 'Try clearing the filter or search.',
                        );
                      }

                      return RefreshIndicator(
                        onRefresh: () async {
                          ref.invalidate(stockListProvider);
                          ref.invalidate(productListProvider(''));
                          await ref.read(stockListProvider.future);
                        },
                        child: ListView.separated(
                          padding: const EdgeInsets.fromLTRB(
                              AppSpacing.lg, 0, AppSpacing.lg, AppSpacing.lg),
                          itemCount: filtered.length,
                          separatorBuilder: (_, __) =>
                              const SizedBox(height: AppSpacing.sm),
                          itemBuilder: (_, i) {
                            final s = filtered[i];
                            return _StockRow(
                                stock: s, product: byId[s.productId]);
                          },
                        ),
                      );
                    },
                    loading: () =>
                        const Center(child: CircularProgressIndicator()),
                    error: (e, _) => ErrorState(
                      message: describeDioError(e),
                      onRetry: () => ref.invalidate(productListProvider('')),
                    ),
                  );
                },
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => ErrorState(
                  message: describeDioError(e),
                  onRetry: () => ref.invalidate(stockListProvider),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StockRow extends StatelessWidget {
  const _StockRow({required this.stock, required this.product});

  final Stock stock;
  final Product? product;

  @override
  Widget build(BuildContext context) {
    final tint = stock.isLow ? AppColors.danger500 : AppColors.success500;
    final bg = stock.isLow ? AppColors.danger50 : AppColors.success50;

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
              color: bg,
              borderRadius: BorderRadius.circular(AppRadius.sm),
            ),
            child: Icon(Icons.inventory_2_outlined, color: tint, size: 20),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  product?.name ?? 'Product #${stock.productId}',
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    color: AppColors.slate900,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  product?.sku ?? '—',
                  style: const TextStyle(
                      fontSize: 12, color: AppColors.slate500),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '${stock.quantity}',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w800,
                  color: stock.isLow ? AppColors.danger700 : AppColors.slate900,
                ),
              ),
              Text(
                stock.isLow
                    ? 'Low · threshold ${stock.lowStockThreshold}'
                    : 'In stock',
                style: TextStyle(
                  fontSize: 11,
                  color: stock.isLow
                      ? AppColors.danger700
                      : AppColors.success700,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
