import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/auth_controller.dart';
import 'screens/desktop_webview.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';

bool get _isDesktopWebHost {
  if (kIsWeb) return false;
  return Platform.isWindows || Platform.isMacOS || Platform.isLinux;
}

class UmojaApp extends ConsumerWidget {
  const UmojaApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (_isDesktopWebHost) {
      // Desktop: load the production Django web app in an embedded WebView
      // so admins get the full server-rendered UI (dashboard, finance,
      // inventory) the same way they would in a browser.
      return MaterialApp(
        title: 'Umoja Pro',
        debugShowCheckedModeBanner: false,
        theme: buildAppTheme(),
        home: const DesktopWebView(),
      );
    }

    final auth = ref.watch(authControllerProvider);
    return MaterialApp(
      title: 'Umoja Pro',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      home: auth.when(
        data: (state) => switch (state.status) {
          AuthStatus.signedIn => const HomeShell(),
          AuthStatus.signedOut => const LoginScreen(),
          AuthStatus.unknown => const _Splash(),
        },
        loading: () => const _Splash(),
        error: (e, _) => const LoginScreen(),
      ),
    );
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: CircularProgressIndicator()),
    );
  }
}
