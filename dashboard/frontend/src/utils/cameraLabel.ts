/** Hide the device-side compatibility id from dashboard users. */
export function cameraDisplayName(cameraId: string): string {
  return cameraId === 'legacy' ? 'cam0' : cameraId
}
