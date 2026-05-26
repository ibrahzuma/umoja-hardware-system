import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/api_client.dart';
import '../data/repositories.dart';
import '../models/branch.dart';
import '../models/product.dart';
import '../screens/home_shell.dart';
import '../theme/app_theme.dart';
import '../widgets/empty_state.dart';

class CartLine {
  CartLine({required this.product, required this.quantity});
  final Product product;
  int quantity;

  double get subtotal => product.price * quantity;
}

class PosScreen extends ConsumerStatefulWidget {
  const PosScreen({super.key});

  @override
  ConsumerState<PosScreen> createState() => _PosScreenState();
}

class _PosScreenState extends ConsumerState<PosScreen> {
  final _cart = <int, CartLine>{};
  final _customerName = TextEditingController(text: 'Walk-in Customer');
  String _paymentMethod = 'cash';
  Branch? _branch;
  bool _submitting = false;

  double get _total => _cart.values.fold(0, (s, l) => s + l.subtotal);

  void _add(Product p) {
    setState(() {
      final existing = _cart[p.id];
      if (existing != null) {
        existing.quantity += 1;
      } else {
        _cart[p.id] = CartLine(product: p, quantity: 1);
      }
    });
  }

  void _changeQty(Product p, int delta) {
    setState(() {
      final line = _cart[p.id];
      if (line == null) return;
      line.quantity += delta;
      if (line.quantity <= 0) _cart.remove(p.id);
    });
  }

