import 'dart:convert';

import 'package:cardforge/core/services/server_json_client.dart';
import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:cardforge/features/runs/run_models.dart';
import 'package:cardforge/features/runs/run_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), status);

ApiRunRepository _repo(MockClient client, {String? token = 'abc'}) =>
    ApiRunRepository(
      client: ServerJsonClient(
        client: client,
        tokenProvider: () async => token,
        baseUrl: 'https://api.test',
      ),
    );

// The shape the CardForge server answers (cardforge_server/repository.py).
const _run = {
  'run_id': 12,
  'local_uuid': '9aa0a9c4-0000-4000-8000-000000000000',
  'status': 'completed',
  'started_at': '2026-11-11T20:00:00.000Z',
  'stopped_at': '2026-11-11T20:10:00.000Z',
  'cards_processed': 3,
  'successful_scans': 2,
  'failed_scans': 1,
  'error_code': null,
  'message': 'Stack is empty: 3 card(s) processed.',
  'synced_at': '2026-11-11T20:10:01.000Z',
};

void main() {
  test('GET /runs with paging and the bearer token', () async {
    late http.Request seen;
    final repo = _repo(MockClient((request) async {
      seen = request;
      return _json({
        'runs': [_run],
        'total': 31,
      });
    }));

    final page = await repo.fetchRuns(offset: 30, limit: 30);

    expect(seen.url.path, '/runs');
    expect(seen.url.queryParameters, {'limit': '30', 'offset': '30'});
    expect(seen.headers['Authorization'], 'Bearer abc');
    expect(page.total, 31);
    final run = page.runs.single;
    expect(run.id, '12');
    expect(run.status, RunStatus.completed);
    expect(run.cardsProcessed, 3);
    expect(run.identified, 2);
    expect(run.notIdentified, 1);
    expect(run.startedAt, DateTime.utc(2026, 11, 11, 20).toLocal());
  });

  test('GET /runs/{id}: folder counts, outcome counts and every card',
      () async {
    late Uri seen;
    final repo = _repo(MockClient((request) async {
      seen = request.url;
      return _json({
        'run': _run,
        'bin_counts': {'1': 1, '4': 1, '6': 1, 'none': 0},
        'scan_counts': {'identified': 2, 'unidentified': 0, 'face_down': 1},
        'cards': [
          {
            'card_number': 1,
            'status': 'identified',
            'bin': 1,
            'card_name': 'Plains',
            'set_code': 'fin',
            'collector_number': '1',
          },
          {
            'card_number': 2,
            'status': 'identified',
            'bin': 4,
            'card_name': 'Lightning Bolt',
          },
          {'card_number': 3, 'status': 'face_down', 'bin': 6},
        ],
      });
    }));

    final details = await repo.fetchRun('12');

    expect(seen.path, '/runs/12');
    expect(details.run.id, '12');
    expect(details.folderCounts, {1: 1, 4: 1, 6: 1});
    expect(details.identified, 2);
    expect(details.faceDown, 1);
    expect(details.cards.map((c) => c.outcome), [
      ScanOutcome.identified,
      ScanOutcome.identified,
      ScanOutcome.faceDown,
    ]);
    expect(details.cards.first.setCode, 'fin');
    expect(details.cards.last.name, isNull);
  });

  test('maps the errors like the card search', () async {
    Future<CardsFailure> failureFor(MockClient client) async {
      try {
        await _repo(client).fetchRuns();
      } on CardsException catch (e) {
        return e.failure;
      }
      fail('no exception');
    }

    expect(
      await failureFor(MockClient((_) async => _json({'code': 'X'}, 401))),
      CardsFailure.unauthorized,
    );
    expect(
      await failureFor(MockClient((_) async => _json({}, 500))),
      CardsFailure.serverError,
    );
    expect(
      await failureFor(MockClient((_) async => http.Response('oops', 200))),
      CardsFailure.serverError,
    );
    expect(
      await failureFor(
          MockClient((_) async => throw http.ClientException('down'))),
      CardsFailure.serverUnreachable,
    );
  });
}
