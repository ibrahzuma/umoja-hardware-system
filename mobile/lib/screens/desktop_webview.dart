import 'package:flutter/material.dart';
import 'package:webview_windows/webview_windows.dart';

import '../config.dart';

/// Desktop entry: fullscreen WebView pointed at the production Django site.
/// Used on Windows so the desktop app shows the same UI as a browser would,
/// without bundling a separate Flutter UI for the same screens.
class DesktopWebView extends StatefulWidget {
  const DesktopWebView({super.key});

  @override
  State<DesktopWebView> createState() => _DesktopWebViewState();
}

class _DesktopWebViewState extends State<DesktopWebView> {
  final _controller = WebviewController();
  String? _initError;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      await _controller.initialize();
      await _controller.setBackgroundColor(Colors.transparent);
      await _controller.setPopupWindowPolicy(WebviewPopupWindowPolicy.deny);
      await _controller.loadUrl(apiBaseUrl);
      if (mounted) setState(() => _ready = true);
    } catch (e) {
      if (mounted) setState(() => _initError = e.toString());
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_initError != null) {
      return Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.error_outline, size: 48, color: Colors.red),
                const SizedBox(height: 16),
                const Text(
                  'Failed to start WebView',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                Text(
                  _initError!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.black54),
                ),
                const SizedBox(height: 16),
                const Text(
                  'Microsoft Edge WebView2 Runtime is required.\n'
                  'Install from: https://developer.microsoft.com/microsoft-edge/webview2',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 12, color: Colors.black45),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return Scaffold(
      body: !_ready
          ? const Center(child: CircularProgressIndicator())
          : Webview(_controller),
    );
  }
}
