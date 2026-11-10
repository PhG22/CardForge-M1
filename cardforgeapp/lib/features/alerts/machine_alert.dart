/// Kinds of events the machine (or the app) can report.
enum AlertType {
  wifiConnected,
  wifiDisconnected,
  batchFinished,
  runFailed,
  jamDetected,
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
    this.read = false,
  });

  final int id;
  final AlertType type;
  final DateTime createdAt;
  final String? machineName;
  final int? cardCount;
  final bool read;

  MachineAlert markRead() => MachineAlert(
        id: id,
        type: type,
        createdAt: createdAt,
        machineName: machineName,
        cardCount: cardCount,
        read: true,
      );
}
