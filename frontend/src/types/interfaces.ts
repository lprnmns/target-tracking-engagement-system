export interface InterfaceRecord {
  interface_id: string
  name: string
  display_name: string | null
  category: string
  category_label: string | null
  direction: string
  producer: string
  consumer: string
  transport: string
  protocol: string
  message_format: string
  endpoint_or_port: string
  update_rate: string
  data_fields: string[]
  safety_boundary: string
  failure_behavior: string
  verification_method: string
  ktr_description: string
  verification_status: string
  readiness_profile_dependency: string[]
  operator_visible: boolean
  export_evidence_path: string | null
}

export interface InterfaceInventoryResponse {
  generated_at: number
  interfaces: InterfaceRecord[]
  categories: Record<string, number>
  no_physical_command_generated: boolean
}

export interface InterfaceKtrSection {
  generated_at: number
  markdown: string
  plain_text: string
  no_physical_command_generated: boolean
}

export interface InterfaceExportRecord {
  export_id: string
  created_at: number
  output_dir: string
  files: string[]
  no_physical_command_generated: boolean
}
