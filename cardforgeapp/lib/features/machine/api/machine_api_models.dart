// Data the machine's REST API (sorter_api on the Raspberry Pi) sends back.
// Field names follow raspberry-api/rest-api.md. Pure Dart, no Flutter.
//
// Parsing is lenient on purpose: a missing or unexpected field becomes null
// (or a safe default) instead of crashing the screen.

int? _int(Object? v) => v is int ? v : (v is num ? v.toInt() : null);
String? _str(Object? v) => v is String ? v : null;
bool? _bool(Object? v) => v is bool ? v : null;
DateTime? _date(Object? v) => v is String ? DateTime.tryParse(v) : null;
Map<String, Object?>? _map(Object? v) =>
    v is Map ? v.cast<String, Object?>() : null;
List<int> _ints(Object? v) =>
    v is List ? v.whereType<num>().map((n) => n.toInt()).toList() : const [];

/// What the API's controller is doing (`state` in GET /run/status).
enum ControllerState { idle, starting, running, stopping, unknown }

/// Status of a run (`run.status`).
enum RunStatus { running, completed, failed, cancelled, unknown }

ControllerState _controllerState(Object? v) => switch (v) {
      'idle' => ControllerState.idle,
      'starting' => ControllerState.starting,
      'running' => ControllerState.running,
      'stopping' => ControllerState.stopping,
      _ => ControllerState.unknown,
    };

RunStatus _runStatus(Object? v) => switch (v) {
      'running' => RunStatus.running,
      'completed' => RunStatus.completed,
      'failed' => RunStatus.failed,
      'cancelled' => RunStatus.cancelled,
      _ => RunStatus.unknown,
    };

/// GET /health
class MachineHealth {
  const MachineHealth({
    required this.ok,
    this.simulate = false,
    this.bottomEnabled = true,
  });

  factory MachineHealth.fromJson(Map<String, Object?> json) => MachineHealth(
        ok: _bool(json['ok']) ?? true,
        simulate: _bool(json['simulate']) ?? false,
        bottomEnabled: _bool(json['bottom_enabled']) ?? true,
      );

  final bool ok;

  /// The API runs its software machine (`--simulate`), not the real one.
  final bool simulate;

  /// False in top-only mode (only the feeder module is wired).
  final bool bottomEnabled;
}

/// The current or last run.
class RunInfo {
  const RunInfo({
    required this.runId,
    required this.status,
    this.startedAt,
    this.stoppedAt,
    this.cardsProcessed = 0,
    this.currentTray,
    this.errorCode,
    this.message,
  });

  factory RunInfo.fromJson(Map<String, Object?> json) => RunInfo(
        runId: _str(json['run_id']) ?? '',
        status: _runStatus(json['status']),
        startedAt: _date(json['started_at']),
        stoppedAt: _date(json['stopped_at']),
        cardsProcessed: _int(json['cards_processed']) ?? 0,
        currentTray: _int(json['current_tray']),
        errorCode: _str(json['error_code']),
        message: _str(json['message']),
      );

  final String runId;
  final RunStatus status;
  final DateTime? startedAt;
  final DateTime? stoppedAt;
  final int cardsProcessed;

  /// Tray of the last card (null in top-only mode or before the first card).
  final int? currentTray;

  /// Why the run failed, e.g. `FEED_TIMEOUT`.
  final String? errorCode;

  /// The API's own message (English).
  final String? message;

  RunInfo withCard({required int cardsProcessed, int? tray}) => RunInfo(
        runId: runId,
        status: status,
        startedAt: startedAt,
        stoppedAt: stoppedAt,
        cardsProcessed: cardsProcessed,
        currentTray: tray ?? currentTray,
        errorCode: errorCode,
        message: message,
      );
}

/// GET /run/status (also the `snapshot` event).
class MachineSnapshot {
  const MachineSnapshot({required this.state, this.run});

  factory MachineSnapshot.fromJson(Map<String, Object?> json) {
    final run = _map(json['run']);
    return MachineSnapshot(
      state: _controllerState(json['state']),
      run: run == null ? null : RunInfo.fromJson(run),
    );
  }

