import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Holds the app language and remembers the choice. English is the default.
class LocaleController extends ChangeNotifier {
  LocaleController(this._prefs) : _locale = _read(_prefs);

  static const String _key = 'language_code';

  final SharedPreferences _prefs;
  Locale _locale;

  Locale get locale => _locale;

  static Locale _read(SharedPreferences prefs) {
    return Locale(prefs.getString(_key) == 'pt' ? 'pt' : 'en');
  }

  Future<void> setLocale(Locale locale) async {
    if (locale.languageCode == _locale.languageCode) return;
    _locale = Locale(locale.languageCode);
    notifyListeners();
    await _prefs.setString(_key, _locale.languageCode);
  }
}
