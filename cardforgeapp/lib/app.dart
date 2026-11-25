import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:go_router/go_router.dart';

import 'core/app_scope.dart';
import 'core/l10n/app_strings.dart';
import 'core/router/app_router.dart';
import 'core/services/server_health_service.dart';
import 'core/theme/app_theme.dart';
import 'features/alerts/alert_style.dart';
import 'features/alerts/machine_alert.dart';

class CardForgeApp extends StatefulWidget {
  const CardForgeApp({
    super.key,
    required this.deps,
    this.initialLocation = '/splash',
    this.healthService,
  });

  final AppDependencies deps;

  /// Start route. Tests use it to open a specific screen.
  final String initialLocation;

  /// Lets tests replace the server check.
  final ServerHealthService? healthService;

  @override
  State<CardForgeApp> createState() => _CardForgeAppState();
}

class _CardForgeAppState extends State<CardForgeApp> {
  late final GoRouter _router = createAppRouter(
    initialLocation: widget.initialLocation,
    healthService: widget.healthService,
  );
  late final ThemeData _theme = AppTheme.dark();
  StreamSubscription<MachineAlert>? _alertSub;

  @override
  void initState() {
    super.initState();
    _alertSub = widget.deps.alerts.onNewAlert.listen(_showAlertBanner);
    // Follow the linked machine (run state, alerts) from any screen.
    widget.deps.machine.start();
  }

  @override
  void dispose() {
    widget.deps.machine.stop();
    _alertSub?.cancel();
    _router.dispose();
    super.dispose();
  }

  /// Shows every new alert as a banner on top of whatever screen is open.
  void _showAlertBanner(MachineAlert alert) {
    final messenger = widget.deps.messengerKey.currentState;
    if (messenger == null) return;
    final strings = AppStrings.forLocale(widget.deps.locale.locale);
    final color = alertColor(alert.type.severity);

    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          duration: const Duration(seconds: 6),
          content: Row(
            children: [
              Icon(alertIcon(alert.type), color: color),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      strings.alertTitle(alert.type),
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    Text(strings.alertMessage(alert)),
                  ],
                ),
              ),
            ],
          ),
          action: SnackBarAction(
            label: strings.view,
            textColor: MagicColors.gold,
            onPressed: () => _router.push('/alerts'),
          ),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final deps = widget.deps;
    return AppScope(
      deps: deps,
      child: ListenableBuilder(
        listenable: deps.locale,
        builder: (context, _) => MaterialApp.router(
          title: 'CardForge',
          debugShowCheckedModeBanner: false,
          theme: _theme,
          locale: deps.locale.locale,
          supportedLocales: AppStrings.supportedLocales,
          localizationsDelegates: const [
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          scaffoldMessengerKey: deps.messengerKey,
          routerConfig: _router,
        ),
      ),
    );
  }
}
