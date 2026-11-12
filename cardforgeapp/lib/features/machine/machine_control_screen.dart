import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import 'api/machine_api_models.dart';
import 'machine_controller.dart';

/// Machine control: connection to the machine over Wi-Fi, start / stop a
/// run, live progress, and diagnostics for testing the hardware.
/// The logic lives in [MachineController]; this widget only draws it.
class MachineControlScreen extends StatelessWidget {
  const MachineControlScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final controller = AppScope.of(context).machine;

    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final machine = controller.machine;
        return MagicScaffold(
          backdropHeight: 220,
          backdropIntensity: 0.5,
          appBar: AppBar(
            title: Text(l10n.menuMachineControl),
            actions: [
              if (machine != null)
                PopupMenuButton<String>(
                  onSelected: (_) => _confirmForget(context, controller),
                  itemBuilder: (_) => [
                    PopupMenuItem(
                      value: 'forget',
                      child: Text(l10n.forgetMachine),
                    ),
                  ],
                ),
            ],
          ),
          body: machine == null
              ? ListView(
                  padding: const EdgeInsets.all(20),
                  children: [_NoMachinePanel()],
                )
              : RefreshIndicator(
                  onRefresh: () async {
                    await controller.reconnect();
                    if (controller.isOnline) await controller.loadHardware();
                  },
                  child: ListView(
                    padding: const EdgeInsets.all(20),
                    children: [
                      _MachineHeader(controller: controller),
                      const SizedBox(height: 16),
                      ..._body(controller),
                    ],
                  ),
                ),
        );
      },
    );
  }

  List<Widget> _body(MachineController controller) {
    switch (controller.connection) {
      case MachineConnection.none:
        return const [];
      case MachineConnection.noAddress:
        return [_FindPanel(controller: controller, offline: false)];
      case MachineConnection.offline:
        return [_FindPanel(controller: controller, offline: true)];
      case MachineConnection.connecting when controller.snapshot == null:
        return const [
          Padding(
            padding: EdgeInsets.all(24),
            child: Center(child: CircularProgressIndicator()),
          ),
        ];
      case MachineConnection.connecting || MachineConnection.online:
        return [
          _RunPanel(controller: controller),
          if (controller.recentCards.isNotEmpty) ...[
            const SizedBox(height: 16),
            _RecentCards(controller: controller),
          ],
          if (controller.isOnline) ...[
            const SizedBox(height: 16),
            _DiagnosticsPanel(controller: controller),
          ],
        ];
    }
  }

  Future<void> _confirmForget(
    BuildContext context,
    MachineController controller,
  ) async {
    final l10n = context.l10n;
    final name = controller.machine?.name ?? '';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: MagicColors.surface,
        title: Text(l10n.forgetMachine),
        content: Text(l10n.forgetMachineConfirm(name)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(l10n.cancel),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: TextButton.styleFrom(foregroundColor: MagicColors.danger),
            child: Text(l10n.forget),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await controller.forget();
    if (context.mounted) context.go('/home');
  }
}

// ---------------------------------------------------------------- header

class _MachineHeader extends StatelessWidget {
  const _MachineHeader({required this.controller});

  final MachineController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final machine = controller.machine!;
    final health = controller.health;

    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.precision_manufacturing_outlined,
                  color: MagicColors.gold, size: 32),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(machine.name, style: text.titleMedium),
                    if (machine.address != null)
                      Text(
                        machine.address!.label,
                        style: text.bodySmall
                            ?.copyWith(color: scheme.onSurfaceVariant),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Flexible(
                  child: ConnectionChip(connection: controller.connection)),
            ],
          ),
          if (controller.isOnline && health != null) ...[
            if (health.simulate) _Note(l10n.simulatedMachine),
            if (!health.bottomEnabled) _Note(l10n.topOnlyMode),
          ],
        ],
      ),
    );
  }
}

class _Note extends StatelessWidget {
  const _Note(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 10),
        child: Row(
          children: [
            const Icon(Icons.info_outline_rounded,
                size: 16, color: MagicColors.goldSoft),
            const SizedBox(width: 8),
            Expanded(
              child: Text(message,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant)),
            ),
          ],
        ),
      );
}

/// Small "Online / Offline / Connecting" badge. Also used on Home.
class ConnectionChip extends StatelessWidget {
  const ConnectionChip({super.key, required this.connection});

