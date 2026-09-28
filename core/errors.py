"""Единая схема ошибок API (ТЗ §4.1): error.code, message, fields, request_id."""


class ApiError(Exception):
    http_status = 400
    code = 'VALIDATION_ERROR'

    def __init__(self, message, *, fields=None, request_id=None, code=None, http_status=None):
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
        self.request_id = request_id
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status

    def to_dict(self):
        return {
            'error': {
                'code': self.code,
                'message': self.message,
                'fields': self.fields,
                'request_id': str(self.request_id) if self.request_id else None,
            }
        }


class ValidationError(ApiError):
    http_status = 400
    code = 'VALIDATION_ERROR'


class LimitExceeded(ApiError):
    http_status = 413
    code = 'LIMIT_EXCEEDED'


class AuthRequired(ApiError):
    http_status = 401
    code = 'AUTH_REQUIRED'


class Forbidden(ApiError):
    http_status = 403
    code = 'FORBIDDEN'


class NotFound(ApiError):
    http_status = 404
    code = 'NOT_FOUND'


class Conflict(ApiError):
    http_status = 409
    code = 'CONFLICT'


class RateLimited(ApiError):
    http_status = 429
    code = 'RATE_LIMITED'


class ServiceUnavailable(ApiError):
    http_status = 503
    code = 'SERVICE_UNAVAILABLE'
