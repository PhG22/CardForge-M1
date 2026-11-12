import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/card_emblem.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/magic_wordmark.dart';
import '../auth/auth_service.dart';
import '../machine/machine_control_screen.dart' show ConnectionChip;

/// Home. It shows the linked machine (tap it for machine control), the alerts
/// bell and the side menu; the menu items that have no screen yet say
/// "coming soon".
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final deps = AppScope.of(context);
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return MagicScaffold(
      backdropHeight: 240,
      backdropIntensity: 0.6,
      appBar: AppBar(
        title: const Text('CARDFORGE'),
        actions: [
          ListenableBuilder(
            listenable: deps.alerts,
            builder: (context, _) => IconButton(
              tooltip: l10n.alerts,
              onPressed: () => context.push('/alerts'),
              icon: Badge(
                isLabelVisible: deps.alerts.unreadCount > 0,
                label: Text('${deps.alerts.unreadCount}'),
                child: const Icon(Icons.notifications_outlined),
              ),
            ),
          ),
        ],
      ),
      drawer: const _HomeDrawer(),
      body: ListenableBuilder(
        listenable: Listenable.merge([deps.machineStore, deps.machine]),
        builder: (context, _) {
          final machine = deps.machineStore.machine;
          return ListView(
            padding: const EdgeInsets.all(20),
            children: [
              const SizedBox(height: 8),
              const Center(child: CardEmblem(scale: 0.7)),
              const SizedBox(height: 24),
              MagicPanel(
                child: machine == null
                    ? Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(l10n.noMachineTitle,
                              style: text.titleMedium,
                              textAlign: TextAlign.center),
                          const SizedBox(height: 8),
                          Text(
                            l10n.noMachineBody,
                            style: text.bodyMedium
                                ?.copyWith(color: scheme.onSurfaceVariant),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 20),
                          FilledButton(
                            onPressed: () => context.push('/link-machine'),
                            child: Text(l10n.linkMachine),
                          ),
                        ],
                      )
                    : InkWell(
                        borderRadius: BorderRadius.circular(12),
                        onTap: () => context.push('/machine'),
                        child: Row(
                          children: [
                            const Icon(Icons.precision_manufacturing_outlined,
                                color: MagicColors.gold, size: 32),
                            const SizedBox(width: 16),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(machine.name, style: text.titleMedium),
                                  const SizedBox(height: 2),
                                  Text(
                                    l10n.machineLinkedBody,
                                    style: text.bodyMedium?.copyWith(
                                        color: scheme.onSurfaceVariant),
                                  ),
                                  const SizedBox(height: 8),
                                  ConnectionChip(
                                      connection: deps.machine.connection),
                                ],
                              ),
                            ),
                            const Icon(Icons.chevron_right_rounded,
                                color: MagicColors.gold),
                          ],
                        ),
                      ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _HomeDrawer extends StatelessWidget {
  const _HomeDrawer();

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;

    // (icon, label, route). A null route means "not built yet".
    final items = <(IconData, String, String?)>[
      (Icons.precision_manufacturing_outlined, l10n.menuMachineControl, '/machine'),
      (Icons.history_rounded, l10n.menuRuns, '/runs'),
      (Icons.folder_copy_outlined, l10n.menuCardsInFolders, '/cards'),
      (Icons.table_chart_outlined, l10n.menuCsvReport, '/export'),
      (Icons.rule_folder_outlined, l10n.menuSortingRules, null),
      (Icons.link_rounded, l10n.linkMachine, '/link-machine'),
      (Icons.notifications_outlined, l10n.alerts, '/alerts'),
      (Icons.settings_outlined, l10n.settings, '/settings'),
    ];

    return Drawer(
      child: SafeArea(
        child: Column(
          children: [
            const SizedBox(height: 24),
            const CardEmblem(scale: 0.6),
            const SizedBox(height: 12),
            const MagicWordmark(fontSize: 22),
            const SizedBox(height: 20),
            const Divider(),
            Expanded(
              child: ListView(
                children: [
                  for (final (icon, label, route) in items)
                    ListTile(
                      leading: Icon(icon),
                      title: Text(label),
                      onTap: () {
                        final router = GoRouter.of(context);
                        final messenger = ScaffoldMessenger.of(context);
                        Navigator.of(context).pop();
                        if (route != null) {
                          router.push(route);
                        } else {
                          messenger.showSnackBar(
                            SnackBar(content: Text(l10n.comingSoon(label))),
                          );
                        }
                      },
                    ),
                ],
              ),
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.logout_rounded),
              title: Text(l10n.signOut),
              onTap: () async {
                await AuthService.instance.signOut();
                if (context.mounted) context.go('/login');
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }
}
