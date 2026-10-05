from app.services.motion_service import MotionService


class TurretService:
    def __init__(self, motion: MotionService) -> None:
        self.motion = motion
