/// Global settings, read from `--dart-define` at build time.
///
///   flutter run --dart-define=USE_MOCK=false --dart-define=API_BASE_URL=https://your-server.com
///
/// Machine (Raspberry Pi) testing recipes are in the README, "Testing with the machine".
class AppConfig {
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://api.cardforge.example',
  );

  /// While the backend does not exist, the app runs on simulated responses.
  static const bool useMockBackend = bool.fromEnvironment(
    'USE_MOCK',
    defaultValue: true,
  );

  /// Simulated Bluetooth setup (works in the emulator, which has no
  /// Bluetooth). Set to false on a real phone to set up the real machine.
  static const bool useMockBluetooth = bool.fromEnvironment(
    'USE_MOCK_BLUETOOTH',
    defaultValue: true,
  );

  /// Simulated machine REST API inside the app. Set to false to talk to the
  /// real API on the Raspberry Pi (or `python -m sorter_api --simulate` on a
  /// computer).
  static const bool useMockMachine = bool.fromEnvironment(
    'USE_MOCK_MACHINE',
    defaultValue: true,
  );

  /// Address the simulated Bluetooth setup reports for the machine.
  /// 10.0.2.2 is the computer that runs the Android emulator; on a real phone
  /// use your computer's IP on the Wi-Fi.
  static const String mockMachineHost = String.fromEnvironment(
    'MOCK_MACHINE_HOST',
    defaultValue: '10.0.2.2',
  );
  static const int mockMachinePort = int.fromEnvironment(
    'MOCK_MACHINE_PORT',
    defaultValue: 8000,
  );

  /// OAuth client ID of type "Web" from Google Cloud. On Android, Google only
  /// returns the idToken (which the backend validates) when this is set.
  static const String googleServerClientId =
      String.fromEnvironment('GOOGLE_SERVER_CLIENT_ID');

  static const Duration healthTimeout = Duration(seconds: 5);

  /// Timeout for regular API requests (card search, etc.).
  static const Duration requestTimeout = Duration(seconds: 10);
  static const Duration splashMinDuration = Duration(milliseconds: 1500);

  /// How long to wait for the machine to confirm the link before giving up.
  static const Duration linkTimeout = Duration(seconds: 90);

  /// Requests to the machine's REST API.
  static const Duration machineRequestTimeout = Duration(seconds: 8);

  /// POST /run/start blocks while the base homes (at most ~40 s).
  static const Duration machineStartTimeout = Duration(seconds: 60);

  /// Stop and base test moves.
  static const Duration machineMoveTimeout = Duration(seconds: 45);
}
