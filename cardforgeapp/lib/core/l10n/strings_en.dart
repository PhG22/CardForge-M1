import '../../features/alerts/machine_alert.dart';
import '../../features/auth/auth_service.dart';
import '../../features/cards/card_models.dart';
import '../../features/machine/api/machine_api_models.dart';
import '../../features/machine/machine_controller.dart';
import '../../features/machine/machine_models.dart';
import '../../features/runs/run_models.dart';
import 'app_strings.dart';

class EnStrings extends AppStrings {
  const EnStrings();

  // Common
  @override
  String get tryAgain => 'Try again';
  @override
  String get back => 'Back';
  @override
  String get view => 'View';
  @override
  String get done => 'Done';
  @override
  String get close => 'Close';
  @override
  String comingSoon(String feature) => '$feature is coming soon.';

  // Splash
  @override
  String get appTagline => 'Organize your Magic cards';
  @override
  String get splashConnecting => 'Connecting to server…';
  @override
  String get splashCheckingSession => 'Checking session…';
  @override
  String get serverUnreachableTitle => 'Could not reach the server';
  @override
  String get serverUnreachableHint =>
      'Check your internet connection and try again.';

  // Login
  @override
  String get loginSubtitle => 'Sign in to manage your sorter';
  @override
  String get usernameOrEmail => 'Username or email';
  @override
  String get password => 'Password';
  @override
  String get showPassword => 'Show password';
  @override
  String get hidePassword => 'Hide password';
  @override
  String get rememberMe => 'Remember me';
  @override
  String get signIn => 'Sign in';
  @override
  String get forgotPassword => 'Forgot password?';
  @override
  String get orDivider => 'or';
  @override
  String get continueWithGoogle => 'Continue with Google';
  @override
  String get newHere => 'New here?';
  @override
  String get createAccount => 'Create account';
  @override
  String get passwordRecoverySoon => 'Password recovery is coming soon.';
  @override
  String get usernameRequired => 'Enter your username or email';
  @override
  String get invalidEmail => 'Invalid email';
  @override
  String get passwordRequired => 'Enter your password';
  @override
  String get passwordTooShort => 'Password must have at least 6 characters';
  @override
  String authError(AuthFailure failure) => switch (failure) {
        AuthFailure.invalidCredentials => 'Invalid username or password.',
        AuthFailure.serverUnreachable =>
          'Cannot reach the server. Please try again.',
        AuthFailure.serverError => 'Server error. Please try again.',
        AuthFailure.googleCancelled => 'Google sign-in was cancelled.',
        AuthFailure.googleTokenMissing =>
          'Could not get a token from Google.',
        AuthFailure.usernameTaken =>
          'This username is taken. Choose another one.',
        AuthFailure.emailTaken =>
          'There is already an account with this email. Sign in instead.',
        AuthFailure.invalidSignUp =>
          'The server did not accept these details. Check them and try again.',
        AuthFailure.accountDisabled => 'This account is disabled.',
      };

  // Sign-up
  @override
  String get signUpSubtitle =>
      'The cards you sort are saved to your account.';
  @override
  String get username => 'Username';
  @override
  String get email => 'Email';
  @override
  String get confirmPassword => 'Confirm password';
  @override
  String get chooseUsername => 'Choose a username';
  @override
  String get usernameRules =>
      "3 to 32 letters, digits, '.', '_' or '-' (no spaces)";
  @override
  String get emailRequired => 'Enter your email';
  @override
  String get newPasswordTooShort => 'Password must have at least 8 characters';
  @override
  String get passwordsDoNotMatch => 'The passwords do not match';
  @override
  String get alreadyHaveAccount => 'Already have an account?';

  // Home and menu
  @override
  String get noMachineTitle => 'No machine linked';
  @override
  String get noMachineBody =>
      'Link your sorter to follow and control card sorting.';
  @override
  String get machineLinkedBody => 'Linked to your account';
  @override
  String get menuMachineControl => 'Machine control';
  @override
  String get menuCardsInFolders => 'Cards in folders';
  @override
  String get menuRuns => 'Sorting runs';
  @override
  String get menuCsvReport => 'CSV report';
  @override
  String get menuSortingRules => 'Sorting rules';
  @override
  String get linkMachine => 'Link machine';
  @override
  String get alerts => 'Alerts';
  @override
  String get settings => 'Settings';
  @override
  String get signOut => 'Sign out';

