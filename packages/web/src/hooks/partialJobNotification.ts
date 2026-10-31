export function partialJobNotificationMessage(failedTargets: string[]): string {
  return `Job delivery incomplete: ${failedTargets.join(', ')}`;
}
