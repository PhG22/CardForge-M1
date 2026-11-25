import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../alerts/alert_style.dart';
import '../alerts/machine_alert.dart';

/// Settings: language and (debug builds only) developer tools.
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final deps = AppScope.of(context);
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return MagicScaffold(
      backdropHeight: 200,
      backdropIntensity: 0.5,
      appBar: AppBar(title: Text(l10n.settings)),
      body: ListenableBuilder(
        listenable: deps.locale,
        builder: (context, _) {
          final current = deps.locale.locale.languageCode;
          return ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Text(l10n.languageSection, style: text.titleMedium),
              const SizedBox(height: 8),
              MagicPanel(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Column(
                  children: [
                    // Language names are always shown in their own language.
                    _LanguageTile(
                      key: const ValueKey('language-en'),
                      name: 'English',
                      selected: current == 'en',
                      onTap: () => deps.locale.setLocale(const Locale('en')),
                    ),
                    const Divider(),
                    _LanguageTile(
                      key: const ValueKey('language-pt'),
                      name: 'Português',
                      selected: current == 'pt',
                      onTap: () => deps.locale.setLocale(const Locale('pt')),
                    ),
                  ],
                ),
              ),
              if (kDebugMode) ...[
                const SizedBox(height: 28),
                Text(l10n.developerTools, style: text.titleMedium),
                const SizedBox(height: 4),
                Text(
                  l10n.simulateAlerts,
                  style: text.bodyMedium
                      ?.copyWith(color: scheme.onSurfaceVariant),
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final type in AlertType.values)
                      ActionChip(
                        avatar: Icon(alertIcon(type),
                            size: 18, color: alertColor(type.severity)),
                        label: Text(l10n.alertTitle(type)),
                        onPressed: () => deps.alerts.add(
                          type,
                          machineName: 'CardForge-A1B2',
                          cardCount: 128,
                        ),
                      ),
                  ],
                ),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _LanguageTile extends StatelessWidget {
  const _LanguageTile({
    super.key,
    required this.name,
    required this.selected,
    required this.onTap,
  });

  final String name;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      title: Text(name),
      trailing: selected
          ? const Icon(Icons.check_rounded, color: MagicColors.gold)
          : null,
      onTap: onTap,
    );
  }
}
