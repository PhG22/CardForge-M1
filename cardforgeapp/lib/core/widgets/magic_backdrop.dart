import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Large translucent circles at the top of the screens (white, blue, black,
/// red and green). `intensity` (0 to 1) softens the effect on inner screens.
class MagicBackdrop extends StatelessWidget {
  const MagicBackdrop({super.key, this.intensity = 1});

  final double intensity;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Opacity(
        opacity: intensity.clamp(0.0, 1.0),
        child: const Stack(
          clipBehavior: Clip.hardEdge,
          children: [
            _Orb(left: -70, top: -90, size: 210, color: MagicColors.orbWhite),
            _Orb(left: 40, top: -120, size: 200, color: MagicColors.orbBlue),
            _Orb(left: 150, top: -100, size: 190, color: MagicColors.orbBlack),
            _Orb(right: 20, top: -90, size: 190, color: MagicColors.orbRed),
            _Orb(right: -70, top: -40, size: 190, color: MagicColors.orbGreen),
          ],
        ),
      ),
    );
  }
}

class _Orb extends StatelessWidget {
  const _Orb({
    this.left,
    this.right,
    required this.top,
    required this.size,
    required this.color,
  });

  final double? left;
  final double? right;
  final double top;
  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Positioned(
      left: left,
      right: right,
      top: top,
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(shape: BoxShape.circle, color: color),
      ),
    );
  }
}
