import 'dart:convert';

import 'package:cardforge/core/services/server_json_client.dart';
import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/collection_export.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import '../../helpers/test_helpers.dart';

class _RecordingSharer {
  String? fileName;
  String? content;

  Future<void> call(String name, String text, Rect? origin) async {
    fileName = name;
    content = text;
  }
}

class _EmptyCollection implements CollectionExportSource {
  @override
  Future<List<CollectionRow>> fetchRows({String? runId}) async => const [];
}

const _bolt = CollectionRow(
  quantity: 4,
  status: 'identified',
  name: 'Lightning Bolt',
  setCode: '2xm',
  collectorNumber: '129',
  rarity: 'uncommon',
  color: ColorGroup.red,
  colorIdentity: 'R',
  typeLine: 'Instant',
  manaCost: '{R}',
  scryfallId: 'e3285e6b',
);
const _plains = CollectionRow(
  quantity: 2,
  status: 'identified',
  name: 'Plains',
  setCode: 'FIN',
  collectorNumber: '1',
  color: ColorGroup.white,
  colorIdentity: 'W',
);
const _helix = CollectionRow(
  quantity: 1,
  status: 'identified',
  name: 'Lightning Helix',
  setCode: 'RAV',
  collectorNumber: '213',
  color: ColorGroup.multicolor,
  colorIdentity: 'RW',
);
const _faceDown = CollectionRow(quantity: 1, status: 'face_down');
const _unidentified = CollectionRow(quantity: 2, status: 'unidentified');

