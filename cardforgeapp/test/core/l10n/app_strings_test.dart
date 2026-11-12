import 'package:cardforge/core/l10n/app_strings.dart';
import 'package:cardforge/core/l10n/locale_controller.dart';
import 'package:cardforge/core/l10n/strings_en.dart';
import 'package:cardforge/core/l10n/strings_pt.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:cardforge/features/auth/auth_service.dart';
import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:cardforge/features/machine/machine_controller.dart';
import 'package:cardforge/features/machine/machine_models.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AppStrings', () {
    test('picks the language from the locale and falls back to English', () {
      expect(AppStrings.forLocale(const Locale('en')), isA<EnStrings>());
      expect(AppStrings.forLocale(const Locale('pt')), isA<PtStrings>());
      expect(AppStrings.forLocale(const Locale('pt', 'BR')), isA<PtStrings>());
      expect(AppStrings.forLocale(const Locale('fr')), isA<EnStrings>());
    });

    test('the two languages really differ', () {
      expect(const EnStrings().signIn, 'Sign in');
      expect(const PtStrings().signIn, 'Entrar');
    });

    test('background notification texts exist in both languages (RF38)', () {
      for (final strings in <AppStrings>[const EnStrings(), const PtStrings()]) {
        expect(strings.alertsChannelName, isNotEmpty);
        expect(strings.alertsChannelDescription, isNotEmpty);
        expect(strings.watchChannelName, isNotEmpty);
        expect(strings.watchChannelDescription, isNotEmpty);
        expect(strings.watchingRunTitle, isNotEmpty);
        expect(strings.watchingRunText('CardForge-A1B2'), contains('CardForge-A1B2'));
        expect(strings.watchingRunText(null),
            contains(strings.yourMachine.toLowerCase()));
      }
      expect(const EnStrings().watchingRunTitle,
          isNot(const PtStrings().watchingRunTitle));
    });

    for (final strings in <AppStrings>[const EnStrings(), const PtStrings()]) {
      test('${strings.runtimeType} has text for every enum value', () {
        for (final type in AlertType.values) {
          expect(strings.alertTitle(type), isNotEmpty, reason: '$type');
          expect(
            strings.alertMessage(MachineAlert(
              id: 1,
              type: type,
              createdAt: DateTime(2026),
              machineName: 'CardForge-A1B2',
              cardCount: 3,
            )),
            contains('CardForge-A1B2'),
            reason: '$type',
          );
        }
        for (final failure in MachineLinkFailure.values) {
          expect(strings.linkFailure(failure), isNotEmpty, reason: '$failure');
        }
        for (final failure in AuthFailure.values) {
          expect(strings.authError(failure), isNotEmpty, reason: '$failure');
        }
        for (final failure in CardsFailure.values) {
          expect(strings.cardsFailure(failure), isNotEmpty, reason: '$failure');
        }
        for (final step in LinkStep.values) {
          expect(strings.linkStep(step), isNotEmpty, reason: '$step');
        }
        for (final connection in MachineConnection.values) {
          expect(strings.connectionLabel(connection), isNotEmpty,
              reason: '$connection');
        }
        for (final state in ControllerState.values) {
          expect(strings.runStateLabel(state), isNotEmpty, reason: '$state');
        }
        for (final failure in MachineApiFailure.values) {
          expect(strings.machineApiError(MachineApiException(failure)),
              isNotEmpty,
              reason: '$failure');
        }
        for (final status in RunStatus.values.where((s) => s != RunStatus.unknown)) {
          expect(
            strings.runResult(RunInfo(runId: 'r', status: status, cardsProcessed: 2)),
            isNotEmpty,
            reason: '$status',
          );
        }
      });

      test('${strings.runtimeType} explains every start problem', () {
        for (final code in [
          'STACK_EMPTY',
          'TRAYS_NOT_EMPTY',
          'PLATFORM_NOT_CLEAR',
          'RUN_ACTIVE',
          'START_CANCELLED',
          'MODULE_OFFLINE',
          'HOME_TIMEOUT',
          'HOMING',
          'SIGN_IN_REQUIRED',
          'SERVER_UNREACHABLE',
        ]) {
          final text = strings.startProblem(
              StartProblem(code: code, message: 'raw', trays: const [2, 5]));
          expect(text, isNot('raw'), reason: '$code has its own text');
        }
        // Unknown codes fall back to the machine's own message.
        expect(
          strings.startProblem(
              const StartProblem(code: 'NEW_CODE', message: 'From the Pi')),
          'From the Pi',
        );
      });
    }

    test('alert text falls back to a generic machine name', () {
      final alert = MachineAlert(
        id: 1,
        type: AlertType.wifiConnected,
        createdAt: DateTime(2026),
      );

      expect(const EnStrings().alertMessage(alert), 'Your machine is online.');
      expect(const PtStrings().alertMessage(alert), 'Sua máquina está online.');
    });
  });

  group('LocaleController', () {
    Future<SharedPreferences> prefsWith(Map<String, Object> values) async {
      SharedPreferences.setMockInitialValues(values);
      return SharedPreferences.getInstance();
    }

    test('defaults to English', () async {
      final controller = LocaleController(await prefsWith({}));

      expect(controller.locale.languageCode, 'en');
    });

    test('ignores an unknown saved language', () async {
      final controller =
          LocaleController(await prefsWith({'language_code': 'xx'}));

      expect(controller.locale.languageCode, 'en');
    });

    test('setLocale saves the choice and notifies listeners', () async {
      final prefs = await prefsWith({});
      final controller = LocaleController(prefs);
      var notified = 0;
      controller.addListener(() => notified++);

      await controller.setLocale(const Locale('pt'));
      await controller.setLocale(const Locale('pt')); // same language: ignored

      expect(controller.locale.languageCode, 'pt');
      expect(notified, 1);
      expect(prefs.getString('language_code'), 'pt');
      expect(LocaleController(prefs).locale.languageCode, 'pt');
    });
  });
}
