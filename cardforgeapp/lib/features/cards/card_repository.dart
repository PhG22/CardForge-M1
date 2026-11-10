import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/config/app_config.dart';
import '../auth/auth_service.dart';
import 'card_models.dart';

/// Where the cards come from. The screen only knows this interface, so the
/// simulated data and the real server are interchangeable.
abstract class CardRepository {
  /// Cards that are in the folders. An empty [query] lists all of them;
  /// otherwise only the cards whose name contains [query]. Throws
  /// [CardsException] on failure.
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  });
}

/// Real server: `GET /cards?query=...&limit=...&offset=...`
/// with `Authorization: Bearer <token>`.
class ApiCardRepository implements CardRepository {
  ApiCardRepository({
    http.Client? client,
    Future<String?> Function()? tokenProvider,
    String? baseUrl,
  })  : _client = client ?? http.Client(),
        _tokenProvider = tokenProvider ?? AuthService.instance.accessToken,
        _baseUrl = baseUrl ?? AppConfig.apiBaseUrl;

  final http.Client _client;
  final Future<String?> Function() _tokenProvider;
  final String _baseUrl;

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  }) async {
    final token = await _tokenProvider();
    final uri = Uri.parse('$_baseUrl/cards').replace(queryParameters: {
      if (query.isNotEmpty) 'query': query,
      'limit': '$limit',
      'offset': '$offset',
    });

    final http.Response res;
    try {
      res = await _client.get(uri, headers: {
        'Accept': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      }).timeout(AppConfig.requestTimeout);
    } on Exception {
      throw const CardsException(CardsFailure.serverUnreachable);
    }

    if (res.statusCode == 401) {
      throw const CardsException(CardsFailure.unauthorized);
    }
    if (res.statusCode != 200) {
      throw const CardsException(CardsFailure.serverError);
    }

    try {
      return CardsPage.fromJson(
        jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>,
      );
    } catch (_) {
      // Unexpected body: treat it as a server problem.
      throw const CardsException(CardsFailure.serverError);
    }
  }
}
