export type ModelType = 'body_detector' | 'balloon_detector' | 'combined_detector' | 'color_classifier_adapter' | 'test_stub'
export type ModelFramework = 'ultralytics' | 'onnx' | 'opencv_stub' | 'external_adapter'

export interface ModelMetadata {
  model_id: string
  name: string
  version: string
  model_type: ModelType
  framework: ModelFramework
  file_path: string | null
  file_name: string | null
  file_size_bytes: number
  class_names: string[]
  input_size: number
  confidence_threshold: number
  iou_threshold: number
  status: string
  provided_by: string
  created_at: number
  last_validated_at: number | null
  last_test_result: Record<string, unknown> | null
  notes: string | null
  warnings: string[]
}

export interface ActiveModels {
  active_body_model_id: string | null
  active_balloon_model_id: string | null
  active_combined_model_id: string | null
  active_test_adapter: string | null
  updated_at: number
}

export interface InferenceDetection {
  detection_id: string
  class_id: number
  class_name: string
  confidence: number
  bbox_xyxy_pixel: number[]
  bbox_xywh_pixel: number[]
  bbox_yolo_normalized: number[]
  source: string
  is_balloon: boolean
}

export interface InferenceResult {
  frame_id: string
  source: string
  model_id: string | null
  adapter: string
  detections: InferenceDetection[]
  latency_ms: number
  warnings: string[]
  errors: string[]
  no_physical_command_generated: boolean
}

export interface SessionScenario {
  target_type: string
  team: string
  distance_m: string
  lane: string
  angle: string
  color_profile: string | null
  lighting: string
  lens_profile: string
  camera_resolution: string
  yolo_imgsz: number
  active_model_ids: string[]
  notes: string | null
}

export interface SessionRecord {
  session_id: string
  name: string
  created_at: number
  ended_at: number | null
  operator: string
  mode: string
  scenario: SessionScenario
  stats: {
    frame_count: number
    snapshot_count: number
    detection_count: number
    annotation_count: number
    duration_sec: number
  }
  safety: {
    dry_run: boolean
    hardware_enabled: boolean
    no_physical_command_generated: boolean
  }
  quality: string
}

export interface SnapshotResponse {
  session_id: string
  frame_id: string
  image_path: string
  metadata_path: string
  no_physical_command_generated: boolean
}

export interface AnnotationObject {
  object_id: string
  class_name: string
  class_id: number
  bbox_format: string
  bbox: number[]
  confidence: number | null
  is_balloon: boolean
  verified_by_operator: boolean
}

export interface AnnotationRecord {
  annotation_id: string
  session_id: string
  frame_id: string
  image_path: string
  source: string
  objects: AnnotationObject[]
  updated_at: number
}

export interface ReplayStatus {
  state: string
  session_id: string | null
  frame_index: number
  frame_count: number
  speed: number
  source: string
  current_frame_path: string | null
  no_physical_command_generated: boolean
  updated_at: number
}

export interface DatasetExportResult {
  dataset_id: string
  output_path: string
  data_yaml_path: string
  image_count: number
  label_count: number
  train_count: number
  val_count: number
  warnings: string[]
  no_physical_command_generated: boolean
}

export interface DatasetValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
  checked_items: number
}

export interface DatasetHealth {
  total_sessions: number
  total_images: number
  total_annotations: number
  class_distribution: Record<string, number>
  distance_distribution: Record<string, number>
  team_distribution: Record<string, number>
  lens_distribution: Record<string, number>
  model_distribution: Record<string, number>
  missing_metadata_warnings: string[]
  recommendations: string[]
  updated_at: number
}

export interface DataLabDetectionRecord {
  frame_id: number | string
  source: string
  camera_source_kind: string | null
  frame_origin: string | null
  detector_kind: string | null
  body_count: number
  balloon_count: number
  detections: Array<Record<string, unknown>>
  latency_ms: number | null
  camera_fps: number | null
  detector_fps: number | null
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DataLabSessionSummary {
  session_id: string
  name: string
  created_at: number
  ended_at: number | null
  mode: string
  scenario: Record<string, unknown>
  stats: Record<string, number>
  safety: Record<string, unknown>
  quality: string
  latest_detection: DataLabDetectionRecord | null
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DataLabStatus {
  generated_at: number
  sessions_count: number
  latest_session_id: string | null
  latest_detection: DataLabDetectionRecord | null
  export_root: string
  replay_status: string
  replay_ready: boolean
  advisory_only: boolean
  no_physical_command_generated: boolean
  warnings: string[]
}

export interface DataLabRecordResponse {
  accepted: boolean
  session: SessionRecord
  detection_record: DataLabDetectionRecord
  no_physical_command_generated: boolean
}

export interface DataLabExportResponse {
  accepted: boolean
  export_id: string
  created_at: number
  output_dir: string
  files: string[]
  sessions_count: number
  detection_events_count: number
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DataLabReplayResult {
  replay_id: string
  source_session_id: string | null
  frame_origin: string
  detector: string
  replay_status: string
  frames_replayed: number
  events_replayed: number
  detections_replayed: number
  advisory_only: boolean
  no_physical_command_generated: boolean
  replay_execution_not_physical: boolean
  created_at: number
  warnings: string[]
}

export interface DataLabAnnotationCandidate {
  candidate_id: string
  session_id: string
  frame_id: number | string
  class_name: string
  target_group: string
  bbox: number[] | Record<string, unknown> | null
  circle: Record<string, unknown> | null
  confidence: number | null
  source: string
  detector: string
  review_status: 'pending' | 'accepted' | 'rejected' | 'uncertain'
  reviewer_note: string | null
  advisory_only: boolean
  no_physical_command_generated: boolean
}

export interface DataLabDatasetHealth {
  sessions_count: number
  detection_events_count: number
  annotation_candidates: number
  accepted_annotations: number
  rejected_annotations: number
  uncertain_annotations: number
  class_distribution: Record<string, number>
  source_distribution: Record<string, number>
  dataset_ready_for_training: boolean
  reason: string
  advisory_only: boolean
  no_physical_command_generated: boolean
}