  final MachineConnection connection;

  @override
  Widget build(BuildContext context) {
    final color = switch (connection) {
      MachineConnection.online => const Color(0xFF4CC38A),
      MachineConnection.connecting => MagicColors.gold,
      _ => MagicColors.danger,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.circle, size: 8, color: color),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              context.l10n.connectionLabel(connection),
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context)
                  .textTheme
                  .labelMedium
                  ?.copyWith(color: color),
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------- not reachable

class _NoMachinePanel extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l10n.noMachineTitle,
              style: text.titleMedium, textAlign: TextAlign.center),
          const SizedBox(height: 8),
          Text(
            l10n.noMachineControlBody,
            textAlign: TextAlign.center,
            style: text.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: () => context.push('/link-machine'),
            child: Text(l10n.linkMachine),
          ),
        ],
      ),
    );
  }
}

/// Offline, or no address yet: retry, or ask the machine over Bluetooth.
class _FindPanel extends StatelessWidget {
  const _FindPanel({required this.controller, required this.offline});

  final MachineController controller;
  final bool offline;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final address = controller.machine?.address;
    final findFailure = controller.findFailure;

    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Icon(offline ? Icons.wifi_off_rounded : Icons.wifi_find_rounded,
              size: 40, color: offline ? MagicColors.danger : MagicColors.gold),
          const SizedBox(height: 12),
          Text(
            offline ? l10n.machineOfflineTitle : l10n.noAddressTitle,
            style: text.titleMedium,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 6),
          Text(
            offline && address != null
                ? l10n.machineOfflineBody(address.label)
                : l10n.noAddressBody,
            style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            textAlign: TextAlign.center,
          ),
          if (findFailure != null) ...[
            const SizedBox(height: 12),
            Text(
              l10n.linkFailure(findFailure),
              style: TextStyle(color: scheme.error),
              textAlign: TextAlign.center,
            ),
          ],
          const SizedBox(height: 20),
          if (offline) ...[
            FilledButton(
              onPressed:
                  controller.isFindingAddress ? null : controller.reconnect,
              child: Text(l10n.tryAgain),
            ),
            const SizedBox(height: 12),
          ],
          OutlinedButton.icon(
            onPressed:
                controller.isFindingAddress ? null : controller.findAgain,
            icon: controller.isFindingAddress
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.bluetooth_searching_rounded),
            label: Text(controller.isFindingAddress
                ? l10n.findingMachine
                : l10n.findOverBluetooth),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------- run

class _RunPanel extends StatelessWidget {
  const _RunPanel({required this.controller});

