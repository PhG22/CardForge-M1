import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../auth/auth_service.dart';
import 'card_models.dart';
import 'collection_controller.dart';
import 'color_filter_bar.dart';

/// My cards: the user's identified cards, from every run or from one run
/// (picker on top), filtered by color (white, blue, black, red, green,
/// colorless, multicolor) and by name. Everything comes from the server.
class CollectionScreen extends StatefulWidget {
  const CollectionScreen({super.key, this.initialRunId});

  /// Opens the screen on one run's cards (`/cards?run=<id>`).
  final String? initialRunId;

  @override
  State<CollectionScreen> createState() => _CollectionScreenState();
}

class _CollectionScreenState extends State<CollectionScreen> {
  late final CollectionController _controller;
  final _searchCtrl = TextEditingController();
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    final deps = AppScope.of(context);
    _controller = CollectionController(
      deps.cardRepository,
      runs: deps.runRepository,
      runId: widget.initialRunId,
    )..load();
    _scroll.addListener(_loadMoreNearTheEnd);
  }

  @override
  void dispose() {
    _scroll.dispose();
    _searchCtrl.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _loadMoreNearTheEnd() {
    if (_scroll.hasClients && _scroll.position.extentAfter < 400) {
      _controller.loadMore();
    }
  }

  void _clearSearch() {
    _searchCtrl.clear();
    _controller.searchNow('');
  }

  Future<void> _signInAgain() async {
    await AuthService.instance.signOut();
    if (mounted) context.go('/login');
  }

  void _export() {
    final run = _controller.runId;
    context.push(run == null
        ? '/export'
        : '/export?run=${Uri.encodeQueryComponent(run)}');
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;

    return MagicScaffold(
      showOrbs: false,
      showSymbols: true,
      appBar: AppBar(
        title: Text(l10n.menuMyCards),
        actions: [
          IconButton(
            tooltip: l10n.menuExport,
            icon: const Icon(Icons.ios_share_rounded),
            onPressed: _export,
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 0),
            child: ListenableBuilder(
              listenable: _controller,
              builder: (context, _) => _ScopePicker(controller: _controller),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
            child: ValueListenableBuilder<TextEditingValue>(
              valueListenable: _searchCtrl,
              builder: (context, value, _) => TextField(
                controller: _searchCtrl,
                textInputAction: TextInputAction.search,
                onChanged: _controller.onQueryChanged,
                onSubmitted: _controller.searchNow,
                decoration: InputDecoration(
                  hintText: l10n.searchCardsHint,
                  prefixIcon: const Icon(Icons.search_rounded),
                  suffixIcon: value.text.isEmpty
                      ? null
                      : IconButton(
                          tooltip: l10n.clearSearch,
                          icon: const Icon(Icons.close_rounded),
                          onPressed: _clearSearch,
                        ),
                ),
              ),
            ),
          ),
          Expanded(
            child: ListenableBuilder(
              listenable: _controller,
              builder: (context, _) => _results(context),
            ),
          ),
        ],
      ),
    );
  }

  Widget _results(BuildContext context) {
    final c = _controller;

    if (c.loading && c.items.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (c.failure != null && c.items.isEmpty) {
      return _ErrorState(
        failure: c.failure!,
        onRetry: c.retry,
        onSignInAgain: _signInAgain,
      );
    }

    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
        // header (colors + count) + cards or the empty message + footer
        itemCount: (c.items.isEmpty ? 1 : c.items.length) + 2,
        itemBuilder: (context, index) {
          if (index == 0) return _Header(controller: c);
          if (c.items.isEmpty) {
            return index == 1
                ? _EmptyState(filtered: c.isFiltered)
                : const SizedBox(height: 8);
          }
          if (index == c.items.length + 1) return _Footer(controller: c);
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: _CardTile(card: c.items[index - 1]),
          );
        },
      ),
    );
  }
}

/// "Cards from": the whole collection or one of the user's runs.
class _ScopePicker extends StatelessWidget {
  const _ScopePicker({required this.controller});

