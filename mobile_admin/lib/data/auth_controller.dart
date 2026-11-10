import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api_client.dart';

enum AuthStatus { unknown, signedOut, signedIn }

class AuthState {
  const AuthState({required this.status, this.username, this.error});

  final AuthStatus status;
  final String? username;
  final String? error;
}

class AuthController extends AsyncNotifier<AuthState> {
  @override
  Future<AuthState> build() async {
    final storage = ref.read(tokenStorageProvider);
    final token = await storage.read();
    return AuthState(
      status: (token == null || token.isEmpty)
          ? AuthStatus.signedOut
          : AuthStatus.signedIn,
    );
  }

  Future<void> signIn(String username, String password) async {
    state = const AsyncValue.loading();
    try {
      final api = ref.read(apiClientProvider);
      await api.login(username.trim(), password);
      state = AsyncValue.data(
        AuthState(status: AuthStatus.signedIn, username: username.trim()),
      );
    } catch (e) {
      state = AsyncValue.data(
        AuthState(status: AuthStatus.signedOut, error: describeDioError(e)),
      );
    }
  }

  Future<void> signOut() async {
    await ref.read(apiClientProvider).logout();
    state = const AsyncValue.data(AuthState(status: AuthStatus.signedOut));
  }
}

final authControllerProvider =
    AsyncNotifierProvider<AuthController, AuthState>(AuthController.new);
