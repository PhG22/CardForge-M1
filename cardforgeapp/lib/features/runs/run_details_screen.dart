import 'package:flutter/material.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/server_error_view.dart';
import '../cards/card_models.dart';
import 'run_models.dart';
import 'runs_screen.dart' show runStatusColor, runStatusIcon;

/// Results of one finished run (RF22): how it ended, the cards per folder,
/// how many were identified, and every card in the order it was sorted.
class RunDetailsScreen extends StatefulWidget {
  const RunDetailsScreen({super.key, required this.runId});

  final String runId;

  @override
  State<RunDetailsScreen> createState() => _RunDetailsScreenState();
}

class _RunDetailsScreenState extends State<RunDetailsScreen> {
  RunDetails? _details;
  CardsFailure? _failure;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final details =
          await AppScope.of(context).runRepository.fetchRun(widget.runId);
      if (!mounted) return;
      setState(() {
        _details = details;
        _failure = null;
      });
    } on CardsException catch (e) {
      if (!mounted) return;
      setState(() => _failure = e.failure);
    }
  }

  void _retry() {
    setState(() => _failure = null);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final details = _details;
    final Widget body;
    if (_failure != null) {
      body = ServerErrorView(
          title: l10n.runLoadFailedTitle, failure: _failure!, onRetry: _retry);
    } else if (details == null) {
      body = const Center(child: CircularProgressIndicator());
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
          children: [
            _Summary(details: details),
            const SizedBox(height: 16),
            _Folders(details: details),
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 8),
              child: Text(l10n.cardsInThisRun,
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            if (details.cards.isEmpty)
              Text(l10n.noCardsInRun,
                  style: Theme.of(context).textTheme.bodyMedium)
            else
              MagicPanel(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Column(
                  children: [
                    for (final card in details.cards) _CardRow(card: card),
                  ],
                ),
              ),
          ],
        ),
      );
    }
    return MagicScaffold(
      showOrbs: false,
      appBar: AppBar(title: Text(l10n.runResultsTitle)),
      body: body,
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.details});

  final RunDetails details;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final run = details.run;
    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(runStatusIcon(run.status),
                  color: runStatusColor(run.status), size: 28),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  run.startedAt == null
                      ? l10n.runStatusLabel(run.status)
                      : l10n.dateTime(run.startedAt!),
                  style: text.titleMedium,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(l10n.runOutcome(run),
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant)),
          const SizedBox(height: 16),
          Wrap(
            spacing: 24,
            runSpacing: 12,
            children: [
              _Count(label: l10n.cardsProcessed, value: run.cardsProcessed),
              _Count(label: l10n.identifiedLabel, value: details.identified),
              _Count(
                  label: l10n.unidentifiedLabel, value: details.unidentified),
              _Count(label: l10n.faceDownLabel, value: details.faceDown),
            ],
          ),
        ],
      ),
    );
  }
}

class _Count extends StatelessWidget {
  const _Count({required this.label, required this.value});

  final String label;
  final int value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('$value',
            style: text.headlineSmall?.copyWith(color: MagicColors.gold)),
        Text(label,
            style: text.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant)),
      ],
    );
  }
}

/// Cards per folder (the per-category counts of RF22).
class _Folders extends StatelessWidget {
  const _Folders({required this.details});

  final RunDetails details;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    return MagicPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(l10n.cardsPerFolder, style: text.titleMedium),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (var folder = 1; folder <= folderCount; folder++)
                Container(
                  key: ValueKey('folder-count-$folder'),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(999),
                    border: Border.all(color: MagicColors.goldSoft),
                    color: MagicColors.cardFace,
                  ),
                  child: Text(
                    '${l10n.folderLabel(folder)}  ×${details.folderCounts[folder] ?? 0}',
                    style: text.bodyMedium,
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _CardRow extends StatelessWidget {
  const _CardRow({required this.card});

  final RunCard card;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final scheme = Theme.of(context).colorScheme;
    final identified = card.outcome == ScanOutcome.identified;
    return ListTile(
      dense: true,
      leading: Text('#${card.number}',
          style: Theme.of(context)
              .textTheme
              .bodyMedium
              ?.copyWith(color: scheme.onSurfaceVariant)),
      title: Text(
          identified ? (card.name ?? '?') : l10n.scanOutcome(card.outcome)),
      subtitle: Text(l10n.runCardDetails(card)),
      trailing: identified
          ? null
          : Icon(
              card.outcome == ScanOutcome.faceDown
                  ? Icons.flip_to_back_rounded
                  : Icons.help_outline_rounded,
              color: MagicColors.goldSoft),
    );
  }
}
