import '../../core/services/server_json_client.dart';
import '../cards/card_models.dart';
import 'server_alert.dart';

/// Reads the user's alerts from the CardForge server (RF38).
abstract class AlertFeed {
  /// The alerts of run [runId] with an id above [after] (oldest first). When
  /// there are none yet, the server holds the request up to [wait] for the
  /// next one.
  ///
  /// Throws a [CardsException]: [CardsFailure.unauthorized] when the session
  /// expired, [CardsFailure.serverUnreachable] / [CardsFailure.serverError].
  Future<AlertPage> fetch({
    required String runId,
    required int after,
    Duration wait = Duration.zero,
  });
}

/// `GET /notifications` on the CardForge server.
class HttpAlertFeed implements AlertFeed {
  HttpAlertFeed({required String token, String? baseUrl, ServerJsonClient Function(Duration)? clientFor})
      : _clientFor = clientFor ??
            ((timeout) => ServerJsonClient(
                  tokenProvider: () async => token,
                  baseUrl: baseUrl,
                  timeout: timeout,
                ));

  final ServerJsonClient Function(Duration timeout) _clientFor;

  @override
  Future<AlertPage> fetch({
    required String runId,
    required int after,
    Duration wait = Duration.zero,
  }) async {
    // The server answers at the latest after `wait`; give the network some margin.
    final client = _clientFor(wait + const Duration(seconds: 15));
    final body = await client.get('/notifications', query: {
      'run_id': runId,
      'after': '$after',
      if (wait > Duration.zero) 'wait': '${wait.inSeconds}',
    });
    return AlertPage.fromJson(body.cast<String, dynamic>());
  }
}

/// Why [AlertWatcher.run] returned.
enum WatchEnd {
  /// The watched run finished (its last alert was shown).
  runEnded,

  /// The session expired: there is nobody to ask the server for.
  signedOut,

  /// [AlertWatcher.stop] was called (the app saw the run end, or the service stopped).
  stopped,

  /// Nothing happened for [AlertWatcher.maxWatch]: a run never lasts that long.
  timedOut,
}

/// The loop of the background service: waits for new alerts on the server and
/// shows each one (RF38), until the watched run ends.
///
/// Pure Dart (no plugin), so it is tested with a fake [AlertFeed] and a fake
/// clock.
class AlertWatcher {
  AlertWatcher({
    required this.feed,
    required this.show,
    required this.runId,
    this.startAfter = 0,
    this.wait = const Duration(seconds: 25),
    this.retryDelays = const [
      Duration(seconds: 2),
      Duration(seconds: 5),
      Duration(seconds: 10),
      Duration(seconds: 30),
    ],
    this.maxWatch = const Duration(hours: 6),
    this.maxAlertAge = const Duration(hours: 1),
    DateTime Function()? clock,
    Future<void> Function(Duration)? sleep,
  })  : _clock = clock ?? DateTime.now,
        _sleep = sleep ?? Future<void>.delayed;

  final AlertFeed feed;

  /// Shows one alert (the notification). Errors are ignored: the next alert
  /// still gets its turn.
  final Future<void> Function(ServerAlert alert) show;

  /// The run being watched: only its alerts are shown, and its end stops the
  /// watcher. Asking for this run's alerts from id 0 also shows a fault that
  /// happened before the service was up.
  final String runId;

  /// Last alert id already shown (saved by an earlier watcher of this run).
  final int startAfter;
  final Duration wait;
  final List<Duration> retryDelays;
  final Duration maxWatch;

  /// Older alerts (left over from long ago) are skipped instead of shown.
  final Duration maxAlertAge;
  final DateTime Function() _clock;
  final Future<void> Function(Duration) _sleep;

  bool _stopped = false;
  late int _lastId = startAfter;

  /// The last alert id seen, to save so a restarted service goes on from it.
  int get lastId => _lastId;

  /// Ends [run] after the request in flight returns.
  void stop() => _stopped = true;

  Future<WatchEnd> run() async {
    final began = _clock();
    var failures = 0;
    while (!_stopped) {
      if (_clock().difference(began) > maxWatch) return WatchEnd.timedOut;
      try {
        final page = await feed.fetch(runId: runId, after: _lastId, wait: wait);
        failures = 0;
        var ended = false;
        for (final alert in page.alerts) {
          _lastId = alert.id;
          if (_clock().difference(alert.time) <= maxAlertAge) {
            try {
              await show(alert);
            } on Object {
              // a notification that could not be shown must not stop the watch
            }
          }
          if (alert.endsRun) ended = true;
        }
        if (ended) return WatchEnd.runEnded;
      } on CardsException catch (e) {
        if (e.failure == CardsFailure.unauthorized) return WatchEnd.signedOut;
        await _backoff(failures++);
      } on Object {
        await _backoff(failures++);
      }
    }
    return WatchEnd.stopped;
  }

  Future<void> _backoff(int failures) {
    final delay = retryDelays.isEmpty
        ? const Duration(seconds: 30)
        : retryDelays[failures.clamp(0, retryDelays.length - 1)];
    return _sleep(delay);
  }
}
