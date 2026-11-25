import 'dart:convert';
import 'dart:io';
import 'dart:ui' show Rect;

import 'package:cross_file/cross_file.dart' show XFile;
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart' show ShareParams, SharePlus;

import '../../core/services/server_json_client.dart';
import 'card_models.dart';

/// The file types the export can make (RF24).
enum ExportFormat {
  /// A table (RFC 4180) for Excel or Google Sheets.
  csv('csv', 'text/csv'),

  /// One line per print, `2 Lightning Bolt (M10) 146`, grouped by color.
  txt('txt', 'text/plain');

  const ExportFormat(this.extension, this.mimeType);

  final String extension;
  final String mimeType;
}

/// One line of the export: how many copies of a print the user has (in the
/// whole collection or in one run). Unidentified and face-down cards have no
/// name (see [status]).
class CollectionRow {
  const CollectionRow({
    required this.quantity,
    required this.status,
    this.name,
    this.setCode,
    this.collectorNumber,
    this.rarity,
    this.color,
    this.colorIdentity,
    this.typeLine,
    this.manaCost,
    this.scryfallId,
  });

  factory CollectionRow.fromJson(Map<String, Object?> json) {
    String? str(String key) => json[key] is String ? json[key] as String : null;
    final quantity = json['quantity'];
    final status = str('status') ?? 'identified';
    final identity = str('color_identity');
    return CollectionRow(
      quantity: quantity is num ? quantity.toInt() : 1,
      status: status,
      name: str('name'),
      setCode: str('set_code'),
      collectorNumber: str('collector_number'),
      rarity: str('rarity'),
      color: status == 'identified'
          ? ColorGroup.fromApi(json['color']) ??
              ColorGroup.fromIdentity(identity)
          : null,
      colorIdentity: identity,
      typeLine: str('type_line'),
      manaCost: str('mana_cost'),
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

  /// Null when the card was not identified.
  final ColorGroup? color;
  final String? colorIdentity;
  final String? typeLine;
  final String? manaCost;
  final String? scryfallId;

  bool get identified => status == 'identified';
}

/// Where the export rows come from. Failures are [CardsException]s.
abstract class CollectionExportSource {
  /// Every card of the user ([runId] null) or of one of their runs.
  Future<List<CollectionRow>> fetchRows({String? runId});
}

/// Real server: `GET /cards/export` or `GET /cards/export?run_id=<id>`.
class ApiCollectionExportSource implements CollectionExportSource {
  ApiCollectionExportSource({ServerJsonClient? client})
      : _client = client ?? ServerJsonClient();

  final ServerJsonClient _client;

  @override
  Future<List<CollectionRow>> fetchRows({String? runId}) async {
    final json = await _client.get('/cards/export',
        query: runId == null ? null : {'run_id': runId});
    final rows = json['rows'];
    if (rows is! List) throw const CardsException(CardsFailure.serverError);
    return rows
        .whereType<Map>()
        .map((r) => CollectionRow.fromJson(r.cast<String, Object?>()))
        .toList();
  }
}

/// The rows in the order of both files: by color group (white, blue, black,
/// red, green, colorless, multicolor), then by name, set and number; the
/// cards that were not identified last.
List<CollectionRow> sortForExport(List<CollectionRow> rows) {
  int rank(CollectionRow r) => r.color?.index ?? ColorGroup.values.length;
  String key(CollectionRow r) => [
        (r.name ?? '').toLowerCase(),
        r.setCode ?? '',
        (r.collectorNumber ?? '').padLeft(6, '0'),
        r.status,
      ].join('\u0000');
  return [...rows]..sort((a, b) {
      final byColor = rank(a).compareTo(rank(b));
      return byColor != 0 ? byColor : key(a).compareTo(key(b));
    });
}

/// Column names of the CSV file, in order.
const List<String> collectionCsvHeader = [
  'quantity',
  'name',
  'set_code',
  'collector_number',
  'rarity',
  'color',
  'color_identity',
  'type_line',
  'mana_cost',
  'status',
  'scryfall_id',
];

/// The cards as CSV text, RFC 4180 (RF24 / RNF17): a header line, CRLF line
/// endings, and fields with a comma, quote or line break are quoted (quotes
/// doubled). `color` is the color group in English (white, blue, black, red,
/// green, colorless, multicolor), empty for a card that was not identified.
String buildCollectionCsv(List<CollectionRow> rows) {
  final out = StringBuffer();
  void line(List<Object?> fields) {
    out
      ..write(fields.map(_csvField).join(','))
      ..write('\r\n');
  }

  line(collectionCsvHeader);
  for (final r in sortForExport(rows)) {
    line([
      r.quantity,
      r.name,
      r.setCode,
      r.collectorNumber,
      r.rarity,
      r.color?.name,
      r.colorIdentity,
      r.typeLine,
      r.manaCost,
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

/// The cards as plain text, one print per line in the usual import format
/// (`2 Lightning Bolt (M10) 146`), under one `// <Color> (n)` heading per
/// color group. Lines starting with `//` are comments for the import tools
/// (Moxfield, Archidekt, ManaBox...). Cards that were not identified have no
/// name, so they are only counted in a comment at the end.
String buildCollectionTxt(List<CollectionRow> rows) {
  final out = StringBuffer();
  ColorGroup? current;
  var first = true;
  final sorted = sortForExport(rows);
  for (final r in sorted.where((r) => r.identified && r.name != null)) {
    if (first || r.color != current) {
      current = r.color;
      final copies = sorted
          .where((x) => x.identified && x.name != null && x.color == current)
          .fold<int>(0, (sum, x) => sum + x.quantity);
      if (!first) out.write('\n');
      out.write('// ${_txtGroupName(current)} ($copies)\n');
      first = false;
    }
    final setPart =
        r.setCode == null ? '' : ' (${r.setCode!.toUpperCase()})';
    final number = r.setCode == null || r.collectorNumber == null
        ? ''
        : ' ${r.collectorNumber}';
    out.write('${r.quantity} ${r.name}$setPart$number\n');
  }
  final unidentified = sorted
      .where((r) => r.status == 'unidentified')
      .fold<int>(0, (sum, r) => sum + r.quantity);
  final faceDown = sorted
      .where((r) => r.status == 'face_down')
      .fold<int>(0, (sum, r) => sum + r.quantity);
  if (unidentified + faceDown > 0) {
    if (!first) out.write('\n');
    out.write('// Not identified: $unidentified unidentified, '
        '$faceDown face-down\n');
  }
  return out.toString();
}

String _txtGroupName(ColorGroup? group) => switch (group) {
      ColorGroup.white => 'White',
      ColorGroup.blue => 'Blue',
      ColorGroup.black => 'Black',
      ColorGroup.red => 'Red',
      ColorGroup.green => 'Green',
      ColorGroup.colorless => 'Colorless',
      ColorGroup.multicolor => 'Multicolor',
      null => 'Other',
    };

/// The file's text in [format].
String buildExport(List<CollectionRow> rows, ExportFormat format) =>
    switch (format) {
      ExportFormat.csv => buildCollectionCsv(rows),
      ExportFormat.txt => buildCollectionTxt(rows),
    };

/// File name for an export made at [now]:
/// `cardforge-collection-20261111-2015.csv` for the whole collection, or
/// `cardforge-run-12-20261111-2015.txt` for one run.
String exportFileName(DateTime now, ExportFormat format, {String? runId}) {
  String two(int n) => n.toString().padLeft(2, '0');
  final what = runId == null
      ? 'collection'
      : 'run-${runId.replaceAll(RegExp('[^A-Za-z0-9_-]'), '')}';
  return 'cardforge-$what-${now.year}${two(now.month)}${two(now.day)}'
      '-${two(now.hour)}${two(now.minute)}.${format.extension}';
}

/// Saves the text as a file and hands it to the system share sheet (save to
/// Files/Drive, email...). [origin] is where the share sheet points from
/// (required on iPad). Swapped in tests.
typedef ExportSharer = Future<void> Function(
    String fileName, String content, Rect? origin);

/// The real [ExportSharer]: writes the file in the app's temporary folder,
/// then opens the share sheet.
Future<void> shareExportFile(
    String fileName, String content, Rect? origin) async {
  final format =
      fileName.endsWith('.txt') ? ExportFormat.txt : ExportFormat.csv;
  final path = await writeExportFile(fileName, content);
  await SharePlus.instance.share(ShareParams(
    files: [XFile(path, mimeType: format.mimeType)],
    subject: 'CardForge cards',
    sharePositionOrigin: origin,
  ));
}

/// Writes [content] to [fileName] in the app's temporary folder and returns
/// its path. A CSV file starts with a UTF-8 byte order mark so that Excel
/// shows accented card names correctly (other tools ignore it); a TXT file
/// does not, because some import tools would read it as part of the first
/// card name.
Future<String> writeExportFile(String fileName, String content) async {
  final dir = await getTemporaryDirectory();
  final file = File('${dir.path}/$fileName');
  final bytes = utf8.encode(content);
  await file.writeAsBytes(
    fileName.endsWith('.csv') ? [0xEF, 0xBB, 0xBF, ...bytes] : bytes,
    flush: true,
  );
  return file.path;
}
