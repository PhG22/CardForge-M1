import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import 'machine_alert.dart';

IconData alertIcon(AlertType type) => switch (type) {
      AlertType.wifiConnected => Icons.wifi_rounded,
      AlertType.wifiDisconnected => Icons.wifi_off_rounded,
      AlertType.batchFinished => Icons.task_alt_rounded,
      AlertType.runFailed => Icons.error_outline_rounded,
      AlertType.jamDetected => Icons.report_problem_outlined,
      AlertType.machineFault => Icons.pause_circle_outline_rounded,
      AlertType.doubleFeed => Icons.layers_outlined,
      AlertType.connectionLost => Icons.link_off_rounded,
      AlertType.machineLinked => Icons.link_rounded,
    };

Color alertColor(AlertSeverity severity) => switch (severity) {
      AlertSeverity.info => const Color(0xFF6FA8FF),
      AlertSeverity.success => const Color(0xFF4CC38A),
      AlertSeverity.warning => MagicColors.gold,
      AlertSeverity.error => MagicColors.danger,
    };