  Future<void> _openProductPicker() async {
    final picked = await showModalBottomSheet<Product>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.slate0,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.lg)),
      ),
      builder: (_) => const _ProductPickerSheet(),
    );
    if (picked != null) _add(picked);
  }

  Future<void> _checkout() async {
    if (_cart.isEmpty) return;
    if (_branch == null) {
      _snack('Pick a branch first');
      return;
    }
    setState(() => _submitting = true);
    try {
      final repo = ref.read(saleRepoProvider);
      final items = _cart.values
          .map((l) => {
                'product': l.product.id,
                'quantity': l.quantity,
                'price_at_sale': l.product.price,
              })
          .toList();
      await repo.create(
        branchId: _branch!.id,
        items: items,
        customerName: _customerName.text.trim().isEmpty
            ? 'Walk-in Customer'
            : _customerName.text.trim(),
        paymentDetails: {
          'amount': _total,
          'method': _paymentMethod,
        },
      );
      if (!mounted) return;
      setState(() {
        _cart.clear();
        _customerName.text = 'Walk-in Customer';
        _paymentMethod = 'cash';
      });
      _snack('Sale recorded · TZS ${_total.toStringAsFixed(0)}');
    } catch (e) {
      _snack(describeDioError(e));
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _snack(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    final branches = ref.watch(branchListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Point of sale'),
        actions: [buildProfileMenu(ref)],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg, AppSpacing.md, AppSpacing.lg, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              branches.when(
                data: (list) {
                  if (list.isEmpty) {
                    return const _Notice(
                        text: 'No branches available. Ask an admin to create one.');
                  }
                  _branch ??= list.first;
                  return _BranchSelector(
                    branches: list,
                    selected: _branch!,
                    onChanged: (b) => setState(() => _branch = b),
                  );
                },
                loading: () => const LinearProgressIndicator(minHeight: 2),
                error: (e, _) =>
                    _Notice(text: describeDioError(e), isError: true),
              ),
              const SizedBox(height: AppSpacing.md),
              Expanded(
                child: _cart.isEmpty
                    ? EmptyState(
                        icon: Icons.add_shopping_cart_rounded,
                        title: 'Cart is empty',
                        message: 'Tap "Add product" to start a new sale.',
                        action: FilledButton.icon(
                          onPressed: _openProductPicker,
                          icon: const Icon(Icons.add_rounded),
                          label: const Text('Add product'),
                        ),
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        itemCount: _cart.length,
                        separatorBuilder: (_, __) =>
                            const SizedBox(height: AppSpacing.sm),
                        itemBuilder: (_, i) {
                          final line = _cart.values.elementAt(i);
                          return _CartLineCard(
                            line: line,
                            onIncrement: () => _changeQty(line.product, 1),
                            onDecrement: () => _changeQty(line.product, -1),
                            onRemove: () => setState(
                                () => _cart.remove(line.product.id)),
                          );
                        },
                      ),
              ),
              if (_cart.isNotEmpty) _CheckoutBar(
                customerNameCtrl: _customerName,
                paymentMethod: _paymentMethod,
                onPaymentChanged: (v) => setState(() => _paymentMethod = v),
                total: _total,
                submitting: _submitting,
                onCheckout: _checkout,
                onAddMore: _openProductPicker,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _BranchSelector extends StatelessWidget {
  const _BranchSelector({
    required this.branches,
    required this.selected,
    required this.onChanged,
  });

  final List<Branch> branches;
  final Branch selected;
  final ValueChanged<Branch> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: AppColors.slate0,
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.slate200),
      ),
      child: Row(
        children: [
          const Icon(Icons.store_outlined, size: 18, color: AppColors.slate500),
          const SizedBox(width: 8),
          const Text(
            'Branch',
            style: TextStyle(
                fontWeight: FontWeight.w600,
                color: AppColors.slate600,
                fontSize: 12.5),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: DropdownButtonHideUnderline(
              child: DropdownButton<Branch>(
                isExpanded: true,
                value: selected,
                items: branches
                    .map((b) => DropdownMenuItem(value: b, child: Text(b.name)))
                    .toList(),
                onChanged: (v) {
                  if (v != null) onChanged(v);
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _CartLineCard extends StatelessWidget {
  const _CartLineCard({
    required this.line,
    required this.onIncrement,
    required this.onDecrement,
    required this.onRemove,
  });

  final CartLine line;
  final VoidCallback onIncrement;
  final VoidCallback onDecrement;
  final VoidCallback onRemove;

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
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  line.product.name,
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    color: AppColors.slate900,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  '${line.product.sku ?? "—"} · ${formatTzs(line.product.price)}',
                  style: const TextStyle(
                      fontSize: 12, color: AppColors.slate500),
                ),
              ],
            ),
          ),
          _QtyStepper(
            quantity: line.quantity,
            onIncrement: onIncrement,
            onDecrement: onDecrement,
          ),
          const SizedBox(width: AppSpacing.md),
          SizedBox(
            width: 88,
            child: Text(
              formatTzs(line.subtotal),
              textAlign: TextAlign.right,
              style: const TextStyle(
                fontWeight: FontWeight.w700,
                color: AppColors.slate900,
              ),
            ),
          ),
          IconButton(
            onPressed: onRemove,
            icon: const Icon(Icons.close_rounded, size: 18),
            color: AppColors.slate500,
            visualDensity: VisualDensity.compact,
          ),
        ],
      ),
    );
  }
}

class _QtyStepper extends StatelessWidget {
  const _QtyStepper({
    required this.quantity,
    required this.onIncrement,
    required this.onDecrement,
  });

  final int quantity;
  final VoidCallback onIncrement;
  final VoidCallback onDecrement;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.slate100,
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Row(
        children: [
          _stepBtn(Icons.remove_rounded, onDecrement),
          SizedBox(
            width: 32,
            child: Text(
              '$quantity',
              textAlign: TextAlign.center,
              style: const TextStyle(
                  fontWeight: FontWeight.w700, color: AppColors.slate900),
            ),
          ),
          _stepBtn(Icons.add_rounded, onIncrement),
        ],
      ),
    );
  }

  Widget _stepBtn(IconData icon, VoidCallback onTap) {
    return InkResponse(
      onTap: onTap,
      radius: 18,
      child: Padding(
        padding: const EdgeInsets.all(6),
        child: Icon(icon, size: 16, color: AppColors.slate700),
      ),
    );
  }
}

class _CheckoutBar extends StatelessWidget {
  const _CheckoutBar({
    required this.customerNameCtrl,
    required this.paymentMethod,
    required this.onPaymentChanged,
    required this.total,
    required this.submitting,
    required this.onCheckout,
    required this.onAddMore,
  });

