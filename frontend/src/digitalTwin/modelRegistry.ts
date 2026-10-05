import type { DigitalTwinAsset } from '../types/digitalTwin'

export const digitalTwinModelPathConvention = {
  deviceRig: '/models/istiklal_c2/istiklal_c2_rigged.glb',
  targetRoot: '/models/targets',
  balloonFallback: '/models/targets/balloon_fallback.glb',
  unknownTarget: '/models/targets/unknown_target.glb',
} as const

export const targetAssetRegistryDraft: Pick<
  DigitalTwinAsset,
  'class_id' | 'label' | 'model_path' | 'source_file' | 'source_sha256' | 'source_size_bytes' | 'confidence_min' | 'status' | 'notes'
>[] = [
  {
    class_id: 'ballistic_missile',
    label: 'Balistik Füze',
    model_path: '/assets/targets/ballistic_missile.glb',
    source_file: 'object_18.model',
    source_sha256: '5a87103883f89d92206c9c2703ec63068a73403998d5df6dcf66b49490e50a08',
    source_size_bytes: 1593671,
    confidence_min: 0,
    status: 'available',
    notes: 'Supplied 3MF source, measured span 500 mm; browser LOD GLB.',
  },
  {
    class_id: 'helicopter',
    label: 'Helikopter',
    model_path: '/assets/targets/helicopter.glb',
    source_file: 'object_19.model',
    source_sha256: 'fc15f070f50663d813d7863df84a415aa732c410e9f121a7e9a7e8a36f37602e',
    source_size_bytes: 50215966,
    confidence_min: 0,
    status: 'available',
    notes: 'Supplied 3MF source, measured span 583 mm; browser LOD GLB.',
  },
  {
    class_id: 'f16',
    label: 'Savaş Uçağı',
    model_path: '/assets/targets/f16.glb',
    source_file: 'object_20.model',
    source_sha256: '20d7b043c78ddc50d36f47b199b7b6a52b81bf233a58fd158ec1daee592929bf',
    source_size_bytes: 57120249,
    confidence_min: 0,
    status: 'available',
    notes: 'Supplied 3MF source, measured span 500 mm; browser LOD GLB.',
  },
  {
    class_id: 'mini_micro_uav',
    label: 'Mini/Mikro İHA',
    model_path: '/assets/targets/mini_micro_uav.glb',
    source_file: 'object_21.model',
    source_sha256: '2ddf78ff54b12b59d142f8895d7d85959f0a343ba227a75b2627cc6455f42f0b',
    source_size_bytes: 58943183,
    confidence_min: 0,
    status: 'available',
    notes: 'Supplied 3MF source, measured span 375 mm; browser LOD GLB.',
  },
]
