import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../features/auth/auth_service.dart';
import '../../features/cards/card_models.dart';
import '../config/app_config.dart';

/// GET requests to the CardForge server with the signed-in user's token
/// (`Authorization: Bearer ...`). Used by the run history and the CSV export.
///
/// Failures are [CardsException]s, the same ones as the card search, so the
/// screens show the same messages: [CardsFailure.unauthorized] (sign in again),
/// [CardsFailure.serverUnreachable] and [CardsFailure.serverError].
class ServerJsonClient {
  ServerJsonClient({
    http.Client? client,
    Future<String?> Function()? tokenProvider,
    String? baseUrl,
    Duration? timeout,
  })  : _client = client ?? http.Client(),
        _tokenProvider = tokenProvider ?? AuthService.instance.accessToken,
        _baseUrl = baseUrl ?? AppConfig.apiBaseUrl,
        _timeout = timeout ?? AppConfig.requestTimeout;

  final http.Client _client;
  final Future<String?> Function() _tokenProvider;
  final String _baseUrl;
  final Duration _timeout;

  Future<Map<String, Object?>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    final token = await _tokenProvider();
    final uri = Uri.parse('$_baseUrl$path').replace(
      queryParameters: query == null || query.isEmpty ? null : query,
    );

    final http.Response res;
    try {
      res = await _client.get(uri, headers: {
        'Accept': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      }).timeout(_timeout);
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
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      if (body is Map) return body.cast<String, Object?>();
    } on FormatException {
      // falls through
    }
    throw const CardsException(CardsFailure.serverError);
  }
}