  // Link machine
  @override
  String get selectMachine => 'Select your machine';
  @override
  String get scanningForMachines => 'Searching for CardForge machines nearby…';
  @override
  String get scanAgain => 'Scan again';
  @override
  String get noMachinesFound => 'No machines found';
  @override
  String get noMachinesHint =>
      'Make sure the machine is powered on and nearby, then scan again.';
  @override
  String get scanFailedTitle => 'Bluetooth search failed';
  @override
  String get enterMachinePassword => 'Machine password';
  @override
  String machinePasswordHint(String machineName) =>
      'Enter the password of $machineName to allow linking.';
  @override
  String get machinePassword => 'Machine password';
  @override
  String get machinePasswordRequired => 'Enter the machine password';
  @override
  String get verifyPassword => 'Verify password';
  @override
  String get chooseAnotherMachine => 'Choose another machine';
  @override
  String get wifiQuestionTitle => 'Send Wi-Fi details?';
  @override
  String get wifiQuestionBody =>
      'The app controls your machine over Wi-Fi. Send your network name and '
      'password now? Skip only if the machine is already on your network.';
  @override
  String get sendWifi => 'Yes, send Wi-Fi';
  @override
  String get skipWifi => 'Skip for now';
  @override
  String get wifiFormTitle => 'Wi-Fi details';
  @override
  String get networkName => 'Network name (SSID)';
  @override
  String get networkNameRequired => 'Enter the network name';
  @override
  String get wifiPassword => 'Wi-Fi password';
  @override
  String get wifiPasswordHint => 'Leave empty for open networks';
  @override
  String get wifiTip =>
      'Use the network your phone is on: the app reaches the machine through it. '
      'Tip: the Raspberry Pi 3 Model B only sees 2.4 GHz networks.';
  @override
  String get sendAndLink => 'Send and link';
  @override
  String get linkingTitle => 'Linking your machine';
  @override
  String get linkingHint =>
      'The machine will confirm when everything is ready. '
      'This can take up to a minute.';
  @override
  String linkStep(LinkStep step) => switch (step) {
        LinkStep.sendingWifi => 'Sending Wi-Fi details…',
        LinkStep.waitingForWifi =>
          'Waiting for the machine to connect to Wi-Fi…',
        LinkStep.checkingConnection => 'Checking the connection over Wi-Fi…',
        LinkStep.done => 'Done',
      };
  @override
  String get linkSuccessTitle => 'Machine linked!';
  @override
  String linkSuccessBody(String machineName) =>
      '$machineName is ready. Start a sorting run from the machine control screen.';
  @override
  String get linkFailureTitle => 'Could not link the machine';
  @override
  String linkFailure(MachineLinkFailure failure) => switch (failure) {
        MachineLinkFailure.bluetoothUnavailable =>
          'Bluetooth is unavailable. Turn it on and try again.',
        MachineLinkFailure.bluetoothPermission =>
          'CardForge needs the Bluetooth (nearby devices) permission. '
              'Allow it in your phone settings and try again.',
        MachineLinkFailure.machineNotFound =>
          'Could not find the machine. Make sure it is on and close to the phone.',
        MachineLinkFailure.incompatibleMachine =>
          'This machine runs a different setup version. Update the machine '
              'or the app.',
        MachineLinkFailure.wrongPassword => 'Wrong machine password.',
        MachineLinkFailure.tooManyAttempts =>
          'Too many wrong passwords. Wait a minute and try again.',
        MachineLinkFailure.connectionLost =>
          'Lost the connection to the machine.',
        MachineLinkFailure.wifiNotFound =>
          'The machine could not find that Wi-Fi network. Check the name '
              '(the Raspberry Pi 3 only sees 2.4 GHz networks).',
        MachineLinkFailure.wifiWrongPassword =>
          'The Wi-Fi network rejected the password. Check it and try again.',
        MachineLinkFailure.wifiFailed =>
          'The machine could not connect to that Wi-Fi network. '
              'Check the name and password.',
        MachineLinkFailure.noNetwork =>
          'The machine is not connected to any network. Send the Wi-Fi details.',
        MachineLinkFailure.machineApiDown =>
          'The machine is on the network, but its control service is not '
              'running. Restart the machine and try again.',
        MachineLinkFailure.apiUnreachable =>
          'The machine joined the network, but this phone cannot reach it. '
              'Make sure the phone is on the same Wi-Fi (not mobile data).',
        MachineLinkFailure.timeout =>
          'The machine did not confirm in time. Please try again.',
        MachineLinkFailure.unknown => 'Something went wrong. Please try again.',
      };
  @override
  String get wifiPasswordInvalid =>
      'Use 8 to 63 characters, or leave it empty for an open network';
  @override
  String get openMachineControl => 'Open machine control';