  final MachineController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final run = controller.run;
    final state =
        controller.isStarting && controller.state == ControllerState.idle
            ? ControllerState.starting
            : controller.state;
    final active = controller.isBusy;
    final paused = controller.isPaused;
    final showCounters =
        run != null && (active || paused || run.cardsProcessed > 0);

    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l10n.runStateLabel(state), style: text.titleLarge),
          if (showCounters) ...[
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: _Counter(
                    label: l10n.cardsProcessed,
                    value: '${run.cardsProcessed}',
                  ),
                ),
                if (controller.bottomEnabled)
                  Expanded(
                    child: _Counter(
                      label: l10n.currentFolder,
                      value:
                          run.currentTray == null ? '–' : '${run.currentTray}',
                    ),
                  ),
              ],
            ),
          ],
          if (paused) ...[
            const SizedBox(height: 16),
            _ProblemsBox(
              key: const ValueKey('fault-box'),
              title: _capitalized(l10n.faultDescription(run?.errorCode)),
              lines: [l10n.pausedHint],
              bullets: false,
            ),
          ],
          if (controller.isStarting || controller.isResuming) ...[
            const SizedBox(height: 16),
            const LinearProgressIndicator(),
            const SizedBox(height: 8),
            Text(
              controller.isResuming ? l10n.resumingHint : l10n.startingHint,
              style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
          if (controller.startProblems.isNotEmpty) ...[
            const SizedBox(height: 16),
            _ProblemsBox(
              title: paused ? l10n.resumeProblemsTitle : l10n.startProblemsTitle,
              lines: [
                for (final problem in controller.startProblems)
                  l10n.startProblem(problem),
              ],
            ),
          ],
          if (controller.actionError != null) ...[
            const SizedBox(height: 12),
            Text(
              l10n.machineApiError(controller.actionError!),
              style: TextStyle(color: scheme.error),
            ),
          ],
          if (!active &&
              !paused &&
              run != null &&
              run.status != RunStatus.running) ...[
            const SizedBox(height: 16),
            Text(l10n.lastRun, style: text.labelLarge),
            const SizedBox(height: 4),
            Text(
              l10n.runResult(run),
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
          const SizedBox(height: 20),
          if (paused) ...[
            // RF26: Resume after a fault; Stop ends the paused run.
            FilledButton.icon(
              onPressed: controller.canResume ? controller.resumeRun : null,
              icon: controller.isResuming
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2.5, color: MagicColors.background),
                    )
                  : const Icon(Icons.play_arrow_rounded),
              label: Text(l10n.resumeSorting),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: controller.canStop ? controller.stopRun : null,
              icon: const Icon(Icons.stop_rounded),
              label: Text(l10n.stopSorting),
            ),
          ] else if (!active)
            FilledButton.icon(
              onPressed: controller.canStart ? controller.startRun : null,
              icon: const Icon(Icons.play_arrow_rounded),
              label: Text(l10n.startSorting),
            )
          else ...[
            FilledButton.icon(
              onPressed: controller.canStop ? controller.stopRun : null,
              icon: controller.isStopping
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2.5, color: MagicColors.background),
                    )
                  : const Icon(Icons.stop_rounded),
              label: Text(l10n.stopSorting),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: controller.isOnline
                  ? () => controller.stopRun(immediate: true)
                  : null,
              style: OutlinedButton.styleFrom(
                foregroundColor: MagicColors.danger,
                side: const BorderSide(color: MagicColors.danger),
              ),
              icon: const Icon(Icons.dangerous_outlined),
              label: Text(l10n.emergencyStop),
            ),
          ],
        ],
      ),
    );
  }
}

class _Counter extends StatelessWidget {
  const _Counter({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value,
            style: text.headlineMedium?.copyWith(color: MagicColors.gold)),
        Text(label,
            style: text.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant)),
      ],
    );
  }
}

String _capitalized(String text) =>
    text.isEmpty ? text : text[0].toUpperCase() + text.substring(1);

class _ProblemsBox extends StatelessWidget {
  const _ProblemsBox({
    super.key,
    required this.title,
    required this.lines,
    this.bullets = true,
  });

  final String title;
  final List<String> lines;
  final bool bullets;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: MagicColors.danger),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.report_problem_outlined,
                  color: MagicColors.danger, size: 20),
              const SizedBox(width: 8),
              Expanded(child: Text(title, style: text.titleSmall)),
            ],
          ),
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(bullets ? '• $line' : line, style: text.bodyMedium),
            ),
        ],
      ),
    );
  }
}

class _RecentCards extends StatelessWidget {
  const _RecentCards({required this.controller});

  final MachineController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.recentlySorted, style: text.titleMedium),
          const SizedBox(height: 8),
          for (final card in controller.recentCards)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Icon(
                    card.cleared ? Icons.cancel_outlined : Icons.style_outlined,
                    size: 18,
                    color: MagicColors.gold,
                  ),
                  const SizedBox(width: 10),
                  Expanded(child: Text(l10n.sortedCard(card))),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

// ----------------------------------------------------------- diagnostics

class _DiagnosticsPanel extends StatefulWidget {
  const _DiagnosticsPanel({required this.controller});

  final MachineController controller;

  @override
  State<_DiagnosticsPanel> createState() => _DiagnosticsPanelState();
}

class _DiagnosticsPanelState extends State<_DiagnosticsPanel> {
  bool _expanded = false;

