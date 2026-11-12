import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import '../../features/alerts/machine_alert.dart';
import '../../features/auth/auth_service.dart';
import '../../features/cards/card_models.dart';
import '../../features/machine/api/machine_api_models.dart';
import '../../features/machine/machine_controller.dart';
import '../../features/machine/machine_models.dart';
import '../../features/runs/run_models.dart';
import 'strings_en.dart';
import 'strings_pt.dart';

/// All user-visible text. Every language must implement every member, so a
/// missing translation is a compile error instead of a blank label at runtime.
///
/// Use it with `context.l10n.signIn`. To add a language, create a new subclass
/// and register it in [supportedLocales] and [forLocale].
abstract class AppStrings {
  const AppStrings();

  static const List<Locale> supportedLocales = [Locale('en'), Locale('pt')];
  static const LocalizationsDelegate<AppStrings> delegate =
      _AppStringsDelegate();

  static AppStrings of(BuildContext context) =>
      Localizations.of<AppStrings>(context, AppStrings)!;

  static AppStrings forLocale(Locale locale) =>
      locale.languageCode == 'pt' ? const PtStrings() : const EnStrings();

  // Common
  String get tryAgain;
  String get back;
  String get view;
  String get done;
  String get close;
  String comingSoon(String feature);

  // Splash
  String get appTagline;
  String get splashConnecting;
  String get splashCheckingSession;
  String get serverUnreachableTitle;
  String get serverUnreachableHint;

  // Login
  String get loginSubtitle;
  String get usernameOrEmail;
  String get password;
  String get showPassword;
  String get hidePassword;
  String get rememberMe;
  String get signIn;
  String get forgotPassword;
  String get orDivider;
  String get continueWithGoogle;
  String get newHere;
  String get createAccount;
  String get passwordRecoverySoon;
  String get usernameRequired;
  String get invalidEmail;
  String get passwordRequired;
  String get passwordTooShort;
  String authError(AuthFailure failure);

  // Sign-up
  String get signUpSubtitle;
  String get username;
  String get email;
  String get confirmPassword;
  String get chooseUsername;
  String get usernameRules;
  String get emailRequired;
  String get newPasswordTooShort;
  String get passwordsDoNotMatch;
  String get alreadyHaveAccount;

  // Home and menu
  String get noMachineTitle;
  String get noMachineBody;
  String get machineLinkedBody;
  String get menuMachineControl;
  String get menuCardsInFolders;
  String get menuRuns;
  String get menuCsvReport;
  String get menuSortingRules;
  String get linkMachine;
  String get alerts;
  String get settings;
  String get signOut;

  // Link machine
  String get selectMachine;
  String get scanningForMachines;
  String get scanAgain;
  String get noMachinesFound;
  String get noMachinesHint;
  String get scanFailedTitle;
  String get enterMachinePassword;
  String machinePasswordHint(String machineName);
  String get machinePassword;
  String get machinePasswordRequired;
  String get verifyPassword;
  String get chooseAnotherMachine;
  String get wifiQuestionTitle;
  String get wifiQuestionBody;
  String get sendWifi;
  String get skipWifi;
  String get wifiFormTitle;
  String get networkName;
  String get networkNameRequired;
  String get wifiPassword;
  String get wifiPasswordHint;
  String get wifiTip;
  String get sendAndLink;
  String get linkingTitle;
  String get linkingHint;
  String linkStep(LinkStep step);
  String get linkSuccessTitle;
  String linkSuccessBody(String machineName);
  String get linkFailureTitle;
  String linkFailure(MachineLinkFailure failure);
  String get wifiPasswordInvalid;
  String get openMachineControl;