  // Machine control
  @override
  String connectionLabel(MachineConnection connection) => switch (connection) {
        MachineConnection.none => 'No machine',
        MachineConnection.noAddress => 'Address unknown',
        MachineConnection.connecting => 'Connecting…',
        MachineConnection.online => 'Online',
        MachineConnection.offline => 'Offline',
      };
  @override
  String get noMachineControlBody =>
      'Link your machine first. You only need Bluetooth for that; '
      'after that the app controls it over Wi-Fi.';
  @override
  String get noAddressTitle => 'Machine address unknown';
  @override
  String get noAddressBody =>
      'This phone does not know where to find the machine on the Wi-Fi. '
      'Stand next to it and find it over Bluetooth.';
  @override
  String get machineOfflineTitle => 'Cannot reach the machine';
  @override
  String machineOfflineBody(String address) =>
      'No answer from $address. Check that the machine is on and that your '
      'phone is on the same Wi-Fi. If the router gave it a new address, '
      'find it over Bluetooth.';
  @override
  String get findOverBluetooth => 'Find over Bluetooth';
  @override
  String get findingMachine => 'Looking for the machine over Bluetooth…';
  @override
  String get simulatedMachine => 'Simulated machine (no hardware)';
  @override
  String get topOnlyMode => 'Top module only: no rotating base';
  @override
  String runStateLabel(ControllerState state) => switch (state) {
        ControllerState.idle => 'Ready',
        ControllerState.starting => 'Preparing…',
        ControllerState.running => 'Sorting',
        ControllerState.stopping => 'Stopping…',
        ControllerState.paused => 'Paused',
        ControllerState.manual => 'Moving the base…',
        ControllerState.unknown => 'Unknown',
      };
  @override
  String get cardsProcessed => 'Cards processed';
  @override
  String get currentFolder => 'Current folder';
  @override
  String get startSorting => 'Start sorting';
  @override
  String get startingHint =>
      'Preparing the machine: checking the sensors and homing the base. '
      'This takes up to 40 seconds.';
  @override
  String get stopSorting => 'Stop';
  @override
  String get resumeSorting => 'Resume';
  @override
  String get resumingHint =>
      'Homing the base again, then sorting continues. '
      'This takes up to 40 seconds.';
  @override
  String get resumeProblemsTitle => 'Cannot resume yet';
  @override
  String get pausedHint =>
      'Fix the problem on the machine, then press Resume. Stop ends the run here.';
  @override
  String faultDescription(String? code) => switch (code) {
        'FEED_TIMEOUT' => 'no card reached the platform (jam or empty stack).',
        'FEED_FAILED' => 'feeding finished but no card is on the platform.',
        'DROP_FAILED' => 'a card did not fall from the platform.',
        'MOVE_TIMEOUT' => 'the base did not reach the folder.',
        'HOME_TIMEOUT' ||
        'HOME_FAILED' =>
          'the base could not find its home position.',
        'NOT_HOMED' => 'the base lost its position.',
        null => 'the machine reported a fault.',
        _ => 'the machine reported $code.',
      };
  String _hms(DateTime t) {
    final local = t.toLocal();
    return '${_two(local.hour)}:${_two(local.minute)}:${_two(local.second)}';
  }
  @override
  String get emergencyStop => 'Emergency stop';
  @override
  String get startProblemsTitle => 'Cannot start yet';
  @override
  String startProblem(StartProblem problem) => switch (problem.code) {
        'STACK_EMPTY' => 'No cards on the stack. Load cards and try again.',
        'TRAYS_NOT_EMPTY' when problem.trays.length == 1 =>
          'Folder ${problem.trays.first} is not empty. Empty it and try again.',
        'TRAYS_NOT_EMPTY' when problem.trays.isNotEmpty =>
          'Folders ${problem.trays.join(', ')} are not empty. '
              'Empty them and try again.',
        'PLATFORM_NOT_CLEAR' =>
          'A card was left on the reading platform. Remove it and try again.',
        'RUN_ACTIVE' => 'A run is already in progress.',
        'START_CANCELLED' => 'The start was cancelled.',
        'MODULE_OFFLINE' => '${_moduleName(problem.module)} is not answering. '
            'Check its cable and power.',
        'HOME_TIMEOUT' => 'The base could not reach its home position in time.',
        'HOMING' =>
          'The base is homing after power-on. Try again in a few seconds.',
        'SIGN_IN_REQUIRED' =>
          'Your session expired. Sign in again to start a run.',
        'SERVER_UNREACHABLE' =>
          'The machine cannot reach the CardForge server, so the run '
              'cannot be saved to your account. Check its network.',
        'SERVER_ERROR' => 'The CardForge server had a problem. Try again.',
        _ => problem.message.isNotEmpty
            ? problem.message
            : 'The machine could not start (${problem.code}).',
      };
  String _moduleName(String? module) => switch (module) {
        'A1' => 'The top module (A1)',
        'A2' => 'The base (A2)',
        _ => 'A module',
      };
  @override
  String machineApiError(MachineApiException error) => switch (error.failure) {
        MachineApiFailure.unreachable =>
          'Cannot reach the machine. Check that it is on and that your phone '
              'is on the same Wi-Fi.',
        MachineApiFailure.conflict => switch (error.code) {
            'NOT_HOMED' => 'Home the base first.',
            'RUN_ACTIVE' => 'Not possible while a run is in progress.',
            'BOTTOM_DISABLED' => 'The base is disabled (top-only mode).',
            _ => error.message ?? 'Not possible right now.',
          },
        MachineApiFailure.moduleOffline =>
          'A module is not answering. Check the cables and power.',
        MachineApiFailure.hardware => switch (error.code) {
            'HOME_TIMEOUT' => 'The base could not reach its home position in time.',
            'HOME_FAILED' => 'The base could not find its reflective strip.',
            _ => 'Hardware problem${error.code == null ? '' : ' (${error.code})'}. '
                'Check the machine.',
          },
        MachineApiFailure.badResponse => 'Unexpected answer from the machine.',
      };
  @override
  String get lastRun => 'Last run';
  @override
  String runResult(RunInfo run) => switch (run.status) {
        RunStatus.running => 'Sorting in progress.',
        RunStatus.paused => 'Paused after ${cardsCount(run.cardsProcessed)}: '
            '${faultDescription(run.errorCode)}',
        RunStatus.completed => 'Finished: ${cardsCount(run.cardsProcessed)} sorted.',
        RunStatus.cancelled =>
          'Stopped by you after ${cardsCount(run.cardsProcessed)}.',
        RunStatus.failed => switch (run.errorCode) {
            'FEED_TIMEOUT' =>
              'Stopped: the feeder could not move a card (jam or empty stack).',
            'MODULE_OFFLINE' => 'Stopped: a module stopped responding.',
            _ => 'Stopped with an error'
                '${run.errorCode == null ? '' : ' (${run.errorCode})'}.',
          },
        RunStatus.unknown => run.message ?? '',
      };
  @override
  String get recentlySorted => 'Recently sorted';
  @override
  String sortedCard(SortedCard card) {
    final folder = card.tray == null ? '' : ' → ${folderLabel(card.tray!)}';
    return card.cleared
        ? 'Card #${card.number} dropped by the stop$folder'
        : 'Card #${card.number}$folder';
  }
  @override
  String get diagnostics => 'Diagnostics';
  @override
  String get refresh => 'Refresh';
  @override
  String get topModule => 'Top module (A1)';
  @override
  String get bottomModule => 'Base (A2)';
  @override
  String get moduleOnline => 'Online';
  @override
  String get moduleOffline => 'Not answering';
  @override
  String get stackHasCards => 'Cards on the stack';
  @override
  String get platformHasCard => 'Card on the platform';
  @override
  String get flap => 'Flap';
  @override
  String flapValue(int angle) => switch (angle) {
        80 => 'Closed (80°)',
        0 => 'Open (0°)',
        _ => '$angle°',
      };
  @override
  String get roller => 'Roller turning';
  @override
  String get homed => 'Homed';
  @override
  String get position => 'Position';
  @override
  String positionValue(int? position) =>
      position == null || position == 0 ? 'Between folders' : folderLabel(position);
  @override
  String get drift => 'Drift (steps)';
  @override
  String get foldersWithCards => 'Folders with cards';
  @override
  String get none => 'None';
  @override
  String get yes => 'Yes';
  @override
  String get no => 'No';
  @override
  String get baseTest => 'Base test';
  @override
  String get baseTestHint =>
      'Moves the base outside a run, to check the folder positions.';
  @override
  String get homeBase => 'Home';
  @override
  String get forgetMachine => 'Forget this machine';
  @override
  String forgetMachineConfirm(String machineName) =>
      'Forget $machineName on this phone? To use it again you will need to '
      'link it over Bluetooth.';
  @override
  String get cancel => 'Cancel';
  @override
  String get forget => 'Forget';