  final ControllerState state;
  final RunInfo? run;
}

/// One reason why POST /run/start refused to start.
class StartProblem {
  const StartProblem({
    required this.code,
    required this.message,
    this.trays = const [],
    this.module,
  });

  factory StartProblem.fromJson(Map<String, Object?> json) => StartProblem(
        code: _str(json['code']) ?? 'UNKNOWN',
        message: _str(json['message']) ?? '',
        trays: _ints(json['trays']),
        module: _str(json['module']),
      );

  /// STACK_EMPTY, TRAYS_NOT_EMPTY, PLATFORM_NOT_CLEAR, RUN_ACTIVE,
  /// START_CANCELLED, MODULE_OFFLINE, HOME_TIMEOUT, ...
  final String code;
  final String message;

  /// For TRAYS_NOT_EMPTY: the trays that still have cards.
  final List<int> trays;

  /// For MODULE_OFFLINE: which module did not answer (A1 or A2).
  final String? module;
}

enum MachineApiFailure {
  /// No answer: wrong address, phone on another network, API not running.
  unreachable,

  /// The API answered with 409: the request is not possible right now.
  conflict,

  /// 503: a module (A1 or A2) did not answer.
  moduleOffline,

  /// 500: hardware fault during the request.
  hardware,

  /// Anything else (unexpected status or body).
  badResponse,
}

class MachineApiException implements Exception {
  const MachineApiException(
    this.failure, {
    this.statusCode,
    this.code,
    this.message,
    this.problems = const [],
  });

  /// Builds the exception from an error response body (with or without the
  /// FastAPI `detail` wrapper).
  factory MachineApiException.fromResponse(int statusCode, Object? body) {
    var json = _map(body) ?? const <String, Object?>{};
    final detail = json['detail'];
    if (detail is Map) json = detail.cast<String, Object?>();
    final problems = json['problems'] is List
        ? (json['problems'] as List)
            .whereType<Map>()
            .map((p) => StartProblem.fromJson(p.cast<String, Object?>()))
            .toList()
        : const <StartProblem>[];
    final code = _str(json['code']) ??
        _str(json['error_code']) ??
        (problems.isNotEmpty ? problems.first.code : null);
    final failure = switch (statusCode) {
      409 => MachineApiFailure.conflict,
      503 => MachineApiFailure.moduleOffline,
      500 => MachineApiFailure.hardware,
      _ => MachineApiFailure.badResponse,
    };
    return MachineApiException(
      failure,
      statusCode: statusCode,
      code: code,
      message: _str(json['message']) ?? (detail is String ? detail : null),
      problems: problems,
    );
  }

  final MachineApiFailure failure;
  final int? statusCode;

  /// Error code from the API, e.g. RUN_ACTIVE, NOT_HOMED, HOME_TIMEOUT.
  final String? code;

  /// The API's own message (English), if any.
  final String? message;

  /// For POST /run/start: everything that has to be fixed first.
  final List<StartProblem> problems;

  @override
  String toString() => 'MachineApiException($failure, $statusCode, $code)';
}

/// Top module (A1, feeder) in GET /hardware/status.
class TopModuleStatus {
  const TopModuleStatus({
    required this.online,
    this.state,
    this.stackHasCard,
    this.platformHasCard,
    this.flapAngle,
    this.rollerRunning,
    this.error,
  });

  factory TopModuleStatus.fromJson(Map<String, Object?> json) =>
      TopModuleStatus(
        online: _bool(json['online']) ?? false,
        state: _str(json['state']),
        stackHasCard: _bool(json['stack_has_card']),
        platformHasCard: _bool(json['platform_has_card']),
        flapAngle: _int(json['flap_angle']),
        rollerRunning: _bool(json['roller_running']),
        error: _str(json['error']),
      );

  final bool online;
  final String? state;
  final bool? stackHasCard;
  final bool? platformHasCard;
  final int? flapAngle;
  final bool? rollerRunning;
  final String? error;
}

