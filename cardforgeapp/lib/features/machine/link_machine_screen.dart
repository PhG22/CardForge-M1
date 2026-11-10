import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/config/app_config.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../alerts/machine_alert.dart';
import 'api/machine_api.dart';
import 'machine_link_flow.dart';
import 'machine_models.dart';

/// Link machine: scan over Bluetooth, pick a machine, enter its password,
/// optionally send the Wi-Fi details, wait for the machine to join the
/// network and check that the phone reaches it over Wi-Fi.
/// The logic lives in [MachineLinkFlow]; this widget only draws each step.
class LinkMachineScreen extends StatefulWidget {
  const LinkMachineScreen({super.key});

  @override
  State<LinkMachineScreen> createState() => _LinkMachineScreenState();
}

class _LinkMachineScreenState extends State<LinkMachineScreen> {
  late final MachineLinkFlow _flow;

  @override
  void initState() {
    super.initState();
    final deps = AppScope.of(context);
    _flow = MachineLinkFlow(
      deps.linkService,
      linkTimeout: AppConfig.linkTimeout,
      checkReachable: (address) async {
        final api = deps.machineApiFor(address);
        try {
          return await isMachineReachable(api);
        } finally {
          api.close();
        }
      },
      onLinked: (machine, {required wifiSent}) {
        deps.machineStore.save(machine);
        deps.alerts.add(AlertType.machineLinked, machineName: machine.name);
        if (wifiSent) {
          deps.alerts.add(AlertType.wifiConnected, machineName: machine.name);
        }
      },
    )..startScan();
  }

  @override
  void dispose() {
    _flow.dispose();
    super.dispose();
  }

  Widget _stepFor(LinkPhase phase) => switch (phase) {
        LinkPhase.deviceList => _DeviceListStep(flow: _flow),
        LinkPhase.password => _PasswordStep(flow: _flow),
        LinkPhase.wifiQuestion => _WifiQuestionStep(flow: _flow),
        LinkPhase.wifiForm => _WifiFormStep(flow: _flow),
        LinkPhase.linking => _LinkingStep(flow: _flow),
        LinkPhase.success => _SuccessStep(flow: _flow),
        LinkPhase.failure => _FailureStep(flow: _flow),
      };

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return ListenableBuilder(
      listenable: _flow,
      builder: (context, _) => PopScope(
        // Going back while the machine is being linked would leave it half set up.
        canPop: !_flow.isBusy,
        child: MagicScaffold(
          backdropHeight: 220,
          backdropIntensity: 0.5,
          appBar: AppBar(title: Text(l10n.linkMachine)),
          body: KeyedSubtree(
            key: ValueKey(_flow.phase),
            child: _stepFor(_flow.phase),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- shared bits

class _MessagePanel extends StatelessWidget {
  const _MessagePanel({
    required this.icon,
    required this.title,
    required this.message,
    this.color = MagicColors.goldSoft,
  });

  final IconData icon;
  final String title;
  final String message;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return MagicPanel(
      child: Column(
        children: [
          Icon(icon, size: 40, color: color),
          const SizedBox(height: 12),
          Text(title, style: text.titleMedium, textAlign: TextAlign.center),
          const SizedBox(height: 6),
          Text(
            message,
            style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class _ButtonSpinner extends StatelessWidget {
  const _ButtonSpinner();

  @override
  Widget build(BuildContext context) => const SizedBox(
        width: 22,
        height: 22,
        child: CircularProgressIndicator(
          strokeWidth: 2.5,
          color: MagicColors.background,
        ),
      );
}

// ------------------------------------------------------------- step: devices

class _DeviceListStep extends StatelessWidget {
  const _DeviceListStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(l10n.selectMachine, style: text.titleLarge),
        const SizedBox(height: 12),
        if (flow.scanning)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              children: [
                const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    l10n.scanningForMachines,
                    style: text.bodyMedium
                        ?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                ),
              ],
            ),
          ),
        if (flow.scanFailure != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _MessagePanel(
              icon: Icons.bluetooth_disabled_rounded,
              title: l10n.scanFailedTitle,
              message: l10n.linkFailure(flow.scanFailure!),
              color: MagicColors.danger,
            ),
          )
        else if (!flow.scanning && flow.machines.isEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _MessagePanel(
              icon: Icons.bluetooth_searching_rounded,
              title: l10n.noMachinesFound,
              message: l10n.noMachinesHint,
            ),
          ),
        for (final machine in flow.machines)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _MachineTile(
              machine: machine,
              onTap: () => flow.select(machine),
            ),
          ),
        const SizedBox(height: 4),
        OutlinedButton.icon(
          onPressed: flow.scanning ? null : flow.startScan,
          icon: const Icon(Icons.refresh_rounded),
          label: Text(l10n.scanAgain),
        ),
      ],
    );
  }
}

class _MachineTile extends StatelessWidget {
  const _MachineTile({required this.machine, required this.onTap});

  final DiscoveredMachine machine;
  final VoidCallback onTap;

  IconData get _signalIcon => switch (machine.signalLevel) {
        3 => Icons.signal_cellular_alt_rounded,
        2 => Icons.signal_cellular_alt_2_bar_rounded,
        _ => Icons.signal_cellular_alt_1_bar_rounded,
      };

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: onTap,
      child: MagicPanel(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            const Icon(Icons.precision_manufacturing_outlined,
                color: MagicColors.gold, size: 30),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(machine.name, style: text.titleMedium),
                  const SizedBox(height: 2),
                  Text(machine.id,
                      style: text.bodySmall
                          ?.copyWith(color: scheme.onSurfaceVariant)),
                ],
              ),
            ),
            Icon(_signalIcon, color: MagicColors.gold),
          ],
        ),
      ),
    );
  }
}

