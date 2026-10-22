from django.conf import settings
from django.utils import translation


class UserLanguageMiddleware:
    """Activate the authenticated user's preferred language on every request."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.valid_languages = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        language = self._get_user_language(request)
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()

        response = self.get_response(request)

        response_language = self._get_user_language(request) or language
        if response_language:
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, response_language, samesite="Lax")

        return response

    def _get_user_language(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return None

        language = getattr(user, "language", None)
        return language if language in self.valid_languages else None
