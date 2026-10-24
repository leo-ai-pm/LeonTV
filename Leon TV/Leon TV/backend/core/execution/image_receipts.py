"""Pure, identity-bound image receipts; delivery is never visual acceptance."""
import hashlib
import io
import re
import warnings
from urllib.parse import urlsplit

from PIL import Image

from core.creator.store import CreatorError, digest
from core.execution.receipts import raw_body, timestamp
from core.planner.protocol import parse_json

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
TASK_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_IMAGE_BYTES = 32 * 1048576
MAX_IMAGE_PIXELS = 32 * 1048576
MAX_IMAGE_DIMENSION = 8192


def _fail(code="IMAGE_RECEIPT_INVALID"):
    raise CreatorError(code, "Bound image receipt evidence is unavailable", 409)


def _require(value):
    if not value:
        _fail()


def _request(request, method):
    from core.execution.image_policy import FrozenImageRequest

    _require(type(request) is FrozenImageRequest)
    # frozen() fully reconstructs the strict descriptor; do not trust attributes
    # on a look-alike object or merely recompute a caller's self-declared hash.
    checked = request.frozen()
    _require(checked["method"] == method)
    return checked


def _raw(raw, request):
    if raw is None:
        return None
    try:
        body = raw_body(raw)
        _require(len(body) <= request["policy"]["max_response_bytes"])
        _require(timestamp(request["permit"]["issued_at"]) <= timestamp(raw["received_at"]))
        # Exact bytes, including whitespace and key order, remain in raw.
        value = parse_json(body.decode("utf-8"))
        return body, value
    except Exception:
        _fail()


def _identifier(value):
    return type(value) is str and bool(IDENTIFIER.fullmatch(value))


def _request_identity(raw, body):
    values = []
    for value in (raw["headers"].get("x-request-id"), body.get("request_id") if type(body) is dict else None):
        if value is None or value == "":
            continue
        if not _identifier(value):
            return None, "invalid"
        values.append(value)
    if not values:
        return None, "missing"
    if len(set(values)) != 1:
        return None, "conflicting"
    return values[0], "direct"


def _envelope(value):
    _require(type(value) is dict)
    code = value.get("code")
    _require(code is None or type(code) is int)
    success = code in (None, 0, 200)
    item = value.get("data") if "data" in value else value
    _require(type(item) is dict)
    return item, success


def _task_ids(value, item):
    values = [mapping[name] for mapping in (value, item) for name in ("id", "task_id")
              if name in mapping and mapping[name] is not None]
    _require(bool(values) and all(type(value) is str and bool(TASK_IDENTIFIER.fullmatch(value)) for value in values))
    _require(len(set(values)) == 1)
    return values[0]


def _base(request, raw, kind):
    body = raw_body(raw) if raw is not None else None
    return {"schema_version": 1, "kind": kind, "classification": "unconfirmed",
            "accepted_task_id": None, "submit_request_id": None, "request_id_status": "missing",
            "descriptor_hash": request["descriptor_hash"],
            "raw_body_sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
            "raw_hash": digest(raw) if raw is not None else None,
            "http_status": raw["http_status"] if raw is not None else None,
            "received_at": raw["received_at"] if raw is not None else None}


def _sealed(value):
    return {**value, "receipt_hash": digest(value)}


def decode_submit(request, raw):
    """Normalize one captured POST; a task ID is never a billing request ID."""
    request = _request(request, "POST")
    decoded = _raw(raw, request)
    result = _base(request, raw, "image_submit_receipt_v1")
    if decoded is None:
        return _sealed(result)
    _, value = decoded
    result["submit_request_id"], result["request_id_status"] = _request_identity(raw, value)
    try:
        item, success = _envelope(value)
        status = raw["http_status"]
        if 200 <= status < 300 and success:
            result["accepted_task_id"] = _task_ids(value, item)
            result["classification"] = "accepted"
        elif (400 <= status < 500 and status != 408) or (200 <= status < 300 and not success):
            result["classification"] = "rejected"
    except CreatorError:
        pass
    if (result["request_id_status"] in {"conflicting", "invalid"}
            or timestamp(raw["received_at"]) > timestamp(request["permit"]["deadline_at"])):
        result.update(classification="unconfirmed", accepted_task_id=None)
    return _sealed(result)


