import 'package:flutter/material.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/server_error_view.dart';
import 'card_models.dart';
import 'collection_export.dart';

/// CSV report (RF24): exports every card in the user's collection, from the
/// server, as one CSV file (RFC 4180) and opens the share sheet so the user can
/// save it or send it (Files, Drive, email...).
class ExportScreen extends StatefulWidget {
  const ExportScreen({super.key});

  @override
  State<ExportScreen> createState() => _ExportScreenState();
}

class _ExportScreenState extends State<ExportScreen> {
  final _buttonKey = GlobalKey();
  bool _working = false;
  CardsFailure? _failure;

  /// Cards in the last export (null before the first one).
  int? _exportedCards;
  String? _fileName;

  Rect? _buttonRect() {
    final box = _buttonKey.currentContext?.findRenderObject();
    if (box is! RenderBox || !box.hasSize) return null;
    return box.localToGlobal(Offset.zero) & box.size;
  }

  Future<void> _export() async {
    final deps = AppScope.of(context);
    final l10n = context.l10n;
    final messenger = ScaffoldMessenger.of(context);
    final origin = _buttonRect();
    setState(() {
      _working = true;
      _failure = null;
    });
    try {
      final rows = await deps.collectionExport.fetchRows();
      if (rows.isEmpty) {
        messenger
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(l10n.exportNothing)));
        return;
      }
      final fileName = collectionCsvFileName(DateTime.now());
      await deps.shareCsv(fileName, buildCollectionCsv(rows), origin);
      if (!mounted) return;
      setState(() {
        _exportedCards = rows.fold<int>(0, (sum, r) => sum + r.quantity);
        _fileName = fileName;
      });
    } on CardsException catch (e) {
      if (mounted) setState(() => _failure = e.failure);
    } on Exception {
      // Could not write or share the file.
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(l10n.exportFileFailed)));
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return MagicScaffold(
      showOrbs: false,
      showSymbols: true,
      appBar: AppBar(title: Text(l10n.menuCsvReport)),
      body: _failure != null
          ? ServerErrorView(
              title: l10n.exportFailedTitle,
              failure: _failure!,
              onRetry: _export,
            )
          : ListView(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
              children: [
                MagicPanel(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const Icon(Icons.table_chart_outlined,
                          size: 40, color: MagicColors.gold),
                      const SizedBox(height: 12),
                      Text(l10n.exportTitle,
                          style: text.titleMedium, textAlign: TextAlign.center),
                      const SizedBox(height: 8),
                      Text(
                        l10n.exportHint,
                        style: text.bodyMedium
                            ?.copyWith(color: scheme.onSurfaceVariant),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 20),
                      FilledButton.icon(
                        key: _buttonKey,
                        onPressed: _working ? null : _export,
                        icon: _working
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2.5,
                                    color: MagicColors.background))
                            : const Icon(Icons.ios_share_rounded),
                        label: Text(l10n.exportCsv),
                      ),
                      if (_exportedCards != null) ...[
                        const SizedBox(height: 16),
                        Text(
                          l10n.exportDone(_exportedCards!, _fileName ?? ''),
                          style: text.bodyMedium,
                          textAlign: TextAlign.center,
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
    );
  }
}