  // Cards in folders
  @override
  String get searchCardsHint => 'Search a card by name';
  @override
  String get clearSearch => 'Clear search';
  @override
  String get allCardsHeader => 'Cards in your folders';
  @override
  String resultsFor(String query) => 'Results for “$query”';
  @override
  String cardsCount(int count) => count == 1 ? '1 card' : '$count cards';
  @override
  String copiesCount(int quantity) =>
      quantity == 1 ? '1 copy' : '$quantity copies';
  @override
  String folderLabel(int folder) => 'Folder $folder';
  @override
  String get cardsEmptyTitle => 'No cards in the folders yet';
  @override
  String get cardsEmptyHint => 'Cards show up here after a sorting run.';
  @override
  String get noCardsFoundTitle => 'No cards found';
  @override
  String get noCardsFoundHint => 'Check the spelling or try another name.';
  @override
  String get cardsLoadFailedTitle => 'Could not load the cards';
  @override
  String cardsFailure(CardsFailure failure) => switch (failure) {
        CardsFailure.unauthorized =>
          'Your session expired. Please sign in again.',
        CardsFailure.serverUnreachable =>
          'Cannot reach the server. Check your connection and try again.',
        CardsFailure.serverError =>
          'The server had a problem. Please try again.',
      };
  @override
  String get signInAgain => 'Sign in again';

