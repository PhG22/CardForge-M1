import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/config/app_config.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/services/server_health_service.dart';
import '../../core/widgets/card_emblem.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/magic_wordmark.dart';
import '../auth/auth_service.dart';

/// Screen 1 - Splash: loads the app, checks the server and picks the next screen.
class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key, this.healthService});

  /// Lets tests replace the server check.
  final ServerHealthService? healthService;

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  bool _failed = false;
  bool _checkingSession = false;

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    setState(() {
      _failed = false;
      _checkingSession = false;
    });

    // Keeps the splash on screen long enough for the brand to be seen.
    final minDelay = Future<void>.delayed(AppConfig.splashMinDuration);
    final serverOk = await (widget.healthService ?? ServerHealthService())
        .isServerReachable();
    await minDelay;
    if (!mounted) return;

    if (!serverOk) {
      setState(() => _failed = true);
      return;
    }

    setState(() => _checkingSession = true);
    final hasSession = await AuthService.instance.hasSession();
    if (!mounted) return;

    context.go(hasSession ? '/home' : '/login');
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;

    return MagicScaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            children: [
              const Spacer(flex: 3),
              const CardEmblem(scale: 0.9),
              const SizedBox(height: 24),
              const MagicWordmark(),
              const SizedBox(height: 8),
              Text(
                l10n.appTagline,
                style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              ),
              const Spacer(flex: 2),
              if (_failed) ...[
                Icon(Icons.cloud_off_rounded, size: 40, color: scheme.error),
                const SizedBox(height: 12),
                Text(l10n.serverUnreachableTitle,
                    style: text.titleMedium, textAlign: TextAlign.center),
                const SizedBox(height: 4),
                Text(l10n.serverUnreachableHint,
                    style: text.bodyMedium
                        ?.copyWith(color: scheme.onSurfaceVariant),
                    textAlign: TextAlign.center),
                const SizedBox(height: 20),
                FilledButton.icon(
                  onPressed: _bootstrap,
                  icon: const Icon(Icons.refresh_rounded),
                  label: Text(l10n.tryAgain),
                ),
              ] else ...[
                const SizedBox(
                    width: 28,
                    height: 28,
                    child: CircularProgressIndicator(strokeWidth: 3)),
                const SizedBox(height: 16),
                Text(
                    _checkingSession
                        ? l10n.splashCheckingSession
                        : l10n.splashConnecting,
                    style: text.bodyMedium
                        ?.copyWith(color: scheme.onSurfaceVariant)),
              ],
              const Spacer(),
              Text('v0.2.0',
                  style: text.labelSmall?.copyWith(color: scheme.outline)),
            ],
          ),
        ),
      ),
    );
  }
}
