import 'dart:collection';
import 'dart:convert';

import 'package:cardforge/core/services/server_json_client.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/notifications/alert_watcher.dart';
import 'package:cardforge/features/notifications/server_alert.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

final _now = DateTime.utc(2026, 11, 11, 20, 5);

ServerAlert _fault(int id, {String code = 'FEED_TIMEOUT', DateTime? time}) =>
    ServerAlert(
      id: id,
      kind: ServerAlertKind.fault,
      runId: 'run-1',
      code: code,
      time: time ?? DateTime.utc(2026, 11, 11, 20, 3, 7),
      cardsProcessed: 4,
    );

ServerAlert _end(int id, {String status = 'completed'}) => ServerAlert(
      id: id,
      kind: ServerAlertKind.runEnded,
      runId: 'run-1',
      status: status,
      time: DateTime.utc(2026, 11, 11, 20, 4),
      cardsProcessed: 12,
    );

/// Answers each fetch with the next scripted page or error.
class _ScriptedFeed implements AlertFeed {
  _ScriptedFeed(Iterable<Object> answers) : _answers = Queue.of(answers);

  final Queue<Object> _answers;
  final List<(String, int, Duration)> requests = [];

  @override
  Future<AlertPage> fetch({
    required String runId,
    required int after,
    Duration wait = Duration.zero,
  }) async {
    requests.add((runId, after, wait));
    if (_answers.isEmpty) return AlertPage(alerts: const [], lastId: after);
    final next = _answers.removeFirst();
    if (next is Exception) throw next;
    return next as AlertPage;
  }
}

