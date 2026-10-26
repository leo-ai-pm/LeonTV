"""Explicit internal reference representations; never crop, resize or authorize."""
import base64
import copy
import hashlib
import io
import json
import warnings

from PIL import Image, features
from PIL import __version__ as pillow_version
from pydantic import ConfigDict, model_validator

from core.creator.store import ID_RE, SHA_RE, CreatorError, digest
from core.execution.image_policy import MAX_BYTES, MAX_DESCRIPTOR_BYTES, ImageReference
from core.planner.protocol import Strict
from core.planner.review import json_copy

KIND = "reference-derivation-v1"
MAX_SOURCE_BYTES = 2 * 1048576
MAX_PIXELS = 32 * 1048576
JPEG_QUALITIES = [85, 75, 65]
SOURCE_FIELDS = {"operation_id", "query_id", "delivery_id", "shot_id", "first_frame_version", "delivery", "bytes_base64"}
PROOF_FIELDS = {"schema_version", "kind", "derivation_id", "scope_id", "task_id", "target_operation_id", "target_shot_id", "target_job_hash",
                "source_operation_id", "source_query_id", "source_delivery_id", "source_delivery_hash", "source_shot_id", "source_first_frame_version",
                "source_sha256", "source_size_bytes", "source_format", "source_decoded_pixel_sha256", "profile_hash", "representation_version",
                "encoder", "transform", "output_bytes_base64", "output_sha256", "output_size_bytes", "output_format", "output_mime_type",
                "width", "height", "output_decoded_pixel_sha256", "pixel_equivalent", "local_attempts", "visual_quality", "derivation_hash"}


def fail(code="MEDIA_REFERENCE_DERIVATION_INVALID"):
    raise CreatorError(code, "Explicit internal reference representation is unavailable or inconsistent", 409)


def require(value, code="MEDIA_REFERENCE_DERIVATION_INVALID"):
    if not value:
        fail(code)


class ReferenceProfile(Strict):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    allow_lossy_jpeg: bool

    @model_validator(mode="before")
    @classmethod
    def strict_bool(cls, value):
        require(type(value) is dict and set(value) == {"allow_lossy_jpeg"} and type(value["allow_lossy_jpeg"]) is bool)
        return value

    def frozen(self):
        result = {"schema_version": 1, "kind": KIND, "allow_lossy_jpeg": self.allow_lossy_jpeg,
                  "jpeg_qualities": list(JPEG_QUALITIES), "jpeg_subsampling": 2, "png_compress_level": 6,
                  "resize": False, "crop": False, "exif_rotate": False, "flatten_alpha": False,
                  "metadata_policy": "reject_icc_gamma_and_nonidentity_orientation", "encoder": encoder_identity()}
        return {**result, "profile_hash": digest(result)}


def checked_profile(value):
    try:
        if type(value) is ReferenceProfile:
            return value.frozen()
        value = json_copy(value)
        require(type(value) is dict and type(value.get("allow_lossy_jpeg")) is bool)
        expected = ReferenceProfile(allow_lossy_jpeg=value["allow_lossy_jpeg"]).frozen()
        encoder = value.get("encoder")
        require(type(encoder) is dict and set(encoder) == set(encoder_identity()) and encoder["implementation"] == "pillow")
        require(type(encoder["pillow"]) is str and bool(encoder["pillow"]) and all(type(item) is str or item is None for item in encoder.values()))
        expected["encoder"] = copy.deepcopy(encoder)
        expected["profile_hash"] = digest({key: item for key, item in expected.items() if key != "profile_hash"})
        require(digest(value) == digest(expected))
        return expected
    except Exception:
        fail()


def encoder_identity():
    return {"implementation": "pillow", "pillow": pillow_version,
            "jpeg": features.version("jpg"), "webp": features.version("webp"), "zlib": features.version("zlib")}


def _pixel_hash(image):
    return hashlib.sha256(f"{image.width}x{image.height}|RGBA8|".encode() + image.tobytes()).hexdigest()


def _decode(raw, formats):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format in formats and 0 < image.width <= 4096 and 0 < image.height <= 4096)
            require(image.width * image.height <= MAX_PIXELS and image.width * 16 == image.height * 9)
            require(getattr(image, "n_frames", 1) == 1 and not getattr(image, "is_animated", False))
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            require(image.getexif().get(274, 1) == 1 and not image.info.get("icc_profile") and "gamma" not in image.info)
            format_name = image.format
            rgba = image.convert("RGBA")
    return rgba, format_name, _pixel_hash(rgba)


