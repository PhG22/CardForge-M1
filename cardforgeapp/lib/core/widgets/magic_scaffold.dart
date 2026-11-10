import 'package:flutter/material.dart';

import 'magic_backdrop.dart';
import 'magic_symbols.dart';

/// Base scaffold for every screen: dark background with decoration on top.
///
/// - By default the decoration is the colored orbs ([showOrbs]).
/// - [showSymbols] adds the row of gold-framed symbols below the app bar (or
///   below the status bar when there is no app bar). Combine it with
///   `showOrbs: false` to replace the orbs with the symbols.
/// - Without `appBar` (Splash, Login): the `body` fills the screen; use SafeArea.
/// - With `appBar`: it stays transparent over the decoration and the `body`
///   starts right below it (and below the symbols, when shown).
class MagicScaffold extends StatelessWidget {
  const MagicScaffold({
    super.key,
    required this.body,
    this.appBar,
    this.drawer,
    this.backdropHeight = 300,
    this.backdropIntensity = 1,
    this.showOrbs = true,
    this.showSymbols = false,
  });

  final Widget body;
  final PreferredSizeWidget? appBar;
  final Widget? drawer;
  final double backdropHeight;
  final double backdropIntensity;
  final bool showOrbs;
  final bool showSymbols;

  /// Space between the symbols and the content below them.
  static const double _symbolsGap = 8;

  @override
  Widget build(BuildContext context) {
    final statusBar = MediaQuery.paddingOf(context).top;
    final width = MediaQuery.sizeOf(context).width;

    final appBarInset =
        appBar == null ? 0.0 : statusBar + appBar!.preferredSize.height;
    final symbolsTop = appBar == null ? statusBar : appBarInset;
    final symbolsBlock =
        showSymbols ? MagicSymbols.heightFor(width) + _symbolsGap : 0.0;
    final bodyTop = showSymbols ? symbolsTop + symbolsBlock : appBarInset;

    // The symbols already used the status bar space, so a SafeArea inside the
    // body must not add it again.
    final content = showSymbols && appBar == null
        ? MediaQuery.removePadding(context: context, removeTop: true, child: body)
        : body;

    return Scaffold(
      extendBodyBehindAppBar: true,
      appBar: appBar,
      drawer: drawer,
      body: Stack(
        children: [
          if (showOrbs)
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              height: backdropHeight,
              child: MagicBackdrop(intensity: backdropIntensity),
            ),
          if (showSymbols)
            Positioned(
              top: symbolsTop,
              left: 0,
              right: 0,
              child: const MagicSymbols(),
            ),
          Positioned.fill(top: bodyTop, child: content),
        ],
      ),
    );
  }
}
