import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_scope.dart';
import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/magic_panel.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../auth/auth_service.dart';
import 'card_models.dart';
import 'cards_in_folders_controller.dart';

/// Cards in folders: a search bar on top and, below it, the cards with the
/// folders (1 to 6) each one is in. With nothing typed, it lists the cards that
/// are in the folders. Everything comes from the server.
class CardsInFoldersScreen extends StatefulWidget {
  const CardsInFoldersScreen({super.key});

  @override
  State<CardsInFoldersScreen> createState() => _CardsInFoldersScreenState();
}

class _CardsInFoldersScreenState extends State<CardsInFoldersScreen> {
  late final CardsInFoldersController _controller;
  final _searchCtrl = TextEditingController();
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _controller =
        CardsInFoldersController(AppScope.of(context).cardRepository)..load();
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

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;

    return MagicScaffold(
      showOrbs: false,
      showSymbols: true,
      appBar: AppBar(title: Text(l10n.menuCardsInFolders)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 8),
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
    if (c.items.isEmpty) {
      return _EmptyState(searching: c.isSearching);
    }

    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
        itemCount: c.items.length + 2, // header + cards + footer
        itemBuilder: (context, index) {
          if (index == 0) return _Header(controller: c);
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

class _Header extends StatelessWidget {
  const _Header({required this.controller});

  final CardsInFoldersController controller;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          Expanded(
            child: Text(
              controller.isSearching
                  ? l10n.resultsFor(controller.query)
                  : l10n.allCardsHeader,
              style: text.titleMedium,
            ),
          ),
          const SizedBox(width: 12),
          Text(
            l10n.cardsCount(controller.total),
            style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.controller});

  final CardsInFoldersController controller;

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

class _CardTile extends StatelessWidget {
  const _CardTile({required this.card});

  final FolderedCard card;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return MagicPanel(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(card.name, style: text.titleMedium),
          if (card.folders.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final entry in card.folders)
                  _FolderChip(
                    key: ValueKey('chip-${card.id}-${entry.folder}'),
                    entry: entry,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _FolderChip extends StatelessWidget {
  const _FolderChip({super.key, required this.entry});

  final CardFolderEntry entry;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;

    return Semantics(
      label: '${l10n.folderLabel(entry.folder)}, '
          '${l10n.copiesCount(entry.quantity)}',
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: MagicColors.goldSoft),
            color: MagicColors.cardFace,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.folder_rounded,
                  size: 16, color: MagicColors.gold),
              const SizedBox(width: 6),
              Text(
                l10n.folderLabel(entry.folder),
                style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
              ),
              const SizedBox(width: 6),
              Text(
                '×${entry.quantity}',
                style:
                    text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.searching});

  final bool searching;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final text = Theme.of(context).textTheme;
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              searching ? Icons.search_off_rounded : Icons.folder_open_rounded,
              size: 48,
              color: MagicColors.goldSoft,
            ),
            const SizedBox(height: 12),
            Text(
              searching ? l10n.noCardsFoundTitle : l10n.cardsEmptyTitle,
              style: text.titleMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 4),
            Text(
              searching ? l10n.noCardsFoundHint : l10n.cardsEmptyHint,
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              textAlign: TextAlign.center,
            ),
          ],
        ),
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
