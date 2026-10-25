"""Pure synthetic reference conversion; original pixels and bytes stay owned."""
import base64
import copy
import hashlib
import io
import random

import pytest
from PIL import Image
from test_image_policy import make_submit

from core.creator.store import CreatorError, digest
from core.execution import reference_derivation
from core.execution.reference_derivation import (
    ReferenceProfile,
    checked_profile,
    derive_references,
    references_from_proofs,
)


def source_for(format_name="WEBP", size=(90, 160), *, noise=False, alpha=False):
    mode = "RGBA" if alpha else "RGB"
    if noise:
        data = random.Random(7).randbytes(size[0] * size[1] * (4 if alpha else 3))
        image = Image.frombytes(mode, size, data)
    else:
        image = Image.new(mode, size, (45, 100, 150, 80) if alpha else (45, 100, 150))
    output = io.BytesIO()
    image.save(output, format=format_name)
    raw = output.getvalue()
    delivery = {"schema_version": 1, "kind": "image_delivery_v1", "delivery_sha256": hashlib.sha256(raw).hexdigest(),
                "delivery_size_bytes": len(raw), "width": size[0], "height": size[1], "format": format_name,
                "visual_quality": "not_reviewed"}
    delivery["delivery_hash"] = digest(delivery)
    return {"operation_id": "1" * 32, "query_id": "2" * 32, "delivery_id": "3" * 32,
            "shot_id": "S01", "first_frame_version": 1, "delivery": delivery,
            "bytes_base64": base64.b64encode(raw).decode()}


def derive_case(source=None, *, allow_lossy=False, max_wire=1048576):
    data = make_submit(shot_index=2).frozen()
    policy = copy.deepcopy(data["policy"])
    policy["max_request_bytes"] = max_wire
    return {"profile": ReferenceProfile(allow_lossy_jpeg=allow_lossy).frozen(), "sources": [source or source_for()],
            "target_job": data["job"], "policy": policy, "scope_id": "4" * 32, "task_id": "5" * 32, "operation_id": "6" * 32}


@pytest.mark.parametrize("format_name", ["PNG", "JPEG"])
def test_png_and_jpeg_identity_keep_original_exact_bytes(format_name):
    source = source_for(format_name)
    args = derive_case(source)
    refs, proofs = derive_references(**args)
    assert refs[0]["bytes_base64"] == source["bytes_base64"]
    assert proofs[0]["transform"]["method"] == "identity"
    assert proofs[0]["pixel_equivalent"] is True
    assert references_from_proofs(**args, proofs=proofs, reencode=True) == refs


@pytest.mark.parametrize("size", [(90, 160), (1440, 2560)])
def test_actual_webp_becomes_pixel_identical_png_without_resize(size):
    source = source_for(size=size)
    args = derive_case(source)
    refs, proofs = derive_references(**args)
    proof = proofs[0]
    assert refs[0]["mime_type"] == "image/png"
    assert proof["source_format"] == "WEBP" and proof["output_format"] == "PNG"
    assert (proof["width"], proof["height"]) == size
    assert proof["source_decoded_pixel_sha256"] == proof["output_decoded_pixel_sha256"]
    assert proof["pixel_equivalent"] is True and proof["visual_quality"] == "not_reviewed"
    assert proof["transform"]["method"] == "lossless_png"
    assert all(proof["transform"][key] is False for key in ("resize", "crop", "exif_rotate", "flatten_alpha"))
    assert references_from_proofs(**args, proofs=proofs, reencode=True) == refs


def test_lossy_jpeg_requires_explicit_profile_and_uses_original_dimensions():
    source = source_for(size=(180, 320), noise=True)
    strict = derive_case(source, max_wire=65000)
    with pytest.raises(CreatorError) as error:
        derive_references(**strict)
    assert error.value.code == "MEDIA_REFERENCE_WIRE_LIMIT"
    args = derive_case(source, allow_lossy=True, max_wire=65000)
    refs, proofs = derive_references(**args)
    proof = proofs[0]
    assert proof["transform"]["method"] == "jpeg" and proof["transform"]["quality"] in [85, 75, 65]
    assert proof["transform"]["subsampling"] == 2 and refs[0]["mime_type"] == "image/jpeg"
    assert (proof["width"], proof["height"]) == (180, 320)
    assert proof["visual_quality"] == "not_reviewed"
    assert references_from_proofs(**args, proofs=proofs, reencode=True) == refs


