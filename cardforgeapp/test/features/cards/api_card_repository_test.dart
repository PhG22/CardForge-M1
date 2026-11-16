import 'dart:convert';

import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/card_repository.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(body)), status);

ApiCardRepository _repo(
  MockClient client, {
  String? token = 'abc',
}) =>
    ApiCardRepository(
      client: client,
      tokenProvider: () async => token,
      baseUrl: 'https://api.test',
    );

void main() {
  test('asks GET /cards with the search, paging and the bearer token',
      () async {
    late http.Request seen;
    final repo = _repo(MockClient((request) async {
      seen = request;
      return _json({
        'items': [
          {
            'id': '7',
            'name': 'Lightning Bolt',
            'quantity': 3,
            'color': 'R',
            'color_identity': 'R',
            'sets': 'FIN, M10',
          },
        ],
        'total': 45,
        'color_counts': {'R': 3},
        'not_identified': 1,
      });
    }));

    final page = await repo.fetchCards(query: 'bolt', offset: 20, limit: 10);

    expect(seen.method, 'GET');
    expect(seen.url.path, '/cards');
    expect(seen.url.queryParameters,
        {'query': 'bolt', 'limit': '10', 'offset': '20'});
    expect(seen.headers['Authorization'], 'Bearer abc');
    expect(page.total, 45);
    expect(page.items.single.name, 'Lightning Bolt');
    expect(page.items.single.quantity, 3);
    expect(page.items.single.color, ColorGroup.red);
    expect(page.colorCounts[ColorGroup.red], 3);
    expect(page.notIdentified, 1);
  });

  test('sends the run and the color when they are picked', () async {
    late Uri seen;
    final repo = _repo(MockClient((request) async {
      seen = request.url;
      return _json({'items': [], 'total': 0});
    }));

    await repo.fetchCards(runId: '12', color: ColorGroup.colorless);
    expect(seen.queryParameters['run_id'], '12');
    expect(seen.queryParameters['color'], 'colorless');

    await repo.fetchCards(color: ColorGroup.white);
    expect(seen.queryParameters.containsKey('run_id'), isFalse);
    expect(seen.queryParameters['color'], 'W');
  });

  test('leaves the query out when it is empty', () async {
    late Uri seen;
    final repo = _repo(MockClient((request) async {
      seen = request.url;
      return _json({'items': [], 'total': 0});
    }));

    await repo.fetchCards();

    expect(seen.queryParameters.containsKey('query'), isFalse);
    expect(seen.queryParameters.containsKey('run_id'), isFalse);
    expect(seen.queryParameters.containsKey('color'), isFalse);
    expect(seen.queryParameters['limit'], '30');
    expect(seen.queryParameters['offset'], '0');
  });

  test('sends no Authorization header when there is no token', () async {
    late http.Request seen;
    final repo = _repo(
      MockClient((request) async {
        seen = request;
        return _json({'items': [], 'total': 0});
      }),
      token: null,
    );

    await repo.fetchCards();

    expect(seen.headers.containsKey('Authorization'), isFalse);
  });

  test('reads accents in card names correctly', () async {
    final repo = _repo(MockClient((request) async => _json({
          'items': [
            {'id': '1', 'name': 'Jötun Grunt'},
          ],
          'total': 1,
        })));

    final page = await repo.fetchCards();

    expect(page.items.single.name, 'Jötun Grunt');
  });

  Future<CardsFailure> failureOf(MockClient client) async {
    try {
      await _repo(client).fetchCards();
    } on CardsException catch (e) {
      return e.failure;
    }
    fail('expected a CardsException');
  }

  test('401 means the session expired', () async {
    expect(await failureOf(MockClient((_) async => _json({}, 401))),
        CardsFailure.unauthorized);
  });

  test('other error statuses are server errors', () async {
    expect(await failureOf(MockClient((_) async => _json({}, 500))),
        CardsFailure.serverError);
  });

  test('a body in an unexpected shape is a server error', () async {
    expect(await failureOf(MockClient((_) async => _json({'oops': true}))),
        CardsFailure.serverError);
    expect(
        await failureOf(
            MockClient((_) async => http.Response('not json at all', 200))),
        CardsFailure.serverError);
  });

  test('no connection means the server is unreachable', () async {
    expect(
      await failureOf(MockClient((_) async => throw http.ClientException('x'))),
      CardsFailure.serverUnreachable,
    );
  });
}