  // Run history and results
  @override
  String get runsLoadFailedTitle => 'Could not load your runs';
  @override
  String get runsEmptyTitle => 'No runs yet';
  @override
  String get runsEmptyHint =>
      'Runs you start from this account show up here when they finish.';
  @override
  String get loadMore => 'Load more';
  @override
  String runStatusLabel(RunStatus status) => switch (status) {
        RunStatus.completed => 'Finished',
        RunStatus.cancelled => 'Stopped',
        RunStatus.failed => 'Ended early',
        RunStatus.running => 'In progress',
        RunStatus.paused => 'Paused',
        RunStatus.unknown => 'Run',
      };
  @override
  String dateTime(DateTime time) => '${time.year}-${_two(time.month)}-'
      '${_two(time.day)} ${_two(time.hour)}:${_two(time.minute)}';
  String _two(int n) => n.toString().padLeft(2, '0');
  @override
  String runCardsSummary(int cards, int identified) =>
      '${cardsCount(cards)}, $identified identified';
  @override
  String get runResultsTitle => 'Run results';
  @override
  String get runLoadFailedTitle => 'Could not load this run';
  @override
  String runOutcome(RunSummary run) => switch (run.status) {
        RunStatus.completed when run.errorCode == 'BATCH_LIMIT' =>
          'Ended at the batch limit: ${cardsCount(run.cardsProcessed)} sorted.',
        RunStatus.completed =>
          'Finished with the stack empty: ${cardsCount(run.cardsProcessed)} sorted.',
        RunStatus.cancelled =>
          'Stopped by you after ${cardsCount(run.cardsProcessed)}.',
        RunStatus.failed => switch (run.errorCode) {
            'MODULE_OFFLINE' => 'Ended early: a module stopped responding.',
            'INTERRUPTED' =>
              'Ended early: the machine restarted during the run.',
            _ => 'Ended early with an error'
                '${run.errorCode == null ? '' : ' (${run.errorCode})'}.',
          },
        RunStatus.running ||
        RunStatus.paused ||
        RunStatus.unknown =>
          run.message ?? '',
      };
  @override
  String get identifiedLabel => 'Identified';
  @override
  String get unidentifiedLabel => 'Unidentified';
  @override
  String get faceDownLabel => 'Face-down';
  @override
  String get cardsPerFolder => 'Cards per folder';
  @override
  String get cardsInThisRun => 'Cards in this run';
  @override
  String get noCardsInRun => 'No card was sorted in this run.';
  @override
  String scanOutcome(ScanOutcome outcome) => switch (outcome) {
        ScanOutcome.identified => 'Identified',
        ScanOutcome.unidentified => 'Unidentified card',
        ScanOutcome.faceDown => 'Face-down card',
      };
  @override
  String runCardDetails(RunCard card) => [
        if (card.setCode != null)
          '${card.setCode!.toUpperCase()} ${card.collectorNumber ?? ''}'.trim(),
        if (card.folder != null) folderLabel(card.folder!),
      ].join(' · ');

