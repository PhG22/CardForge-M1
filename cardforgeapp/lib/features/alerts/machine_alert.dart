/// Kinds of events the machine (or the app) can report.
enum AlertType {
  wifiConnected,
  wifiDisconnected,
  batchFinished,
  runFailed,
  jamDetected,

  /// Any other fault that paused the run (stuck card, base); see
  /// [MachineAlert.faultCode].
  machineFault,
  doubleFeed,
  connectionLost,
  machineLinked,
}

enum AlertSeverity { info, success, warning, error }

extension AlertTypeSeverity on AlertType {
  AlertSeverity get severity => switch (this) {
        AlertType.wifiConnected ||
        AlertType.machineLinked =>
          AlertSeverity.info,
        AlertType.batchFinished => AlertSeverity.success,
        AlertType.wifiDisconnected ||
        AlertType.connectionLost =>
          AlertSeverity.warning,
        AlertType.jamDetected ||
        AlertType.machineFault ||
        AlertType.doubleFeed ||
        AlertType.runFailed =>
          AlertSeverity.error,
      };
}

/// One alert. It stores the type and its data (not the text), so the message
/// is translated when shown and follows the current language.
class MachineAlert {
  const MachineAlert({
    required this.id,
    required this.type,
    required this.createdAt,
    this.machineName,
    this.cardCount,
    this.faultCode,
    this.read = false,
  });

  final int id;
  final AlertType type;

  /// When the app received it (the alert's timestamp, RF27).
  final DateTime createdAt;
  final String? machineName;
  final int? cardCount;

  /// For a fault: the machine's code, e.g. `FEED_TIMEOUT`, `DROP_FAILED`.
  final String? faultCode;
  final bool read;

  MachineAlert markRead() => MachineAlert(
        id: id,
        type: type,
        createdAt: createdAt,
        machineName: machineName,
        cardCount: cardCount,
        faultCode: faultCode,
        read: true,
      );
}
