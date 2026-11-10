import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/l10n/app_strings.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/card_emblem.dart';
import '../../core/widgets/magic_scaffold.dart';
import '../../core/widgets/magic_wordmark.dart';
import 'auth_service.dart';

/// Screen 2 - Authentication.
/// Username/email + password, "Remember me" and Google sign-in.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _identifierCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool _obscure = true;
  bool _remember = true;
  bool _loading = false;

  @override
  void dispose() {
    _identifierCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _loading = true);
    try {
      await action();
      if (mounted) context.go('/home');
    } on AuthException catch (e) {
      _showMessage(context.l10n.authError(e.failure));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  void _submit() {
    if (!_formKey.currentState!.validate()) return;
    _run(() => AuthService.instance.signInWithPassword(
          identifier: _identifierCtrl.text.trim(),
          password: _passwordCtrl.text,
          remember: _remember,
        ));
  }

  String? _validateIdentifier(String? v) {
    final value = v?.trim() ?? '';
    if (value.isEmpty) return context.l10n.usernameRequired;
    if (value.contains('@') &&
        !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(value)) {
      return context.l10n.invalidEmail;
    }
    return null;
  }

  String? _validatePassword(String? v) {
    if (v == null || v.isEmpty) return context.l10n.passwordRequired;
    if (v.length < 6) return context.l10n.passwordTooShort;
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;

    return MagicScaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 400),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SizedBox(height: 24),
                    const Center(child: CardEmblem()),
                    const SizedBox(height: 20),
                    const MagicWordmark(),
                    const SizedBox(height: 4),
                    Text(
                      l10n.loginSubtitle,
                      textAlign: TextAlign.center,
                      style: text.bodyMedium
                          ?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                    const SizedBox(height: 32),
                    TextFormField(
                      controller: _identifierCtrl,
                      enabled: !_loading,
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      autofillHints: const [AutofillHints.username],
                      decoration: InputDecoration(
                        labelText: l10n.usernameOrEmail,
                        prefixIcon: const Icon(Icons.person_outline),
                      ),
                      validator: _validateIdentifier,
                    ),
                    const SizedBox(height: 16),
                    TextFormField(
                      controller: _passwordCtrl,
                      enabled: !_loading,
                      obscureText: _obscure,
                      textInputAction: TextInputAction.done,
                      autofillHints: const [AutofillHints.password],
                      onFieldSubmitted: (_) => _submit(),
                      decoration: InputDecoration(
                        labelText: l10n.password,
                        prefixIcon: const Icon(Icons.lock_outline),
                        suffixIcon: IconButton(
                          tooltip:
                              _obscure ? l10n.showPassword : l10n.hidePassword,
                          icon: Icon(_obscure
                              ? Icons.visibility_outlined
                              : Icons.visibility_off_outlined),
                          onPressed: () => setState(() => _obscure = !_obscure),
                        ),
                      ),
                      validator: _validatePassword,
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        Checkbox(
                          value: _remember,
                          onChanged: _loading
                              ? null
                              : (v) => setState(() => _remember = v ?? true),
                        ),
                        Text(l10n.rememberMe, style: text.bodyMedium),
                      ],
                    ),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: _loading ? null : _submit,
                      child: _loading
                          ? const SizedBox(
                              width: 22,
                              height: 22,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2.5,
                                  color: MagicColors.background))
                          : Text(l10n.signIn),
                    ),
                    Center(
                      child: TextButton(
                        onPressed: _loading
                            ? null
                            : () => _showMessage(l10n.passwordRecoverySoon),
                        child: Text(l10n.forgotPassword),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Row(children: [
                      const Expanded(child: Divider()),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        child: Text(l10n.orDivider,
                            style: text.bodySmall
                                ?.copyWith(color: scheme.onSurfaceVariant)),
                      ),
                      const Expanded(child: Divider()),
                    ]),
                    const SizedBox(height: 16),
                    OutlinedButton.icon(
                      onPressed: _loading
                          ? null
                          : () => _run(AuthService.instance.signInWithGoogle),
                      // Replace with the official Google logo (asset) before publishing.
                      icon: const Icon(Icons.g_mobiledata_rounded, size: 28),
                      label: Text(l10n.continueWithGoogle),
                    ),
                    const SizedBox(height: 16),
                    // Wrap, not Row: on narrow screens or with large text the
                    // button moves to the next line instead of overflowing.
                    Wrap(
                      alignment: WrapAlignment.center,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(l10n.newHere, style: text.bodyMedium),
                        TextButton(
                          onPressed: _loading
                              ? null
                              : () => _showMessage(l10n.signUpSoon),
                          child: Text(l10n.createAccount),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
