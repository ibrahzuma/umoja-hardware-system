import 'package:flutter/material.dart';

/// Design tokens matching the web app's redesigned style.css.
class AppColors {
  // Brand
  static const brand50 = Color(0xFFEEF1FF);
  static const brand100 = Color(0xFFDDE3FF);
  static const brand300 = Color(0xFF8A9BFF);
  static const brand500 = Color(0xFF4055E6);
  static const brand600 = Color(0xFF3142C2);
  static const brand700 = Color(0xFF28349E);

  // Slate
  static const slate0 = Color(0xFFFFFFFF);
  static const slate50 = Color(0xFFF6F8FB);
  static const slate100 = Color(0xFFEEF1F6);
  static const slate200 = Color(0xFFE1E6EE);
  static const slate300 = Color(0xFFC9D1DD);
  static const slate400 = Color(0xFF9AA4B5);
  static const slate500 = Color(0xFF6B7686);
  static const slate600 = Color(0xFF4A5363);
  static const slate700 = Color(0xFF303846);
  static const slate800 = Color(0xFF1F2632);
  static const slate900 = Color(0xFF141A23);

  // Semantic
  static const success50 = Color(0xFFE6F8F3);
  static const success500 = Color(0xFF16A37B);
  static const success700 = Color(0xFF0F7A5B);
  static const warning50 = Color(0xFFFFF5E5);
  static const warning500 = Color(0xFFD97706);
  static const warning700 = Color(0xFFA35804);
  static const danger50 = Color(0xFFFDECEC);
  static const danger500 = Color(0xFFD93B3B);
  static const danger700 = Color(0xFFA72929);
  static const info50 = Color(0xFFE8F4FD);
  static const info500 = Color(0xFF2784D9);
  static const info700 = Color(0xFF1D63A3);
}

class AppRadius {
  static const xs = 6.0;
  static const sm = 8.0;
  static const md = 10.0;
  static const lg = 14.0;
  static const xl = 20.0;
  static const pill = 999.0;
}

class AppSpacing {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;
  static const xxl = 32.0;
}

ThemeData buildAppTheme() {
  const seed = AppColors.brand500;

  final scheme = ColorScheme.fromSeed(
    seedColor: seed,
    brightness: Brightness.light,
    primary: AppColors.brand500,
    onPrimary: Colors.white,
    secondary: AppColors.brand700,
    surface: AppColors.slate0,
    onSurface: AppColors.slate800,
    error: AppColors.danger500,
  );

  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.slate50,
    fontFamily: 'Inter',
    visualDensity: VisualDensity.adaptivePlatformDensity,
  );

  return base.copyWith(
    textTheme: base.textTheme
        .apply(
          bodyColor: AppColors.slate800,
          displayColor: AppColors.slate900,
        )
        .copyWith(
          headlineLarge: const TextStyle(
            fontWeight: FontWeight.w700,
            fontSize: 26,
            letterSpacing: -0.4,
            color: AppColors.slate900,
          ),
          headlineMedium: const TextStyle(
            fontWeight: FontWeight.w700,
            fontSize: 22,
            letterSpacing: -0.3,
            color: AppColors.slate900,
          ),
          titleLarge: const TextStyle(
            fontWeight: FontWeight.w600,
            fontSize: 18,
            color: AppColors.slate900,
          ),
          titleMedium: const TextStyle(
            fontWeight: FontWeight.w600,
            fontSize: 15,
            color: AppColors.slate900,
          ),
          bodyLarge: const TextStyle(fontSize: 15, color: AppColors.slate800),
          bodyMedium: const TextStyle(fontSize: 14, color: AppColors.slate700),
          bodySmall: const TextStyle(fontSize: 12.5, color: AppColors.slate500),
          labelLarge: const TextStyle(
            fontSize: 13.5,
            fontWeight: FontWeight.w600,
          ),
        ),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.slate0,
      foregroundColor: AppColors.slate900,
      elevation: 0,
      scrolledUnderElevation: 0,
      surfaceTintColor: Colors.transparent,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontWeight: FontWeight.w700,
        fontSize: 18,
        color: AppColors.slate900,
      ),
      iconTheme: IconThemeData(color: AppColors.slate800),
    ),
    cardTheme: CardThemeData(
      color: AppColors.slate0,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.lg),
        side: const BorderSide(color: AppColors.slate200),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AppColors.brand500,
        foregroundColor: Colors.white,
        elevation: 0,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.slate800,
        side: const BorderSide(color: AppColors.slate300),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppColors.brand600,
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.brand500,
        foregroundColor: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.sm),
        ),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.slate0,
      hintStyle: const TextStyle(color: AppColors.slate400),
      labelStyle: const TextStyle(
        color: AppColors.slate700,
        fontWeight: FontWeight.w600,
        fontSize: 13,
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.sm),
        borderSide: const BorderSide(color: AppColors.slate300),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.sm),
        borderSide: const BorderSide(color: AppColors.slate300),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.sm),
        borderSide: const BorderSide(color: AppColors.brand500, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.sm),
        borderSide: const BorderSide(color: AppColors.danger500),
      ),
      prefixIconColor: AppColors.slate500,
      suffixIconColor: AppColors.slate500,
    ),
    chipTheme: ChipThemeData(
      backgroundColor: AppColors.slate100,
      labelStyle: const TextStyle(
        color: AppColors.slate700,
        fontWeight: FontWeight.w600,
        fontSize: 12,
      ),
      side: BorderSide.none,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
    ),
    dividerTheme: const DividerThemeData(
      color: AppColors.slate200,
      thickness: 1,
      space: 1,
    ),
    bottomNavigationBarTheme: const BottomNavigationBarThemeData(
      backgroundColor: AppColors.slate0,
      selectedItemColor: AppColors.brand600,
      unselectedItemColor: AppColors.slate500,
      type: BottomNavigationBarType.fixed,
      selectedLabelStyle: TextStyle(fontWeight: FontWeight.w600, fontSize: 12),
      unselectedLabelStyle: TextStyle(fontWeight: FontWeight.w500, fontSize: 12),
      elevation: 0,
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: AppColors.slate0,
      indicatorColor: AppColors.brand50,
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return TextStyle(
          fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          fontSize: 12,
          color: selected ? AppColors.brand700 : AppColors.slate500,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return IconThemeData(
          color: selected ? AppColors.brand600 : AppColors.slate500,
          size: 22,
        );
      }),
      elevation: 0,
      height: 68,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AppColors.slate900,
      contentTextStyle: const TextStyle(color: Colors.white, fontSize: 13.5),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.md),
      ),
      behavior: SnackBarBehavior.floating,
    ),
    listTileTheme: const ListTileThemeData(
      iconColor: AppColors.slate600,
      titleTextStyle: TextStyle(
        fontSize: 14.5,
        fontWeight: FontWeight.w600,
        color: AppColors.slate900,
      ),
      subtitleTextStyle: TextStyle(fontSize: 12.5, color: AppColors.slate500),
    ),
  );
}

/// Format a numeric amount as TZS currency.
String formatTzs(num amount) {
  final s = amount.toStringAsFixed(0);
  final buf = StringBuffer();
  for (var i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 == 0) buf.write(',');
    buf.write(s[i]);
  }
  return 'TZS $buf';
}