/// Bottom module (A2, rotating base). Also the `base` object of the
/// /base/* endpoints.
class BaseStatus {
  const BaseStatus({
    required this.online,
    this.enabled = true,
    this.state,
    this.homed,
    this.position,
    this.steps,
    this.drift,
    this.zeroSensor,
    this.traysWithCards = const [],
    this.error,
  });

  factory BaseStatus.fromJson(Map<String, Object?> json) => BaseStatus(
        // The /base/* endpoints don't send `online`: they only answer when it is.
        online: _bool(json['online']) ?? true,
        enabled: _bool(json['enabled']) ?? true,
        state: _str(json['state']),
        homed: _bool(json['homed']),
        position: _int(json['position']),
        steps: _int(json['steps']),
        drift: _int(json['drift']),
        zeroSensor: _bool(json['zero_sensor']),
        traysWithCards: _ints(json['trays_with_cards']),
        error: _str(json['error']),
      );

  final bool online;

  /// False in top-only mode.
  final bool enabled;
  final String? state;
  final bool? homed;

  /// Tray under the platform, 1-6. 0 or null = between trays / unknown.
  final int? position;
  final int? steps;
  final int? drift;
  final bool? zeroSensor;
  final List<int> traysWithCards;
  final String? error;
}

/// GET /hardware/status
class HardwareStatus {
  const HardwareStatus({required this.top, required this.bottom});

  factory HardwareStatus.fromJson(Map<String, Object?> json) => HardwareStatus(
        top: TopModuleStatus.fromJson(_map(json['top']) ?? const {}),
        bottom: BaseStatus.fromJson(
          _map(json['bottom']) ?? const {'online': false},
        ),
      );

  final TopModuleStatus top;
  final BaseStatus bottom;
}

/// Result of a /base/* call.
class BaseMoveResult {
  const BaseMoveResult({required this.base, this.message});

  factory BaseMoveResult.fromJson(Map<String, Object?> json) => BaseMoveResult(
        base: BaseStatus.fromJson(_map(json['base']) ?? const {}),
        message: _str(json['message']),
      );

  final BaseStatus base;
  final String? message;
}

/// One message from WS /ws/events.
sealed class MachineEvent {
  const MachineEvent();

  factory MachineEvent.fromJson(Map<String, Object?> json) {
    final run = _map(json['run']);
    switch (json['type']) {
      case 'snapshot':
        return SnapshotEvent(MachineSnapshot.fromJson(json));
      case 'run_started' when run != null:
        return RunStartedEvent(RunInfo.fromJson(run));
      case 'card_sorted':
        return CardSortedEvent(
          runId: _str(json['run_id']) ?? '',
          cardNumber: _int(json['card_number']) ?? 0,
          tray: _int(json['tray']),
          cardIdentifier: _str(json['card_identifier']),
        );
      case 'card_cleared':
        return CardSortedEvent(
          runId: _str(json['run_id']) ?? '',
          cardNumber: _int(json['card_number']) ?? 0,
          tray: _int(json['tray']),
          cleared: true,
        );
      case 'run_finished' when run != null:
        return RunFinishedEvent(RunInfo.fromJson(run), _str(json['message']));
      default:
        return UnknownMachineEvent(_str(json['type']));
    }
  }
}

class SnapshotEvent extends MachineEvent {
  const SnapshotEvent(this.snapshot);
  final MachineSnapshot snapshot;
}

class RunStartedEvent extends MachineEvent {
  const RunStartedEvent(this.run);
  final RunInfo run;
}

/// A card was dropped in a tray (`card_sorted`), or dropped by a stop
/// (`card_cleared`, [cleared] = true).
class CardSortedEvent extends MachineEvent {
  const CardSortedEvent({
    required this.runId,
    required this.cardNumber,
    this.tray,
    this.cardIdentifier,
    this.cleared = false,
  });

  final String runId;
  final int cardNumber;
  final int? tray;

  /// Null until the camera identifies cards.
  final String? cardIdentifier;
  final bool cleared;
}

class RunFinishedEvent extends MachineEvent {
  const RunFinishedEvent(this.run, this.message);
  final RunInfo run;
  final String? message;
}

class UnknownMachineEvent extends MachineEvent {
  const UnknownMachineEvent(this.type);
  final String? type;
}