def test_multiple_references_share_the_actual_base64_and_prompt_budget():
    args = derive_case(source_for("PNG", size=(90, 160), noise=True))
    second = copy.deepcopy(args["sources"][0])
    second.update(shot_id="S02", operation_id="7" * 32, query_id="8" * 32, delivery_id="9" * 32)
    args["target_job"].update(shot_id="S03", reference_shot_ids=["S01", "S02"])
    args["sources"].append(second)
    args["policy"]["max_request_bytes"] = 90000
    with pytest.raises(CreatorError) as error:
        derive_references(**args)
    assert error.value.code == "MEDIA_REFERENCE_WIRE_LIMIT"


def test_transparent_pixels_are_never_silently_flattened_for_jpeg():
    source = source_for(size=(180, 320), noise=True, alpha=True)
    args = derive_case(source, allow_lossy=True, max_wire=65000)
    with pytest.raises(CreatorError) as error:
        derive_references(**args)
    assert error.value.code == "MEDIA_REFERENCE_WIRE_LIMIT"
    refs, proofs = derive_references(**derive_case(source))
    assert refs[0]["mime_type"] == "image/png" and proofs[0]["pixel_equivalent"] is True


@pytest.mark.parametrize("mutation", ["bytes", "delivery_hash", "wrong_shot", "boolean_version", "wrong_dimensions"])
def test_invalid_or_foreign_source_is_rejected(mutation):
    args = derive_case()
    source = args["sources"][0]
    if mutation == "bytes":
        source["bytes_base64"] = base64.b64encode(b"bad-image").decode()
    elif mutation == "delivery_hash":
        source["delivery"]["delivery_hash"] = "0" * 64
    elif mutation == "wrong_shot":
        source["shot_id"] = "S02"
    elif mutation == "boolean_version":
        source["first_frame_version"] = True
    else:
        source["delivery"]["width"] = 91
        source["delivery"]["delivery_hash"] = digest({key: value for key, value in source["delivery"].items() if key != "delivery_hash"})
    with pytest.raises(CreatorError):
        derive_references(**args)


@pytest.mark.parametrize("field,value", [("source_delivery_id", "f" * 32), ("source_shot_id", "S02"),
                                       ("target_shot_id", "S03"), ("profile_hash", "0" * 64),
                                       ("output_sha256", "0" * 64), ("representation_version", True),
                                       ("pixel_equivalent", 1), ("visual_quality", "accepted")])
def test_rehashed_proof_cannot_change_source_target_profile_or_quality(field, value):
    args = derive_case()
    _, proofs = derive_references(**args)
    proofs[0][field] = value
    proofs[0]["derivation_hash"] = digest({key: item for key, item in proofs[0].items() if key != "derivation_hash"})
    with pytest.raises(CreatorError):
        references_from_proofs(**args, proofs=proofs)


def test_encoder_upgrade_can_validate_stored_bytes_without_overwriting_them(monkeypatch):
    args = derive_case()
    refs, proofs = derive_references(**args)
    upgraded = dict(reference_derivation.encoder_identity(), pillow="upgraded-encoder")
    monkeypatch.setattr(reference_derivation, "encoder_identity", lambda: upgraded)
    before = copy.deepcopy(proofs)
    assert references_from_proofs(**args, proofs=proofs, reencode=False) == refs
    assert proofs == before
    with pytest.raises(CreatorError) as error:
        references_from_proofs(**args, proofs=proofs, reencode=True)
    assert error.value.code == "MEDIA_REFERENCE_ENCODER_MISMATCH"


def test_proof_cannot_invent_an_encoder_different_from_the_disclosed_profile():
    args = derive_case()
    _, proofs = derive_references(**args)
    proofs[0]["encoder"]["pillow"] = "invented-history"
    proofs[0]["derivation_hash"] = digest({key: value for key, value in proofs[0].items() if key != "derivation_hash"})
    with pytest.raises(CreatorError):
        references_from_proofs(**args, proofs=proofs)


@pytest.mark.parametrize("value", [{"allow_lossy_jpeg": True}, {"allow_lossy_jpeg": 1}, None])
def test_profile_requires_its_exact_frozen_contract(value):
    with pytest.raises(CreatorError):
        checked_profile(value)


def test_profile_hash_discloses_lossy_permission_and_caps_do_not_grow():
    no = ReferenceProfile(allow_lossy_jpeg=False).frozen()
    yes = ReferenceProfile(allow_lossy_jpeg=True).frozen()
    assert no["profile_hash"] != yes["profile_hash"] and yes["jpeg_qualities"] == [85, 75, 65]
    invalid = dict(yes, jpeg_qualities=[90, 80, 70])
    invalid["profile_hash"] = digest({key: value for key, value in invalid.items() if key != "profile_hash"})
    with pytest.raises(CreatorError):
        checked_profile(invalid)