  // CSV export
  @override
  String get exportTitle => 'Export your collection';
  @override
  String get exportHint =>
      'Every card you own, from all your runs, in one CSV file: name, set, '
      'collector number, folder and quantity. It opens in Excel, Google '
      'Sheets and most collection sites.';
  @override
  String get exportCsv => 'Export CSV';
  @override
  String get exportNothing =>
      'Your collection is empty: sort some cards first.';
  @override
  String get exportFileFailed => 'Could not create or share the file.';
  @override
  String get exportFailedTitle => 'Could not export the collection';
  @override
  String exportDone(int cards, String fileName) =>
      '${cardsCount(cards)} exported to $fileName.';

  // Settings
  @override
  String get languageSection => 'Language';
  @override
  String get developerTools => 'Developer tools';
  @override
  String get simulateAlerts => 'Simulate alerts';

  // Alerts
  @override
  String get noAlerts => 'No alerts yet';
  @override
  String get noAlertsHint => 'Machine events will show up here.';
  @override
  String get markAllRead => 'Mark all as read';
  @override
  String get clearAll => 'Clear all';
  @override
  String get yourMachine => 'Your machine';
  @override
  String alertTitle(AlertType type) => switch (type) {
        AlertType.wifiConnected => 'Machine connected to Wi-Fi',
        AlertType.wifiDisconnected => 'Machine lost Wi-Fi',
        AlertType.batchFinished => 'Sorting finished',
        AlertType.runFailed => 'Sorting stopped',
        AlertType.jamDetected => 'Card jam detected',
        AlertType.machineFault => 'Sorting paused',
        AlertType.doubleFeed => 'Double feed detected',
        AlertType.connectionLost => 'Connection lost',
        AlertType.machineLinked => 'Machine linked',
      };
  @override
  String alertMessage(MachineAlert alert) {
    final name = alert.machineName ?? yourMachine;
    return switch (alert.type) {
      AlertType.wifiConnected => '$name is online.',
      AlertType.wifiDisconnected => '$name is offline. Check your Wi-Fi network.',
      AlertType.batchFinished =>
        '$name processed ${alert.cardCount ?? 0} cards.',
      AlertType.runFailed =>
        '$name stopped with an error. Open machine control for details.',
      AlertType.jamDetected => '$name paused at ${_hms(alert.createdAt)}: '
          'no card reached the platform. Clear the jam, then press Resume.',
      AlertType.machineFault => '$name paused at ${_hms(alert.createdAt)}: '
          '${faultDescription(alert.faultCode)} Fix it, then press Resume.',
      AlertType.doubleFeed => '$name picked up two cards at once.',
      AlertType.connectionLost =>
        'Lost contact with $name. The app keeps trying to reconnect.',
      AlertType.machineLinked => '$name is now linked to your account.',
    };
  }
}
