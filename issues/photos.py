import hashlib
import io
import uuid
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageOps

from core.errors import LimitExceeded, ValidationError
from .models import Photo

ALLOWED_MIME = {'image/jpeg': 'jpg', 'image/png': 'png'}
PREVIEW_LONG_SIDE = 1280
PREVIEW_QUALITY = 80


def validate_and_stage(uploaded_file) -> dict:
    """Проверяет фото до записи на диск (ТЗ §5.2, §5.3): размер, MIME,
    декодирование и число пикселей. Возвращает байты и метаданные —
    сама запись на диск выполняется save_photo() после прохождения всех
    проверок бизнес-логики (зона/исполнитель/статус и т.д.).
    """
    uploaded_file.seek(0)
    data = uploaded_file.read()

    if not data:
        raise ValidationError('Файл не получен', fields={'photo': 'Обязательное поле'})
    if len(data) > settings.PHOTO_MAX_BYTES:
        raise LimitExceeded('Файл превышает лимит размера', fields={'photo': 'Максимум 10 МиБ'})

    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValidationError(
            'Файл повреждён или не является изображением',
            fields={'photo': 'Ожидается JPEG или PNG'},
        ) from exc

    mime_by_format = {'JPEG': 'image/jpeg', 'PNG': 'image/png'}
    mime = mime_by_format.get(image.format)
    if mime is None:
        raise ValidationError(
            'Недопустимый формат файла',
            fields={'photo': 'Ожидается JPEG или PNG'},
        )

    width, height = image.size
    if width * height > settings.PHOTO_MAX_PIXELS:
        raise LimitExceeded('Изображение превышает лимит пикселей', fields={'photo': 'Максимум 25 млн пикселей'})

    return {
        'data': data,
        'mime': mime,
        'extension': ALLOWED_MIME[mime],
        'sha256': hashlib.sha256(data).hexdigest(),
        'image': image,
    }


def save_photo(*, issue, kind, staged: dict, author) -> Photo:
    """Пишет оригинал и превью на диск, затем создаёт запись Photo.

    Файл должен существовать на диске раньше, чем зафиксирована
    транзакция БД (ТЗ §2.1): при откате транзакции файл останется
    сиротой и будет удалён фоновой очисткой через 24 ч (ТЗ §5.2).
    """
    originals_dir = Path(settings.MEDIA_ROOT) / 'originals'
    previews_dir = Path(settings.MEDIA_ROOT) / 'previews'
    originals_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    original_name = f'{uuid.uuid4().hex}.{staged["extension"]}'
    original_path = originals_dir / original_name
    original_path.write_bytes(staged['data'])

    preview_name = f'{uuid.uuid4().hex}.jpg'
    preview_path = previews_dir / preview_name
    preview_image = ImageOps.exif_transpose(staged['image'])
    if preview_image is None:
        preview_image = staged['image']
    if preview_image.mode not in ('RGB', 'L'):
        preview_image = preview_image.convert('RGB')
    preview_image.thumbnail((PREVIEW_LONG_SIDE, PREVIEW_LONG_SIDE), Image.LANCZOS)
    # Re-encoding without passing exif= strips all EXIF from the preview.
    preview_image.save(preview_path, format='JPEG', quality=PREVIEW_QUALITY)

    return Photo.objects.create(
        issue=issue,
        kind=kind,
        original_path=str(original_path.relative_to(settings.MEDIA_ROOT)).replace('\\', '/'),
        preview_path=str(preview_path.relative_to(settings.MEDIA_ROOT)).replace('\\', '/'),
        mime=staged['mime'],
        size_bytes=len(staged['data']),
        sha256=staged['sha256'],
        author=author,
    )
