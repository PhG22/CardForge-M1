import '../../core/services/server_json_client.dart';
import '../cards/card_models.dart';
import 'run_models.dart';

/// Where the run history comes from. Failures are [CardsException]s.
abstract class RunRepository {
  /// The user's runs, newest first.
  Future<RunsPage> fetchRuns({int offset = 0, int limit = 30});

  /// Per-folder counts and every card of one run.
  Future<RunDetails> fetchRun(String id);
}

/// Real server: `GET /runs?limit=&offset=` and `GET /runs/{id}`.
class ApiRunRepository implements RunRepository {
  ApiRunRepository({ServerJsonClient? client})
      : _client = client ?? ServerJsonClient();

  final ServerJsonClient _client;

  @override
  Future<RunsPage> fetchRuns({int offset = 0, int limit = 30}) async {
    final json = await _client.get('/runs', query: {
      'limit': '$limit',
      'offset': '$offset',
    });
    return RunsPage.fromJson(json);
  }

  @override
  Future<RunDetails> fetchRun(String id) async {
    final json = await _client.get('/runs/${Uri.encodeComponent(id)}');
    if (json['run'] is! Map) {
      throw const CardsException(CardsFailure.serverError);
    }
    return RunDetails.fromJson(json);
  }
}
