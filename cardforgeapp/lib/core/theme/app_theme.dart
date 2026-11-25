import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// CardForge palette: night background, gold frame and parchment text.
/// Every screen must use these colors (no loose hex values).
class MagicColors {
  static const Color background = Color(0xFF0E0B1A);
  static const Color surface = Color(0xFF1A1530);
  static const Color cardFace = Color(0xFF241C45);
  static const Color gold = Color(0xFFD4AF37);
  static const Color goldSoft = Color(0x80D4AF37); // gold at 50%
  static const Color parchment = Color(0xFFF3E6C4);
  static const Color parchmentMuted = Color(0xB3F3E6C4); // parchment at 70%
  static const Color danger = Color(0xFFE5645A);

  // Decorative orbs (an abstract nod to Magic's five colors)
  static const Color orbWhite = Color(0x55F2E8C9);
  static const Color orbBlue = Color(0x662F6FDB);
  static const Color orbBlack = Color(0x665B4B6B);
  static const Color orbRed = Color(0x66D64532);
  static const Color orbGreen = Color(0x662E9E5B);
}

class AppTheme {
  static ThemeData dark() {
    final scheme = ColorScheme.fromSeed(
      seedColor: MagicColors.gold,
      brightness: Brightness.dark,
    ).copyWith(
      primary: MagicColors.gold,
      onPrimary: MagicColors.background,
      surface: MagicColors.background,
      onSurface: MagicColors.parchment,
      onSurfaceVariant: MagicColors.parchmentMuted,
      primaryContainer: MagicColors.surface,
      onPrimaryContainer: MagicColors.gold,
      error: MagicColors.danger,
      outline: MagicColors.goldSoft,
    );

    // Headings use Cinzel; body text keeps the default font.
    final base = ThemeData(brightness: Brightness.dark, useMaterial3: true);
    final body = base.textTheme.apply(
      bodyColor: MagicColors.parchment,
      displayColor: MagicColors.parchment,
    );
    TextStyle heading(TextStyle? s, {FontWeight w = FontWeight.w700}) =>
        GoogleFonts.cinzel(
          textStyle: s,
          fontWeight: w,
          letterSpacing: 1.5,
          color: MagicColors.parchment,
        );
    final textTheme = body.copyWith(
      headlineLarge: heading(body.headlineLarge),
      headlineMedium: heading(body.headlineMedium),
      headlineSmall: heading(body.headlineSmall),
      titleLarge: heading(body.titleLarge, w: FontWeight.w600),
    );

    OutlineInputBorder pill(Color color, {double width = 1}) =>
        OutlineInputBorder(
          borderRadius: BorderRadius.circular(32),
          borderSide: BorderSide(color: color, width: width),
        );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      textTheme: textTheme,
      scaffoldBackgroundColor: MagicColors.background,
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: true,
        foregroundColor: MagicColors.parchment,
        iconTheme: const IconThemeData(color: MagicColors.gold),
        titleTextStyle: GoogleFonts.cinzel(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          letterSpacing: 3,
          color: MagicColors.parchment,
        ),
      ),
      drawerTheme: const DrawerThemeData(
        backgroundColor: MagicColors.background,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.horizontal(right: Radius.circular(24)),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        iconColor: MagicColors.gold,
        textColor: MagicColors.parchment,
      ),
      dividerTheme: const DividerThemeData(
        color: MagicColors.goldSoft,
        thickness: 1,
        space: 1,
      ),
      progressIndicatorTheme:
          const ProgressIndicatorThemeData(color: MagicColors.gold),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: MagicColors.surface,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        border: pill(MagicColors.goldSoft),
        enabledBorder: pill(MagicColors.goldSoft),
        focusedBorder: pill(MagicColors.gold, width: 2),
        errorBorder: pill(MagicColors.danger),
        focusedErrorBorder: pill(MagicColors.danger, width: 2),
        prefixIconColor: MagicColors.gold,
        suffixIconColor: MagicColors.gold,
        labelStyle: const TextStyle(color: MagicColors.parchmentMuted),
        floatingLabelStyle: const TextStyle(color: MagicColors.gold),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: MagicColors.gold,
          foregroundColor: MagicColors.background,
          minimumSize: const Size.fromHeight(52),
          shape: const StadiumBorder(),
          textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: MagicColors.parchment,
          side: const BorderSide(color: MagicColors.goldSoft),
          minimumSize: const Size.fromHeight(52),
          shape: const StadiumBorder(),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: MagicColors.gold),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? MagicColors.gold
              : Colors.transparent,
        ),
        checkColor: const WidgetStatePropertyAll(MagicColors.background),
        side: const BorderSide(color: MagicColors.gold, width: 1.5),
      ),
      snackBarTheme: const SnackBarThemeData(
        backgroundColor: MagicColors.cardFace,
        contentTextStyle: TextStyle(color: MagicColors.parchment),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }
}
