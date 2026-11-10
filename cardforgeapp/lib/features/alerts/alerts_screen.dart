import 'package:flutter/material.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import 'alert_style.dart';
import 'alerts_controller.dart';
import 'machine_alert.dart';

/// Alerts center: every event reported by the machine, newest first.
class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final controller = AppScope.of(context).alerts;

    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final items = controller.alerts;
        return MagicScaffold(
          backdropHeight: 200,
          backdropIntensity: 0.5,
          appBar: AppBar(
            title: Text(l10n.alerts),
            actions: [
              IconButton(
                tooltip: l10n.markAllRead,
                icon: const Icon(Icons.done_all_rounded),
                onPressed: controller.unreadCount == 0
                    ? null
                    : controller.markAllRead,
              ),
              IconButton(
                tooltip: l10n.clearAll,
                icon: const Icon(Icons.delete_outline_rounded),
                onPressed: items.isEmpty ? null : controller.clear,
              ),
            ],
          ),
          body: items.isEmpty
              ? const _EmptyState()
              : ListView.separated(
                  padding: const EdgeInsets.all(20),
                  itemCount: items.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 12),
                  itemBuilder: (context, index) =>
                      _AlertTile(alert: items[index], controller: controller),
                ),
        );
      },
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.notifications_none_rounded,
                size: 48, color: MagicColors.goldSoft),
            const SizedBox(height: 12),
            Text(l10n.noAlerts, style: text.titleMedium),
            const SizedBox(height: 4),
            Text(
              l10n.noAlertsHint,
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

class _AlertTile extends StatelessWidget {
  const _AlertTile({required this.alert, required this.controller});

  final MachineAlert alert;
  final AlertsController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final color = alertColor(alert.type.severity);

    return InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: () => controller.markRead(alert.id),
      child: MagicPanel(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: color.withAlpha(40),
              ),
              child: Icon(alertIcon(alert.type), color: color, size: 22),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    l10n.alertTitle(alert.type),
                    style: text.titleSmall
                        ?.copyWith(fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    l10n.alertMessage(alert),
                    style: text.bodyMedium
                        ?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    TimeOfDay.fromDateTime(alert.createdAt).format(context),
                    style: text.labelSmall?.copyWith(color: scheme.outline),
                  ),
                ],
              ),
            ),
            if (!alert.read)
              Container(
                key: const ValueKey('unread-dot'),
                width: 8,
                height: 8,
                margin: const EdgeInsets.only(top: 6, left: 8),
                decoration: const BoxDecoration(
                  shape: BoxShape.circle,
                  color: MagicColors.gold,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