  final CollectionController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final runs = controller.runs;
    final selected = controller.runId;
    // A run opened from a link may not be in the list yet: keep it selectable.
    final known = selected == null || runs.any((r) => r.id == selected);
    return InputDecorator(
      decoration: InputDecoration(
        labelText: l10n.cardsFromLabel,
        prefixIcon: const Icon(Icons.history_rounded),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String?>(
          key: const ValueKey('scope-picker'),
          value: known ? selected : null,
          isExpanded: true,
          items: [
            DropdownMenuItem<String?>(
              value: null,
              child:
                  Text(l10n.wholeCollection, overflow: TextOverflow.ellipsis),
            ),
            for (final run in runs)
              DropdownMenuItem<String?>(
                value: run.id,
                child: Text(l10n.runScopeLabel(run),
                    overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: controller.selectRun,
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.controller});

  final CollectionController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final title = controller.isSearching
        ? l10n.resultsFor(controller.query)
        : controller.runId == null
            ? l10n.allCardsHeader
            : l10n.runCardsHeader;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ColorFilterBar(
            counts: controller.colorCounts,
            selected: controller.color,
            onSelected: controller.selectColor,
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(child: Text(title, style: text.titleMedium)),
              const SizedBox(width: 12),
              Text(
                l10n.cardsCount(controller.total),
                style:
                    text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              ),
            ],
          ),
          if (controller.notIdentified > 0) ...[
            const SizedBox(height: 4),
            Text(
              l10n.notIdentifiedHidden(controller.notIdentified),
              style: text.bodySmall?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
        ],
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.controller});

  final CollectionController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final scheme = Theme.of(context).colorScheme;

    if (controller.loadingMore) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 8),
        child: Center(
          child: SizedBox(
            width: 24,
            height: 24,
            child: CircularProgressIndicator(strokeWidth: 2.5),
          ),
        ),
      );
    }
    if (controller.failure != null) {
      return Column(
        children: [
          Text(
            l10n.cardsFailure(controller.failure!),
            style: TextStyle(color: scheme.error),
            textAlign: TextAlign.center,
          ),
          TextButton(onPressed: controller.retry, child: Text(l10n.tryAgain)),
        ],
      );
    }
    return const SizedBox(height: 8);
  }
}

/// One card: name, how many copies, its color and the sets they come from.
class _CardTile extends StatelessWidget {
  const _CardTile({required this.card});

  final CollectionCard card;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final details = [
      l10n.colorGroupLabel(card.color),
      if (card.sets.isNotEmpty) card.sets,
      if (card.typeLine != null) card.typeLine!,
    ].join(' · ');
    return MagicPanel(
      key: ValueKey('card-${card.name}'),
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          ColorDot(group: card.color, size: 14),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(card.name, style: text.titleMedium),
                const SizedBox(height: 4),
                Text(
                  details,
                  style: text.bodySmall
                      ?.copyWith(color: scheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Semantics(
            label: l10n.copiesCount(card.quantity),
            child: ExcludeSemantics(
              child: Text(
                '×${card.quantity}',
                style: text.titleMedium?.copyWith(color: MagicColors.gold),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.filtered});

  final bool filtered;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 32),
      child: Column(
        children: [
          Icon(
            filtered ? Icons.search_off_rounded : Icons.style_outlined,
            size: 48,
            color: MagicColors.goldSoft,
          ),
          const SizedBox(height: 12),
          Text(
            filtered ? l10n.noCardsFoundTitle : l10n.cardsEmptyTitle,
            style: text.titleMedium,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 4),
          Text(
            filtered ? l10n.noCardsFoundHint : l10n.cardsEmptyHint,
            style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({
    required this.failure,
    required this.onRetry,
    required this.onSignInAgain,
  });

  final CardsFailure failure;
  final VoidCallback onRetry;
  final VoidCallback onSignInAgain;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    final expired = failure == CardsFailure.unauthorized;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off_rounded,
                size: 48, color: MagicColors.danger),
            const SizedBox(height: 12),
            Text(l10n.cardsLoadFailedTitle,
                style: text.titleMedium, textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text(
              l10n.cardsFailure(failure),
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: expired ? onSignInAgain : onRetry,
              child: Text(expired ? l10n.signInAgain : l10n.tryAgain),
            ),
          ],
        ),
      ),
    );
  }
}
