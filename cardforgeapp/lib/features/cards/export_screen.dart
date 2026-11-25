import 'package:flutter/material.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/server_error_view.dart';
import '../runs/run_models.dart';
import 'card_models.dart';
import 'collection_export.dart';

/// Export (RF24): the user's whole collection or the cards of one run, from
/// the server, as a CSV (RFC 4180) or TXT file, then the share sheet so the
/// user can save it or send it (Files, Drive, email...). Cards are listed by
/// color group, not by tray.
class ExportScreen extends StatefulWidget {
  const ExportScreen({super.key, this.initialRunId});

  /// Starts on one run (`/export?run=<id>`); null = the whole collection.
  final String? initialRunId;

  @override
  State<ExportScreen> createState() => _ExportScreenState();
}

class _ExportScreenState extends State<ExportScreen> {
  final _buttonKey = GlobalKey();
  bool _working = false;
  CardsFailure? _failure;

  /// null = the whole collection.
  String? _runId;
  ExportFormat _format = ExportFormat.csv;
  List<RunSummary> _runs = const [];

  /// Cards in the last export (null before the first one).
  int? _exportedCards;
  String? _fileName;

  @override
  void initState() {
    super.initState();
    _runId = widget.initialRunId;
    _loadRuns();
  }

  Future<void> _loadRuns() async {
    try {
      final page =
          await AppScope.of(context).runRepository.fetchRuns(limit: 100);
      if (mounted) setState(() => _runs = page.runs);
    } catch (_) {
      // Only "whole collection" (and the run we came from) can be picked.
    }
  }

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
    final runId = _runId;
    final format = _format;
    setState(() {
      _working = true;
      _failure = null;
    });
    try {
      final rows = await deps.collectionExport.fetchRows(runId: runId);
      if (rows.isEmpty) {
        messenger
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(l10n.exportNothing)));
        return;
      }
      final fileName = exportFileName(DateTime.now(), format, runId: runId);
      await deps.shareFile(fileName, buildExport(rows, format), origin);
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
    final muted = text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant);

    // The run we came from stays selectable until the list arrives.
    final runIds = {for (final r in _runs) r.id};
    final orphan = _runId != null && !runIds.contains(_runId);

    return MagicScaffold(
      showOrbs: false,
      showSymbols: true,
      appBar: AppBar(title: Text(l10n.menuExport)),
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
                      const Icon(Icons.ios_share_rounded,
                          size: 40, color: MagicColors.gold),
                      const SizedBox(height: 12),
                      Text(l10n.exportTitle,
                          style: text.titleMedium, textAlign: TextAlign.center),
                      const SizedBox(height: 8),
                      Text(l10n.exportHint,
                          style: muted, textAlign: TextAlign.center),
                      const SizedBox(height: 20),
                      InputDecorator(
                        decoration: InputDecoration(
                          labelText: l10n.exportWhatLabel,
                          contentPadding: const EdgeInsets.symmetric(
                              horizontal: 12, vertical: 4),
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String?>(
                            key: const ValueKey('export-scope'),
                            value: _runId,
                            isExpanded: true,
                            items: [
                              DropdownMenuItem<String?>(
                                value: null,
                                child: Text(l10n.wholeCollection,
                                    overflow: TextOverflow.ellipsis),
                              ),
                              if (orphan)
                                DropdownMenuItem<String?>(
                                  value: _runId,
                                  child: Text(l10n.runCardsHeader,
                                      overflow: TextOverflow.ellipsis),
                                ),
                              for (final run in _runs)
                                DropdownMenuItem<String?>(
                                  value: run.id,
                                  child: Text(l10n.runScopeLabel(run),
                                      overflow: TextOverflow.ellipsis),
                                ),
                            ],
                            onChanged: _working
                                ? null
                                : (id) => setState(() => _runId = id),
                          ),
                        ),
                      ),
                      const SizedBox(height: 20),
                      Text(l10n.exportFormatLabel, style: text.titleSmall),
                      const SizedBox(height: 8),
                      SegmentedButton<ExportFormat>(
                        key: const ValueKey('export-format'),
                        segments: const [
                          ButtonSegment(
                            value: ExportFormat.csv,
                            label: Text('CSV'),
                            icon: Icon(Icons.table_chart_outlined),
                          ),
                          ButtonSegment(
                            value: ExportFormat.txt,
                            label: Text('TXT'),
                            icon: Icon(Icons.notes_rounded),
                          ),
                        ],
                        selected: {_format},
                        onSelectionChanged: _working
                            ? null
                            : (s) => setState(() => _format = s.first),
                      ),
                      const SizedBox(height: 8),
                      Text(l10n.exportFormatHint(_format), style: muted),
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
                        label: Text(l10n.exportAction),
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
