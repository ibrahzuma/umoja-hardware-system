import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/auth_controller.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';

class UmojaAdminApp extends ConsumerWidget {
  const UmojaAdminApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    return MaterialApp(
      title: 'Umoja Approvals',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      home: auth.when(
        loading: () =>
            const Scaffold(body: Center(child: CircularProgressIndicator())),
        error: (e, _) => const LoginScreen(),
        data: (state) => state.status == AuthStatus.signedIn
            ? const HomeScreen()
            : const LoginScreen(),
      ),
    );
  }
}
