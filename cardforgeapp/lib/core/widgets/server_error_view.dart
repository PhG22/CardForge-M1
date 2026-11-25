import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../features/auth/auth_service.dart';
import '../../features/cards/card_models.dart';
import '../l10n/app_strings.dart';
import '../theme/app_theme.dart';

/// Full-screen error for data that comes from the server (runs, export).
/// An expired session offers "Sign in again"; anything else "Try again".
class ServerErrorView extends StatelessWidget {
  const ServerErrorView({
    super.key,
    required this.title,
    required this.failure,
    required this.onRetry,
  });

  final String title;
  final CardsFailure failure;
  final VoidCallback onRetry;

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
            Text(title, style: text.titleMedium, textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text(
              l10n.cardsFailure(failure),
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: expired
                  ? () async {
                      await AuthService.instance.signOut();
                      if (context.mounted) context.go('/login');
                    }
                  : onRetry,
              child: Text(expired ? l10n.signInAgain : l10n.tryAgain),
            ),
          ],
        ),
      ),
    );
  }
}