// ------------------------------------------------------------ step: password

class _PasswordStep extends StatefulWidget {
  const _PasswordStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  State<_PasswordStep> createState() => _PasswordStepState();
}

class _PasswordStepState extends State<_PasswordStep> {
  final _formKey = GlobalKey<FormState>();
  final _controller = TextEditingController();
  bool _obscure = true;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    if (!_formKey.currentState!.validate()) return;
    widget.flow.submitPassword(_controller.text);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final flow = widget.flow;
    final machine = flow.selected!;
    final failure = flow.passwordFailure;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(l10n.enterMachinePassword, style: text.titleLarge),
        const SizedBox(height: 8),
        Text(
          l10n.machinePasswordHint(machine.name),
          style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
        ),
        const SizedBox(height: 20),
        Form(
          key: _formKey,
          child: TextFormField(
            controller: _controller,
            enabled: !flow.checkingPassword,
            obscureText: _obscure,
            textInputAction: TextInputAction.done,
            onChanged: (_) => flow.clearPasswordFailure(),
            onFieldSubmitted: (_) => _submit(),
            decoration: InputDecoration(
              labelText: l10n.machinePassword,
              prefixIcon: const Icon(Icons.key_rounded),
              suffixIcon: IconButton(
                tooltip: _obscure ? l10n.showPassword : l10n.hidePassword,
                icon: Icon(_obscure
                    ? Icons.visibility_outlined
                    : Icons.visibility_off_outlined),
                onPressed: () => setState(() => _obscure = !_obscure),
              ),
            ),
            validator: (v) => (v == null || v.isEmpty)
                ? l10n.machinePasswordRequired
                : null,
          ),
        ),
        if (failure != null)
          Padding(
            padding: const EdgeInsets.only(top: 8, left: 16),
            child: Text(
              l10n.linkFailure(failure),
              style: TextStyle(color: scheme.error),
            ),
          ),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: flow.checkingPassword ? null : _submit,
          child: flow.checkingPassword
              ? const _ButtonSpinner()
              : Text(l10n.verifyPassword),
        ),
        const SizedBox(height: 4),
        TextButton(
          onPressed: flow.checkingPassword ? null : flow.backToList,
          child: Text(l10n.chooseAnotherMachine),
        ),
      ],
    );
  }
}

// -------------------------------------------------------------- step: Wi-Fi

class _WifiQuestionStep extends StatelessWidget {
  const _WifiQuestionStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        _MessagePanel(
          icon: Icons.wifi_rounded,
          color: MagicColors.gold,
          title: l10n.wifiQuestionTitle,
          message: l10n.wifiQuestionBody,
        ),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: () => flow.answerWifiQuestion(send: true),
          child: Text(l10n.sendWifi),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: () => flow.answerWifiQuestion(send: false),
          child: Text(l10n.skipWifi),
        ),
      ],
    );
  }
}

class _WifiFormStep extends StatefulWidget {
  const _WifiFormStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  State<_WifiFormStep> createState() => _WifiFormStepState();
}

class _WifiFormStepState extends State<_WifiFormStep> {
  final _formKey = GlobalKey<FormState>();
  final _ssidCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool _obscure = true;

