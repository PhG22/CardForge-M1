import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import '../theme/app_theme.dart';

/// App name in Cinzel, uppercase, with wide letter spacing.
class MagicWordmark extends StatelessWidget {
  const MagicWordmark({super.key, this.fontSize = 30});

  final double fontSize;

  @override
  Widget build(BuildContext context) {
    return Text(
      'CARDFORGE',
      textAlign: TextAlign.center,
      style: GoogleFonts.cinzel(
        fontSize: fontSize,
        fontWeight: FontWeight.w700,
        color: MagicColors.parchment,
        letterSpacing: fontSize * 0.13,
      ),
    );
  }
}
