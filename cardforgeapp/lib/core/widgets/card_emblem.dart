import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// App emblem: three fanned cards with a gold frame.
class CardEmblem extends StatelessWidget {
  const CardEmblem({super.key, this.scale = 1});

  final double scale;

  @override
  Widget build(BuildContext context) {
    final w = 92 * scale;
    final h = 128 * scale;
    return SizedBox(
      width: w * 1.9,
      height: h * 1.12,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Transform.rotate(angle: -0.22, child: _card(w, h, front: false)),
          Transform.rotate(angle: 0.22, child: _card(w, h, front: false)),
          _card(w, h, front: true),
        ],
      ),
    );
  }

  Widget _card(double w, double h, {required bool front}) {
    return Container(
      width: w,
      height: h,
      decoration: BoxDecoration(
        color: front ? MagicColors.cardFace : MagicColors.surface,
        borderRadius: BorderRadius.circular(10 * scale),
        border: Border.all(
          color: front ? MagicColors.gold : MagicColors.goldSoft,
          width: 2.5 * scale,
        ),
      ),
      child: front
          ? Padding(
              padding: EdgeInsets.all(7 * scale),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(5 * scale),
                  border: Border.all(color: MagicColors.goldSoft, width: 1),
                ),
                child: const SizedBox.expand(),
              ),
            )
          : null,
    );
  }
}
