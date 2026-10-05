from app.schemas.report_export import ReportExportRecord, ReportExportRequest
from app.services.report_export_service import ReportExportService


class KtrExportService:
    def __init__(self, reports: ReportExportService) -> None:
        self.reports = reports

    def generate_summary(self, runtime, request: ReportExportRequest | None = None) -> ReportExportRecord:
        return self.reports.generate_ktr_summary(runtime, request)