  final TextEditingController customerNameCtrl;
  final String paymentMethod;
  final ValueChanged<String> onPaymentChanged;
  final double total;
  final bool submitting;
  final VoidCallback onCheckout;
  final VoidCallback onAddMore;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.slate0,
        borderRadius: BorderRadius.circular(AppRadius.lg),
        border: Border.all(color: AppColors.slate200),
        boxShadow: const [
          BoxShadow(
            color: Color(0x10141A23),
            blurRadius: 18,
            offset: Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: customerNameCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Customer',
                    prefixIcon: Icon(Icons.person_outline_rounded),
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              SizedBox(
                width: 150,
                child: DropdownButtonFormField<String>(
                  initialValue: paymentMethod,
                  decoration: const InputDecoration(
                    labelText: 'Payment',
                    isDense: true,
                  ),
                  items: const [
                    DropdownMenuItem(value: 'cash', child: Text('Cash')),
                    DropdownMenuItem(value: 'mobile', child: Text('Mobile')),
                    DropdownMenuItem(value: 'bank', child: Text('Bank')),
                    DropdownMenuItem(value: 'credit', child: Text('Credit')),
                  ],
                  onChanged: (v) {
                    if (v != null) onPaymentChanged(v);
                  },
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: submitting ? null : onAddMore,
                icon: const Icon(Icons.add_rounded, size: 18),
                label: const Text('Add'),
              ),
              const Spacer(),
              const Text(
                'Total',
                style: TextStyle(color: AppColors.slate500, fontSize: 12.5),
              ),
              const SizedBox(width: 10),
              Text(
                formatTzs(total),
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 18,
                  color: AppColors.slate900,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          SizedBox(
            height: 46,
            width: double.infinity,
            child: ElevatedButton(
              onPressed: submitting ? null : onCheckout,
              child: submitting
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white),
                    )
                  : Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Icon(Icons.check_circle_outline_rounded,
                            size: 18),
                        const SizedBox(width: 8),
                        Text('Charge ${formatTzs(total)}'),
                      ],
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text, this.isError = false});
  final String text;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: isError ? AppColors.danger50 : AppColors.warning50,
        borderRadius: BorderRadius.circular(AppRadius.sm),
      ),
      child: Row(
        children: [
          Icon(
            isError ? Icons.error_outline_rounded : Icons.info_outline_rounded,
            color: isError ? AppColors.danger700 : AppColors.warning700,
            size: 18,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: isError ? AppColors.danger700 : AppColors.warning700,
                fontSize: 13,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Product picker bottom sheet
// ---------------------------------------------------------------------------

class _ProductPickerSheet extends ConsumerStatefulWidget {
  const _ProductPickerSheet();

  @override
  ConsumerState<_ProductPickerSheet> createState() =>
      _ProductPickerSheetState();
}

class _ProductPickerSheetState extends ConsumerState<_ProductPickerSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final products = ref.watch(productListProvider(_query));
    final viewInsets = MediaQuery.of(context).viewInsets.bottom;

    return Padding(
      padding: EdgeInsets.only(bottom: viewInsets),
      child: FractionallySizedBox(
        heightFactor: 0.85,
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.slate200,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
              child: TextField(
                autofocus: true,
                decoration: const InputDecoration(
                  hintText: 'Search products…',
                  prefixIcon: Icon(Icons.search_rounded),
                  isDense: true,
                ),
                onChanged: (v) => setState(() => _query = v.trim()),
              ),
            ),
            Expanded(
              child: products.when(
                data: (list) {
                  if (list.isEmpty) {
                    return const EmptyState(
                      icon: Icons.inventory_2_outlined,
                      title: 'No products found',
                    );
                  }
                  return ListView.separated(
                    itemCount: list.length,
                    separatorBuilder: (_, __) =>
                        const Divider(height: 1, color: AppColors.slate100),
                    itemBuilder: (_, i) {
                      final p = list[i];
                      return ListTile(
                        onTap: () => Navigator.pop(context, p),
                        leading: Container(
                          width: 36,
                          height: 36,
                          decoration: BoxDecoration(
                            color: AppColors.brand50,
                            borderRadius:
                                BorderRadius.circular(AppRadius.sm),
                          ),
                          child: const Icon(Icons.inventory_2_outlined,
                              color: AppColors.brand600, size: 18),
                        ),
                        title: Text(p.name),
                        subtitle:
                            Text('${p.sku ?? "—"} · ${formatTzs(p.price)}'),
                        trailing: const Icon(Icons.add_circle_outline_rounded,
                            color: AppColors.brand500),
                      );
                    },
                  );
                },
                loading: () =>
                    const Center(child: CircularProgressIndicator()),
                error: (e, _) => ErrorState(
                  message: describeDioError(e),
                  onRetry: () => ref.invalidate(productListProvider(_query)),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
