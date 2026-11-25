import 'package:go_router/go_router.dart';

import '../../features/alerts/alerts_screen.dart';
import '../../features/auth/login_screen.dart';
import '../../features/auth/signup_screen.dart';
import '../../features/cards/collection_screen.dart';
import '../../features/cards/export_screen.dart';
import '../../features/home/home_screen.dart';
import '../../features/machine/link_machine_screen.dart';
import '../../features/machine/machine_control_screen.dart';
import '../../features/runs/run_details_screen.dart';
import '../../features/runs/runs_screen.dart';
import '../../features/settings/settings_screen.dart';
import '../../features/splash/splash_screen.dart';
import '../services/server_health_service.dart';

/// Creates the app routes. New screens are registered here.
/// The parameters exist for tests (start on another route or swap the service
/// that checks the server).
GoRouter createAppRouter({
  String initialLocation = '/splash',
  ServerHealthService? healthService,
}) {
  return GoRouter(
    initialLocation: initialLocation,
    routes: [
      GoRoute(
        path: '/splash',
        builder: (_, __) => SplashScreen(healthService: healthService),
      ),
      GoRoute(path: '/login', builder: (_, __) => const LoginScreen()),
      GoRoute(path: '/signup', builder: (_, __) => const SignUpScreen()),
      GoRoute(path: '/home', builder: (_, __) => const HomeScreen()),
      GoRoute(
        path: '/link-machine',
        builder: (_, __) => const LinkMachineScreen(),
      ),
      GoRoute(
        path: '/machine',
        builder: (_, __) => const MachineControlScreen(),
      ),
      GoRoute(path: '/alerts', builder: (_, __) => const AlertsScreen()),
      // ?run=<id> opens them on one run's cards instead of the whole collection
      GoRoute(
        path: '/cards',
        builder: (_, state) =>
            CollectionScreen(initialRunId: state.uri.queryParameters['run']),
      ),
      GoRoute(
        path: '/export',
        builder: (_, state) =>
            ExportScreen(initialRunId: state.uri.queryParameters['run']),
      ),
      GoRoute(path: '/runs', builder: (_, __) => const RunsScreen()),
      GoRoute(
        path: '/runs/:id',
        builder: (_, state) =>
            RunDetailsScreen(runId: state.pathParameters['id'] ?? ''),
      ),
      GoRoute(path: '/settings', builder: (_, __) => const SettingsScreen()),
    ],
  );
}