void main() {
  group('buildCollectionCsv (RFC 4180)', () {
    test('header, CRLF line endings, one line per print, no folder column',
        () {
      final csv = buildCollectionCsv(const [_bolt, _faceDown]);

      expect(
        csv,
        'quantity,name,set_code,collector_number,rarity,color,color_identity,'
        'type_line,mana_cost,status,scryfall_id\r\n'
        '4,Lightning Bolt,2xm,129,uncommon,red,R,Instant,{R},identified,e3285e6b\r\n'
        '1,,,,,,,,,face_down,\r\n',
      );
      expect(csv, isNot(contains('folder')));
    });

    test('rows are sorted by color, then name; not identified last', () {
      final csv = buildCollectionCsv(
          const [_faceDown, _helix, _bolt, _plains, _unidentified]);
      final names = csv
          .split('\r\n')
          .skip(1)
          .where((l) => l.isNotEmpty)
          .map((l) => l.split(',')[1])
          .toList();

      expect(names, ['Plains', 'Lightning Bolt', 'Lightning Helix', '', '']);
    });

    test('quotes fields with commas, quotes or line breaks', () {
      final csv = buildCollectionCsv(const [
        CollectionRow(
          quantity: 1,
          status: 'identified',
          name: 'Borborygmos, Enraged',
          color: ColorGroup.multicolor,
          typeLine: 'Legendary Creature — Cyclops "Big"',
          manaCost: '{4}{R}{R}{G}{G}',
        ),
      ]);
      final line = csv.split('\r\n')[1];

      expect(line,
          '1,"Borborygmos, Enraged",,,,multicolor,,"Legendary Creature — Cyclops ""Big""",{4}{R}{R}{G}{G},identified,');
    });
  });

  group('buildCollectionTxt', () {
    test('one import line per print, grouped by color', () {
      final txt = buildCollectionTxt(
          const [_helix, _bolt, _plains, _faceDown, _unidentified]);

      expect(
        txt,
        '// White (2)\n'
        '2 Plains (FIN) 1\n'
        '\n'
        '// Red (4)\n'
        '4 Lightning Bolt (2XM) 129\n'
        '\n'
        '// Multicolor (1)\n'
        '1 Lightning Helix (RAV) 213\n'
        '\n'
        '// Not identified: 2 unidentified, 1 face-down\n',
      );
    });

    test('a card without a set is just quantity and name', () {
      final txt = buildCollectionTxt(const [
        CollectionRow(
            quantity: 3,
            status: 'identified',
            name: 'Sol Ring',
            color: ColorGroup.colorless),
      ]);

      expect(txt, '// Colorless (3)\n3 Sol Ring\n');
    });
  });

  test('file names say what was exported, when, and the format', () {
    final at = DateTime(2026, 11, 1, 9, 5);
    expect(exportFileName(at, ExportFormat.csv),
        'cardforge-collection-20261101-0905.csv');
    expect(exportFileName(at, ExportFormat.txt, runId: '12'),
        'cardforge-run-12-20261101-0905.txt');
  });

  test('GET /cards/export rows from the server, whole or one run', () async {
    final seen = <http.Request>[];
    final source = ApiCollectionExportSource(
      client: ServerJsonClient(
        client: MockClient((request) async {
          seen.add(request);
          return http.Response.bytes(
            utf8.encode(jsonEncode({
              'rows': [
                {
                  'quantity': 2,
                  'name': 'Plains',
                  'set_code': 'FIN',
                  'collector_number': '1',
                  'rarity': null,
                  'color': 'W',
                  'color_identity': 'W',
                  'type_line': 'Basic Land — Plains',
                  'mana_cost': '',
                  'status': 'identified',
                  'scryfall_id': 'abc',
                },
                {'quantity': 1, 'status': 'face_down'},
              ],
              'total_cards': 3,
            })),
            200,
          );
        }),
        tokenProvider: () async => 'abc',
        baseUrl: 'https://api.test',
      ),
    );

    final rows = await source.fetchRows();
    await source.fetchRows(runId: '12');

    expect(seen.first.url.path, '/cards/export');
    expect(seen.first.url.queryParameters, isEmpty);
    expect(seen.first.headers['Authorization'], 'Bearer abc');
    expect(seen.last.url.queryParameters, {'run_id': '12'});
    expect(rows.first.quantity, 2);
    expect(rows.first.name, 'Plains');
    expect(rows.first.color, ColorGroup.white);
    expect(rows.last.color, isNull);
  });

  group('export screen', () {
    setUpAll(configureTestEnvironment);
    setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

    Future<void> tapExport(WidgetTester tester) async {
      await tester.tap(find.text('Export'));
      await tester.pumpAndSettle();
    }

    testWidgets('builds the CSV of the whole collection and shares it',
        (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester, initialLocation: '/export', shareFile: sharer.call);
      await tester.pumpAndSettle();

      expect(find.text('All my runs (whole collection)'), findsOneWidget);
      await tapExport(tester);

      expect(sharer.fileName, startsWith('cardforge-collection-'));
      expect(sharer.fileName, endsWith('.csv'));
      final lines = sharer.content!.split('\r\n');
      expect(lines.first, startsWith('quantity,name,set_code'));
      expect(lines,
          contains('2,Lightning Bolt,2XM,129,,red,R,,,identified,mock-Lightning Bolt'));
      expect(lines, contains('1,,,,,,,,,face_down,'));
      expect(find.textContaining('cards exported to cardforge-collection-'),
          findsOneWidget);
    });

    testWidgets('exports one run as TXT', (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester, initialLocation: '/export', shareFile: sharer.call);
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('export-scope')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('2026-11-08 15:05 · 3 cards').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('TXT'));
      await tester.pumpAndSettle();
      await tapExport(tester);

      expect(sharer.fileName, startsWith('cardforge-run-2-'));
      expect(sharer.fileName, endsWith('.txt'));
      expect(
        sharer.content,
        '// White (1)\n'
        '1 Wall of Omens (ROE) 53\n'
        '\n'
        '// Blue (1)\n'
        '1 Opt (ELD) 59\n'
        '\n'
        '// Red (1)\n'
        '1 Shock (M19) 156\n',
      );
    });

    testWidgets('opens on one run from /export?run=<id>', (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester,
          initialLocation: '/export?run=1', shareFile: sharer.call);
      await tester.pumpAndSettle();

      expect(find.text('2026-11-02 10:30 · 1 card'), findsOneWidget);
      await tapExport(tester);

      expect(sharer.fileName, startsWith('cardforge-run-1-'));
      expect(sharer.content, contains('1,Giant Growth,M12,176,,green,G,'));
    });

    testWidgets('says so when there is nothing to export', (tester) async {
      final sharer = _RecordingSharer();
      await pumpApp(tester,
          initialLocation: '/export',
          collectionExport: _EmptyCollection(),
          shareFile: sharer.call);
      await tester.pumpAndSettle();

      await tapExport(tester);

      expect(sharer.content, isNull);
      expect(find.text('Nothing to export: no card was sorted here yet.'),
          findsOneWidget);
    });
  });
}