def _image_url(result):
    _require(type(result) is dict and type(result.get("images")) is list and len(result["images"]) == 1)
    entry = result["images"][0]
    _require(type(entry) is dict)
    value = entry.get("url")
    if type(value) is list:
        _require(len(value) == 1)
        value = value[0]
    _require(type(value) is str and 0 < len(value) <= 8192 and not any(ord(char) <= 32 for char in value))
    parsed = urlsplit(value)
    _require(parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None
             and parsed.password is None and not parsed.fragment)
    _require(parsed.port in (None, 443))
    return value


def decode_query(request, raw):
    """A GET proves only its original task's current state, never a new submit."""
    request = _request(request, "GET")
    frozen = request
    from core.execution.image_policy import FrozenImageRequest

    parent = FrozenImageRequest.from_frozen(frozen["parent_submit"])
    submitted = decode_submit(parent, frozen["submit_raw"])
    _require(submitted["classification"] == "accepted")
    _require(frozen["accepted_task_id"] == submitted["accepted_task_id"])
    decoded = _raw(raw, request)
    result = _base(request, raw, "image_query_receipt_v1")
    result.update(accepted_task_id=submitted["accepted_task_id"], submit_request_id=submitted["submit_request_id"],
                  request_id_status=submitted["request_id_status"], query_request_id=None,
                  query_status=None, result_url=None, result_url_sha256=None,
                  submit_descriptor_hash=frozen["parent_submit"]["descriptor_hash"], submit_raw_hash=digest(frozen["submit_raw"]))
    if decoded is None:
        return _sealed(result)
    _, value = decoded
    result["query_request_id"], query_identity_status = _request_identity(raw, value)
    try:
        _require(query_identity_status not in {"conflicting", "invalid"})
        _require(timestamp(raw["received_at"]) <= timestamp(request["permit"]["deadline_at"]))
        item, success = _envelope(value)
        _require(200 <= raw["http_status"] < 300 and success)
        _require(_task_ids(value, item) == submitted["accepted_task_id"])
        status = item.get("status")
        _require(type(status) is str)
        status = status.lower()
        result["query_status"] = status
        if status in {"pending", "queued", "processing", "running", "submitted", "accepted"}:
            result["classification"] = "pending"
        elif status in {"failed", "error", "expired", "cancelled", "rejected"}:
            result["classification"] = "failed"
        elif status in {"completed", "succeeded"}:
            url = _image_url(item.get("result"))
            result.update(classification="completed", result_url=url,
                          result_url_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest())
    except (CreatorError, ValueError):
        result.update(classification="unconfirmed", result_url=None, result_url_sha256=None)
    return _sealed(result)


def verify_delivery(query_request, query_raw, image_bytes):
    """Decode exact delivered bytes; image dimensions do not establish quality."""
    try:
        receipt = decode_query(query_request, query_raw)
        _require(receipt["classification"] == "completed")
        _require(type(image_bytes) is bytes and 0 < len(image_bytes) <= MAX_IMAGE_BYTES)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                format_name = image.format
                _require(format_name in {"PNG", "JPEG", "WEBP"})
                _require(type(width) is int and type(height) is int and width > 0 and height > 0)
                _require(width <= MAX_IMAGE_DIMENSION and height <= MAX_IMAGE_DIMENSION
                         and width * height <= MAX_IMAGE_PIXELS and width * 16 == height * 9)
                _require(getattr(image, "n_frames", 1) == 1 and not getattr(image, "is_animated", False))
                image.verify()
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                _require(image.size == (width, height) and image.format == format_name)
        result = {"schema_version": 1, "kind": "image_delivery_v1",
                  "descriptor_hash": query_request.descriptor_hash, "query_raw_hash": digest(query_raw),
                  "query_receipt_hash": receipt["receipt_hash"], "accepted_task_id": receipt["accepted_task_id"],
                  "submit_request_id": receipt["submit_request_id"],
                  "delivery_sha256": hashlib.sha256(image_bytes).hexdigest(), "delivery_size_bytes": len(image_bytes),
                  "width": width, "height": height, "format": format_name, "visual_quality": "not_reviewed"}
        return {**result, "delivery_hash": digest(result)}
    except Exception:
        _fail("IMAGE_DELIVERY_INVALID")
