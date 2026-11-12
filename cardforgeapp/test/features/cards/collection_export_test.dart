import 'dart:convert';
import 'dart:ui' show Rect;

import 'package:cardforge/core/services/server_json_client.dart';
import 'package:cardforge/features/cards/collection_export.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../../helpers/test_helpers.dart';

class _RecordingSharer {
  String? fileName;
  String? csv;

  Future<void> call(String name, String text, Rect? origin) async {
    fileName = name;
    csv = text;
  }
}

class _EmptyCollection implements CollectionExportSource {
  @override
  Future<List<CollectionRow>> fetchRows() async => const [];
}

void main() {
  group('buildCollectionCsv (RFC 4180)', () {
    test('header, CRLF line endings and one line per row', () {
      final csv = buildCollectionCsv(const [
        CollectionRow(
          quantity: 4,
          status: 'identified',
          name: 'Lightning Bolt',
          setCode: '2xm',
          collectorNumber: '129',
          rarity: 'uncommon',
          colorIdentity: 'R',
          typeLine: 'Instant',
          manaCost: '{R}',
          folder: 4,
          scryfallId: 'e3285e6b',
        ),
        CollectionRow(quantity: 1, status: 'face_down', folder: 6),
      ]);

      expect(
        csv,
        'quantity,name,set_code,collector_number,rarity,color_identity,'
        'type_line,mana_cost,folder,status,scryfall_id\r\n'
        '4,Lightning Bolt,2xm,129,uncommon,R,Instant,{R},4,identified,e3285e6b\r\n'
        '1,,,,,,,,6,face_down,\r\n',
      );
    });

    test('quotes fields with commas, quotes or line breaks', () {
      final csv = buildCollectionCsv(const [
        CollectionRow(
          quantity: 1,
          status: 'identified',
          name: 'Borborygmos, Enraged',
          typeLine: 'Legendary Creature — Cyclops "Big"',
          manaCost: '{4}{R}{R}{G}{G}',
        ),
      ]);
      final line = csv.split('\r\n')[1];

      expect(line,
          '1,"Borborygmos, Enraged",,,,,"Legendary Creature — Cyclops ""Big""",{4}{R}{R}{G}{G},,identified,');
    });

    test('file name carries the date and time', () {
      expect(collectionCsvFileName(DateTime(2026, 11, 1, 9, 5)),
          'cardforge-collection-20261101-0905.csv');
    });
  });

  test('GET /cards/export rows from the server', () async {
    late http.Request seen;
    final source = ApiCollectionExportSource(
      client: ServerJsonClient(
        client: MockClient((request) async {
          seen = request;
          return http.Response.bytes(
            utf8.encode(jsonEncode({
              'rows': [
                {
                  'quantity': 2,
                  'name': 'Plains',
                  'set_code': 'FIN',
                  'collector_number': '1',
                  'rarity': null,
                  'color_identity': '',
                  'type_line': 'Basic Land — Plains',
                  'mana_cost': '',
                  'folder': 1,
                  'status': 'identified',
                  'scryfall_id': 'abc',
                },
              ],
              'total_cards': 2,
            })),
            200,
          );
        }),
        tokenProvider: () async => 'abc',
        baseUrl: 'https://api.test',
      ),
    );

    final rows = await source.fetchRows();

    expect(seen.url.path, '/cards/export');
    expect(seen.headers['Authorization'], 'Bearer abc');
    expect(rows.single.quantity, 2);
    expect(rows.single.name, 'Plains');
    expect(rows.single.folder, 1);
  });

  group('export screen', () {
    setUpAll(configureTestEnvironment);
    setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

    testWidgets('builds the CSV of the whole collection and shares it',
        (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester, initialLocation: '/export', shareCsv: sharer.call);

      await tester.tap(find.text('Export CSV'));
      await tester.pumpAndSettle();

      expect(sharer.fileName, startsWith('cardforge-collection-'));
      final lines = sharer.csv!.split('\r\n');
      expect(lines.first, startsWith('quantity,name,set_code'));
      expect(lines, contains('4,Lightning Bolt,,,,,,,4,identified,mock-17'));
      expect(lines, contains('1,,,,,,,,6,face_down,'));
      expect(find.textContaining('cards exported to cardforge-collection-'),
          findsOneWidget);
    });

    testWidgets('says so when there is nothing to export', (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester,
          initialLocation: '/export',
          collectionExport: _EmptyCollection(),
          shareCsv: sharer.call);

      await tester.tap(find.text('Export CSV'));
      await tester.pumpAndSettle();

      expect(sharer.csv, isNull);
      expect(find.text('Your collection is empty: sort some cards first.'),
          findsOneWidget);
    });
  });
}
