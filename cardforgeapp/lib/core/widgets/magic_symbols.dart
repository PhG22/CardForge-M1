import 'dart:math' as math;

import 'package:flutter/material.dart';

/// The row of five gold-framed symbols (sun, drop, skull, flame, tree) shown at
/// the top of a screen. The image has a transparent background, so it sits
/// directly on the screen color.
class MagicSymbols extends StatelessWidget {
  const MagicSymbols({super.key});

  static const String asset = 'assets/images/mana_symbols.png';

  /// Width divided by height of the image file (1200 x 319).
  static const double aspectRatio = 1200 / 319;

  static const double _sidePadding = 24;
  static const double _maxWidth = 372;

  /// Height the row takes on a screen that is [screenWidth] wide, so screens can
  /// place their content below it.
  static double heightFor(double screenWidth) =>
      math.min(screenWidth - 2 * _sidePadding, _maxWidth) / aspectRatio;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: _sidePadding),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: _maxWidth),
          child: AspectRatio(
            aspectRatio: aspectRatio,
            child: Image.asset(
              asset,
              key: const ValueKey('mana-symbols'),
              fit: BoxFit.contain,
              excludeFromSemantics: true, // decorative
              errorBuilder: (_, __, ___) => const SizedBox.shrink(),
            ),
          ),
        ),
      ),
    );
  }
}
