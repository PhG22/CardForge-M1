import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Standard panel for grouping content: dark purple fill and gold border.
/// Use it for lists, forms and status cards on the next screens.
///
/// It is a [Material] (not a coloured Container) so that ListTiles and
/// InkWells inside it show their ink splashes. Flutter 3.47+ reports an error
/// for a ListTile placed on a coloured DecoratedBox.
class MagicPanel extends StatelessWidget {
  const MagicPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: Material(
        color: MagicColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: MagicColors.goldSoft),
        ),
        clipBehavior: Clip.antiAlias,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}
