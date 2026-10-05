"""FAZ 1 / T06: Kural 1 (gövdesiz balon reddi) ve Kural 2 (%250 alt ROI).

Atölye yanlış pozitifi: BALLOON_AIRCRAFT modunda gövde modeli hiçbir hava
aracı görmediği halde balon modeli kırmızı gövde silüetine tetikleniyor ve
kokpitte "HEDEF BALONU #3 | ATEŞ SERBEST" olarak etiketleniyordu.  Kök
nedenler: (1) gövde yokluğunda balonların korunduğu workshop fallback'u,
(2) gövde silüetini içine alan eski ROI (üst sınır top-0.2h), (3) 2.8h
derinlik.  Bu sözleşme stage3_roi modülündeki paylaşılan geometriyi ve
her iki servisin o modüle bağlılığını doğrular.
"""

from unittest.mock import MagicMock

import pytest

from app.schemas.vision import BBox, BalloonDetection, BodyDetection
from app.services import stage3_roi
from app.services.body_balloon_association_service import BodyBalloonAssociationService
from app.services.vision_pipeline import VisionPipeline


def _body(track_id: int = 1, x: int = 100, y: int = 80, w: int = 120, h: int = 60) -> BodyDetection:
    return BodyDetection(
        id=track_id,
        track_id=track_id,
        class_name="mini_micro_uav",
        class_id=0,
        confidence=0.9,
        target_team="enemy",
        bbox=BBox(x=x, y=y, w=w, h=h),
    )


def _balloon(identifier: int, x: int, y: int, w: int = 20, h: int = 20) -> BalloonDetection:
    return BalloonDetection(
        id=identifier,
        confidence=0.9,
        bbox=BBox(x=x - w // 2, y=y - h // 2, w=w, h=h),
        center_x=x,
        center_y=y,
    )


def _pipeline() -> VisionPipeline:
    return VisionPipeline(camera=MagicMock(), vision=MagicMock())


# Reference body (100, 80, 120, 60): Kural 2 ROI = x [70, 250], y [140, 392].
ROI = (70.0, 250.0, 140.0, 392.0)


def test_kural2_roi_boundary_contract() -> None:
    roi = stage3_roi.attachment_roi(_body().bbox)
    assert roi == ROI

    assert stage3_roi.attachment_roi_contains(_body().bbox, 160, 200) is True
    # Inclusive horizontal tolerance edges (+-25% of body width).
    assert stage3_roi.attachment_roi_contains(_body().bbox, 70, 200) is True
    assert stage3_roi.attachment_roi_contains(_body().bbox, 250, 200) is True
    assert stage3_roi.attachment_roi_contains(_body().bbox, 69, 200) is False
    assert stage3_roi.attachment_roi_contains(_body().bbox, 251, 200) is False
    # Vertical window: [y+h, y+h+4.2h] — nothing inside the fuselage.
    assert stage3_roi.attachment_roi_contains(_body().bbox, 160, 140) is True
    assert stage3_roi.attachment_roi_contains(_body().bbox, 160, 392) is True
    assert stage3_roi.attachment_roi_contains(_body().bbox, 160, 139) is False
    assert stage3_roi.attachment_roi_contains(_body().bbox, 160, 393) is False


def test_kural2_rejects_fuselage_silhouette_as_balloon() -> None:
    """The balloon model firing ON the red fuselage must never survive.

    Centre (160, 110) sits inside the body bbox.  The old inline geometry
    started at ``top - 0.2h`` (128) and accepted it; Kural 2 starts at the
    lower edge (140) and rejects the silhouette.
    """
    body = _body()
    assert stage3_roi.attachment_roi_contains(body.bbox, 160, 110) is False

    kept = VisionPipeline._balloons_inside_attachment_regions(
        [_balloon(1, 160, 110)], [body]
    )
    assert kept == []


def test_l4_overlap_rejects_balloon_box_on_body_box() -> None:
    """IoU >= 0.05 means one object seen twice, not a tethered balloon."""
    body = _body()
    # Centre (170, 140) is inside the Kural 2 ROI, but the 40x40 balloon box
    # overlaps the fuselage (IoU ~0.28): only L4 can reject it.
    overlapping = BalloonDetection(
        id=1, confidence=0.9, bbox=BBox(x=150, y=120, w=40, h=40), center_x=170, center_y=140
    )
    assert stage3_roi.attachment_roi_contains(body.bbox, 170, 140) is True
    assert stage3_roi.body_balloon_overlap_reject(body.bbox, overlapping.bbox) is True

    kept = VisionPipeline._balloons_inside_attachment_regions([overlapping], [body])
    assert kept == []

    # A genuinely tethered balloon below the fuselage passes both gates.
    below = _balloon(2, 160, 250)
    assert VisionPipeline._balloons_inside_attachment_regions([below], [body]) == [below]


def test_kural1_workshop_fp_no_body_no_balloon() -> None:
    """Atölye FP'si: gövde yokken balon ASLA bağımsız hedef olamaz."""
    pipeline = _pipeline()
    balloons = [_balloon(1, 160, 180), _balloon(2, 400, 300)]

    assert pipeline._filter_balloons_by_attachment_regions(balloons, []) == []
    assert VisionPipeline._balloons_inside_attachment_regions(balloons, []) == []


def test_kural1_balloon_kept_when_body_present() -> None:
    pipeline = _pipeline()
    balloon = _balloon(1, 160, 180)

    kept = pipeline._filter_balloons_by_attachment_regions([balloon], [_body()])
    assert [item.id for item in kept] == [1]


def test_kural2_depth_cap_applied_in_pipeline_filter() -> None:
    """4.2h derinlik sınırı."""
    pipeline = _pipeline()
    body = _body()

    within = _balloon(1, 160, 385)
    beyond = _balloon(2, 160, 400)
    kept = pipeline._filter_balloons_by_attachment_regions([within, beyond], [body])

    assert [item.id for item in kept] == [1]


def test_association_service_uses_shared_kural2_roi() -> None:
    body = _body()

    # Horizontal tolerance (+-25% w): x=72 was rejected by the old strict
    # [x, x+w] gate; Kural 2 accepts the tether swing-out.
    assert BodyBalloonAssociationService._inside_attachment_region(72, 150, body) is True
    # 4.2h depth: y=385 is accepted.
    assert BodyBalloonAssociationService._inside_attachment_region(160, 385, body) is True
    # Fuselage interior and beyond the right edge stay rejected.
    assert BodyBalloonAssociationService._inside_attachment_region(160, 120, body) is False
    assert BodyBalloonAssociationService._inside_attachment_region(255, 200, body) is False


def test_both_services_route_through_stage3_roi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tek kaynak kanıtı: inline geometri kopyası kalmadı."""

    def _boom(*args, **kwargs):
        raise AssertionError("inline geometry copy: stage3_roi.attachment_roi_contains not used")

    monkeypatch.setattr(stage3_roi, "attachment_roi_contains", _boom)

    body = _body()
    balloon = _balloon(1, 160, 180)

    with pytest.raises(AssertionError):
        _pipeline()._filter_balloons_by_attachment_regions([balloon], [body])
    with pytest.raises(AssertionError):
        VisionPipeline._balloons_inside_attachment_regions([balloon], [body])
    with pytest.raises(AssertionError):
        BodyBalloonAssociationService._inside_attachment_region(160, 180, body)