void main() {
  group('AlertWatcher', () {
    late List<ServerAlert> shown;
    late List<Duration> slept;

    setUp(() {
      shown = [];
      slept = [];
    });

    AlertWatcher watcher(AlertFeed feed,
            {int startAfter = 0, DateTime Function()? clock}) =>
        AlertWatcher(
          feed: feed,
          runId: 'run-1',
          startAfter: startAfter,
          show: (alert) async => shown.add(alert),
          clock: clock ?? () => _now,
          sleep: (d) async => slept.add(d),
        );

    test('shows each alert of the run and stops when the run ends', () async {
      final feed = _ScriptedFeed([
        AlertPage(alerts: [_fault(3)], lastId: 3),
        const AlertPage(alerts: [], lastId: 3),
        AlertPage(alerts: [_end(5)], lastId: 5),
      ]);
      final w = watcher(feed);

      expect(await w.run(), WatchEnd.runEnded);
      expect(shown.map((a) => a.id), [3, 5]);
      expect(w.lastId, 5);
      // Always this run's alerts, from where it left off, waiting 25 s each time.
      expect(feed.requests, [
        ('run-1', 0, const Duration(seconds: 25)),
        ('run-1', 3, const Duration(seconds: 25)),
        ('run-1', 3, const Duration(seconds: 25)),
      ]);
    });

    test('goes on from the last alert a restarted service had shown', () async {
      final feed = _ScriptedFeed([
        AlertPage(alerts: [_end(9)], lastId: 9)
      ]);
      expect(await watcher(feed, startAfter: 7).run(), WatchEnd.runEnded);
      expect(feed.requests.single.$2, 7);
    });

    test('waits longer after each network failure, then carries on', () async {
      final feed = _ScriptedFeed([
        const CardsException(CardsFailure.serverUnreachable),
        const CardsException(CardsFailure.serverError),
        const FormatException('bad json'),
        AlertPage(alerts: [_end(2)], lastId: 2),
      ]);

      expect(await watcher(feed).run(), WatchEnd.runEnded);
      expect(slept, const [
        Duration(seconds: 2),
        Duration(seconds: 5),
        Duration(seconds: 10),
      ]);
      expect(shown.single.id, 2);
    });

    test('an expired session ends the watch', () async {
      final feed =
          _ScriptedFeed([const CardsException(CardsFailure.unauthorized)]);
      expect(await watcher(feed).run(), WatchEnd.signedOut);
      expect(shown, isEmpty);
    });

    test('an alert from long ago is skipped, not shown', () async {
      final feed = _ScriptedFeed([
        AlertPage(alerts: [
          _fault(1, time: _now.subtract(const Duration(hours: 3))),
          _end(2),
        ], lastId: 2),
      ]);
      expect(await watcher(feed).run(), WatchEnd.runEnded);
      expect(shown.map((a) => a.id), [2]);
    });

    test('a notification that fails does not stop the watch', () async {
      final feed = _ScriptedFeed([
        AlertPage(alerts: [_fault(1), _end(2)], lastId: 2),
      ]);
      final shownIds = <int>[];
      final w = AlertWatcher(
        feed: feed,
        runId: 'run-1',
        show: (alert) async {
          shownIds.add(alert.id);
          if (alert.id == 1) throw StateError('notifications blocked');
        },
        clock: () => _now,
        sleep: (_) async {},
      );
      expect(await w.run(), WatchEnd.runEnded);
      expect(shownIds, [1, 2]);
    });

    test('stop() ends the watch after the request in flight', () async {
      final feed = _ScriptedFeed([]);
      late AlertWatcher w;
      var calls = 0;
      final stopping = _StoppingFeed(feed, () {
        if (++calls == 2) w.stop();
      });
      w = AlertWatcher(
        feed: stopping,
        runId: 'run-1',
        show: (_) async {},
        clock: () => _now,
        sleep: (_) async {},
      );
      expect(await w.run(), WatchEnd.stopped);
      expect(calls, 2);
    });

    test('gives up after maxWatch: a run never lasts that long', () async {
      var t = _now;
      final feed = _StoppingFeed(_ScriptedFeed([]), () {
        t = t.add(const Duration(hours: 1));
      });
      final w = AlertWatcher(
        feed: feed,
        runId: 'run-1',
        show: (_) async {},
        clock: () => t,
        sleep: (_) async {},
      );
      expect(await w.run(), WatchEnd.timedOut);
      expect(t.difference(_now), greaterThan(const Duration(hours: 6)));
    });
  });

  group('HttpAlertFeed', () {
    test('asks the server for one run\'s alerts after an id, with the token',
        () async {
      late http.Request sent;
      Duration? timeout;
      final feed = HttpAlertFeed(
        token: 'user-token',
        clientFor: (t) {
          timeout = t;
          return ServerJsonClient(
            client: MockClient((request) async {
              sent = request;
              return http.Response(
                  jsonEncode({
                    'notifications': [
                      {
                        'id': 4,
                        'kind': 'run_ended',
                        'status': 'completed',
                        'run_id': 'run-1',
                        'time': '2026-11-11T20:10:00.000Z',
                      }
                    ],
                    'last_id': 4,
                  }),
                  200);
            }),
            tokenProvider: () async => 'user-token',
            baseUrl: 'http://server.test',
            timeout: t,
          );
        },
      );

      final page = await feed.fetch(
          runId: 'run-1', after: 3, wait: const Duration(seconds: 25));

      expect(sent.url.path, '/notifications');
      expect(sent.url.queryParameters,
          {'run_id': 'run-1', 'after': '3', 'wait': '25'});
      expect(sent.headers['Authorization'], 'Bearer user-token');
      expect(timeout, greaterThan(const Duration(seconds: 25)));
      expect(page.alerts.single.endsRun, isTrue);
    });

    test('an expired session is reported as unauthorized', () async {
      final feed = HttpAlertFeed(
        token: 'old',
        clientFor: (t) => ServerJsonClient(
          client: MockClient((_) async => http.Response('{}', 401)),
          tokenProvider: () async => 'old',
          baseUrl: 'http://server.test',
          timeout: t,
        ),
      );
      await expectLater(
        feed.fetch(runId: 'run-1', after: 0),
        throwsA(isA<CardsException>().having(
            (e) => e.failure, 'failure', CardsFailure.unauthorized)),
      );
    });
  });

  group('ServerAlert', () {
    test('reads the server JSON with its type and time (RF38)', () {
      final page = AlertPage.fromJson({
        'notifications': [
          {
            'id': 7,
            'run_id': 'run-1',
            'kind': 'fault',
            'code': 'DROP_FAILED',
            'status': null,
            'message': 'The card did not fall.',
            'cards_processed': 4,
            'time': '2026-11-11T20:03:07.000Z',
            'received_at': '2026-11-11T20:03:07.200Z',
          }
        ],
        'last_id': 7,
      });
      final alert = page.alerts.single;
      expect(page.lastId, 7);
      expect(alert.kind, ServerAlertKind.fault);
      expect(alert.code, 'DROP_FAILED');
      expect(alert.time, DateTime.utc(2026, 11, 11, 20, 3, 7));
      expect(alert.cardsProcessed, 4);
      expect(alert.endsRun, isFalse);
    });

    test('maps to the same alert types as the app', () {
      expect(_fault(1).alertType, AlertType.jamDetected);
      expect(_fault(1, code: 'FEED_FAILED').alertType, AlertType.jamDetected);
      expect(_fault(1, code: 'DROP_FAILED').alertType, AlertType.machineFault);
      expect(_end(2).alertType, AlertType.batchFinished);
      expect(
          ServerAlert(
                  id: 3,
                  kind: ServerAlertKind.runEnded,
                  status: 'failed',
                  code: 'MODULE_OFFLINE',
                  time: _now)
              .alertType,
          AlertType.runFailed);
      // The user stopped the run: nothing to notify.
      expect(_end(4, status: 'cancelled').alertType, isNull);
      expect(_end(4, status: 'cancelled').toMachineAlert(), isNull);
    });

    test('the alert keeps the machine time, card count and fault code', () {
      final alert = _fault(1, code: 'DROP_FAILED')
          .toMachineAlert(machineName: 'CardForge-A1B2')!;
      expect(alert.createdAt, DateTime.utc(2026, 11, 11, 20, 3, 7));
      expect(alert.machineName, 'CardForge-A1B2');
      expect(alert.cardCount, 4);
      expect(alert.faultCode, 'DROP_FAILED');
      expect(_end(2).toMachineAlert()!.faultCode, isNull);
    });

    test('an unknown kind is ignored', () {
      final alert = ServerAlert.fromJson(
          {'id': 1, 'kind': 'party', 'time': '2026-11-11T20:00:00Z'});
      expect(alert.kind, ServerAlertKind.unknown);
      expect(alert.toMachineAlert(), isNull);
    });
  });
}

/// Calls [onFetch] before each answer of [inner].
class _StoppingFeed implements AlertFeed {
  _StoppingFeed(this.inner, this.onFetch);

  final AlertFeed inner;
  final void Function() onFetch;

  @override
  Future<AlertPage> fetch({
    required String runId,
    required int after,
    Duration wait = Duration.zero,
  }) {
    onFetch();
    return inner.fetch(runId: runId, after: after, wait: wait);
  }
}