  @override
  void dispose() {
    _ssidCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  void _submit() {
    if (!_formKey.currentState!.validate()) return;
    widget.flow.submitWifi(
      WifiCredentials(
        ssid: _ssidCtrl.text.trim(),
        password: _passwordCtrl.text,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(l10n.wifiFormTitle, style: text.titleLarge),
        const SizedBox(height: 20),
        Form(
          key: _formKey,
          child: Column(
            children: [
              TextFormField(
                controller: _ssidCtrl,
                textInputAction: TextInputAction.next,
                decoration: InputDecoration(
                  labelText: l10n.networkName,
                  prefixIcon: const Icon(Icons.wifi_rounded),
                ),
                validator: (v) => (v == null || v.trim().isEmpty)
                    ? l10n.networkNameRequired
                    : null,
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _passwordCtrl,
                obscureText: _obscure,
                textInputAction: TextInputAction.done,
                onFieldSubmitted: (_) => _submit(),
                validator: (v) => WifiCredentials.isValidPassword(v ?? '')
                    ? null
                    : l10n.wifiPasswordInvalid,
                decoration: InputDecoration(
                  labelText: l10n.wifiPassword,
                  helperText: l10n.wifiPasswordHint,
                  prefixIcon: const Icon(Icons.lock_outline),
                  suffixIcon: IconButton(
                    tooltip: _obscure ? l10n.showPassword : l10n.hidePassword,
                    icon: Icon(_obscure
                        ? Icons.visibility_outlined
                        : Icons.visibility_off_outlined),
                    onPressed: () => setState(() => _obscure = !_obscure),
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Text(
          l10n.wifiTip,
          style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
        ),
        const SizedBox(height: 20),
        FilledButton(onPressed: _submit, child: Text(l10n.sendAndLink)),
        const SizedBox(height: 4),
        TextButton(
          onPressed: widget.flow.backToWifiQuestion,
          child: Text(l10n.back),
        ),
      ],
    );
  }
}

// ----------------------------------------------------------- step: linking

class _LinkingStep extends StatelessWidget {
  const _LinkingStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    final steps = flow.sentWifi
        ? const [
            LinkStep.sendingWifi,
            LinkStep.waitingForWifi,
            LinkStep.checkingConnection,
          ]
        : const [LinkStep.checkingConnection];
    final current = flow.step == null ? 0 : steps.indexOf(flow.step!);

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(l10n.linkingTitle, style: text.titleLarge),
        const SizedBox(height: 8),
        Text(
          l10n.linkingHint,
          style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
        ),
        const SizedBox(height: 20),
        MagicPanel(
          child: Column(
            children: [
              for (var i = 0; i < steps.length; i++)
                Padding(
                  padding: EdgeInsets.only(bottom: i == steps.length - 1 ? 0 : 16),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 24,
                        height: 24,
                        child: i < current
                            ? const Icon(Icons.check_circle_rounded,
                                color: MagicColors.gold)
                            : i == current
                                ? const Padding(
                                    padding: EdgeInsets.all(3),
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2.5),
                                  )
                                : Icon(Icons.circle_outlined,
                                    color: scheme.outline),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Text(
                          l10n.linkStep(steps[i]),
                          style: text.bodyLarge?.copyWith(
                            color: i <= current
                                ? MagicColors.parchment
                                : scheme.onSurfaceVariant,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------- step: result

class _SuccessStep extends StatelessWidget {
  const _SuccessStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final name = flow.linked?.name ?? '';
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        _MessagePanel(
          icon: Icons.check_circle_rounded,
          color: MagicColors.gold,
          title: l10n.linkSuccessTitle,
          message: l10n.linkSuccessBody(name),
        ),
        const SizedBox(height: 20),
        FilledButton(
          onPressed: () => context.go('/home'),
          child: Text(l10n.done),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: () {
            final router = GoRouter.of(context);
            router.go('/home'); // so "back" from machine control goes home
            router.push('/machine');
          },
          child: Text(l10n.openMachineControl),
        ),
      ],
    );
  }
}

class _FailureStep extends StatelessWidget {
  const _FailureStep({required this.flow});

  final MachineLinkFlow flow;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        _MessagePanel(
          icon: Icons.error_outline_rounded,
          color: MagicColors.danger,
          title: l10n.linkFailureTitle,
          message: l10n.linkFailure(flow.failure ?? MachineLinkFailure.unknown),
        ),
        const SizedBox(height: 20),
        if (flow.canEditWifi) ...[
          FilledButton(
            onPressed: flow.editWifi,
            child: Text(l10n.wifiFormTitle),
          ),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: flow.startScan, child: Text(l10n.tryAgain)),
        ] else
          FilledButton(onPressed: flow.startScan, child: Text(l10n.tryAgain)),
        const SizedBox(height: 12),
        OutlinedButton(
          onPressed: () => context.pop(),
          child: Text(l10n.close),
        ),
      ],
    );
  }
}
