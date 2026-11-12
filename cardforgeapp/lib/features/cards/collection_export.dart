import 'dart:convert';
import 'dart:io';
import 'dart:ui' show Rect;

import 'package:cross_file/cross_file.dart' show XFile;
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart' show ShareParams, SharePlus;

import '../../core/services/server_json_client.dart';
import 'card_models.dart';

/// One line of the collection export: how many copies of a print are in one
/// folder. Unidentified and face-down cards have no name (see [status]).
class CollectionRow {
  const CollectionRow({
    required this.quantity,
    required this.status,
    this.name,
    this.setCode,
    this.collectorNumber,
    this.rarity,
    this.colorIdentity,
    this.typeLine,
    this.manaCost,
    this.folder,
    this.scryfallId,
  });

  factory CollectionRow.fromJson(Map<String, Object?> json) {
    String? str(String key) => json[key] is String ? json[key] as String : null;
    final quantity = json['quantity'];
    final folder = json['folder'];
    return CollectionRow(
      quantity: quantity is num ? quantity.toInt() : 1,
      status: str('status') ?? 'identified',
      name: str('name'),
      setCode: str('set_code'),
      collectorNumber: str('collector_number'),
      rarity: str('rarity'),
      colorIdentity: str('color_identity'),
      typeLine: str('type_line'),
      manaCost: str('mana_cost'),
      folder: folder is num ? folder.toInt() : null,
      scryfallId: str('scryfall_id'),
    );
  }

  final int quantity;

  /// `identified`, `unidentified` or `face_down`.
  final String status;
  final String? name;
  final String? setCode;
  final String? collectorNumber;
  final String? rarity;
  final String? colorIdentity;
  final String? typeLine;
  final String? manaCost;
  final int? folder;
  final String? scryfallId;
}

/// Where the export rows come from. Failures are [CardsException]s.
abstract class CollectionExportSource {
  Future<List<CollectionRow>> fetchRows();
}

/// Real server: `GET /cards/export` (every card the user owns).
class ApiCollectionExportSource implements CollectionExportSource {
  ApiCollectionExportSource({ServerJsonClient? client})
      : _client = client ?? ServerJsonClient();

  final ServerJsonClient _client;

  @override
  Future<List<CollectionRow>> fetchRows() async {
    final json = await _client.get('/cards/export');
    final rows = json['rows'];
    if (rows is! List) throw const CardsException(CardsFailure.serverError);
    return rows
        .whereType<Map>()
        .map((r) => CollectionRow.fromJson(r.cast<String, Object?>()))
        .toList();
  }
}

/// Column names of the CSV file, in order.
const List<String> collectionCsvHeader = [
  'quantity',
  'name',
  'set_code',
  'collector_number',
  'rarity',
  'color_identity',
  'type_line',
  'mana_cost',
  'folder',
  'status',
  'scryfall_id',
];

/// The collection as CSV text, RFC 4180 (RF24 / RNF17): a header line, CRLF
/// line endings, and fields with a comma, quote or line break are quoted
/// (quotes doubled).
String buildCollectionCsv(List<CollectionRow> rows) {
  final out = StringBuffer();
  void line(List<Object?> fields) {
    out
      ..write(fields.map(_csvField).join(','))
      ..write('\r\n');
  }

  line(collectionCsvHeader);
  for (final r in rows) {
    line([
      r.quantity,
      r.name,
      r.setCode,
      r.collectorNumber,
      r.rarity,
      r.colorIdentity,
      r.typeLine,
      r.manaCost,
      r.folder,
      r.status,
      r.scryfallId,
    ]);
  }
  return out.toString();
}

final RegExp _needsQuotes = RegExp('[",\r\n]');

String _csvField(Object? value) {
  final text = value?.toString() ?? '';
  return text.contains(_needsQuotes) ? '"${text.replaceAll('"', '""')}"' : text;
}

/// File name for an export made at [now], e.g. `cardforge-collection-20261111-2015.csv`.
String collectionCsvFileName(DateTime now) {
  String two(int n) => n.toString().padLeft(2, '0');
  return 'cardforge-collection-${now.year}${two(now.month)}${two(now.day)}'
      '-${two(now.hour)}${two(now.minute)}.csv';
}

/// Saves the CSV text as a file and hands it to the system share sheet (save
/// to Files/Drive, email...). [origin] is where the share sheet points from
/// (required on iPad). Swapped in tests.
typedef CsvSharer = Future<void> Function(
    String fileName, String csv, Rect? origin);

/// The real [CsvSharer]: writes the file in the app's temporary folder, then
/// opens the share sheet.
Future<void> shareCsvFile(String fileName, String csv, Rect? origin) async {
  final path = await writeCollectionCsv(fileName, csv);
  await SharePlus.instance.share(ShareParams(
    files: [XFile(path, mimeType: 'text/csv')],
    subject: 'CardForge collection',
    sharePositionOrigin: origin,
  ));
}

/// Writes [csv] to [fileName] in the app's temporary folder and returns its
/// path. The file starts with a UTF-8 byte order mark so that Excel shows
/// accented card names correctly (other tools ignore it).
Future<String> writeCollectionCsv(String fileName, String csv) async {
  final dir = await getTemporaryDirectory();
  final file = File('${dir.path}/$fileName');
  await file.writeAsBytes([0xEF, 0xBB, 0xBF, ...utf8.encode(csv)], flush: true);
  return file.path;
}
