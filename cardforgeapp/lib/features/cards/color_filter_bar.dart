import 'package:flutter/material.dart';

import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import 'card_models.dart';

/// The dot drawn next to a color group.
Color colorGroupSwatch(ColorGroup group) => switch (group) {
      ColorGroup.white => const Color(0xFFF2E8C9),
      ColorGroup.blue => const Color(0xFF2F6FDB),
      ColorGroup.black => const Color(0xFF5B4B6B),
      ColorGroup.red => const Color(0xFFD64532),
      ColorGroup.green => const Color(0xFF2E9E5B),
      ColorGroup.colorless => const Color(0xFF9E9A92),
      ColorGroup.multicolor => MagicColors.gold,
    };

/// A small round dot in the color of [group].
class ColorDot extends StatelessWidget {
  const ColorDot({super.key, required this.group, this.size = 12});

  final ColorGroup group;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: colorGroupSwatch(group),
        shape: BoxShape.circle,
        border: Border.all(color: MagicColors.goldSoft),
      ),
    );
  }
}

/// The color filter: "All colors" plus one chip per color group, each with
/// its count. [selected] null = "All colors". With [notIdentified] set, a last
/// chip shows the cards that were not identified (run results only).
class ColorFilterBar extends StatelessWidget {
  const ColorFilterBar({
    super.key,
    required this.counts,
    required this.selected,
    required this.onSelected,
    this.notIdentified,
    this.notIdentifiedSelected = false,
    this.onNotIdentifiedSelected,
  });

  final Map<ColorGroup, int> counts;
  final ColorGroup? selected;
  final ValueChanged<ColorGroup?> onSelected;
  final int? notIdentified;
  final bool notIdentifiedSelected;
  final VoidCallback? onNotIdentifiedSelected;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final all = counts.values.fold<int>(0, (sum, n) => sum + n) +
        (notIdentified ?? 0);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        ChoiceChip(
          key: const ValueKey('color-all'),
          label: Text('${l10n.allColors}  $all'),
          selected: selected == null && !notIdentifiedSelected,
          onSelected: (_) => onSelected(null),
        ),
        for (final group in ColorGroup.values)
          ChoiceChip(
            key: ValueKey('color-${group.name}'),
            avatar: ColorDot(group: group),
            label: Text('${l10n.colorGroupLabel(group)}  ${counts[group] ?? 0}'),
            selected: selected == group,
            onSelected: (_) => onSelected(group),
          ),
        if (notIdentified != null)
          ChoiceChip(
            key: const ValueKey('color-not-identified'),
            avatar: const Icon(Icons.help_outline_rounded,
                size: 16, color: MagicColors.goldSoft),
            label: Text('${l10n.notIdentifiedLabel}  $notIdentified'),
            selected: notIdentifiedSelected,
            onSelected: (_) => onNotIdentifiedSelected?.call(),
          ),
      ],
    );
  }
}
