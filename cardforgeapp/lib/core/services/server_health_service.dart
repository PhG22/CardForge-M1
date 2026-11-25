import 'package:http/http.dart' as http;

import '../config/app_config.dart';

/// Checks whether the CardForge server is reachable (GET /health).
class ServerHealthService {
  Future<bool> isServerReachable() async {
    if (AppConfig.useMockBackend) {
      await Future<void>.delayed(const Duration(milliseconds: 600));
      return true;
    }
    try {
      final res = await http
          .get(Uri.parse('${AppConfig.apiBaseUrl}/health'))
          .timeout(AppConfig.healthTimeout);
      return res.statusCode == 200;
    } on Exception {
      return false; // timeout, no internet, DNS failure, etc.
    }
  }
}
