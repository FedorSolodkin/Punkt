import json
import logging
import uuid

from django.http import JsonResponse

from core.errors import ApiError, ValidationError

logger = logging.getLogger('punkt')


def error_response(exc: ApiError):
    return JsonResponse(exc.to_dict(), status=exc.http_status)


def parse_json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError('Некорректный JSON в теле запроса') from exc
    if not isinstance(data, dict):
        raise ValidationError('Тело запроса должно быть JSON-объектом')
    return data


def require_request_id(source: dict):
    request_id = source.get('request_id')
    if not request_id:
        raise ValidationError(
            'Обязательное поле',
            fields={'request_id': 'Обязательное поле'},
        )
    try:
        return str(uuid.UUID(str(request_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationError(
            'Некорректный request_id',
            fields={'request_id': 'Должен быть UUID'},
        ) from exc
