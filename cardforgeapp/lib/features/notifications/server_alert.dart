import '../alerts/machine_alert.dart';

/// What happened, as the server stores it (RF38).
enum ServerAlertKind {
  /// A fault paused the run (feed jam, stuck card, base). See [ServerAlert.code].
  fault,

  /// The run finished. See [ServerAlert.status].
  runEnded,

  unknown,
}

/// One alert of a run, from `GET /notifications` on the CardForge server. The
/// machine sends it to the server when it happens, so the phone gets it even
/// when the app is closed or the screen is locked.
class ServerAlert {
  const ServerAlert({
    required this.id,
    required this.kind,
    required this.time,
    this.runId,
    this.code,
    this.status,
    this.message,
    this.cardsProcessed,
  });

  factory ServerAlert.fromJson(Map<String, dynamic> json) {
    final kind = switch (json['kind']) {
      'fault' => ServerAlertKind.fault,
      'run_ended' => ServerAlertKind.runEnded,
      _ => ServerAlertKind.unknown,
    };
    return ServerAlert(
      id: (json['id'] as num).toInt(),
      kind: kind,
      time: DateTime.tryParse(json['time'] as String? ?? '') ?? DateTime.now(),
      runId: json['run_id'] as String?,
      code: json['code'] as String?,
      status: json['status'] as String?,
      message: json['message'] as String?,
      cardsProcessed: (json['cards_processed'] as num?)?.toInt(),
    );
  }

  final int id;
  final ServerAlertKind kind;

  /// When it happened on the machine (shown in the notification, RF38).
  final DateTime time;
  final String? runId;

  /// Fault code (`FEED_TIMEOUT`, `DROP_FAILED`...) or the error code of the end.
  final String? code;

  /// End of a run: `completed`, `cancelled` or `failed`.
  final String? status;
  final String? message;
  final int? cardsProcessed;

  bool get endsRun => kind == ServerAlertKind.runEnded;

  /// The in-app alert type with the same text, or null when there is nothing
  /// to tell (the user stopped the run themselves, or an unknown kind).
  AlertType? get alertType => switch (kind) {
        ServerAlertKind.fault => code == 'FEED_TIMEOUT' || code == 'FEED_FAILED'
            ? AlertType.jamDetected
            : AlertType.machineFault,
        ServerAlertKind.runEnded => switch (status) {
            'completed' => AlertType.batchFinished,
            'failed' =>
              code == 'FEED_TIMEOUT' ? AlertType.jamDetected : AlertType.runFailed,
            _ => null,
          },
        ServerAlertKind.unknown => null,
      };

  /// The same alert the app shows when it is open (RF27), so the notification
  /// says the same thing, with the time it happened on the machine.
  MachineAlert? toMachineAlert({String? machineName}) {
    final type = alertType;
    if (type == null) return null;
    return MachineAlert(
      id: id,
      type: type,
      createdAt: time,
      machineName: machineName,
      cardCount: cardsProcessed,
      faultCode: kind == ServerAlertKind.fault ? code : null,
    );
  }
}

/// One answer of `GET /notifications`: the new alerts (oldest first) and the
/// id to ask after next time.
class AlertPage {
  const AlertPage({required this.alerts, required this.lastId});

  factory AlertPage.fromJson(Map<String, dynamic> json) => AlertPage(
        alerts: [
          for (final item in (json['notifications'] as List? ?? const []))
            ServerAlert.fromJson(item as Map<String, dynamic>),
        ],
        lastId: (json['last_id'] as num?)?.toInt() ?? 0,
      );

  final List<ServerAlert> alerts;
  final int lastId;
}