def _source(value):
    require(type(value) is dict and set(value) == SOURCE_FIELDS)
    for name in ("operation_id", "query_id", "delivery_id"):
        require(type(value[name]) is str and ID_RE.fullmatch(value[name]) is not None)
    require(type(value["first_frame_version"]) is int and value["first_frame_version"] == 1)
    raw = base64.b64decode(value["bytes_base64"], validate=True)
    require(base64.b64encode(raw).decode() == value["bytes_base64"] and 0 < len(raw) <= MAX_SOURCE_BYTES)
    delivery = value["delivery"]
    require(type(delivery) is dict and delivery["delivery_hash"] == digest({key: item for key, item in delivery.items() if key != "delivery_hash"}))
    rgba, format_name, pixels = _decode(raw, {"PNG", "JPEG", "WEBP"})
    require(delivery["delivery_sha256"] == hashlib.sha256(raw).hexdigest() and delivery["delivery_size_bytes"] == len(raw))
    require(delivery["width"] == rgba.width and delivery["height"] == rgba.height and delivery["format"] == format_name)
    return raw, rgba, format_name, pixels


def _wire(job, policy, references):
    body = {"model": policy["model"], "prompt": job["prompt"], "size": policy["ratio"],
            "resolution": policy["resolution"], "quality": policy["quality"]}
    if references:
        body["images"] = [f"data:{reference['mime_type']};base64,{reference['bytes_base64']}" for reference in references]
    return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _reference(source, raw, format_name):
    return {"shot_id": source["shot_id"], "first_frame_version": 1,
            "mime_type": "image/png" if format_name == "PNG" else "image/jpeg",
            "bytes_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}


def _fits(sources, choices, job, policy):
    refs = [_reference(source, choice["raw"], choice["format"]) for source, choice in zip(sources, choices, strict=True)]
    return all(len(choice["raw"]) <= MAX_BYTES for choice in choices) and len(_wire(job, policy, refs).encode()) <= policy["max_request_bytes"]


def _attempt(raw, method, quality):
    return {"method": method, "quality": quality, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def derive_references(*, profile, sources, target_job, policy, scope_id, task_id, operation_id):
    """Pure bounded local conversion. Only the controller can supply provenance."""
    try:
        profile = checked_profile(profile)
        require(profile["encoder"] == encoder_identity(), "MEDIA_REFERENCE_ENCODER_MISMATCH")
        json_copy(sources, limit=16 * 1048576)
        for identifier in (scope_id, task_id, operation_id):
            require(type(identifier) is str and ID_RE.fullmatch(identifier) is not None)
        require(type(sources) is list and [source["shot_id"] for source in sources] == target_job["reference_shot_ids"])
        require(type(policy["max_request_bytes"]) is int and 128 <= policy["max_request_bytes"] <= MAX_BYTES)
        require(len(_wire(target_job, policy, []).encode()) <= policy["max_request_bytes"], "MEDIA_REFERENCE_WIRE_LIMIT")
        decoded, choices, attempts = [], [], []
        for source in sources:
            raw, rgba, format_name, pixels = _source(source)
            decoded.append((raw, rgba, format_name, pixels))
            if format_name in {"PNG", "JPEG"} and len(raw) <= MAX_BYTES:
                method, output, output_format = "identity", raw, format_name
            else:
                sink = io.BytesIO()
                rgba.save(sink, format="PNG", compress_level=6)
                method, output, output_format = "lossless_png", sink.getvalue(), "PNG"
            choices.append({"raw": output, "format": output_format, "method": method, "quality": None})
            attempts.append([_attempt(output, method, None)])
        if not _fits(sources, choices, target_job, policy):
            require(profile["allow_lossy_jpeg"], "MEDIA_REFERENCE_WIRE_LIMIT")
            for quality in JPEG_QUALITIES:
                for index, (_, rgba, _, _) in enumerate(decoded):
                    if rgba.getchannel("A").getextrema() != (255, 255):
                        continue
                    sink = io.BytesIO()
                    rgba.convert("RGB").save(sink, format="JPEG", quality=quality, subsampling=2, optimize=True, progressive=False)
                    output = sink.getvalue()
                    choices[index] = {"raw": output, "format": "JPEG", "method": "jpeg", "quality": quality}
                    attempts[index].append(_attempt(output, "jpeg", quality))
                if _fits(sources, choices, target_job, policy):
                    break
            require(_fits(sources, choices, target_job, policy), "MEDIA_REFERENCE_WIRE_LIMIT")
        references, proofs = [], []
        encoder = encoder_identity()
        for source, decoded_source, choice, history in zip(sources, decoded, choices, attempts, strict=True):
            raw, rgba, format_name, pixels = decoded_source
            output = choice["raw"]
            _, output_format, output_pixels = _decode(output, {"PNG", "JPEG"})
            reference = ImageReference.model_validate(_reference(source, output, output_format)).model_dump()
            identity = {"target_operation_id": operation_id, "source_delivery_id": source["delivery_id"],
                        "target_job_hash": digest(target_job), "profile_hash": profile["profile_hash"]}
            proof = {"schema_version": 1, "kind": KIND, "derivation_id": digest(identity)[:32],
                     "scope_id": scope_id, "task_id": task_id, "target_operation_id": operation_id,
                     "target_shot_id": target_job["shot_id"], "target_job_hash": digest(target_job),
                     "source_operation_id": source["operation_id"], "source_query_id": source["query_id"],
                     "source_delivery_id": source["delivery_id"], "source_delivery_hash": source["delivery"]["delivery_hash"],
                     "source_shot_id": source["shot_id"], "source_first_frame_version": 1,
                     "source_sha256": hashlib.sha256(raw).hexdigest(), "source_size_bytes": len(raw), "source_format": format_name,
                     "source_decoded_pixel_sha256": pixels, "profile_hash": profile["profile_hash"], "representation_version": 1,
                     "encoder": encoder, "transform": {"method": choice["method"], "quality": choice["quality"],
                         "subsampling": 2 if choice["method"] == "jpeg" else None, "resize": False, "crop": False,
                         "exif_rotate": False, "flatten_alpha": False},
                     "output_bytes_base64": reference["bytes_base64"], "output_sha256": reference["sha256"],
                     "output_size_bytes": len(output), "output_format": output_format, "output_mime_type": reference["mime_type"],
                     "width": rgba.width, "height": rgba.height, "output_decoded_pixel_sha256": output_pixels,
                     "pixel_equivalent": output_pixels == pixels, "local_attempts": history, "visual_quality": "not_reviewed"}
            proofs.append({**proof, "derivation_hash": digest(proof)})
            references.append(reference)
        return references, proofs
    except CreatorError:
        raise
    except Exception:
        fail()


def references_from_proofs(*, profile, sources, target_job, policy, scope_id, task_id, operation_id, proofs, reencode=False):
    """Validate stored representations; historical replay never overwrites bytes."""
    try:
        profile = checked_profile(profile)
        json_copy(proofs, limit=16 * 1048576)
        require(type(proofs) is list and len(proofs) == len(sources))
        require([source["shot_id"] for source in sources] == target_job["reference_shot_ids"])
        refs = []
        for source, proof in zip(sources, proofs, strict=True):
            require(type(proof) is dict and set(proof) == PROOF_FIELDS)
            require(type(proof["schema_version"]) is int and proof["schema_version"] == 1 and proof["kind"] == KIND)
            require(proof["derivation_hash"] == digest({key: value for key, value in proof.items() if key != "derivation_hash"}))
            raw, rgba, format_name, pixels = _source(source)
            identity = {"target_operation_id": operation_id, "source_delivery_id": source["delivery_id"],
                        "target_job_hash": digest(target_job), "profile_hash": profile["profile_hash"]}
            expected = {"derivation_id": digest(identity)[:32], "scope_id": scope_id, "task_id": task_id,
                        "target_operation_id": operation_id, "target_shot_id": target_job["shot_id"], "target_job_hash": digest(target_job),
                        "source_operation_id": source["operation_id"], "source_query_id": source["query_id"], "source_delivery_id": source["delivery_id"],
                        "source_delivery_hash": source["delivery"]["delivery_hash"], "source_shot_id": source["shot_id"],
                        "source_first_frame_version": 1, "source_sha256": hashlib.sha256(raw).hexdigest(),
                        "source_size_bytes": len(raw), "source_format": format_name, "source_decoded_pixel_sha256": pixels,
                        "profile_hash": profile["profile_hash"], "representation_version": 1, "width": rgba.width, "height": rgba.height,
                        "visual_quality": "not_reviewed"}
            require(all(digest(proof[key]) == digest(value) for key, value in expected.items()))
            require(type(proof["source_first_frame_version"]) is int and type(proof["representation_version"]) is int)
            output = base64.b64decode(proof["output_bytes_base64"], validate=True)
            require(base64.b64encode(output).decode() == proof["output_bytes_base64"] and 0 < len(output) <= MAX_BYTES)
            decoded, output_format, output_pixels = _decode(output, {"PNG", "JPEG"})
            require(decoded.size == rgba.size and proof["output_sha256"] == hashlib.sha256(output).hexdigest()
                    and type(proof["output_size_bytes"]) is int and proof["output_size_bytes"] == len(output))
            require(proof["output_format"] == output_format and proof["output_decoded_pixel_sha256"] == output_pixels)
            require(type(proof["pixel_equivalent"]) is bool and proof["pixel_equivalent"] == (pixels == output_pixels))
            transform = proof["transform"]
            require(type(transform) is dict and set(transform) == {"method", "quality", "subsampling", "resize", "crop", "exif_rotate", "flatten_alpha"})
            require(all(transform[field] is False for field in ("resize", "crop", "exif_rotate", "flatten_alpha")))
            method = transform["method"]
            if method == "identity":
                require(raw == output and format_name == output_format and transform["quality"] is transform["subsampling"] is None)
            elif method == "lossless_png":
                require(output_format == "PNG" and pixels == output_pixels and transform["quality"] is transform["subsampling"] is None)
            else:
                require(method == "jpeg" and profile["allow_lossy_jpeg"] and output_format == "JPEG")
                require(type(transform["quality"]) is int and transform["quality"] in JPEG_QUALITIES
                        and type(transform["subsampling"]) is int and transform["subsampling"] == 2)
                require(rgba.getchannel("A").getextrema() == (255, 255))
            encoder = proof["encoder"]
            require(type(encoder) is dict and set(encoder) == set(encoder_identity()) and encoder["implementation"] == "pillow")
            require(all(type(value) is str or value is None for value in encoder.values()))
            require(encoder == profile["encoder"])
            history = proof["local_attempts"]
            require(type(history) is list and 1 <= len(history) <= 4)
            for index, attempt in enumerate(history):
                require(type(attempt) is dict and set(attempt) == {"method", "quality", "sha256", "size_bytes"})
                require(type(attempt["size_bytes"]) is int and attempt["size_bytes"] > 0)
                require(type(attempt["sha256"]) is str and SHA_RE.fullmatch(attempt["sha256"]) is not None)
                require(attempt["method"] in {"identity", "lossless_png"} and attempt["quality"] is None if index == 0
                        else attempt["method"] == "jpeg" and type(attempt["quality"]) is int and attempt["quality"] == JPEG_QUALITIES[index - 1])
            require(history[-1] == _attempt(output, method, transform["quality"]))
            reference = _reference(source, output, output_format)
            require(reference["mime_type"] == proof["output_mime_type"])
            refs.append(ImageReference.model_validate(reference).model_dump())
        require(len(_wire(target_job, policy, refs).encode()) <= policy["max_request_bytes"], "MEDIA_REFERENCE_WIRE_LIMIT")
        if reencode:
            require(all(proof["encoder"] == encoder_identity() for proof in proofs), "MEDIA_REFERENCE_ENCODER_MISMATCH")
            expected_refs, expected_proofs = derive_references(profile=profile, sources=sources, target_job=target_job,
                policy=policy, scope_id=scope_id, task_id=task_id, operation_id=operation_id)
            require(digest(refs) == digest(expected_refs) and digest(proofs) == digest(expected_proofs))
        return refs
    except CreatorError:
        raise
    except Exception:
        fail()


def check_descriptor_capacity(descriptor, *, worker_factory=None, worker_options=None):
    """Include query nesting and IPC copies before a financial intent is written."""
    frozen = descriptor.frozen()
    # A GET repeats references plus its complete parent POST descriptor. Budget
    # for its empty wire, raw submission envelope, binding and collector wrapper.
    probe = copy.deepcopy(frozen)
    raw_limit = frozen["policy"]["max_response_bytes"]
    probe.update(method="GET", wire="", parent_submit=frozen,
                 submit_raw={"body_base64": "x" * (4 * ((raw_limit + 2) // 3))}, accepted_task_id="x" * 128)
    require(len(json.dumps(probe, ensure_ascii=False).encode()) + 65536 <= MAX_DESCRIPTOR_BYTES, "MEDIA_REFERENCE_DESCRIPTOR_LIMIT")
    job = {"kind": probe["kind"], "descriptor": probe, "binding": descriptor.binding(),
           "worker_factory": worker_factory, "worker_options": worker_options or {}}
    ipc = {"capability": "x" * 64, "api_key": "x" * 16384, "job": job}
    require(len(json.dumps(ipc, ensure_ascii=False).encode()) + 8192 <= 8 * MAX_BYTES, "MEDIA_REFERENCE_DESCRIPTOR_LIMIT")