  void _toggle() {
    setState(() => _expanded = !_expanded);
    final controller = widget.controller;
    if (_expanded &&
        controller.hardware == null &&
        !controller.loadingHardware) {
      controller.loadHardware();
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final controller = widget.controller;
    final hardware = controller.hardware;

    return MagicPanel(
      padding: const EdgeInsets.fromLTRB(20, 8, 8, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(child: Text(l10n.diagnostics, style: text.titleMedium)),
              if (_expanded)
                IconButton(
                  tooltip: l10n.refresh,
                  onPressed: controller.loadingHardware
                      ? null
                      : controller.loadHardware,
                  icon: const Icon(Icons.refresh_rounded),
                ),
              IconButton(
                onPressed: _toggle,
                icon: Icon(_expanded
                    ? Icons.expand_less_rounded
                    : Icons.expand_more_rounded),
              ),
            ],
          ),
          if (_expanded)
            Padding(
              padding: const EdgeInsets.only(right: 12, bottom: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (controller.loadingHardware && hardware == null)
                    const Padding(
                      padding: EdgeInsets.all(12),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                  if (controller.hardwareError != null)
                    Text(
                      l10n.machineApiError(controller.hardwareError!),
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  if (hardware != null) ..._modules(context, hardware),
                  if (controller.bottomEnabled) ...[
                    const SizedBox(height: 16),
                    _BaseTools(controller: controller),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }

  List<Widget> _modules(BuildContext context, HardwareStatus hardware) {
    final l10n = context.l10n;
    String yesNo(bool? value) =>
        value == null ? '–' : (value ? l10n.yes : l10n.no);
    final top = hardware.top;
    final bottom = hardware.bottom;
    return [
      _ModuleHeader(
          title: l10n.topModule, online: top.online, error: top.error),
      if (top.online) ...[
        _InfoRow(l10n.stackHasCards, yesNo(top.stackHasCard)),
        _InfoRow(l10n.platformHasCard, yesNo(top.platformHasCard)),
        _InfoRow(l10n.flap,
            top.flapAngle == null ? '–' : l10n.flapValue(top.flapAngle!)),
        _InfoRow(l10n.roller, yesNo(top.rollerRunning)),
      ],
      if (bottom.enabled) ...[
        const SizedBox(height: 12),
        _ModuleHeader(
            title: l10n.bottomModule,
            online: bottom.online,
            error: bottom.error),
        if (bottom.online) ...[
          _InfoRow(l10n.homed, yesNo(bottom.homed)),
          _InfoRow(l10n.position, l10n.positionValue(bottom.position)),
          if (bottom.drift != null) _InfoRow(l10n.drift, '${bottom.drift}'),
          _InfoRow(
            l10n.foldersWithCards,
            bottom.traysWithCards.isEmpty
                ? l10n.none
                : bottom.traysWithCards.join(', '),
          ),
        ],
      ],
    ];
  }
}

class _ModuleHeader extends StatelessWidget {
  const _ModuleHeader({required this.title, required this.online, this.error});

  final String title;
  final bool online;
  final String? error;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final ok = online && error == null;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        children: [
          Icon(ok ? Icons.check_circle_rounded : Icons.error_outline_rounded,
              size: 18, color: ok ? MagicColors.gold : MagicColors.danger),
          const SizedBox(width: 8),
          Expanded(
            child: Text(title, style: Theme.of(context).textTheme.labelLarge),
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Text(
              !online ? l10n.moduleOffline : (error ?? l10n.moduleOnline),
              textAlign: TextAlign.end,
              style: TextStyle(
                  color: ok ? MagicColors.parchmentMuted : MagicColors.danger),
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(left: 26, top: 2, bottom: 2),
      child: Row(
        children: [
          Expanded(
            child: Text(label,
                style: text.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ),
          Text(value, style: text.bodyMedium),
        ],
      ),
    );
  }
}

class _BaseTools extends StatelessWidget {
  const _BaseTools({required this.controller});

  final MachineController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final enabled = controller.canMoveBase;
    final result = controller.baseResult;
    final error = controller.baseError;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(l10n.baseTest, style: text.labelLarge),
        const SizedBox(height: 4),
        Text(l10n.baseTestHint,
            style: text.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant)),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            ActionChip(
              avatar: const Icon(Icons.home_outlined, size: 18),
              label: Text(l10n.homeBase),
              onPressed: enabled ? controller.homeBase : null,
            ),
            for (var tray = 1; tray <= 6; tray++)
              ActionChip(
                label: Text('$tray'),
                tooltip: l10n.folderLabel(tray),
                onPressed: enabled ? () => controller.gotoTray(tray) : null,
              ),
          ],
        ),
        if (controller.isMovingBase)
          const Padding(
            padding: EdgeInsets.only(top: 12),
            child: LinearProgressIndicator(),
          ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(l10n.machineApiError(error),
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          )
        else if (result != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              '${l10n.position}: ${l10n.positionValue(result.base.position)}'
              '${result.base.drift == null ? '' : ' · ${l10n.drift}: ${result.base.drift}'}',
            ),
          ),
      ],
    );
  }
}