  // Machine control
  String connectionLabel(MachineConnection connection);
  String get noMachineControlBody;
  String get noAddressTitle;
  String get noAddressBody;
  String get machineOfflineTitle;
  String machineOfflineBody(String address);
  String get findOverBluetooth;
  String get findingMachine;
  String get simulatedMachine;
  String get topOnlyMode;
  String runStateLabel(ControllerState state);
  String get cardsProcessed;
  String get currentFolder;
  String get startSorting;
  String get startingHint;
  String get stopSorting;
  String get resumeSorting;
  String get resumingHint;
  String get resumeProblemsTitle;
  String get pausedHint;
  String faultDescription(String? code);
  String get emergencyStop;
  String get startProblemsTitle;
  String startProblem(StartProblem problem);
  String machineApiError(MachineApiException error);
  String get lastRun;
  String runResult(RunInfo run);
  String get recentlySorted;
  String sortedCard(SortedCard card);
  String get diagnostics;
  String get refresh;
  String get topModule;
  String get bottomModule;
  String get moduleOnline;
  String get moduleOffline;
  String get stackHasCards;
  String get platformHasCard;
  String get flap;
  String flapValue(int angle);
  String get roller;
  String get homed;
  String get position;
  String positionValue(int? position);
  String get drift;
  String get foldersWithCards;
  String get none;
  String get yes;
  String get no;
  String get baseTest;
  String get baseTestHint;
  String get homeBase;
  String get forgetMachine;
  String forgetMachineConfirm(String machineName);
  String get cancel;
  String get forget;

  // Cards in folders
  String get searchCardsHint;
  String get clearSearch;
  String get allCardsHeader;
  String resultsFor(String query);
  String cardsCount(int count);
  String copiesCount(int quantity);
  String folderLabel(int folder);
  String get cardsEmptyTitle;
  String get cardsEmptyHint;
  String get noCardsFoundTitle;
  String get noCardsFoundHint;
  String get cardsLoadFailedTitle;
  String cardsFailure(CardsFailure failure);
  String get signInAgain;

  // Run history and results (RF22)
  String get runsLoadFailedTitle;
  String get runsEmptyTitle;
  String get runsEmptyHint;
  String get loadMore;
  String runStatusLabel(RunStatus status);
  String dateTime(DateTime time);
  String runCardsSummary(int cards, int identified);
  String get runResultsTitle;
  String get runLoadFailedTitle;
  String runOutcome(RunSummary run);
  String get identifiedLabel;
  String get unidentifiedLabel;
  String get faceDownLabel;
  String get cardsPerFolder;
  String get cardsInThisRun;
  String get noCardsInRun;
  String scanOutcome(ScanOutcome outcome);
  String runCardDetails(RunCard card);

  // CSV export (RF24)
  String get exportTitle;
  String get exportHint;
  String get exportCsv;
  String get exportNothing;
  String get exportFileFailed;
  String get exportFailedTitle;
  String exportDone(int cards, String fileName);

  // Settings
  String get languageSection;
  String get developerTools;
  String get simulateAlerts;

  // Alerts
  String get noAlerts;
  String get noAlertsHint;
  String get markAllRead;
  String get clearAll;
  String get yourMachine;
  String alertTitle(AlertType type);
  String alertMessage(MachineAlert alert);

  // Notifications with the app in the background (RF38)
  String get alertsChannelName;
  String get alertsChannelDescription;
  String get watchChannelName;
  String get watchChannelDescription;
  String get watchingRunTitle;
  String watchingRunText(String? machineName);
}

class _AppStringsDelegate extends LocalizationsDelegate<AppStrings> {
  const _AppStringsDelegate();

  @override
  bool isSupported(Locale locale) => AppStrings.supportedLocales
      .any((supported) => supported.languageCode == locale.languageCode);

  @override
  Future<AppStrings> load(Locale locale) =>
      SynchronousFuture<AppStrings>(AppStrings.forLocale(locale));

  @override
  bool shouldReload(covariant LocalizationsDelegate<AppStrings> old) => false;
}

extension AppStringsContext on BuildContext {
  AppStrings get l10n => AppStrings.of(this);
}
