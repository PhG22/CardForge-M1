import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/server_error_view.dart';
import '../cards/card_models.dart';
import '../cards/color_filter_bar.dart';
import 'run_models.dart';
import 'runs_screen.dart' show runStatusColor, runStatusIcon;

/// Results of one finished run (RF22): how it ended, how many were
/// identified, the cards per color (tap a color to see only those cards),
/// and every card in the order it was sorted.
class RunDetailsScreen extends StatefulWidget {
  const RunDetailsScreen({super.key, required this.runId});

  final String runId;

  @override
  State<RunDetailsScreen> createState() => _RunDetailsScreenState();
}

class _RunDetailsScreenState extends State<RunDetailsScreen> {
  RunDetails? _details;
  CardsFailure? _failure;

  /// The color shown in the list; null = every card.
  ColorGroup? _color;

  /// Shows only the unidentified and face-down cards.
  bool _onlyNotIdentified = false;

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

  List<RunCard> _visibleCards(RunDetails details) => [
        for (final card in details.cards)
          if (_onlyNotIdentified
              ? card.outcome != ScanOutcome.identified
              : _color == null || card.color == _color)
            card,
      ];

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
            MagicPanel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(l10n.cardsPerColor,
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 12),
                  ColorFilterBar(
                    counts: details.colorCounts,
                    selected: _color,
                    onSelected: (group) => setState(() {
                      _color = group;
                      _onlyNotIdentified = false;
                    }),
                    notIdentified: details.unidentified + details.faceDown,
                    notIdentifiedSelected: _onlyNotIdentified,
                    onNotIdentifiedSelected: () => setState(() {
                      _color = null;
                      _onlyNotIdentified = true;
                    }),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 8),
              child: Text(l10n.cardsInThisRun,
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            if (details.cards.isEmpty)
              Text(l10n.noCardsInRun,
                  style: Theme.of(context).textTheme.bodyMedium)
            else if (_visibleCards(details).isEmpty)
              Text(l10n.noCardsFoundTitle,
                  style: Theme.of(context).textTheme.bodyMedium)
            else
              MagicPanel(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Column(
                  children: [
                    for (final card in _visibleCards(details))
                      _CardRow(card: card),
                  ],
                ),
              ),
          ],
        ),
      );
    }
    return MagicScaffold(
      showOrbs: false,
      appBar: AppBar(
        title: Text(l10n.runResultsTitle),
        actions: [
          IconButton(
            tooltip: l10n.menuMyCards,
            icon: const Icon(Icons.style_outlined),
            onPressed: () => context.push(
                '/cards?run=${Uri.encodeQueryComponent(widget.runId)}'),
          ),
          IconButton(
            tooltip: l10n.exportThisRun,
            icon: const Icon(Icons.ios_share_rounded),
            onPressed: () => context.push(
                '/export?run=${Uri.encodeQueryComponent(widget.runId)}'),
          ),
        ],
      ),
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
          ? (card.color == null ? null : ColorDot(group: card.color!))
          : Icon(
              card.outcome == ScanOutcome.faceDown
                  ? Icons.flip_to_back_rounded
                  : Icons.help_outline_rounded,
              color: MagicColors.goldSoft),
    );
  }
}
