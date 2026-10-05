export type KtrInspectorSelection = {
  nodeName: string
  groupName: string
  materialName: string
  materialColor: string
  boundingBox?: {
    min: [number, number, number]
    max: [number, number, number]
  }
}

export const KTR_MODEL_INSPECTOR_FIELDS = [
  'nodeName',
  'groupName',
  'materialName',
  'materialColor',
  'boundingBox',
] as const
