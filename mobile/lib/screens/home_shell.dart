import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/auth_controller.dart';
import 'customers_screen.dart';
import 'pos_screen.dart';
import 'quotations_screen.dart';
import 'stock_screen.dart';

class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});

  @override
  ConsumerState<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends ConsumerState<HomeShell> {
  int _index = 0;

  static const _tabs = <_Tab>[
    _Tab('POS', Icons.point_of_sale_outlined, Icons.point_of_sale_rounded),
    _Tab('Stock', Icons.inventory_2_outlined, Icons.inventory_2_rounded),
    _Tab('Quotes', Icons.description_outlined, Icons.description_rounded),
    _Tab('Customers', Icons.people_outline_rounded, Icons.people_rounded),
  ];

  @override
  Widget build(BuildContext context) {
    final pages = const [
      PosScreen(),
      StockScreen(),
      QuotationsScreen(),
      CustomersScreen(),
    ];

    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in _tabs)
            NavigationDestination(
              icon: Icon(t.icon),
              selectedIcon: Icon(t.selectedIcon),
              label: t.label,
            ),
        ],
      ),
    );
  }
}

class _Tab {
  const _Tab(this.label, this.icon, this.selectedIcon);
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

/// Shared profile menu in app bars (sign-out).
PopupMenuButton<String> buildProfileMenu(WidgetRef ref) {
  return PopupMenuButton<String>(
    icon: const Icon(Icons.account_circle_outlined),
    onSelected: (v) {
      if (v == 'signout') ref.read(authControllerProvider.notifier).signOut();
    },
    itemBuilder: (_) => const [
      PopupMenuItem(
        value: 'signout',
        child: Row(
          children: [
            Icon(Icons.logout_rounded, size: 18),
            SizedBox(width: 10),
            Text('Sign out'),
          ],
        ),
      ),
    ],
  );
}
