import 'package:cardforge/core/theme/app_theme.dart';
import 'package:cardforge/core/widgets/card_emblem.dart';
import 'package:cardforge/core/widgets/magic_backdrop.dart';
import 'package:cardforge/core/widgets/magic_panel.dart';
import 'package:cardforge/core/widgets/magic_scaffold.dart';
import 'package:cardforge/core/widgets/magic_symbols.dart';
import 'package:cardforge/core/widgets/magic_wordmark.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';

Widget _app(Widget home) => MaterialApp(theme: AppTheme.dark(), home: home);

void main() {
  setUpAll(() => GoogleFonts.config.allowRuntimeFetching = false);

  group('MagicScaffold', () {
    testWidgets('without an AppBar, the content starts at the top of the screen',
        (tester) async {
      await tester.pumpWidget(_app(const MagicScaffold(body: Text('Body'))));

      expect(find.byType(MagicBackdrop), findsOneWidget);
      expect(tester.getTopLeft(find.text('Body')).dy, 0);
    });

    testWidgets('with an AppBar, the content starts right below it',
        (tester) async {
      await tester.pumpWidget(_app(MagicScaffold(
        appBar: AppBar(title: const Text('Title')),
        body: const Text('Body'),
      )));

      expect(find.text('Title'), findsOneWidget);
      expect(tester.getTopLeft(find.text('Body')).dy,
          greaterThanOrEqualTo(kToolbarHeight));
    });
  });

  group('MagicScaffold symbols', () {
    testWidgets('are off by default and the orbs are on', (tester) async {
      await tester.pumpWidget(_app(const MagicScaffold(body: Text('Body'))));

      expect(find.byType(MagicBackdrop), findsOneWidget);
      expect(find.byKey(const ValueKey('mana-symbols')), findsNothing);
    });

    testWidgets('can replace the orbs and push the body below them',
        (tester) async {
      await tester.pumpWidget(_app(MagicScaffold(
        appBar: AppBar(title: const Text('Title')),
        showOrbs: false,
        showSymbols: true,
        body: const Text('Body'),
      )));

      expect(find.byType(MagicBackdrop), findsNothing);
      expect(find.byKey(const ValueKey('mana-symbols')), findsOneWidget);

      final screenWidth = tester.view.physicalSize.width / tester.view.devicePixelRatio;
      final symbols = tester.getRect(find.byType(MagicSymbols));
      expect(symbols.top, closeTo(kToolbarHeight, 0.01));
      expect(tester.getTopLeft(find.text('Body')).dy,
          closeTo(kToolbarHeight + MagicSymbols.heightFor(screenWidth) + 8, 0.5));
    });

    testWidgets('without an app bar they start at the top of the screen',
        (tester) async {
      await tester.pumpWidget(_app(const MagicScaffold(
        showOrbs: false,
        showSymbols: true,
        body: Text('Body'),
      )));

      expect(tester.getRect(find.byType(MagicSymbols)).top, 0);
    });

    test('the row height follows the screen width, up to a maximum', () {
      expect(MagicSymbols.heightFor(390),
          closeTo((390 - 48) / MagicSymbols.aspectRatio, 0.01));
      expect(MagicSymbols.heightFor(1200),
          closeTo(372 / MagicSymbols.aspectRatio, 0.01));
    });
  });

  testWidgets('MagicPanel shows its content', (tester) async {
    await tester.pumpWidget(
        _app(const Scaffold(body: MagicPanel(child: Text('Inside')))));

    expect(find.text('Inside'), findsOneWidget);
  });

  testWidgets('MagicWordmark writes CARDFORGE in uppercase', (tester) async {
    await tester.pumpWidget(_app(const Scaffold(body: MagicWordmark())));

    expect(find.text('CARDFORGE'), findsOneWidget);
  });

  group('CardEmblem', () {
    testWidgets('has the expected size at scale 1', (tester) async {
      await tester.pumpWidget(
          _app(const Scaffold(body: Center(child: CardEmblem()))));

      final size = tester.getSize(find.byType(CardEmblem));
      expect(size.width, closeTo(92 * 1.9, 0.01));
      expect(size.height, closeTo(128 * 1.12, 0.01));
    });

    testWidgets('the scale shrinks it proportionally', (tester) async {
      await tester.pumpWidget(_app(
          const Scaffold(body: Center(child: CardEmblem(scale: 0.5)))));

      final size = tester.getSize(find.byType(CardEmblem));
      expect(size.width, closeTo(92 * 1.9 * 0.5, 0.01));
      expect(size.height, closeTo(128 * 1.12 * 0.5, 0.01));
    });
  });
}
