import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/server_error_view.dart';
import '../cards/card_models.dart';
import '../machine/api/machine_api_models.dart' show RunStatus;
import 'run_models.dart';
import 'run_repository.dart';

/// Every sorting run of the signed-in user, newest first (from the server).
/// A run shows up here once it finished and the machine uploaded it. Tap one
/// for its results (RF22).
class RunsScreen extends StatefulWidget {
  const RunsScreen({super.key});

  static const int pageSize = 30;

  @override
  State<RunsScreen> createState() => _RunsScreenState();
}

class _RunsScreenState extends State<RunsScreen> {
  late final RunRepository _repository;
  List<RunSummary> _runs = const [];
  int _total = 0;
  bool _loading = true;
  bool _loadingMore = false;
  CardsFailure? _failure;

  @override
  void initState() {
    super.initState();
    _repository = AppScope.of(context).runRepository;
    _loadFirstPage();
  }

  Future<void> _loadFirstPage() async {
    try {
      final page = await _repository.fetchRuns(limit: RunsScreen.pageSize);
      if (!mounted) return;
      setState(() {
        _runs = page.runs;
        _total = page.total;
        _failure = null;
        _loading = false;
      });
    } on CardsException catch (e) {
      if (!mounted) return;
      setState(() {
        _failure = e.failure;
        _loading = false;
      });
    }
  }

  void _retry() {
    setState(() {
      _loading = true;
      _failure = null;
    });
    _loadFirstPage();
  }

  Future<void> _loadMore() async {
    if (_loadingMore) return;
    setState(() => _loadingMore = true);
    try {
      final page = await _repository.fetchRuns(
          offset: _runs.length, limit: RunsScreen.pageSize);
      if (!mounted) return;
      setState(() {
        _runs = [..._runs, ...page.runs];
        _total = page.total;
      });
    } on CardsException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(
            SnackBar(content: Text(context.l10n.cardsFailure(e.failure))));
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return MagicScaffold(
      showOrbs: false,
      showSymbols: true,
      appBar: AppBar(title: Text(l10n.menuRuns)),
      body: _body(context),
    );
  }

  Widget _body(BuildContext context) {
    final l10n = context.l10n;
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_failure != null) {
      return ServerErrorView(
        title: l10n.runsLoadFailedTitle,
        failure: _failure!,
        onRetry: _retry,
      );
    }
    if (_runs.isEmpty) {
      return RefreshIndicator(
        onRefresh: _loadFirstPage,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(32),
          children: [
            const SizedBox(height: 48),
            const Icon(Icons.history_rounded,
                size: 48, color: MagicColors.goldSoft),
            const SizedBox(height: 12),
            Text(l10n.runsEmptyTitle,
                style: Theme.of(context).textTheme.titleMedium,
                textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text(
              l10n.runsEmptyHint,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    final hasMore = _runs.length < _total;
    return RefreshIndicator(
      onRefresh: _loadFirstPage,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
        itemCount: _runs.length + 1,
        itemBuilder: (context, index) {
          if (index == _runs.length) {
            if (!hasMore) return const SizedBox(height: 8);
            return Center(
              child: _loadingMore
                  ? const Padding(
                      padding: EdgeInsets.all(8),
                      child: SizedBox(
                        width: 24,
                        height: 24,
                        child: CircularProgressIndicator(strokeWidth: 2.5),
                      ),
                    )
                  : TextButton(
                      onPressed: _loadMore, child: Text(l10n.loadMore)),
            );
          }
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: RunTile(run: _runs[index]),
          );
        },
      ),
    );
  }
}

/// One run in the list: when, how it ended, how many cards.
class RunTile extends StatelessWidget {
  const RunTile({super.key, required this.run});

  final RunSummary run;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return MagicPanel(
      padding: EdgeInsets.zero,
      child: InkWell(
        onTap: () => context.push('/runs/${Uri.encodeComponent(run.id)}'),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Icon(runStatusIcon(run.status),
                  color: runStatusColor(run.status), size: 28),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      run.startedAt == null
                          ? l10n.runStatusLabel(run.status)
                          : l10n.dateTime(run.startedAt!),
                      style: text.titleMedium,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '${l10n.runStatusLabel(run.status)} · '
                      '${l10n.runCardsSummary(run.cardsProcessed, run.identified)}',
                      style: text.bodyMedium
                          ?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: MagicColors.gold),
            ],
          ),
        ),
      ),
    );
  }
}

IconData runStatusIcon(RunStatus status) => switch (status) {
      RunStatus.completed => Icons.check_circle_outline_rounded,
      RunStatus.cancelled => Icons.stop_circle_outlined,
      RunStatus.failed => Icons.error_outline_rounded,
      RunStatus.paused => Icons.pause_circle_outline_rounded,
      RunStatus.running || RunStatus.unknown => Icons.history_rounded,
    };

Color runStatusColor(RunStatus status) => switch (status) {
      RunStatus.failed => MagicColors.danger,
      _ => MagicColors.gold,
    };
